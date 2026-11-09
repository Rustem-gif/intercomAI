"""Fetch the back-office record of the players about to be graded, into the bo_players cache.

Run before a grading batch (service.py) and by `intercom-summary bo-sync`. A player read in the
last BO_MAX_AGE_HOURS is not read again. Two at a time: the BO is the casino's production
system, and one player is seven page loads (King Billy) or about ten API reads (Tomb Riches).

Each brand has its own back office: King Billy's is SoftSwiss (backoffice/client.py +
player.py), Tomb Riches' is Apuesta Cloud (backoffice/apuesta.py + apuesta_player.py). Both
produce the same PlayerFacts, so the cache and the BACK OFFICE block are shared.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from typing import Iterable

from intercom_summary.backoffice import apuesta_player, player
from intercom_summary.backoffice.apuesta import ApuestaClient
from intercom_summary.backoffice.client import BackOfficeClient, BackOfficeError
from intercom_summary.intercom.models import Conversation
from intercom_summary.logging_setup import get_logger
from intercom_summary.settings import settings
from intercom_summary.storage.bo_players_store import BoPlayersStore

log = get_logger(__name__)

# Brands whose back office can be read, by contact.bo_brand (intercom/segments.py):
# brand → (its client class, its fetch(client, player_id) → PlayerFacts).
BRANDS = {
    "kingbilly": (BackOfficeClient, player.fetch),
    "tombriches": (ApuestaClient, apuesta_player.fetch),
}
SUPPORTED = set(BRANDS)


def configured(brand: str) -> bool:
    """The brand's BO credentials are all in .env."""
    if brand == "kingbilly":
        return bool(settings.bo_kb_email and settings.bo_kb_password and settings.bo_kb_otp_secret)
    if brand == "tombriches":
        return bool(settings.bo_tr_email and settings.bo_tr_password and settings.bo_tr_otp_secret)
    return False


def enabled(brand: str | None = None) -> bool:
    """BO_SYNC is on and that brand (or, with no brand, any brand) is configured."""
    if not settings.bo_sync:
        return False
    return configured(brand) if brand else any(configured(b) for b in BRANDS)


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
        for brand in sorted({b for b, _ in todo}):
            # A test passes a fake `client`; otherwise one client per brand, closed after.
            if client is None and not configured(brand):
                stats["failed"] += sum(1 for b, _ in todo if b == brand)
                log.warning("BO sync: %s back office is not configured in .env", brand)
                continue
            make, fetch = BRANDS[brand]
            bo = client or make()
            try:
                def one(pid: str, bo=bo, fetch=fetch):
                    return fetch(bo, pid)

                with ThreadPoolExecutor(max_workers=concurrency) as pool:
                    for future in [pool.submit(one, pid) for b, pid in todo if b == brand]:
                        try:
                            store.put(brand, future.result())
                            stats["fetched"] += 1
                        except BackOfficeError as exc:
                            stats["failed"] += 1
                            log.warning("BO sync (%s): %s", brand, exc)
                            if "login" in str(exc).lower():
                                break
                        except Exception as exc:  # noqa: BLE001 — one player must not stop a batch
                            stats["failed"] += 1
                            log.warning("BO sync (%s): %s: %s", brand, type(exc).__name__, exc)
            finally:
                if client is None:
                    bo.close()
    finally:
        store.close()
    log.info("BO sync: %s", stats)
    return stats


def sync_for(conversations: Iterable[Conversation], **kw) -> dict[str, int]:
    """sync() for the players of these conversations; a no-op when the BO is not configured."""
    if not enabled():
        return {"players": 0, "fresh": 0, "fetched": 0, "failed": 0, "disabled": 1}
    return sync([p for p in players_of(conversations) if enabled(p[0])], **kw)
