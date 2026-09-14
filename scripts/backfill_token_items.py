"""Freeze the membership of review links created before links were frozen.

A review link is a record of what was handed to an agent, so its contents must not drift. Links
created from 10 September carry a frozen membership; older ones do not, and the page falls back
to an unscoped live query over the agent's whole history — so an agent opening an old "August
review" link today sees September chats, and sees chats appear as grading catches up.

This resolves what each unfrozen link *should* have shown and writes it down, using the same
query the create endpoint uses, bounded by the link's own creation time (nothing graded after
the link was handed over can have been part of it).

    python scripts/backfill_token_items.py --dry-run
    python scripts/backfill_token_items.py

Coaching links (`session_id` set) are skipped: their contents come from the session's items,
which are already explicit.
"""
from __future__ import annotations

import argparse

from intercom_summary.settings import settings
from intercom_summary.storage.conversations_store import ConversationsStore
from intercom_summary.storage.db import connect


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="report, write nothing")
    args = ap.parse_args()

    conn = connect(settings.db_path)
    cstore = ConversationsStore()
    try:
        tokens = conn.execute(
            """SELECT t.token, t.agent_name, t.tag, t.label, t.created_at, t.since, t.until,
                      t.session_id,
                      (SELECT COUNT(*) FROM agent_review_token_items i WHERE i.token=t.token) n
                 FROM agent_review_tokens t ORDER BY t.created_at"""
        ).fetchall()

        frozen = skipped = filled = 0
        for t in tokens:
            if t["n"]:
                frozen += 1
                continue
            if t["session_id"]:
                skipped += 1
                continue

            # An unscoped legacy link had no date range of its own. Bound it at its creation
            # time: whatever existed and was graded when it was handed over is what the agent
            # was asked to review. Anything graded afterwards was never part of the deal.
            until = t["until"] and f"{t['until']}T23:59:59+00:00" or t["created_at"]
            rows, _ = cstore.query(
                agents=[t["agent_name"]], tag=t["tag"] or None,
                since=t["since"] or None, until=until,
                graded_only=True, sort="created_at", descending=True, limit=10_000,
            )
            ids = [r["id"] for r in rows]
            print(f"  {t['created_at'][:10]}  {t['agent_name']:<12} "
                  f"{t['label'][:30]:<30} → {len(ids)} chats")
            if not args.dry_run and ids:
                conn.executemany(
                    "INSERT OR IGNORE INTO agent_review_token_items (token, conversation_id) "
                    "VALUES (?, ?)",
                    [(t["token"], cid) for cid in ids],
                )
            filled += 1

        if not args.dry_run:
            conn.commit()
        print(f"\n{len(tokens)} links: {frozen} already frozen, {skipped} coaching (skipped), "
              f"{filled} {'would be' if args.dry_run else ''} backfilled")
    finally:
        cstore.close()
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
