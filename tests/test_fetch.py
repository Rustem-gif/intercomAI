import pytest

from intercom_summary.intercom.fetch import (
    build_search_query,
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
