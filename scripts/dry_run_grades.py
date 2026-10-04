"""Grade a few real conversations with the Claude grader WITHOUT saving anything.

Prints the new grade next to the stored one (and the QA analyst's score, when there is one)
plus token usage and cost, so a model / effort / ruleset change can be judged on live data
before it touches the database.

    .venv/bin/python scripts/dry_run_grades.py                     # 10 chats, kb-v41
    .venv/bin/python scripts/dry_run_grades.py -n 5 --ruleset vip --effort low
    .venv/bin/python scripts/dry_run_grades.py --ids 123 456       # specific chats

Half the sample (by default) is chats a human has already re-scored — the closest thing to
ground truth we have.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import time

from intercom_summary.settings import settings
from intercom_summary.storage.conversations_store import ConversationsStore, tags_are_ignored

# $ per million tokens — Claude Sonnet 5.5 (input, 5-minute cache write, cache read, output).
PRICES = {"in": 2.00, "cache_write": 2.50, "cache_read": 0.20, "out": 10.00}


def pick_ids(n: int) -> list[str]:
    db = sqlite3.connect(settings.db_path)
    try:
        reviewed = [r[0] for r in db.execute(
            "SELECT conversation_id FROM grades WHERE human_score IS NOT NULL "
            "ORDER BY graded_at DESC LIMIT ?", (n // 2,))]
        recent = [r[0] for r in db.execute(
            "SELECT conversation_id FROM grades WHERE human_score IS NULL "
            "ORDER BY graded_at DESC LIMIT ?", (n - len(reviewed),))]
    finally:
        db.close()
    return reviewed + recent


def stored(cid: str) -> dict:
    db = sqlite3.connect(settings.db_path)
    try:
        row = db.execute(
            "SELECT overall_score, human_score, ruleset_id, model FROM grades "
            "WHERE conversation_id = ?", (cid,)).fetchone()
    finally:
        db.close()
    if not row:
        return {}
    return {"score": row[0], "human": row[1], "ruleset": row[2], "model": row[3]}


def cost(u) -> float:
    if u is None:
        return 0.0
    g = lambda k: getattr(u, k, 0) or 0
    return (g("input_tokens") * PRICES["in"] + g("cache_creation_input_tokens") * PRICES["cache_write"]
            + g("cache_read_input_tokens") * PRICES["cache_read"]
            + g("output_tokens") * PRICES["out"]) / 1e6


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-n", type=int, default=10)
    ap.add_argument("--ids", nargs="*")
    ap.add_argument("--ruleset", default="kb-v41")
    ap.add_argument("--effort", choices=["low", "medium", "high", "xhigh", "max"])
    ap.add_argument("--json", action="store_true", help="also dump each new grade's verdicts")
    ap.add_argument("--jev", choices=["off", "shadow", "flag", "reconcile"],
                    help="override JEV_MODE for this run (v4.1 only)")
    args = ap.parse_args()

    if args.effort:
        object.__setattr__(settings, "qa_effort", args.effort)
    if args.jev:
        object.__setattr__(settings, "jev_mode", args.jev)

    from intercom_summary.qa.grader import Grader

    grader = Grader(ruleset_id=args.ruleset)
    print(f"model={settings.qa_model} effort={settings.qa_effort} jev={settings.jev_mode} "
          f"ruleset={args.ruleset} "
          f"({grader.rules_version}) — nothing is saved\n")

    cs = ConversationsStore()
    total_cost, rows = 0.0, []
    try:
        for cid in args.ids or pick_ids(args.n):
            c = cs.get(cid)
            if c is None or tags_are_ignored(c.tags):
                print(f"{cid}: not in cache or ignored-tag — skipped")
                continue
            t0 = time.time()
            try:
                g = grader.grade(c)
            except Exception as exc:  # noqa: BLE001 — a dry run reports, it doesn't stop
                print(f"{cid}: FAILED — {exc}")
                continue
            dt = time.time() - t0
            u = grader.last_usage
            total_cost += (cc := cost(u))
            old = stored(cid)
            failed = [r.rule_id for r in g.rule_results if r.verdict == "fail"]
            undet = [r.rule_id for r in g.rule_results if r.verdict == "cannot_determine"]
            rows.append((cid, g, old))
            print(
                f"{cid}  new {g.overall_score:>3} {g.overall_result:<4}"
                f"  | stored {old.get('score', '—')!s:>3} ({old.get('ruleset', '—')})"
                f"  human {old.get('human') if old.get('human') is not None else '—'!s:>3}"
                f"  | {g.case_type or '-'} / {g.risk_flag or '-'} / {g.outcome_status or '-'}"
                f"  | {dt:4.1f}s ${cc:.4f}"
                f" cache_read={getattr(u, 'cache_read_input_tokens', 0)} out={getattr(u, 'output_tokens', 0)}"
                f"  model={g.model}"
            )
            print(f"      fail: {', '.join(failed) or '—'}"
                  + (f"   cannot_determine: {', '.join(undet)}" if undet else "")
                  + ("   [manual review]" if g.manual_review_needed else ""))
            if g.jev:
                j = g.jev
                found = "; ".join(
                    f"{f['rule']}" + (f"({f['criterion']})" if f.get("criterion") else "")
                    + (f" p={f['p']}" if f.get("p") is not None else "")
                    for f in j.get("findings") or []) or "agrees"
                print(f"      jev[{j.get('mode')}]: {j.get('error') or found}"
                      f"  ({(j.get('usage') or {}).get('input_tokens', 0)} tok)")
            if args.json:
                print(json.dumps({r.rule_id: [r.verdict, r.evidence] for r in g.rule_results},
                                 ensure_ascii=False, indent=1))
    finally:
        cs.close()

    if rows:
        with_human = [(g.overall_score, o["human"]) for _, g, o in rows if o.get("human") is not None]
        print(f"\n{len(rows)} graded, total ${total_cost:.3f} (${total_cost / len(rows):.4f}/chat)")
        if with_human:
            mae = sum(abs(a - b) for a, b in with_human) / len(with_human)
            print(f"vs human score on {len(with_human)} reviewed chats: mean |Δ| = {mae:.1f}")


if __name__ == "__main__":
    main()
