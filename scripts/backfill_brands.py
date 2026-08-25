"""One-time backfill: label already-cached conversations with the brand they came through.

Background: the workspace hosts several brands (King Billy, Tomb Riches — see
`intercom/brands.py`), but conversations fetched before brand capture existed were stored
without one. This reads each unbranded conversation back from Intercom and records its
`Brand` attribute.

Two deliberate choices:

1. **It writes only the `brand` column.** It never goes through `ConversationsStore.save()`,
   whose `INSERT OR REPLACE` would rewrite `payload_json`, `state`, `tags` and `agent_name` —
   and an assignee reassigned in Intercom since the original fetch would silently move that
   conversation to a different agent, shifting per-agent QA averages. The `grades` table is
   never touched, so no score changes and nothing is marked stale for re-grading.
2. **A date-guarded fallback.** Conversations that error out or come back with no `Brand`
   fall back to the default brand when they predate the second brand's launch — every
   conversation older than that provably belongs to the default brand.

Usage:
    python scripts/backfill_brands.py --dry-run     # report only, writes nothing
    python scripts/backfill_brands.py               # apply
    python scripts/backfill_brands.py --concurrency 20
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter

from intercom_summary.intercom.brands import brand_label
from intercom_summary.intercom.client import IntercomClient
from intercom_summary.settings import settings
from intercom_summary.storage.db import connect

# The default brand, and the moment the first non-default brand went live. Anything created
# before this instant can only be the default brand, which is what makes the fallback safe.
DEFAULT_BRAND = "Betncare"
MULTI_BRAND_SINCE = "2026-08-21"


async def _resolve(
    client: IntercomClient, rows: list[tuple[str, str]], concurrency: int
) -> tuple[dict[str, str], int, int]:
    """Read each conversation's Brand from Intercom.

    Returns (brand_by_id, n_from_api, n_from_fallback).
    """
    resolved: dict[str, str] = {}
    from_api = 0
    fallback = 0
    sem = asyncio.Semaphore(concurrency)
    done = 0

    async def one(cid: str, created_at: str) -> None:
        nonlocal from_api, fallback, done
        brand = ""
        async with sem:
            try:
                payload = await client.get_conversation(cid)
                brand = str((payload.get("custom_attributes") or {}).get("Brand") or "")
            except Exception as exc:  # noqa: BLE001 — one bad row must not stop the batch
                print(f"  ! {cid}: {str(exc)[:100]}")
        if brand:
            from_api += 1
        elif (created_at or "") < MULTI_BRAND_SINCE:
            # Predates the second brand, so it can only be the default one.
            brand = DEFAULT_BRAND
            fallback += 1
        if brand:
            resolved[cid] = brand
        done += 1
        if done % 500 == 0:
            print(f"  … {done}/{len(rows)}")

    await asyncio.gather(*[one(cid, created) for cid, created in rows])
    return resolved, from_api, fallback


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="report what would change, write nothing")
    ap.add_argument("--concurrency", type=int, default=10, help="parallel Intercom reads (default 10)")
    args = ap.parse_args()

    settings.require_intercom()
    conn = connect(settings.db_path)
    rows = [
        (r["id"], r["created_at"] or "")
        for r in conn.execute(
            "SELECT id, created_at FROM conversations WHERE brand = '' ORDER BY created_at"
        ).fetchall()
    ]
    if not rows:
        print("Nothing to do — every cached conversation already has a brand.")
        return

    print(f"{len(rows)} unbranded conversation(s); reading Brand from Intercom "
          f"(concurrency={args.concurrency})…")

    async def run() -> tuple[dict[str, str], int, int]:
        client = IntercomClient()
        try:
            return await _resolve(client, rows, args.concurrency)
        finally:
            await client.aclose()

    resolved, from_api, fallback = asyncio.run(run())

    tally = Counter(resolved.values())
    print("\nResolved:")
    for brand, n in tally.most_common():
        print(f"  {n:6d}  {brand!r}  (shown as {brand_label(brand)!r})")
    print(f"\n  {from_api} read from Intercom, {fallback} assigned by the "
          f"pre-{MULTI_BRAND_SINCE} fallback, "
          f"{len(rows) - len(resolved)} still unresolved")

    if args.dry_run:
        print("\n--dry-run: nothing written.")
        return

    # Only the brand column — see the module docstring for why.
    conn.executemany(
        "UPDATE conversations SET brand=? WHERE id=?",
        [(brand, cid) for cid, brand in resolved.items()],
    )
    conn.commit()
    print(f"\nUpdated {len(resolved)} row(s) in {settings.db_path}.")


if __name__ == "__main__":
    main()
