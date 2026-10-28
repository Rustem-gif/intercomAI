"""The BACK OFFICE block of the grader's transcript header, from the bo_players cache.

The BO is a snapshot taken at `fetched_at`, which can be days after the chat. Dated rows are
history and are placed relative to the chat (before / during / after); the status, tags and
groups are as of the read. The BO shows times in UTC (configs.displayed_timezone), the same
clock as Intercom's timestamps.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from intercom_summary.backoffice.player import PlayerFacts
from intercom_summary.backoffice.sync import SUPPORTED
from intercom_summary.intercom.models import Contact

STATUS = {"enable": "active", "disable": "DISABLED (account closed)", "suspend": "SUSPENDED",
          "restrict_casino": "casino restricted", "restrict_sb": "sportsbook restricted",
          "activate_in_24h": "to be re-activated in 24 h"}
# How far around the chat each list reaches.
PAYMENTS_BEFORE, BONUS_BEFORE, AFTER = timedelta(days=14), timedelta(days=30), timedelta(days=3)
MAX_ROWS = {"payments": 12, "bonuses": 10, "freespins": 6, "comments": 5}
# VIP-relevant marketing groups worth showing (the full list runs to 60 items).
_GROUP_HINTS = ("vip", "self", "exclu", "closed", "cool", "responsible", "abuse")


def _ts(value: str) -> datetime | None:
    try:
        return datetime.strptime(value.strip()[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except (ValueError, AttributeError):
        return None


def _when(value: str, start: datetime | None, end: datetime | None) -> str:
    t = _ts(value)
    if t is None or start is None:
        return value
    end = end or start
    rel = "before the chat" if t < start else "during the chat" if t <= end else "after the chat"
    return f"{value[:16]} UTC ({rel})"


def _in(value: str, lo: datetime | None, hi: datetime | None) -> bool:
    t = _ts(value)
    return t is None or lo is None or (lo <= t <= (hi or lo))


def block(contact: Contact, start: datetime | None, end: datetime | None,
          store=None) -> str:
    """The header block, or a one-line "not available" so the model knows to fall back."""
    if not contact.bo_id or contact.bo_brand not in SUPPORTED:
        return "=== BACK OFFICE: not available for this player ==="
    from intercom_summary.storage.bo_players_store import BoPlayersStore

    own = store is None
    store = store or BoPlayersStore()
    try:
        facts = store.get(contact.bo_brand, contact.bo_id)
    finally:
        if own:
            store.close()
    if facts is None:
        return "=== BACK OFFICE: not read for this player ==="
    return render(facts, start, end)


def render(f: PlayerFacts, start: datetime | None, end: datetime | None) -> str:
    end = end or start
    lines = [f"=== BACK OFFICE (the casino's own records; player {f.player_id}; read "
             f"{f.fetched_at[:16].replace('T', ' ')} UTC — status, tags and groups are as of "
             f"that read, dated rows are history) ==="]
    lines.append(f"Account status now: {STATUS.get(f.status, f.status or 'unknown')}")
    if f.locks:
        lines.append("Locks: " + "; ".join(
            f"{l['kind']} {l['reason']}: \"{l['comment']}\" {_when(l['created_at'], start, end)}"
            for l in f.locks))
    else:
        lines.append("Locks: none")
    if f.limits:
        lines.append("RG limits: " + "; ".join(
            f"{l['type']} {l['period']} — {l['status']}, set {_when(l['created_at'], start, end)}"
            + (f", ends {l['disable_at'][:16]}" if l.get("disable_at") not in ("", "-") else "")
            for l in f.limits))
    else:
        lines.append("RG limits: none")
    lines.append("Tags: " + (", ".join(f.tags) or "none"))
    hints = [g for g in f.groups if any(h in g.lower() for h in _GROUP_HINTS)]
    if hints:
        lines.append("Groups (relevant): " + ", ".join(hints[:12]))
    if f.duplicates is not None:
        lines.append(f"Duplicate accounts linked: {f.duplicates}")

    def window(rows, key, before, cap):
        lo = start - before if start else None
        hi = end + AFTER if end else None
        picked = [r for r in rows if _in(r.get(key, ""), lo, hi)]
        return picked[:cap], len(picked) > cap

    pays, more = window(f.payments, "created_at", PAYMENTS_BEFORE, MAX_ROWS["payments"])
    lines.append(f"Payments from 14 days before the chat to 3 days after (newest first, of the "
                 f"latest {len(f.payments)}): " + ("none" if not pays else ""))
    for p in pays:
        state = {"Yes": "succeeded", "No": "failed / not completed"}.get(p["success"], p["success"] or "pending")
        extra = f"; approval {p['approval']}" if p.get("approval") else ""
        lines.append(f"  - {_when(p['created_at'], start, end)} {p['action']} {p['amount']} via "
                     f"{p['source'][:40]} — {state}"
                     + (f", finished {p['finished_at'][:16]}" if p.get("finished_at") else "")
                     + extra)
    if more:
        lines.append("  - … more in that period")
    for label, rows, cap in (("Bonuses issued", f.bonuses, MAX_ROWS["bonuses"]),
                             ("Free spins issued", f.freespins, MAX_ROWS["freespins"])):
        picked, more = window(rows, "issued_at", BONUS_BEFORE, cap)
        lines.append(f"{label} from 30 days before the chat to 3 days after: "
                     + ("none" if not picked else ""))
        for b in picked:
            lines.append(f"  - {_when(b['issued_at'], start, end)} {b['title'][:40]} — {b['stage']}"
                         + (f", {b['amount']}" if b.get("amount") else ""))
        if more:
            lines.append("  - … more in that period")
    notes, _ = window(f.comments, "created_at", BONUS_BEFORE, MAX_ROWS["comments"])
    for c in (c for c in notes if c.get("text")):
        lines.append(f"Staff comment {_when(c['created_at'], start, end)}: {c['text'][:200]}")
    if f.errors:
        lines.append("Not read: " + ", ".join(e.split(":")[0] for e in f.errors))
    lines.append("=== END BACK OFFICE ===")
    return "\n".join(lines)
