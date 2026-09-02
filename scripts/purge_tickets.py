"""One-time cleanup: remove Intercom *tickets* from the QA cache, keeping chats only.

Background: `/conversations/search` returns tickets alongside chats — same id namespace, and
`"type": "conversation"` on both — so every fetch made before `intercom/fetch.is_ticket`
existed imported tickets too. They were then listed, exported and graded like chats. The
client asked for chats only, so the ones already cached have to come out.

Two deliberate choices:

1. **It asks Intercom, not the cache.** A cached row carries none of the ticket marker: the
   normalised `payload_json` never stored it, so the only way to classify the back catalogue
   is to ask which ids Intercom calls tickets. `/tickets/search` over the cache's own date
   range answers that in a handful of requests — far cheaper than re-reading every
   conversation one by one.
2. **It soft-deletes rather than deleting.** Matches go to the Trash through `TrashStore`,
   which snapshots the conversation *and its grade* first, so a mistake is one Restore away.
   `--blacklist` additionally bars them from re-import; that is belt and braces, since fetch
   now drops ticket stubs before they are ever fetched, and it costs a Trash row apiece.

Usage:
    python scripts/purge_tickets.py --dry-run     # report only, changes nothing
    python scripts/purge_tickets.py               # move the tickets to the Trash
    python scripts/purge_tickets.py --since 2026-07-01 --until 2026-09-01
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta, timezone

from intercom_summary.intercom.client import IntercomClient
from intercom_summary.settings import settings
from intercom_summary.storage.db import connect
from intercom_summary.storage.trash_store import TrashStore

# Ticket search windows are padded by a day on each side: the cache stores ISO timestamps and
# Intercom compares unix seconds with strict >/<, so an exact bound can drop a boundary row.
_PAD = timedelta(days=1)


def _cached_ids(conn) -> tuple[dict[str, str], str, str]:
    """Every cached conversation id → its created_at, plus the min/max dates as YYYY-MM-DD."""
    rows = conn.execute(
        "SELECT id, created_at FROM conversations WHERE created_at IS NOT NULL"
    ).fetchall()
    ids = {r["id"]: r["created_at"] for r in rows}
    if not ids:
        return {}, "", ""
    lo = min(ids.values())
    hi = max(ids.values())
    fmt = lambda iso, delta: (  # noqa: E731 - a one-line date shim, not worth a def
        datetime.fromisoformat(iso).astimezone(timezone.utc) + delta
    ).strftime("%Y-%m-%d")
    return ids, fmt(lo, -_PAD), fmt(hi, _PAD)


async def _ticket_ids(since: str, until: str) -> set[str]:
    """Ids Intercom classes as tickets in the window."""
    client = IntercomClient()
    try:
        query = {
            "operator": "AND",
            "value": [
                {"field": "created_at", "operator": ">",
                 "value": int(datetime.fromisoformat(since).replace(
                     tzinfo=timezone.utc).timestamp())},
                {"field": "created_at", "operator": "<",
                 "value": int(datetime.fromisoformat(until).replace(
                     tzinfo=timezone.utc).timestamp())},
            ],
        }
        found: set[str] = set()
        async for ticket in client.search_tickets(query):
            # A ticket reports two ids: `id` is the conversation-namespace id our cache keys
            # on, `ticket_id` is the short human-facing number. Only the first one joins.
            if tid := str(ticket.get("id", "")):
                found.add(tid)
            if len(found) % 500 == 0 and found:
                print(f"  {len(found)} ticket(s) so far…")
        return found
    finally:
        await client.aclose()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would be removed, change nothing")
    ap.add_argument("--since", help="Ticket search lower bound (YYYY-MM-DD). "
                                    "Default: the oldest cached conversation.")
    ap.add_argument("--until", help="Ticket search upper bound (YYYY-MM-DD). "
                                    "Default: the newest cached conversation.")
    ap.add_argument("--blacklist", action="store_true",
                    help="Also bar the removed ids from re-import (Trash blacklist).")
    args = ap.parse_args()

    settings.require_intercom()
    conn = connect(settings.db_path)
    cached, lo, hi = _cached_ids(conn)
    if not cached:
        print("Nothing to do — the conversation cache is empty.")
        return

    since = args.since or lo
    until = args.until or hi
    print(f"{len(cached)} cached conversation(s) spanning {lo} … {hi}.")
    print(f"Asking Intercom for tickets created {since} … {until}…")

    tickets = asyncio.run(_ticket_ids(since, until))
    print(f"Intercom reports {len(tickets)} ticket(s) in that window.")

    matched = sorted(tickets & cached.keys(), key=lambda cid: cached[cid])
    if not matched:
        print("None of them are in the cache — nothing to remove.")
        return

    graded = conn.execute(
        "SELECT COUNT(*) AS n FROM grades WHERE conversation_id IN "
        f"({','.join('?' * len(matched))})", matched
    ).fetchone()["n"]
    print(f"\n{len(matched)} cached conversation(s) are tickets, {graded} of them graded.")
    print(f"  oldest {cached[matched[0]][:10]} · newest {cached[matched[-1]][:10]}")

    if args.dry_run:
        print("\n--dry-run: nothing written. Re-run without it to move them to the Trash.")
        return

    store = TrashStore()
    try:
        moved = store.move_to_trash(matched, deleted_by="purge_tickets",
                                    blacklist=args.blacklist)
    finally:
        store.close()
    print(f"\nMoved {moved} ticket(s) (and their grades) to the Trash in {settings.db_path}.")
    print("Restore them from the Trash page if this was not what you wanted.")


if __name__ == "__main__":
    main()
