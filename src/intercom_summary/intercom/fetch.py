"""High-level conversation fetching: agents in, normalised Conversations out."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Iterable

from intercom_summary.intercom.client import IntercomClient
from intercom_summary.intercom.htmltext import html_to_text
from intercom_summary.intercom.models import (
    Admin,
    Contact,
    Conversation,
    Message,
    ts_to_dt,
)
from intercom_summary.logging_setup import get_logger

log = get_logger(__name__)


def _to_unix(value: str | datetime | None) -> int | None:
    """Accept 'YYYY-MM-DD', ISO datetime, or datetime -> unix seconds (UTC)."""
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        v = value.strip()
        try:
            dt = datetime.fromisoformat(v)
        except ValueError:
            dt = datetime.strptime(v, "%Y-%m-%d")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


async def resolve_admin_ids(
    client: IntercomClient, agents: Iterable[str]
) -> tuple[dict[str, Admin], list[str]]:
    """Map agent identifiers (name OR email, case-insensitive) to Admin objects.

    Returns (admins_by_id, unresolved) where unresolved lists inputs we couldn't match.
    """
    raw = await client.list_admins()
    by_email = {a.get("email", "").lower(): a for a in raw if a.get("email")}
    by_name = {a.get("name", "").lower(): a for a in raw if a.get("name")}

    resolved: dict[str, Admin] = {}
    unresolved: list[str] = []
    for ident in agents:
        key = ident.strip().lower()
        a = by_email.get(key) or by_name.get(key)
        if not a:
            unresolved.append(ident)
            continue
        admin = Admin(id=str(a["id"]), name=a.get("name", ""), email=a.get("email", ""))
        resolved[admin.id] = admin
    return resolved, unresolved


def build_search_query(
    admin_ids: list[str],
    since: str | datetime | None = None,
    until: str | datetime | None = None,
    state: str | None = None,
) -> dict[str, Any]:
    """Build an Intercom conversation-search query.

    Filters by assigned admin (OR across multiple), optional created_at window, and
    optional state. See: https://developers.intercom.com/docs/references/rest-api/api.intercom.io/conversations/searchconversations
    """
    clauses: list[dict[str, Any]] = []

    if len(admin_ids) == 1:
        clauses.append({"field": "admin_assignee_id", "operator": "=", "value": admin_ids[0]})
    elif admin_ids:
        clauses.append(
            {
                "operator": "OR",
                "value": [
                    {"field": "admin_assignee_id", "operator": "=", "value": aid}
                    for aid in admin_ids
                ],
            }
        )

    since_ts = _to_unix(since)
    until_ts = _to_unix(until)
    if since_ts is not None:
        clauses.append({"field": "created_at", "operator": ">", "value": since_ts})
    if until_ts is not None:
        clauses.append({"field": "created_at", "operator": "<", "value": until_ts})
    if state:
        clauses.append({"field": "state", "operator": "=", "value": state})

    if len(clauses) == 1:
        return clauses[0]
    return {"operator": "AND", "value": clauses}


def is_ticket(raw: dict[str, Any]) -> bool:
    """True when an Intercom conversation payload is really a *ticket*, not a chat.

    Tickets and chats share one id namespace and one search endpoint: `/conversations/search`
    returns both, and every payload carries `"type": "conversation"` either way, so neither the
    endpoint nor the type field separates them. The one reliable marker — present on the search
    *stub* as well as the full thread — is the `ticket` object, which is `null` for a chat and a
    `{"type": "ticket", "ticket_type": …, "ticket_state": …}` dict for a ticket.

    Filtering on the stub is what matters: it keeps tickets out before we spend a full-thread
    GET on each one.
    """
    return bool(raw.get("ticket"))


def _author_name(author: dict[str, Any]) -> str:
    return author.get("name") or author.get("email") or author.get("type", "") or "unknown"


def normalise_conversation(
    raw: dict[str, Any],
    known_admins: dict[str, Admin] | None = None,
) -> Conversation:
    """Turn a full Intercom conversation payload into our Conversation dataclass.

    `known_admins` (id → Admin) is used as a fallback when the `assignee` field
    in the API response is a team rather than an admin object — the actual admin
    is then identified via `admin_assignee_id`.
    """
    assignee_admin = None
    admin_obj = raw.get("assignee")
    if isinstance(admin_obj, dict) and admin_obj.get("type") == "admin":
        assignee_admin = Admin(
            id=str(admin_obj.get("id", "")),
            name=admin_obj.get("name", ""),
            email=admin_obj.get("email", ""),
        )

    # Intercom often sets assignee to a *team* while admin_assignee_id carries the
    # individual admin.  Fall back to our already-resolved roster when that happens.
    if assignee_admin is None:
        admin_id = str(raw.get("admin_assignee_id") or "")
        if admin_id and known_admins and admin_id in known_admins:
            assignee_admin = known_admins[admin_id]

    contact = Contact()
    contacts = (raw.get("contacts") or {}).get("contacts") or []
    if contacts:
        c = contacts[0]
        contact = Contact(id=str(c.get("id", "")), name=c.get("name", ""), email=c.get("email", ""))

    messages: list[Message] = []
    seq = 0

    # The opening message (source) is the first part of the thread.
    source = raw.get("source") or {}
    if source.get("body") or source.get("type"):
        author = source.get("author") or {}
        messages.append(
            Message(
                seq=seq,
                author_type=author.get("type", "user"),
                author_name=_author_name(author),
                created_at=ts_to_dt(raw.get("created_at")),
                text=html_to_text(source.get("body")),
                part_type="comment",
            )
        )
        seq += 1

    for part in (raw.get("conversation_parts") or {}).get("conversation_parts", []):
        body = html_to_text(part.get("body"))
        ptype = part.get("part_type", "")
        # Skip empty system bookkeeping parts that carry no readable content.
        if not body and ptype in ("", "conversation_attribute_updated_by_admin"):
            continue
        author = part.get("author") or {}
        messages.append(
            Message(
                seq=seq,
                author_type=author.get("type", "system"),
                author_name=_author_name(author),
                created_at=ts_to_dt(part.get("created_at")),
                text=body,
                part_type=ptype,
            )
        )
        seq += 1

    stats = raw.get("statistics") or {}
    rating = (raw.get("conversation_rating") or {})

    tags = [t.get("name", "") for t in (raw.get("tags") or {}).get("tags", []) if t.get("name")]

    # Which brand of the multi-brand workspace this arrived through. Intercom puts it on every
    # conversation payload (full threads and search stubs alike), so capturing it is free — but
    # it is NOT a searchable field, hence we record it here and filter locally. See
    # intercom/brands.py for why the raw value is kept verbatim.
    brand = str((raw.get("custom_attributes") or {}).get("Brand") or "")

    return Conversation(
        id=str(raw.get("id", "")),
        is_ticket=is_ticket(raw),
        created_at=ts_to_dt(raw.get("created_at")),
        updated_at=ts_to_dt(raw.get("updated_at")),
        state=raw.get("state", ""),
        subject=raw.get("title") or source.get("subject") or "",
        assignee=assignee_admin,
        contact=contact,
        messages=messages,
        tags=tags,
        brand=brand,
        csat_rating=rating.get("rating"),
        csat_remark=rating.get("remark") or "",
        first_response_time=stats.get("first_admin_reply_time") or stats.get("time_to_admin_reply"),
        time_to_close=stats.get("time_to_last_close") or stats.get("median_time_to_reply"),
    )


async def fetch_conversations_for_agents(
    agents: list[str],
    since: str | datetime | None = None,
    until: str | datetime | None = None,
    state: str | None = None,
    client: IntercomClient | None = None,
    limit: int | None = None,
    concurrency: int = 10,
    on_conversation: Callable[[Conversation, int, int], None] | None = None,
    stats: dict[str, int] | None = None,
) -> list[Conversation]:
    """End-to-end: resolve agents, search, fetch full threads, normalise.

    **Chats only.** Intercom's conversation search returns tickets alongside chats and we
    grade and export chats only, so ticket stubs are dropped here — before the full-thread
    GET, so they cost nothing.

    `limit` caps the number of conversations fetched (useful for smoke tests) and is applied
    to chats, so `limit=50` still yields 50 chats in a ticket-heavy window.
    `concurrency` controls how many full-thread GETs run in parallel.
    `on_conversation` is called with (conv, fetched_so_far, total) after each conversation
    is fetched — useful for incremental saves and progress reporting.
    `stats`, when given, is filled with {"matched", "tickets_skipped"} so callers can report
    the gap between what Intercom matched and what we imported instead of leaving it
    looking like a short fetch.
    """
    import asyncio

    own = client is None
    client = client or IntercomClient()
    try:
        admins, unresolved = await resolve_admin_ids(client, agents)
        if unresolved:
            log.warning("Could not resolve agent(s): %s", ", ".join(unresolved))
        if not admins:
            raise ValueError(
                "No agents resolved to Intercom admins. Check names/emails against your workspace."
            )

        query = build_search_query(list(admins.keys()), since, until, state)
        log.info("Searching conversations for %d agent(s)…", len(admins))

        # Collect all stubs first (pagination is sequential but fast — no body data).
        stubs: list[dict] = []
        matched = 0
        tickets_skipped = 0
        async for stub in client.search_conversations(query):
            matched += 1
            if is_ticket(stub):
                tickets_skipped += 1
                continue
            stubs.append(stub)
            if limit and len(stubs) >= limit:
                break

        if stats is not None:
            stats["matched"] = matched
            stats["tickets_skipped"] = tickets_skipped
        if tickets_skipped:
            log.info("Skipping %d ticket(s) of %d matched — chats only.", tickets_skipped, matched)

        total = len(stubs)
        log.info("Found %d chat stub(s), fetching full threads (concurrency=%d)…", total, concurrency)

        conversations: list[Conversation] = []
        sem = asyncio.Semaphore(concurrency)

        async def fetch_one(stub: dict) -> Conversation:
            async with sem:
                full = await client.get_conversation(str(stub["id"]))
            conv = normalise_conversation(full, known_admins=admins)
            # No await after sem exit, so this block is atomic from asyncio's perspective.
            conversations.append(conv)
            if on_conversation:
                on_conversation(conv, len(conversations), total)
            return conv

        await asyncio.gather(*[fetch_one(s) for s in stubs])

        log.info("Fetched %d chat(s).", len(conversations))
        return conversations
    finally:
        if own:
            await client.aclose()
