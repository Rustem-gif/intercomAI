import pytest

from intercom_summary.intercom.fetch import (
    build_search_query,
    fetch_conversations_for_agents,
    is_ticket,
    normalise_conversation,
    resolve_admin_ids,
)


class FakeClient:
    def __init__(self, admins):
        self._admins = admins

    async def list_admins(self):
        return self._admins


async def test_resolve_by_email_and_name_case_insensitive():
    client = FakeClient([
        {"id": "1", "name": "Ada Lovelace", "email": "ada@co.com"},
        {"id": "2", "name": "Bob", "email": "bob@co.com"},
    ])
    resolved, unresolved = await resolve_admin_ids(client, ["ADA@CO.COM", "bob", "ghost"])
    assert set(resolved.keys()) == {"1", "2"}
    assert unresolved == ["ghost"]


def test_build_query_single_agent_with_window():
    q = build_search_query(["1"], since="2026-05-01", until="2026-06-01", state="closed")
    assert q["operator"] == "AND"
    fields = {c.get("field") for c in q["value"]}
    assert {"admin_assignee_id", "created_at", "state"} <= fields


def test_build_query_multiple_agents_uses_or():
    q = build_search_query(["1", "2"])
    assert q["operator"] == "OR"
    assert {c["value"] for c in q["value"]} == {"1", "2"}


def test_normalise_extracts_thread_and_metadata():
    raw = {
        "id": "42",
        "created_at": 1_700_000_000,
        "updated_at": 1_700_000_500,
        "state": "closed",
        "title": "Login issue",
        "assignee": {"type": "admin", "id": "1", "name": "Ada", "email": "ada@co.com"},
        "contacts": {"contacts": [{"id": "c1", "name": "Cara", "email": "cara@x.com"}]},
        "source": {"type": "conversation", "body": "<p>I can't log in</p>",
                   "author": {"type": "user", "name": "Cara"}},
        "conversation_parts": {"conversation_parts": [
            {"part_type": "comment", "body": "<p>Try resetting</p>", "created_at": 1_700_000_100,
             "author": {"type": "admin", "name": "Ada"}},
        ]},
        "tags": {"tags": [{"name": "login"}]},
        "conversation_rating": {"rating": 5, "remark": "great"},
        "statistics": {"first_admin_reply_time": 120},
    }
    convo = normalise_conversation(raw)
    assert convo.id == "42"
    assert convo.assignee_name == "Ada"
    assert convo.contact.name == "Cara"
    assert convo.message_count == 2
    assert convo.messages[0].text == "I can't log in"
    assert convo.messages[1].author_type == "admin"
    assert convo.csat_rating == 5
    assert convo.first_response_time == 120
    assert "login" in convo.tags


def _raw_convo(custom_attributes=None):
    raw = {
        "id": "7",
        "created_at": 1_700_000_000,
        "state": "closed",
        "source": {"body": "<p>Hi</p>", "author": {"type": "user", "name": "Cara"}},
        "conversation_parts": {"conversation_parts": []},
    }
    if custom_attributes is not None:
        raw["custom_attributes"] = custom_attributes
    return raw


def test_normalise_reads_brand_from_custom_attributes():
    conv = normalise_conversation(_raw_convo({"Brand": "Tomb Riches", "Language": "English"}))
    assert conv.brand == "Tomb Riches"


def test_normalise_brand_is_empty_when_absent():
    # Payloads without custom_attributes, or without a Brand in them, must not blow up —
    # they simply come back unbranded and the backfill can fill them in later.
    assert normalise_conversation(_raw_convo()).brand == ""
    assert normalise_conversation(_raw_convo({"Language": "English"})).brand == ""


def test_brand_survives_conversation_roundtrip():
    from intercom_summary.intercom.models import Conversation

    conv = normalise_conversation(_raw_convo({"Brand": "Betncare"}))
    assert Conversation.from_dict(conv.to_dict()).brand == "Betncare"


def test_brand_label_maps_default_brand_to_product_name():
    # The whole point of the label map: King Billy's conversations say "Betncare".
    from intercom_summary.intercom.brands import UNBRANDED_LABEL, brand_label

    assert brand_label("Betncare") == "King Billy"
    assert brand_label("Tomb Riches") == "Tomb Riches"   # unmapped → raw value
    assert brand_label("") == UNBRANDED_LABEL


# ── tickets are not chats ────────────────────────────────────────────────────────
def test_is_ticket_reads_the_ticket_object_not_the_type_field():
    # Both chats and tickets come back from /conversations/search as type "conversation";
    # only the `ticket` object separates them, and it is present on the search stub too.
    chat_stub = {"id": "1", "type": "conversation", "ticket": None}
    ticket_stub = {"id": "2", "type": "conversation",
                   "ticket": {"type": "ticket", "ticket_state": "resolved"}}
    assert is_ticket(chat_stub) is False
    assert is_ticket(ticket_stub) is True
    assert is_ticket({"id": "3"}) is False          # field absent entirely
    assert normalise_conversation(ticket_stub).is_ticket is True
    assert normalise_conversation(chat_stub).is_ticket is False


