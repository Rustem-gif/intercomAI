"""The Player panel of the chat drawer: the player's back-office record, for people.

The grader reads the same cache as a text block (backoffice/context.py); this gives the web
app the record as structured rows, each dated row placed before / during / after the chat,
plus a link to the player in the brand's own back office and an on-demand re-read.

Nothing here reaches the BO except refresh(), which is the ordinary read-only sync for one
player. No names, addresses, documents, IPs or card numbers are in the cache to begin with.
"""
from __future__ import annotations

from datetime import datetime, timezone

from intercom_summary.backoffice import sync as bo_sync
from intercom_summary.backoffice.context import (
    AFTER, BONUS_BEFORE, PAYMENTS_BEFORE, STATUS, _GROUP_HINTS, _ts)
from intercom_summary.backoffice.player import PlayerFacts
from intercom_summary.intercom.models import Conversation
from intercom_summary.settings import settings

BRAND_LABELS = {"kingbilly": "King Billy", "tombriches": "Tomb Riches"}
_LIST_LABELS = {"payments": "payments", "bonuses": "bonuses", "freespins": "free spins",
                "comments": "staff comments", "limits": "RG limits",
                "history": "status history", "level": "loyalty level"}


def chat_window(convo: Conversation) -> tuple[datetime | None, datetime | None]:
    """The chat's start and end, as the grader takes them (qa/grader.back_office_block)."""
    times = [m.created_at for m in convo.messages if m.created_at]
    start = convo.created_at or (min(times) if times else None)
    end = max(times) if times else convo.updated_at
    return start, end or start


def bo_url(brand: str, player_id: str) -> str:
    """The player's page in the brand's own back office."""
    if brand == "kingbilly":
        return f"{settings.bo_kb_url}/players/{player_id}"
    if brand == "tombriches":
        return f"{settings.bo_tr_url}/customers/accounts/{player_id}"
    return ""


def _iso(value: str) -> str:
    t = _ts(value)
    return t.isoformat().replace("+00:00", "Z") if t else ""


def _rel(value: str, start: datetime | None, end: datetime | None) -> str:
    t = _ts(value)
    if t is None or start is None:
        return ""
    return "before" if t < start else "during" if t <= (end or start) else "after"


def _near(value: str, start: datetime | None, end: datetime | None, before) -> bool:
    """Inside the window the grader sees (so the panel can show the same rows by default)."""
    t = _ts(value)
    if t is None or start is None:
        return True
    return start - before <= t <= (end or start) + AFTER


def _state(success: str) -> tuple[str, str]:
    """(label, kind) of a payment state; kind drives the colour: ok / fail / pending / rejected."""
    s = (success or "").strip()
    if s == "Yes":
        return "succeeded", "ok"
    if s == "No":
        return "failed / not completed", "fail"
    if not s:
        return "pending", "pending"
    low = s.lower()
    if low.startswith("rejected"):
        return s, "rejected"
    if low.startswith(("failed", "canceled", "cancelled")):
        return s, "fail"
    return s, "pending"


