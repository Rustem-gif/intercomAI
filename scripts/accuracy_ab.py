"""PASS/FAIL agreement with QA, per grading configuration ("arm"), without saving a grade.

The client's bar is ≥ 95% agreement between the AI's PASS/FAIL and QA's. This grades the chats
QA has reviewed once per arm and reports, per arm, the result agreement with QA, the false
FAILs / false PASSes and the criteria behind them. Arms are `+`-joined switches:

    base      the ruleset as it is (no history, Jev off, fail verifier off)
    hist      + player history and QA precedents (qa/history_context.py)
    jev       + Jev in reconcile mode
    verify    + the fail verifier (qa/fail_verifier.py) in `on` mode
    vshadow   + the fail verifier in `shadow` mode (records, changes nothing)
    vote      + the majority vote on borderline chats (QA_VOTES=3; off in every other arm)

    .venv/bin/python scripts/accuracy_ab.py --set dev --arms base,verify
    .venv/bin/python scripts/accuracy_ab.py --set holdout --arms verify --live
    .venv/bin/python scripts/accuracy_ab.py --set sample:kb-v41-180 --arms verify

Sets: `reviewed` (every kb-v41 chat a human re-scored or confirmed), `dev` / `holdout` (its
split — qa/agreement.split_of; tune prompts on dev only), `sample:<id>` (a calibration sample;
human labels from its newest run's QA verdicts, if any), or `--ids`.

The prompt is the worktree's live prompt file, so a prompt variant is a separate invocation;
each output records the rules_version it ran on. Output: EVAL_DIR/accuracy_ab_<time>.json with
every chat's verdicts and evidence (scripts/agreement_report.py reads it). Nothing is written
to `grades`. Runs through the Message Batches API unless --live.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import time
from collections import Counter
from datetime import datetime, timezone

from history_ab import compare, grounding

from intercom_summary.qa.agreement import paired_agreement, result_of, split_of
from intercom_summary.settings import settings
from intercom_summary.storage.conversations_store import ConversationsStore, tags_are_ignored

ARM_SWITCHES = {"base", "hist", "jev", "verify", "vshadow"}


def reviewed(ruleset: str) -> dict[str, dict]:
    """Every chat of `ruleset` a human re-scored or confirmed, with the human's label."""
    db = sqlite3.connect(settings.db_path)
    try:
        cols = {r[1] for r in db.execute("PRAGMA table_info(grades)")}
        confirmed = "human_confirmed_at IS NOT NULL" if "human_confirmed_at" in cols else "0"
        rows = db.execute(
            f"SELECT conversation_id, overall_score, human_score, human_criteria, "
            f"override_reason, {confirmed} FROM grades WHERE ruleset_id = ? "
            f"AND (human_score IS NOT NULL OR {confirmed})", (ruleset,)).fetchall()
    finally:
        db.close()
    out = {}
    for cid, ai, hs, hc, reason, conf in rows:
        stored = result_of(ai)
        out[cid] = {"human": hs if hs is not None else ai,
                    "human_result": result_of(hs) if hs is not None else stored,
                    "stored_ai": ai, "stored_result": stored,
                    "human_criteria": json.loads(hc) if hc else {}, "reason": reason or "",
                    "confirmed": bool(conf) and hs is None}
    return out


def sample_labels(sample_id: str) -> dict[str, dict]:
    """A calibration sample's members with QA's verdict: the sample's imported reference label
    (CalibrationStore.labels), else the newest review made on one of its runs."""
    from intercom_summary.storage.calibration_store import CalibrationStore

    db = sqlite3.connect(settings.db_path)
    try:
        ids = [r[0] for r in db.execute(
            "SELECT conversation_id FROM calibration_sample_items WHERE sample_id = ? ORDER BY seq",
            (sample_id,))]
        reviews = {}
        for cid, hs, crit, note in db.execute(
                "SELECT r.conversation_id, r.human_score, r.human_criteria, r.human_note "
                "FROM calibration_results r JOIN calibration_runs u ON u.id = r.run_id "
                "WHERE u.sample_id = ? AND r.human_score IS NOT NULL ORDER BY u.started_at",
                (sample_id,)):
            reviews[cid] = {"human": hs, "human_result": result_of(hs),   # newest run wins
                            "human_criteria": json.loads(crit) if crit else {}, "reason": note or ""}
    finally:
        db.close()
    cal = CalibrationStore()
    try:
        ref = cal.labels(sample_id)
    finally:
        cal.close()
    out = {}
    for cid in ids:
        lab = ref.get(cid)
        if lab:
            hum = {"human": lab["human_score"],
                   "human_result": lab["human_result"] or result_of(lab["human_score"]),
                   "human_criteria": lab["human_criteria"], "reason": lab["human_note"] or ""}
        else:
            hum = reviews.get(cid) or {"human": None, "human_result": None,
                                       "human_criteria": {}, "reason": ""}
        out[cid] = {**hum, "stored_ai": None, "stored_result": None, "confirmed": False}
    return out


