"""Freeze a random sample of chats the AI PASSED, for QA to check — the false-PASS audit.

QA opens the AI's FAILs far more often than its PASSes, so how often a PASS is wrong is
unknown, and without it AI ↔ QA agreement cannot be certified (qa/agreement.py). This picks N
random kb-v41 grades the AI passed and no human has reviewed, reproducibly (--seed), and
stores them as a frozen calibration sample. QA then reviews them in the Calibration tab after
one run (Agree, or Re-score), and scripts/accuracy_ab.py --set sample:<id> reads the verdicts.

    .venv/bin/python scripts/pass_audit_sample.py                 # 80 chats → kb-v41-pass-audit
    .venv/bin/python scripts/pass_audit_sample.py -n 100 --since 2026-10-05 --dry-run
"""
from __future__ import annotations

import argparse
import random

from intercom_summary.qa.agreement import PASS_THRESHOLD
from intercom_summary.storage.calibration_store import CalibrationStore
from intercom_summary.storage.grades_store import GradesStore, _not_ignored_sql


def candidates(ruleset: str, since: str | None) -> list[str]:
    ign_sql, ign_args = _not_ignored_sql()
    sql = ("SELECT conversation_id FROM grades WHERE ruleset_id = ? AND overall_score >= ? "
           "AND human_score IS NULL AND human_confirmed_at IS NULL AND " + ign_sql)
    args: list[object] = [ruleset, PASS_THRESHOLD, *ign_args]
    if since:
        sql += " AND graded_at >= ?"
        args.append(since)
    store = GradesStore()
    try:
        return sorted(r[0] for r in store._conn.execute(sql + " ORDER BY conversation_id", args))
    finally:
        store.close()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-n", type=int, default=80)
    ap.add_argument("--ruleset", default="kb-v41")
    ap.add_argument("--since", help="only grades made on or after this date (YYYY-MM-DD)")
    ap.add_argument("--seed", type=int, default=20261020)
    ap.add_argument("--sample-id", default="kb-v41-pass-audit")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    pool = candidates(args.ruleset, args.since)
    picked = sorted(random.Random(args.seed).sample(pool, min(args.n, len(pool))))
    print(f"{len(pool)} unreviewed AI-PASS {args.ruleset} chats → {len(picked)} picked (seed {args.seed})")
    if args.dry_run:
        print("\n".join(picked))
        return
    cal = CalibrationStore()
    try:
        cal.create(args.sample_id, f"False-PASS audit — {len(picked)} random AI-PASS {args.ruleset} chats",
                   source=f"scripts/pass_audit_sample.py seed={args.seed} since={args.since or '-'}")
        n = cal.replace_items(args.sample_id, [{"conversation_id": cid, "seq": i + 1}
                                               for i, cid in enumerate(picked)])
    finally:
        cal.close()
    print(f"sample {args.sample_id}: {n} chats stored (frozen)")


if __name__ == "__main__":
    main()
