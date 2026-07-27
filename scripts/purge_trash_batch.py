#!/usr/bin/env python3
"""Permanently purge a batch of trashed conversations, selected by when/who deleted them.

Tombstones in `deleted_conversations` block re-import: `ConversationsStore.save()` refuses
to store any conversation whose id is in the trash. A bulk "Delete ALL" therefore silently
blocks every future Intercom fetch of that date range. This script clears such a batch.

Purging is irreversible — a tombstone's snapshot holds the conversation row and its grade,
so purging a graded item loses that grade for good. Items carrying a grade are skipped
unless --include-graded is passed.

Usage:
    scripts/purge_trash_batch.py --deleted-on 2026-07-27 --dry-run
    scripts/purge_trash_batch.py --deleted-on 2026-07-27 --by christina --vacuum
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from intercom_summary.settings import settings  # noqa: E402
from intercom_summary.storage.db import connect  # noqa: E402
from intercom_summary.storage.trash_store import TrashStore  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--deleted-on", metavar="YYYY-MM-DD",
                    help="Purge tombstones deleted on this UTC date.")
    ap.add_argument("--by", metavar="USERNAME",
                    help="Further restrict to items deleted by this user.")
    ap.add_argument("--include-graded", action="store_true",
                    help="Also purge items whose snapshot holds a grade (destroys the grade).")
    ap.add_argument("--dry-run", action="store_true",
                    help="Report what would be purged and exit without changing anything.")
    ap.add_argument("--vacuum", action="store_true",
                    help="Run VACUUM afterwards to reclaim the freed disk space.")
    args = ap.parse_args()

    if not args.deleted_on and not args.by:
        ap.error("give at least one of --deleted-on / --by")

    where, params = [], []
    if args.deleted_on:
        where.append("deleted_at LIKE ?")
        params.append(f"{args.deleted_on}%")
    if args.by:
        where.append("deleted_by = ?")
        params.append(args.by)

    conn = connect(settings.db_path)
    rows = conn.execute(
        f"SELECT conversation_id, snapshot_json FROM deleted_conversations "
        f"WHERE {' AND '.join(where)}",
        params,
    ).fetchall()
    total_before = conn.execute(
        "SELECT COUNT(*) AS n FROM deleted_conversations"
    ).fetchone()["n"]
    conn.close()

    if not rows:
        print("No tombstones matched — nothing to do.")
        return 0

    graded, plain = [], []
    for r in rows:
        target = graded if json.loads(r["snapshot_json"]).get("grade") else plain
        target.append(r["conversation_id"])

    ids = plain + graded if args.include_graded else plain

    print(f"Trash holds {total_before} tombstone(s); {len(rows)} match this filter:")
    print(f"  {len(plain)} without a saved grade")
    print(f"  {len(graded)} carrying a saved grade"
          f"{' — will be purged (--include-graded)' if args.include_graded else ' — skipped'}")
    print(f"→ {len(ids)} to purge, {total_before - len(ids)} tombstone(s) remaining after.")

    if args.dry_run:
        print("\nDry run — nothing changed.")
        return 0
    if not ids:
        print("\nNothing to purge.")
        return 0

    store = TrashStore()
    try:
        purged = store.purge(ids)
    finally:
        store.close()
    print(f"\nPurged {purged} tombstone(s). Those conversations can be re-imported again.")

    if args.vacuum:
        conn = connect(settings.db_path)
        before = Path(settings.db_path).stat().st_size
        conn.execute("VACUUM")
        conn.close()
        after = Path(settings.db_path).stat().st_size
        print(f"VACUUM: {before / 1e6:.1f} MB → {after / 1e6:.1f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