def record(c, g, hum: dict, dt: float) -> dict:
    u = g.usage or {}
    gr, tot = grounding(g, c.transcript_text(include_bots=False))
    return {
        "id": c.id, "split": split_of(c.id), "score": g.overall_score, "result": g.overall_result,
        "human": hum.get("human"), "human_result": hum.get("human_result"),
        "stored_ai": hum.get("stored_ai"), "reason": hum.get("reason", ""),
        "human_criteria": hum.get("human_criteria", {}),
        "votes": g.votes,
        "fails": sorted(r.rule_id for r in g.rule_results if r.verdict == "fail"),
        "cannot": sorted(r.rule_id for r in g.rule_results if r.verdict == "cannot_determine"),
        "criteria": {r.rule_id: [r.verdict, r.evidence] for r in g.rule_results
                     if r.verdict in ("fail", "cannot_determine")},
        "requests": g.requests if hasattr(g, "requests") else [],
        "manual_review": bool(g.manual_review_needed),
        "case_type": g.case_type, "outcome": g.outcome_status,
        "flags": list(g.signal_flags or []),
        "verifier": getattr(g, "fail_verifier", {}) or {},
        "grounded": gr, "fail_quotes": tot,
        "cost_usd": u.get("cost_usd", 0.0), "output_tokens": u.get("output", 0),
        "seconds": round(dt, 1),
    }


def build_grader(switches: set[str], ruleset: str, effort: str | None):
    from intercom_summary.qa.fail_verifier import FailVerifier
    from intercom_summary.qa.grader import Grader
    from intercom_summary.qa.history_context import HistoryProvider
    from intercom_summary.qa.jev_verifier import JevVerifier

    jev = False
    if "jev" in switches:
        object.__setattr__(settings, "jev_mode", "reconcile")
        jev = JevVerifier()
    verifier = False
    if "verify" in switches or "vshadow" in switches:
        verifier = FailVerifier(mode="on" if "verify" in switches else "shadow")
    return Grader(ruleset_id=ruleset, effort=effort, jev=jev, fail_verifier=verifier,
                  history=HistoryProvider(mode="on") if "hist" in switches else False,
                  votes=settings.qa_votes if "vote" in switches else 1)


def run_arm(arm: str, convos: list, hum: dict, *, ruleset: str, effort: str | None,
            batch: bool) -> tuple[list[dict], str]:
    from intercom_summary.qa import batch as batch_mod

    switches = set(arm.split("+"))
    grader = build_grader(switches, ruleset, effort)
    if grader.history is not None:
        grader.history.prepare(convos)
    out: list[dict] = []
    if not batch:
        for c in convos:
            t0 = time.time()
            try:
                g = grader.grade(c)
            except Exception as exc:  # noqa: BLE001
                print(f"  {c.id}: FAILED — {exc}")
                continue
            out.append(record(c, g, hum.get(c.id, {}), time.time() - t0))
            print(f"  {c.id}: {g.overall_score} {g.overall_result}  human {hum.get(c.id, {}).get('human')}")
        return out, grader.rules_version
    t0 = time.time()
    warm = batch_mod.warm_up_set(grader, convos) if len(convos) > 1 else []
    for c in warm:
        g = grader.grade(c, batch_shape=True)
        out.append(record(c, g, hum.get(c.id, {}), time.time() - t0))
    rest = [c for c in convos if c not in warm]
    by_id = {c.id: c for c in rest}
    ids = batch_mod.submit(grader, rest)
    print(f"  batch {', '.join(ids)}: {len(rest)} chats — waiting…")
    batch_mod.wait(grader.client, ids, on_status=lambda st: print(f"    {st['counts']}"), poll=20)
    dt = time.time() - t0
    for custom_id, result in batch_mod.results(grader.client, ids):
        c = by_id[custom_id]
        try:
            g, _ = batch_mod.grade_result(grader, c, result)
        except Exception as exc:  # noqa: BLE001
            print(f"  {c.id}: FAILED — {exc}")
            continue
        out.append(record(c, g, hum.get(c.id, {}), dt / max(len(rest), 1)))
    print(f"  batch ended after {dt:.0f}s")
    return out, grader.rules_version