class _StubSearchClient:
    """Minimal client double: yields the given stubs, serves full threads from a dict."""

    def __init__(self, stubs):
        self._stubs = stubs
        self.fetched: list[str] = []

    async def list_admins(self):
        return [{"id": "1", "name": "Ada", "email": "ada@co.com"}]

    async def search_conversations(self, query, per_page=150):
        for s in self._stubs:
            yield s

    async def get_conversation(self, conversation_id):
        self.fetched.append(conversation_id)
        return {"id": conversation_id, "state": "closed",
                "source": {"type": "conversation", "body": "<p>hi</p>",
                           "author": {"type": "user", "name": "Cara"}}}

    async def aclose(self):
        pass


async def test_fetch_skips_tickets_before_spending_a_full_thread_fetch():
    client = _StubSearchClient([
        {"id": "chat-1", "ticket": None},
        {"id": "ticket-1", "ticket": {"type": "ticket"}},
        {"id": "chat-2", "ticket": None},
    ])
    stats: dict[str, int] = {}
    convos = await fetch_conversations_for_agents(
        agents=["Ada"], client=client, stats=stats
    )

    assert {c.id for c in convos} == {"chat-1", "chat-2"}
    # The point of filtering on the stub: the ticket costs no GET at all.
    assert sorted(client.fetched) == ["chat-1", "chat-2"]
    assert stats == {"matched": 3, "tickets_skipped": 1}


async def test_fetch_limit_counts_chats_not_matched_stubs():
    client = _StubSearchClient([
        {"id": "ticket-1", "ticket": {"type": "ticket"}},
        {"id": "ticket-2", "ticket": {"type": "ticket"}},
        {"id": "chat-1", "ticket": None},
        {"id": "chat-2", "ticket": None},
    ])
    convos = await fetch_conversations_for_agents(agents=["Ada"], client=client, limit=2)
    assert {c.id for c in convos} == {"chat-1", "chat-2"}


# ── the customer's identity ──────────────────────────────────────────────────────
def test_contact_recovered_from_message_authors_when_contacts_is_a_stub():
    # This workspace returns `contacts.contacts` as id-only stubs, which left Contact.name
    # empty on 100% of cached conversations — and so put "Customer name: unknown" in every
    # grader prompt, matching the open-name-use criterion's own "name not visible" escape.
    raw = {
        "id": "42",
        "created_at": 1_700_000_000,
        "state": "closed",
        "contacts": {"contacts": [{"type": "contact", "id": "c1"}]},   # no name, no email
        "source": {"type": "conversation", "body": "<p>Game error</p>",
                   "author": {"type": "user", "name": "Keely Smith", "email": "keely@x.com"}},
        "conversation_parts": {"conversation_parts": [
            {"part_type": "comment", "body": "<p>Hello, Keely!</p>", "created_at": 1_700_000_100,
             "author": {"type": "admin", "name": "Lenny"}},
        ]},
    }
    convo = normalise_conversation(raw)
    assert convo.contact.id == "c1"          # the stub still supplies the id
    assert convo.contact.name == "Keely Smith"
    assert convo.contact.email == "keely@x.com"


def test_contact_prefers_the_contacts_record_when_it_is_populated():
    raw = {
        "id": "43", "state": "closed",
        "contacts": {"contacts": [{"id": "c1", "name": "Real Name", "email": "real@x.com"}]},
        "source": {"type": "conversation", "body": "<p>hi</p>",
                   "author": {"type": "user", "name": "Byline Name", "email": "byline@x.com"}},
    }
    convo = normalise_conversation(raw)
    assert (convo.contact.name, convo.contact.email) == ("Real Name", "real@x.com")


def test_contact_recovery_ignores_agents_and_bots():
    raw = {
        "id": "44", "state": "closed",
        "contacts": {"contacts": []},
        "source": {"type": "conversation", "body": "<p>hi</p>",
                   "author": {"type": "bot", "name": "Billy Jr."}},
        "conversation_parts": {"conversation_parts": [
            {"part_type": "comment", "body": "<p>hello</p>",
             "author": {"type": "admin", "name": "Lenny", "email": "lenny@co.com"}},
            {"part_type": "comment", "body": "<p>my issue</p>",
             "author": {"type": "lead", "name": "Sanna Rokka"}},
        ]},
    }
    convo = normalise_conversation(raw)
    # A lead is a customer; the bot and the agent are not.
    assert convo.contact.name == "Sanna Rokka"
    assert convo.contact.email == ""
