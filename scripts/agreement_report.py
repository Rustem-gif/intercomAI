"""How far are we from the client's bar — PASS/FAIL agreement with QA ≥ 95%?

    .venv/bin/python scripts/agreement_report.py                       # live grades only
    .venv/bin/python scripts/agreement_report.py data/eval/accuracy_ab_X.json [more.json]

Live: the population-weighted agreement of the stored kb-v41 grades (qa/agreement.py) — what
the Accuracy page shows — overall and for the last 30 days.

Per A/B arm (scripts/accuracy_ab.py output): agreement with QA on the dev and holdout splits,
the false FAILs / PASSes and the criteria behind them, and the PROJECTED population agreement
had the stored grades been made by that arm:

    projected = [ N_stored_PASS · agree_PASS_side  +  N_stored_FAIL · agree(arm on reviewed FAILs) ] / N

The reviewed chats are nearly all stored-AI-FAILs, so the arm's rate on them stands in for the
whole FAIL side. The PASS side comes from the false-PASS audit sample when QA has reviewed it
(--pass-sample), else from --pass-side (default 1.0 — an upper bound; say so when quoting it).

--pass-run <accuracy_ab json on the audit sample>: the arm's FAIL share on chats the stored grades
passed. Until QA labels them, each such new FAIL is counted as a disagreement (QA rarely fails a
chat the AI passed), so the PASS side becomes (QA PASS-side rate) × (1 − new FAIL share).

A `vshadow` arm (verifier recorded, not applied) is also scored as if the verifier had been on
("<arm>→on"), by re-scoring each chat with its overturned criteria passed.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter
from datetime import datetime, timedelta, timezone

from intercom_summary.qa.agreement import paired_agreement, result_of
from intercom_summary.qa.schema import score_from_verdicts
from intercom_summary.settings import settings
from intercom_summary.storage.grades_store import GradesStore


def pct(x) -> str:
    return "—" if x is None else f"{100 * x:.1f}%"


def live(ruleset: str) -> None:
    store = GradesStore()
    try:
        since = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        for label, kw in (("all time", {}), ("last 30 days", {"since": since})):
            r = store.result_agreement(ruleset, **kw)
            p, f = r["sides"]["PASS"], r["sides"]["FAIL"]
            print(f"live {ruleset} ({label}): estimate {pct(r['estimate'])} "
                  f"(95% {r['ci95'] and ' – '.join(pct(x) for x in r['ci95'])}) · "
                  f"reviewed-only {pct(r['reviewed_only'])} · known disagreements {r['known_disagreements']}")
            print(f"   AI FAIL: {f['agreed']}/{f['reviewed']} agree of {f['population']} · "
                  f"AI PASS: {p['agreed']}/{p['reviewed']} agree of {p['population']} · "
                  f"{r['overridden']} re-scored, {r['confirmed']} agreed")
    finally:
        store.close()


def stored_split(ruleset: str) -> tuple[int, int]:
    db = sqlite3.connect(settings.db_path)
    try:
        rows = db.execute("SELECT overall_score FROM grades WHERE ruleset_id = ?", (ruleset,)).fetchall()
    finally:
        db.close()
    n_pass = sum(1 for (s,) in rows if result_of(s) == "PASS")
    return n_pass, len(rows) - n_pass


def pass_side_from_sample(sample_id: str) -> float | None:
    """Share of the audit's AI-PASS chats QA also passed — read from the stored grades QA
    agreed with or re-scored (the audit never re-grades anything)."""
    db = sqlite3.connect(settings.db_path)
    try:
        rows = db.execute(
            "SELECT g.overall_score, g.human_score FROM grades g "
            "JOIN calibration_sample_items i ON i.conversation_id = g.conversation_id "
            "WHERE i.sample_id = ? AND (g.human_score IS NOT NULL OR g.human_confirmed_at IS NOT NULL)",
            (sample_id,)).fetchall()
    finally:
        db.close()
    if not rows:
        return None
    return sum(result_of(hs if hs is not None else s) == "PASS" for s, hs in rows) / len(rows)


def with_verifier(row: dict, ruleset: str) -> str:
    """The result had the shadow verifier's overturns been applied."""
    over = set((row.get("verifier") or {}).get("overturned") or [])
    if not over:
        return row["result"]
    verdicts = {cid: v for cid, (v, _ev) in row["criteria"].items()}
    for cid in over:
        verdicts[cid] = "pass"
    return score_from_verdicts(verdicts, ruleset_id=ruleset)[2]


