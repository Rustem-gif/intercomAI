"""One-time backfill: freeze a snapshot for knowledge-base cases that predate the
snapshot feature, so they stay viewable after their source conversation is deleted.

For each iconic case without a snapshot, builds one from the still-cached conversation +
grade. Cases whose conversation was already deleted can't be recovered and are reported.

Usage:
    python scripts/backfill_iconic_snapshots.py
"""
from __future__ import annotations

from intercom_summary import service
from intercom_summary.storage.iconic_cases_store import IconicCasesStore


def main() -> None:
    store = IconicCasesStore()
    try:
        cases = store.list_all()
        done, skipped, lost = 0, 0, []
        for case in cases:
            cid = case["conversation_id"]
            if case.get("snapshot"):
                skipped += 1
                continue
            snap = service.build_conversation_snapshot(cid)
            if snap and store.set_snapshot(cid, snap):
                done += 1
            else:
                lost.append(cid)
        print(f"Snapshotted {done} case(s); {skipped} already had one.")
        if lost:
            print(f"{len(lost)} case(s) had no cached conversation to snapshot: {', '.join(lost)}")
    finally:
        store.close()


if __name__ == "__main__":
    main()
