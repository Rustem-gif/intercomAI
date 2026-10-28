"""The player's VIP segment, read from the Intercom contact.

QC's answers to the contradictions (2026-10-21) turn on whether the player is VIP / preVIP: a
VIP's closure request or demand for a manager must go to the VIP department, a regular
player's need not. QC assumed the AI cannot see that; it can. The contact's custom attributes,
synced from the back office, carry it:

- `Groups` — a comma list of marketing segments: `regular_vip` / `vip_except_ba` mark a VIP,
  `previp` a preVIP, `all_except_vip_and_ba` a regular player;
- `Profitable` — `regular_vip` for a VIP, `-` otherwise;
- `Status` — the loyalty level (`new_knight`, `new_baron`, `new_marquess`, …).

Checked against the 7 chats QC classified in its answers: 7/7 agree. These are the contact's
*current* attributes, not as of the chat — the header says so.

The conversation payload carries only a contact id, so this costs one GET /contacts/{id} per
player, cached for the run and in the `contact_segments` table.
"""
from __future__ import annotations

import asyncio
import re
from typing import Any, Protocol

from intercom_summary.intercom.client import IntercomClient
from intercom_summary.intercom.models import Contact, Conversation
from intercom_summary.logging_setup import get_logger

log = get_logger(__name__)

VIP = "VIP"
PREVIP = "preVIP"
REGULAR = "regular"

_VIP_GROUPS = frozenset({"regular_vip", "vip_except_ba"})
_PREVIP_GROUPS = frozenset({"previp"})


def _groups(attrs: dict[str, Any]) -> set[str]:
    raw = attrs.get("Groups") or ""
    items = raw if isinstance(raw, list) else str(raw).split(",")
    return {str(g).strip().lower() for g in items if str(g).strip()}


def segment_from_attrs(attrs: dict[str, Any] | None) -> tuple[str, str]:
    """(segment, loyalty_level) from a contact's custom attributes; ("", "") when it has none.

    VIP wins over preVIP: a promoted player can keep the `previp` group for a while.
    """
    if not attrs:
        return "", ""
    level = str(attrs.get("Status") or "").strip()
    level = level.removeprefix("new_").replace("_", " ") if level not in ("-", "None") else ""
    groups = _groups(attrs)
    profitable = str(attrs.get("Profitable") or "").strip()
    if not groups and profitable in ("", "-"):
        return "", level
    if groups & _VIP_GROUPS or profitable == "regular_vip":
        return VIP, level
    if groups & _PREVIP_GROUPS:
        return PREVIP, level
    return REGULAR, level


def bo_id_from(contact: dict[str, Any]) -> tuple[str, str]:
    """(BO player id, brand) from an Intercom contact: `cleanid`, else external_id "c<id>"."""
    attrs = contact.get("custom_attributes") or {}
    raw = str(attrs.get("cleanid") or "").strip()
    if not raw.isdigit():
        m = re.fullmatch(r"c?(\d+)", str(contact.get("external_id") or "").strip())
        raw = m.group(1) if m else ""
    brand = str(attrs.get("brand") or attrs.get("brandsmall") or "").strip().lower()
    return raw, brand


def header_line(contact: Contact) -> str:
    """The transcript header line; "unknown" tells the model a VIP-dependent rule can't be applied."""
    if not contact.segment:
        return "Player segment: unknown"
    level = f" (loyalty level: {contact.loyalty_level})" if contact.loyalty_level else ""
    return (f"Player segment: {contact.segment}{level} — Intercom contact attributes, "
            f"current, not as of the chat")


class SegmentCache(Protocol):
    def get(self, contact_id: str) -> tuple[str, str] | None: ...
    def bo(self, contact_id: str) -> tuple[str, str] | None: ...
    def put(self, contact_id: str, segment: str, loyalty_level: str, groups_raw: str,
            bo_id: str = "", bo_brand: str = "") -> None: ...


class ContactSegmenter:
    """Fills `conversation.contact.segment` / `.loyalty_level`, one GET per player per run.

    `cache` (storage.contact_segments_store.ContactSegmentsStore) is optional: without it the
    lookups are remembered for this run only. A failed lookup leaves the segment empty — the
    header then says "unknown" and the grader cannot apply a VIP-only rule, which is the safe
    side. It never fails the fetch.
    """

    def __init__(self, client: IntercomClient, cache: SegmentCache | None = None,
                 concurrency: int = 10) -> None:
        self._client = client
        self._cache = cache
        self._sem = asyncio.Semaphore(concurrency)
        self._seen: dict[str, tuple[str, str]] = {}
        self._bo: dict[str, tuple[str, str]] = {}
        self._pending: dict[str, asyncio.Task] = {}
        self.lookups = 0
        self.failures = 0

    async def lookup(self, contact_id: str) -> tuple[str, str]:
        if not contact_id:
            return "", ""
        if contact_id in self._seen:
            return self._seen[contact_id]
        if self._cache is not None:
            hit = self._cache.get(contact_id)
            if hit is not None:
                self._seen[contact_id] = hit
                self._bo[contact_id] = self._cache.bo(contact_id) or ("", "")
                return hit
        task = self._pending.get(contact_id)
        if task is None:
            task = asyncio.ensure_future(self._fetch(contact_id))
            self._pending[contact_id] = task
        return await task

    async def _fetch(self, contact_id: str) -> tuple[str, str]:
        async with self._sem:
            self.lookups += 1
            try:
                raw = await self._client.get_contact(contact_id)
            except Exception as exc:  # IntercomError or network trouble: never sinks the fetch
                self.failures += 1
                log.debug("Contact %s: no segment (%s)", contact_id, exc)
                self._seen[contact_id] = ("", "")
                return "", ""
        attrs = raw.get("custom_attributes") or {}
        result = segment_from_attrs(attrs)
        self._seen[contact_id] = result
        self._bo[contact_id] = bo_id_from(raw)
        if self._cache is not None:
            self._cache.put(contact_id, result[0], result[1], str(attrs.get("Groups") or ""),
                            *self._bo[contact_id])
        return result

    def bo_of(self, contact_id: str) -> tuple[str, str]:
        """(BO player id, brand) found by the last lookup of this contact."""
        return self._bo.get(contact_id, ("", ""))

    async def annotate(self, conversation: Conversation) -> Conversation:
        segment, level = await self.lookup(conversation.contact.id)
        conversation.contact.segment = segment
        conversation.contact.loyalty_level = level
        conversation.contact.bo_id, conversation.contact.bo_brand = self.bo_of(conversation.contact.id)
        return conversation
