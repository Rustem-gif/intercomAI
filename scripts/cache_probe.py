"""Check that prompt caching works: grade one real conversation twice and compare usage.

    .venv/bin/python scripts/cache_probe.py                 # most recent standard chat, kb-v41
    .venv/bin/python scripts/cache_probe.py --id 123 --ruleset vip

The second request must read the system prompt from the cache. Exit code 1 if it doesn't —
run this after any change to how the grading request is put together. Nothing is saved;
it costs about $0.03 (two Sonnet calls).
"""
from __future__ import annotations

import argparse
import sqlite3
import sys

from intercom_summary.settings import settings
from intercom_summary.storage.conversations_store import ConversationsStore


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--id", help="conversation id (default: the most recently graded one)")
    ap.add_argument("--ruleset", default="kb-v41")
    args = ap.parse_args()

    from intercom_summary.qa.grader import Grader
    from intercom_summary.qa.pricing import cost_usd, usage_dict

    cid = args.id
    if not cid:
        db = sqlite3.connect(settings.db_path)
        try:
            cid = db.execute("SELECT conversation_id FROM grades ORDER BY graded_at DESC LIMIT 1").fetchone()[0]
        finally:
            db.close()
    cs = ConversationsStore()
    try:
        convo = cs.get(cid)
    finally:
        cs.close()
    if convo is None:
        print(f"{cid}: not in the conversation cache")
        return 2

    grader = Grader(ruleset_id=args.ruleset, jev=False)
    messages = grader.messages_for(convo)
    print(f"{settings.qa_model} · ruleset {args.ruleset} ({grader.rules_version}) · chat {cid}\n")
    print(f"{'':8}{'input':>8}{'write':>8}{'read':>8}{'output':>8}{'$':>9}")
    rows = []
    for label in ("first", "second"):
        resp = grader._request(convo, messages)
        u = usage_dict(resp.usage)
        rows.append(u)
        print(f"{label:8}{u['input']:>8}{u['cache_write_5m'] + u['cache_write_1h']:>8}"
              f"{u['cache_read']:>8}{u['output']:>8}{cost_usd(u, resp.model):>9.4f}")
    if rows[1]["cache_read"] == 0:
        print("\nFAIL: the second request read nothing from the cache — something in the "
              "system prompt, model or output format changed between the two requests.")
        return 1
    print(f"\nOK: {rows[1]['cache_read']} prompt tokens served from the cache on the second call.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
