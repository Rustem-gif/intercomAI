"""Backfill: give already-cached chats and VIP threads the player's VIP segment.

Background: QC's rules for closure requests, manager requests and "write to your VIP manager"
depend on whether the player is VIP / preVIP (QC answers, 2026-10-21). The segment comes from
the Intercom contact (`intercom/segments.py`); conversations fetched before that lookup
existed carry none, and the grader then reads "Player segment: unknown".

This reads each player once from Intercom (GET /contacts/{id}, cached in `contact_segments`)
and writes the segment into the cached payload with `json_set`, the same way the brand
backfill writes only its column:

- it never goes through `ConversationsStore.save()` (an INSERT OR REPLACE that would rewrite
  the agent, state and tags from a fresh fetch);
- the `grades` table is never touched — no score changes, nothing is re-graded. Only chats
  graded after the backfill see the new header line.

Usage:
    python scripts/backfill_contact_segments.py --dry-run                 # count, write nothing
    python scripts/backfill_contact_segments.py --since 2026-10-01        # apply, recent only
    python scripts/backfill_contact_segments.py --db /path/to/copy.db     # a scratch copy
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter

from intercom_summary.intercom.client import IntercomClient
from intercom_summary.intercom.segments import ContactSegmenter
from intercom_summary.settings import settings
from intercom_summary.storage.contact_segments_store import ContactSegmentsStore
from intercom_summary.storage.db import connect

_TABLES = ("conversations", "vip_conversations")


def _rows(conn, table: str, since: str | None) -> list[tuple[str, str]]:
    """(row id, contact id) for rows whose payload has a contact but no segment yet."""
    sql = (f"SELECT id, json_extract(payload_json, '$.contact.id') AS contact_id FROM {table} "
           "WHERE COALESCE(json_extract(payload_json, '$.contact.id'), '') != '' "
           "AND COALESCE(json_extract(payload_json, '$.contact.segment'), '') = ''")
    args: list[str] = []
    if since:
        sql += " AND created_at >= ?"
        args.append(since)
    return [(r["id"], r["contact_id"]) for r in conn.execute(sql, args).fetchall()]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="look players up, write no payload")
    ap.add_argument("--since", help="only rows created on or after this date (YYYY-MM-DD)")
    ap.add_argument("--db", help="database path (default: DB_PATH)")
    ap.add_argument("--concurrency", type=int, default=10, help="parallel Intercom reads")
    args = ap.parse_args()

    settings.require_intercom()
    db = args.db or settings.db_path
    conn = connect(db)
    todo = {t: _rows(conn, t, args.since) for t in _TABLES}
    contacts = sorted({cid for rows in todo.values() for _id, cid in rows})
    print(f"{db}: " + ", ".join(f"{len(r)} {t} row(s)" for t, r in todo.items())
          + f" without a segment; {len(contacts)} distinct player(s).")
    if not contacts:
        return

    cache = ContactSegmentsStore(db)

    async def run() -> tuple[dict[str, tuple[str, str]], ContactSegmenter]:
        client = IntercomClient()
        try:
            seg = ContactSegmenter(client, cache=cache, concurrency=args.concurrency)
            found: dict[str, tuple[str, str]] = {}

            async def one(cid: str) -> None:
                found[cid] = await seg.lookup(cid)
                if len(found) % 500 == 0:
                    print(f"  … {len(found)}/{len(contacts)}")

            await asyncio.gather(*[one(c) for c in contacts])
            return found, seg
        finally:
            await client.aclose()

    found, seg = asyncio.run(run())
    tally = Counter(s or "(none)" for s, _level in found.values())
    print(f"Players: {dict(tally)}; {seg.lookups} Intercom read(s), {seg.failures} failed.")

    for table, rows in todo.items():
        updates = [(found[cid][0], found[cid][1], rid) for rid, cid in rows
                   if found.get(cid, ("", ""))[0]]
        per = Counter(found.get(cid, ("", ""))[0] or "(none)" for _rid, cid in rows)
        print(f"  {table}: {dict(per)}")
        if args.dry_run or not updates:
            continue
        conn.executemany(
            f"UPDATE {table} SET payload_json = json_set(payload_json, "
            "'$.contact.segment', ?, '$.contact.loyalty_level', ?) WHERE id = ?",
            updates,
        )
        conn.commit()
        print(f"  {table}: wrote the segment into {len(updates)} payload(s).")
    if args.dry_run:
        print("--dry-run: no payload written (the contact_segments cache was filled).")
    cache.close()


if __name__ == "__main__":
    main()
