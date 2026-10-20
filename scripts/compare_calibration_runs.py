"""Compare two calibration runs of the same sample, chat by chat.

The consistency question the client asks after every grader change: graded twice, does the
same chat get the same answer? Reads `calibration_results` only — nothing is written.

    .venv/bin/python scripts/compare_calibration_runs.py RUN_A RUN_B
    .venv/bin/python scripts/compare_calibration_runs.py RUN_A RUN_B --list   # every flip

Reports, over the chats graded in both runs:
  * identical scores, identical results (PASS / FAIL / CANNOT DETERMINE), identical bands;
  * severe flips — one run clean (PASS, or Excellent/Good) and the other Major or Critical;
  * the criteria that flip most often, and what they flip between;
  * per run: result counts, Major / Critical counts, manual review, mean score, cost.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from intercom_summary.settings import settings  # noqa: E402

RESULTS = ("PASS", "FAIL", "CANNOT DETERMINE")


def _load(conn: sqlite3.Connection, run_id: str) -> tuple[dict, dict[str, dict]]:
    run = conn.execute("SELECT * FROM calibration_runs WHERE id=?", (run_id,)).fetchone()
    if run is None:
        sys.exit(f"No calibration run {run_id!r}")
    rows = conn.execute(
        "SELECT conversation_id, payload_json FROM calibration_results "
        "WHERE run_id=? AND status='graded'", (run_id,)).fetchall()
    return dict(run), {cid: json.loads(p) for cid, p in rows if p}


def _result(g: dict) -> str:
    # Runs made before the CANNOT DETERMINE rule only ever stored PASS / FAIL.
    return g.get("overall_result") or ""


def _verdicts(g: dict) -> dict[str, str]:
    return {r["rule_id"]: r["verdict"] for r in g.get("rule_results") or []}


def _is_severe(g: dict) -> bool:
    return bool(g.get("critical_fail") or g.get("major_outcome_failure")
                or g.get("severity") in ("Critical", "Major"))


def _is_clean(g: dict) -> bool:
    return _result(g) == "PASS" or g.get("band") in ("Excellent", "Good")


def _stats(run: dict, grades: dict[str, dict]) -> list[str]:
    n = len(grades) or 1
    res = Counter(_result(g) for g in grades.values())
    scores = [g.get("overall_score", 0) for g in grades.values()]
    return [
        f"run {run['id']}  ({run['started_at'][:16]}, rules {run['rules_version']}, "
        f"${run['cost_usd'] or 0:.2f})",
        f"  graded {len(grades)}  mean score {sum(scores) / n:.1f}",
        "  results " + "  ".join(f"{r} {res.get(r, 0)}" for r in RESULTS),
        f"  Critical {sum(bool(g.get('critical_fail')) for g in grades.values())}  "
        f"Major {sum(g.get('severity') == 'Major' for g in grades.values())}  "
        f"manual review {sum(bool(g.get('manual_review_needed')) for g in grades.values())}",
    ]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("run_a")
    ap.add_argument("run_b")
    ap.add_argument("--list", action="store_true", help="print every chat whose result flips")
    args = ap.parse_args()

    conn = sqlite3.connect(f"file:{settings.db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    run_a, a = _load(conn, args.run_a)
    run_b, b = _load(conn, args.run_b)
    both = sorted(set(a) & set(b))
    n = len(both) or 1

    for line in _stats(run_a, a) + _stats(run_b, b):
        print(line)
    print()

    same_score = sum(a[c].get("overall_score") == b[c].get("overall_score") for c in both)
    same_result = sum(_result(a[c]) == _result(b[c]) for c in both)
    same_band = sum(a[c].get("band") == b[c].get("band") for c in both)
    severe = [c for c in both if (_is_clean(a[c]) and _is_severe(b[c]))
              or (_is_clean(b[c]) and _is_severe(a[c]))]
    deltas = [abs((a[c].get("overall_score") or 0) - (b[c].get("overall_score") or 0))
              for c in both]
    print(f"chats in both runs: {len(both)}")
    print(f"  identical score   {same_score:4d}  ({100 * same_score / n:.1f}%)")
    print(f"  identical result  {same_result:4d}  ({100 * same_result / n:.1f}%)")
    print(f"  identical band    {same_band:4d}  ({100 * same_band / n:.1f}%)")
    print(f"  mean |Δscore|     {sum(deltas) / n:.2f}   max {max(deltas, default=0)}")
    print(f"  severe flips (clean in one run, Major/Critical in the other): {len(severe)}")

    transitions = Counter((_result(a[c]), _result(b[c])) for c in both
                          if _result(a[c]) != _result(b[c]))
    if transitions:
        print("  result transitions A → B:")
        for (ra, rb), k in transitions.most_common():
            print(f"    {ra or '—'} → {rb or '—'}: {k}")

    flips: Counter = Counter()
    kinds: dict[str, Counter] = {}
    for c in both:
        va, vb = _verdicts(a[c]), _verdicts(b[c])
        for cid in set(va) | set(vb):
            if va.get(cid) != vb.get(cid):
                flips[cid] += 1
                pair = " ↔ ".join(sorted((va.get(cid) or "—", vb.get(cid) or "—")))
                kinds.setdefault(cid, Counter())[pair] += 1
    # pass ↔ n/a moves nothing; a flip touching fail or cannot_determine changes the result.
    material = sum(k for kc in kinds.values() for pair, k in kc.items()
                   if "fail" in pair.split(" ↔ ") or "cannot_determine" in pair)
    print(f"\ncriterion verdict flips: {sum(flips.values())} across {len(flips)} criteria "
          f"({material} involve fail or cannot_determine)")
    for cid, k in flips.most_common(10):
        detail = ", ".join(f"{p} {m}" for p, m in kinds[cid].most_common(3))
        print(f"  {cid:28s} {k:4d}   {detail}")

    if args.list:
        print("\nchats whose result differs:")
        for c in both:
            if _result(a[c]) != _result(b[c]) or c in severe:
                print(f"  {c}  {a[c].get('overall_score'):>3} {_result(a[c]):17s} "
                      f"→ {b[c].get('overall_score'):>3} {_result(b[c]):17s}"
                      f"{'  SEVERE' if c in severe else ''}")


if __name__ == "__main__":
    main()
