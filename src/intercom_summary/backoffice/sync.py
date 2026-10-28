"""Fetch the back-office record of the players about to be graded, into the bo_players cache.

Run before a grading batch (service.py) and by `intercom-summary bo-sync`. A player read in the
last BO_MAX_AGE_HOURS is not read again. Two at a time: the BO is the casino's production
system, and one player is seven page loads.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from typing import Iterable

from intercom_summary.backoffice import player
from intercom_summary.backoffice.client import BackOfficeClient, BackOfficeError
from intercom_summary.intercom.models import Conversation
from intercom_summary.logging_setup import get_logger
from intercom_summary.settings import settings
from intercom_summary.storage.bo_players_store import BoPlayersStore

log = get_logger(__name__)

# Brands whose back office the client can read, by the contact's `brand` attribute.
SUPPORTED = {"kingbilly"}


def enabled() -> bool:
    return bool(settings.bo_kb_email and settings.bo_kb_password and settings.bo_kb_otp_secret
                and settings.bo_sync)


def players_of(conversations: Iterable[Conversation]) -> list[tuple[str, str]]:
    """(brand, BO player id) of every conversation whose player a supported BO holds."""
    out = {(c.contact.bo_brand, c.contact.bo_id) for c in conversations
           if c.contact.bo_id and c.contact.bo_brand in SUPPORTED}
    return sorted(out)


def sync(players: list[tuple[str, str]], max_age_hours: float | None = None,
         concurrency: int = 2, client: BackOfficeClient | None = None,
         db_path=None) -> dict[str, int]:
    """Read the stale or missing players. Never raises for one player; a login failure stops
    the run (every later read would fail the same way) and is reported."""
    hours = settings.bo_max_age_hours if max_age_hours is None else max_age_hours
    store = BoPlayersStore(db_path)
    stats = {"players": len(players), "fresh": 0, "fetched": 0, "failed": 0}
    try:
        todo = []
        for brand, pid in players:
            if store.fresh(brand, pid, timedelta(hours=hours)):
                stats["fresh"] += 1
            else:
                todo.append((brand, pid))
        if not todo:
            return stats
        own = client is None
        bo = client or BackOfficeClient()
        try:
            def one(item: tuple[str, str]):
                brand, pid = item
                return brand, player.fetch(bo, pid)

            with ThreadPoolExecutor(max_workers=concurrency) as pool:
                for future in [pool.submit(one, item) for item in todo]:
                    try:
                        brand, facts = future.result()
                        store.put(brand, facts)
                        stats["fetched"] += 1
                    except BackOfficeError as exc:
                        stats["failed"] += 1
                        log.warning("BO sync: %s", exc)
                        if "login" in str(exc).lower():
                            break
                    except Exception as exc:  # noqa: BLE001 — one player must not stop a batch
                        stats["failed"] += 1
                        log.warning("BO sync: %s: %s", type(exc).__name__, exc)
        finally:
            if own:
                bo.close()
    finally:
        store.close()
    log.info("BO sync: %s", stats)
    return stats


def sync_for(conversations: Iterable[Conversation], **kw) -> dict[str, int]:
    """sync() for the players of these conversations; a no-op when the BO is not configured."""
    if not enabled():
        return {"players": 0, "fresh": 0, "fetched": 0, "failed": 0, "disabled": 1}
    return sync(players_of(conversations), **kw)