def summarise(arm: str, rows: list[dict]) -> dict:
    ai = {r["id"]: r["result"] for r in rows}
    human = {r["id"]: r["human_result"] for r in rows if r["human_result"]}
    agree = paired_agreement(ai, human)
    false_fail = [r for r in rows if r["result"] == "FAIL" and r["human_result"] == "PASS"]
    false_pass = [r for r in rows if r["result"] == "PASS" and r["human_result"] == "FAIL"]
    n = len(rows) or 1
    return {
        "arm": arm, "n": len(rows), "labelled": agree["n"], "agree": agree["same"],
        "agreement": agree["rate"], "ci95": agree["ci95"],
        "false_fail": len(false_fail), "false_pass": len(false_pass),
        "false_fail_criteria": Counter(x for r in false_fail for x in r["fails"]).most_common(12),
        "ai_fail_rate": round(sum(r["result"] == "FAIL" for r in rows) / n, 3),
        "manual_review_rate": round(sum(r["manual_review"] for r in rows) / n, 3),
        "verified": sum(1 for r in rows if r["verifier"].get("checked")),
        "verifier_overturned": sum(len(r["verifier"].get("overturned") or []) for r in rows),
        "cost_per_chat": round(sum(r["cost_usd"] for r in rows) / n, 4),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--set", default="dev", help="reviewed | dev | holdout | sample:<id>")
    ap.add_argument("--ids", nargs="*")
    ap.add_argument("-n", type=int, help="only the first N chats of the set")
    ap.add_argument("--ruleset", default="kb-v41")
    ap.add_argument("--arms", default="base", help="comma-separated, e.g. base,verify,hist+verify")
    ap.add_argument("--effort", choices=["low", "medium", "high", "xhigh", "max"])
    ap.add_argument("--live", action="store_true", help="live calls instead of the Batch API")
    args = ap.parse_args()
    object.__setattr__(settings, "jev_mode", "off")

    if args.set.startswith("sample:"):
        hum = sample_labels(args.set.split(":", 1)[1])
    else:
        hum = reviewed(args.ruleset)
        if args.set in ("dev", "holdout"):
            hum = {k: v for k, v in hum.items() if split_of(k) == args.set}
    ids = args.ids or sorted(hum)
    if args.n:
        ids = ids[:args.n]
    cs = ConversationsStore()
    try:
        convos = [c for cid in ids if (c := cs.get(cid)) is not None and not tags_are_ignored(c.tags)]
    finally:
        cs.close()
    arms = [a.strip() for a in args.arms.split(",") if a.strip()]
    for arm in arms:
        if bad := set(arm.split("+")) - ARM_SWITCHES:
            raise SystemExit(f"unknown arm switch(es) {sorted(bad)} — use {sorted(ARM_SWITCHES)}")
    print(f"{len(convos)} chats ({args.set}) · ruleset {args.ruleset} · model {settings.qa_model} · "
          f"effort {args.effort or settings.qa_effort} · arms {arms} · {'live' if args.live else 'batch'}\n")

    results: dict[str, list[dict]] = {}
    versions: dict[str, str] = {}
    for arm in arms:
        print(f"== arm: {arm}")
        results[arm], versions[arm] = run_arm(arm, convos, hum, ruleset=args.ruleset,
                                              effort=args.effort, batch=not args.live)
    summaries = {arm: summarise(arm, rows) for arm, rows in results.items()}
    pairs = {f"{a}→{b}": compare([{**r, "delta": None} for r in results[a]],
                                 [{**r, "delta": None} for r in results[b]])
             for a, b in zip(arms, arms[1:])}

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    tag = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in args.set)
    out = settings.eval_dir / f"accuracy_ab_{tag}_{stamp}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"set": args.set, "ids": ids, "effort": args.effort or settings.qa_effort,
                               "model": settings.qa_model, "ruleset": args.ruleset,
                               "rules_version": versions, "summaries": summaries, "pairs": pairs,
                               "rows": results}, indent=1, ensure_ascii=False))

    keys = ["n", "labelled", "agree", "agreement", "ci95", "false_fail", "false_pass",
            "ai_fail_rate", "manual_review_rate", "verified", "verifier_overturned", "cost_per_chat"]
    print("\n| metric | " + " | ".join(arms) + " |")
    print("|---|" + "---|" * len(arms))
    for k in keys:
        print(f"| {k} | " + " | ".join(str(summaries[a][k]) for a in arms) + " |")
    for arm in arms:
        print(f"\n{arm} false-FAIL criteria: {summaries[arm]['false_fail_criteria']}")
    for name, p in pairs.items():
        print(f"\n{name}: result flips {len(p['result_flips'])}")
        for i, ra, rb, h in p["result_flips"]:
            print(f"   flip {i}: {ra} → {rb} (human {h})")
    print(f"\nsaved {out}")


if __name__ == "__main__":
    main()
