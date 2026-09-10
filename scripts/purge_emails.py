"""One-time cleanup: remove email conversations from the QA cache, keeping chats only.

Background: the team grades and exports Messenger chats only. A previous change excluded Intercom
*tickets*, but an email is not a ticket — `raw["ticket"]` is null on every one of them, so
`intercom/fetch.is_ticket` never matched a single email and they kept arriving. Email is a whole
separate channel (`source.type == "email"`) and 46% of this workspace; the search query now
excludes it, but the ones already cached have to come out.

Two deliberate choices, both inherited from `scripts/purge_tickets.py`:

1. **It asks Intercom, not the cache.** A cached row carries no channel: `normalise_conversation`
   read `source.type` only as a non-emptiness test and never stored it. So the only way to classify
   the back catalogue is to ask which ids Intercom calls emails. `source.type` is a searchable
   field, so one search sweep over the cache's own date range answers it — far cheaper than
   re-reading every conversation.
2. **It soft-deletes rather than deleting.** Matches go to the Trash through `TrashStore`, which
   snapshots the conversation *and its grade* first, so a mistake is one Restore away. `--blacklist`
   additionally bars them from re-import; that is belt and braces, since the search no longer
   returns emails at all, and it costs a Trash row apiece in a Trash that already holds thousands.

Usage:
    python scripts/purge_emails.py --dry-run     # report only, changes nothing
    python scripts/purge_emails.py               # move the emails to the Trash
"""
from __future__ import annotations

import argparse
import asyncio
import collections
from datetime import datetime, timedelta, timezone

from intercom_summary.intercom.client import IntercomClient
from intercom_summary.intercom.fetch import build_search_query
from intercom_summary.settings import settings
from intercom_summary.storage.db import connect
from intercom_summary.storage.trash_store import TrashStore

# Search windows are padded a day either side: the cache stores ISO timestamps and Intercom
# compares unix seconds with strict >/<, so an exact bound can drop a boundary row.
_PAD = timedelta(days=1)


def _cached(conn) -> tuple[dict[str, str], str, str]:
    """Every cached conversation id → created_at, plus the padded min/max as YYYY-MM-DD."""
    rows = conn.execute(
        "SELECT id, created_at FROM conversations WHERE created_at IS NOT NULL"
    ).fetchall()
    ids = {r["id"]: r["created_at"] for r in rows}
    if not ids:
        return {}, "", ""
    day = lambda iso, delta: (  # noqa: E731 - a one-line date shim
        datetime.fromisoformat(iso).astimezone(timezone.utc) + delta
    ).strftime("%Y-%m-%d")
    return ids, day(min(ids.values()), -_PAD), day(max(ids.values()), _PAD)


async def _email_ids(since: str, until: str) -> set[str]:
    """Ids Intercom classes as emails in the window."""
    client = IntercomClient()
    try:
        # chats_only=False then narrowed to email: the same builder the fetch path uses, so the
        # two cannot drift apart on what a window means.
        query = build_search_query([], since, until, chats_only=False)
        clauses = query["value"] if query.get("operator") == "AND" else [query]
        query = {"operator": "AND", "value": [
            *clauses, {"field": "source.type", "operator": "=", "value": "email"},
        ]}
        found: set[str] = set()
        async for stub in client.search_conversations(query):
            if sid := str(stub.get("id", "")):
                found.add(sid)
            if found and len(found) % 2000 == 0:
                print(f"  {len(found)} email(s) so far…")
        return found
    finally:
        await client.aclose()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would be removed, change nothing")
    ap.add_argument("--since", help="Search lower bound (YYYY-MM-DD). Default: oldest cached.")
    ap.add_argument("--until", help="Search upper bound (YYYY-MM-DD). Default: newest cached.")
    ap.add_argument("--blacklist", action="store_true",
                    help="Also bar the removed ids from re-import (Trash blacklist).")
    args = ap.parse_args()

    settings.require_intercom()
    conn = connect(settings.db_path)
    cached, lo, hi = _cached(conn)
    if not cached:
        print("Nothing to do — the conversation cache is empty.")
        return

    since = args.since or lo
    until = args.until or hi
    print(f"{len(cached)} cached conversation(s) spanning {lo} … {hi}.")
    print(f"Asking Intercom for emails created {since} … {until}…")

    emails = asyncio.run(_email_ids(since, until))
    print(f"Intercom reports {len(emails)} email(s) in that window.")

    matched = sorted(emails & cached.keys(), key=lambda cid: cached[cid])
    if not matched:
        print("None of them are in the cache — nothing to remove.")
        return

    placeholders = ",".join("?" * len(matched))
    graded = conn.execute(
        f"SELECT COUNT(*) AS n FROM grades WHERE conversation_id IN ({placeholders})", matched
    ).fetchone()["n"]
    by_agent = collections.Counter(
        r["agent_name"] for r in conn.execute(
            f"SELECT agent_name FROM conversations WHERE id IN ({placeholders})", matched)
    )
    print(f"\n{len(matched)} cached conversation(s) are emails "
          f"({100 * len(matched) / len(cached):.1f}%), {graded} of them graded.")
    print(f"  oldest {cached[matched[0]][:10]} · newest {cached[matched[-1]][:10]}")
    print("  most affected agents: "
          + ", ".join(f"{a} {n}" for a, n in by_agent.most_common(6)))

    if args.dry_run:
        print("\n--dry-run: nothing written. Re-run without it to move them to the Trash.")
        return

    store = TrashStore()
    try:
        moved = store.move_to_trash(matched, deleted_by="purge_emails",
                                    blacklist=args.blacklist)
    finally:
        store.close()
    print(f"\nMoved {moved} email(s) (and their grades) to the Trash in {settings.db_path}.")
    print("Restore them from the Trash page if this was not what you wanted.")


if __name__ == "__main__":
    main()