def arm_report(name: str, rows: list[dict], results: dict[str, str], stored: tuple[int, int],
               pass_side: float) -> None:
    human = {r["id"]: r["human_result"] for r in rows if r["human_result"]}
    print(f"\n== {name}")
    for split in ("dev", "holdout", None):
        ids = {r["id"] for r in rows if split is None or r["split"] == split}
        a = paired_agreement({k: v for k, v in results.items() if k in ids}, human)
        print(f"   {split or 'all':8} n={a['n']:4}  agreement {pct(a['rate'])} "
              f"(95% {a['ci95'] and ' – '.join(pct(x) for x in a['ci95'])})  "
              f"false FAIL {len(a['a_fail_b_pass'])}  false PASS {len(a['a_pass_b_fail'])}")
    by_id = {r["id"]: r for r in rows}
    ff = [by_id[k] for k in paired_agreement(results, human)["a_fail_b_pass"]]
    crit = Counter(c for r in ff for c in r["fails"]
                   if c not in set((r.get("verifier") or {}).get("overturned") or [])
                   or results[r["id"]] == r["result"])
    print(f"   false-FAIL criteria: {crit.most_common(10)}")
    # Projection over every stored grade: the reviewed set is (almost) all stored FAILs.
    fail_rows = [r for r in rows if r.get("stored_ai") is not None and result_of(r["stored_ai"]) == "FAIL"
                 and r["human_result"]]
    if fail_rows:
        agree_fail = sum(results[r["id"]] == r["human_result"] for r in fail_rows) / len(fail_rows)
        n_pass, n_fail = stored
        proj = (n_pass * pass_side + n_fail * agree_fail) / (n_pass + n_fail)
        print(f"   projected population agreement {pct(proj)}  "
              f"(FAIL side {pct(agree_fail)} on {len(fail_rows)} reviewed stored-FAILs × {n_fail}; "
              f"PASS side {pct(pass_side)} × {n_pass})")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="*")
    ap.add_argument("--ruleset", default="kb-v41")
    ap.add_argument("--pass-sample", default="kb-v41-pass-audit")
    ap.add_argument("--pass-side", type=float, default=1.0)
    ap.add_argument("--pass-run", help="accuracy_ab JSON of the arm on the false-PASS audit sample")
    args = ap.parse_args()

    live(args.ruleset)
    if not args.files:
        return
    stored = stored_split(args.ruleset)
    audited = pass_side_from_sample(args.pass_sample)
    pass_side = audited if audited is not None else args.pass_side
    print(f"\nPASS side: {pct(pass_side)} "
          f"({'from the audit sample ' + args.pass_sample if audited is not None else 'ASSUMED — no audit verdicts yet'})")
    if args.pass_run:
        prow = next(iter(json.load(open(args.pass_run))["rows"].values()))
        on = [with_verifier(r, args.ruleset) if r.get("verifier") else r["result"] for r in prow]
        flip = sum(x == "FAIL" for x in on) / len(on)
        pass_side *= 1 - flip
        print(f"   × (1 − {pct(flip)} new FAILs on {len(prow)} stored-PASS audit chats, verifier applied) "
              f"= {pct(pass_side)}")
    for path in args.files:
        d = json.load(open(path))
        print(f"\n### {path} · set {d.get('set')} · rules {d.get('rules_version')}")
        for arm, rows in d["rows"].items():
            results = {r["id"]: r["result"] for r in rows}
            arm_report(arm, rows, results, stored, pass_side)
            if any(r.get("verifier") for r in rows) and "vshadow" in arm:
                arm_report(f"{arm}→on (simulated)", rows,
                           {r["id"]: with_verifier(r, args.ruleset) for r in rows}, stored, pass_side)


if __name__ == "__main__":
    main()
