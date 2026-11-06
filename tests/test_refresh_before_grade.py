"""Chats are re-read from Intercom right before grading (service._refresh_before_grading).

6 Nov 2026: a chat fetched while still open was graded an hour later from the cache — "no tags
set on this conversation" — although the agent had tagged it right after closing.
"""
import time
from datetime import datetime, timezone

import pytest

from intercom_summary import service
from intercom_summary.intercom import client as client_mod
from intercom_summary.intercom.fetch import normalise_conversation
from intercom_summary.settings import settings
from intercom_summary.storage.conversations_store import ConversationsStore

LONG_AGO = int(time.time()) - 3 * 3600


def raw(cid, *, state="closed", tags=(), closed_at=LONG_AGO):
    return {
        "id": cid, "created_at": LONG_AGO - 600, "updated_at": closed_at, "state": state,
        "assignee": {"type": "admin", "id": "1", "name": "Ada", "email": "ada@co.com"},
        "contacts": {"contacts": [{"id": "p1", "name": "Cara"}]},
        "source": {"type": "conversation", "body": "<p>My cashout is stuck</p>",
                   "author": {"type": "user", "name": "Cara"}},
        "conversation_parts": {"conversation_parts": []},
        "tags": {"tags": [{"name": t} for t in tags]},
        "statistics": {"last_close_at": closed_at},
    }


class FakeIntercom:
    now: dict = {}

    def __init__(self, *a, **k):
        pass

    async def list_admins(self):
        return [{"id": "1", "name": "Ada", "email": "ada@co.com"}]

    async def get_conversation(self, cid):
        r = self.now[cid]
        if isinstance(r, Exception):
            raise r
        return r

    async def aclose(self):
        pass


@pytest.fixture
def env(tmp_path, monkeypatch):
    saved = {k: getattr(settings, k) for k in ("db_path", "intercom_token", "qa_settle_minutes")}
    object.__setattr__(settings, "db_path", tmp_path / "g.db")
    object.__setattr__(settings, "qa_refresh_before_grade", True)
    object.__setattr__(settings, "intercom_token", "test-token")
    object.__setattr__(settings, "qa_settle_minutes", 30)
    monkeypatch.setattr(client_mod, "IntercomClient", FakeIntercom)
    store = ConversationsStore()
    # What the cache saw at fetch time: open, no tags.
    for cid in ("tagged-later", "still-open", "just-closed", "unreachable"):
        store.save(normalise_conversation(raw(cid, state="open")))
    store.close()
    FakeIntercom.now = {
        "tagged-later": raw("tagged-later", tags=["Cashout Problem", "Duplicate"]),
        "still-open": raw("still-open", state="open"),
        "just-closed": raw("just-closed", tags=["KYC"], closed_at=int(time.time()) - 60),
        "unreachable": RuntimeError("502 Bad Gateway"),
    }
    yield
    object.__setattr__(settings, "qa_refresh_before_grade", False)
    for k, v in saved.items():
        object.__setattr__(settings, k, v)


def _cached():
    s = ConversationsStore()
    try:
        return {cid: s.get(cid) for cid in ("tagged-later", "still-open", "just-closed", "unreachable")}
    finally:
        s.close()


def test_grades_the_chat_as_it_is_now(env):
    fresh, held = service._refresh_before_grading(list(_cached().values()))
    assert fresh["tagged-later"].tags == ["Cashout Problem", "Duplicate"]
    assert fresh["tagged-later"].state == "closed"
    # The cache is updated too, so the UI and exports see the tags.
    assert _cached()["tagged-later"].tags == ["Cashout Problem", "Duplicate"]
    assert held == 2                                   # still open + closed a minute ago
    assert "still-open" not in fresh and "just-closed" not in fresh


def test_unreachable_chat_falls_back_to_the_cache(env):
    fresh, _ = service._refresh_before_grading(list(_cached().values()))
    assert fresh["unreachable"].state == "open"        # graded from the cache, not dropped


def test_off_switch_and_no_token_leave_the_cache_alone(env):
    object.__setattr__(settings, "qa_refresh_before_grade", False)
    convos = list(_cached().values())
    fresh, held = service._refresh_before_grading(convos)
    assert held == 0 and set(fresh) == {c.id for c in convos}