def render(facts: PlayerFacts, brand: str, convo: Conversation) -> dict:
    start, end = chat_window(convo)
    not_read = sorted({e.split(":")[0] for e in facts.errors})
    fetched = facts.fetched_at
    try:
        age_h = (datetime.now(timezone.utc) - datetime.fromisoformat(fetched)).total_seconds() / 3600
    except ValueError:
        age_h = None
    row = lambda value: {"when": _iso(value), "rel": _rel(value, start, end)}  # noqa: E731
    payments = []
    for p in facts.payments:
        label, kind = _state(p.get("success", ""))
        note = str(p.get("comments", "") or "").strip()
        # King Billy's comments column is a count, not text; a reason already in the state
        # ("failed (Insufficient balance)") is not repeated.
        if note.isdigit() or (note and note in label):
            note = ""
        payments.append({**row(p.get("created_at", "")),
                         "action": p.get("action", ""), "amount": p.get("amount", ""),
                         "source": p.get("source", ""), "state": label, "kind": kind,
                         "finished": _iso(p.get("finished_at", "")),
                         "approval": p.get("approval", ""), "comment": note,
                         "near": _near(p.get("created_at", ""), start, end, PAYMENTS_BEFORE)})
    bonuses = []
    for b in facts.bonuses:
        stage = b.get("stage", "")
        bonuses.append({**row(b.get("issued_at", "")), "title": b.get("title", ""),
                        "stage": stage.split(", given by staff")[0].split(", automatic")[0],
                        "by_staff": True if "given by staff" in stage
                        else False if "automatic" in stage else None,
                        "amount": b.get("amount", ""),
                        "near": _near(b.get("issued_at", ""), start, end, BONUS_BEFORE)})
    freespins = [{**row(s.get("issued_at", "")), "title": s.get("title", ""),
                  "stage": s.get("stage", ""),
                  "near": _near(s.get("issued_at", ""), start, end, BONUS_BEFORE)}
                 for s in facts.freespins]
    return {
        "available": True,
        "brand": brand, "brand_label": BRAND_LABELS.get(brand, brand),
        "player_id": facts.player_id, "bo_url": bo_url(brand, facts.player_id),
        "read_at": fetched, "age_hours": round(age_h, 1) if age_h is not None else None,
        "chat_start": start.isoformat() if start else None,
        "chat_end": end.isoformat() if end else None,
        "status": STATUS.get(facts.status, facts.status or "unknown"),
        "status_key": facts.status, "status_detail": facts.status_detail,
        "level": facts.level, "segment": convo.contact.segment,
        "loyalty_level": convo.contact.loyalty_level,
        "tags": facts.tags,
        "groups": [g for g in facts.groups if any(h in g.lower() for h in _GROUP_HINTS)],
        "duplicates": facts.duplicates, "history_note": facts.history_note,
        "locks": [{**row(l.get("created_at", "")), "kind": l.get("kind", ""),
                   "reason": l.get("reason", ""), "comment": l.get("comment", ""),
                   "by": l.get("by", ""), "until": _iso(l.get("until", ""))}
                  for l in facts.locks],
        "limits": [{**row(l.get("created_at", "")),
                    "type": f"{l.get('type', '')} {l.get('period', '')}".strip(),
                    "status": l.get("status", ""),
                    "ends": _iso(l.get("disable_at", "") or "")}
                   for l in facts.limits],
        "payments": payments, "bonuses": bonuses, "freespins": freespins,
        "comments": [{**row(c.get("created_at", "")), "text": c.get("text", "")}
                     for c in facts.comments],
        "not_read": [_LIST_LABELS.get(n, n) for n in not_read],
        "unavailable": [_LIST_LABELS.get(n, n) for n in facts.unavailable],
    }


def _missing(reason: str, convo: Conversation, brand: str = "") -> dict:
    return {"available": False, "reason": reason, "brand": brand,
            "brand_label": BRAND_LABELS.get(brand, brand),
            "player_id": convo.contact.bo_id,
            "bo_url": bo_url(brand, convo.contact.bo_id) if convo.contact.bo_id else "",
            "segment": convo.contact.segment}


def panel(convo: Conversation) -> dict:
    """The panel for a chat (or a VIP thread / case) from the cache; never reads the BO."""
    from intercom_summary.storage.bo_players_store import BoPlayersStore

    brand, pid = convo.contact.bo_brand, convo.contact.bo_id
    if not pid:
        return _missing("This chat's contact has no back-office player id (an unidentified "
                        "visitor, or a contact Intercom has no player id for).", convo, brand)
    if brand not in bo_sync.SUPPORTED:
        return _missing(f"No back-office integration for this brand ({brand or 'unknown'}).",
                        convo, brand)
    store = BoPlayersStore()
    try:
        facts = store.get(brand, pid)
    finally:
        store.close()
    if facts is None:
        out = _missing("Not read from the back office yet — press Refresh.", convo, brand)
        out["can_refresh"] = bo_sync.enabled(brand)
        return out
    out = render(facts, brand, convo)
    out["can_refresh"] = bo_sync.enabled(brand)
    return out


def refresh(convo: Conversation) -> dict:
    """Re-read this one player from the BO now (read-only), then return the panel."""
    brand, pid = convo.contact.bo_brand, convo.contact.bo_id
    if not pid or brand not in bo_sync.SUPPORTED:
        return panel(convo)
    if not bo_sync.enabled(brand):
        raise RuntimeError(f"The {BRAND_LABELS.get(brand, brand)} back office is not configured "
                           f"(or BO_SYNC is off).")
    stats = bo_sync.sync([(brand, pid)], max_age_hours=0, concurrency=1)
    if stats.get("failed"):
        raise RuntimeError("The back office could not be read just now — see the server log.")
    return panel(convo)
