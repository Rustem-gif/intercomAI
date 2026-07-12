from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from intercom_summary import service
from intercom_summary.intercom.models import Admin, Contact, Conversation, Message
from intercom_summary.settings import settings
from intercom_summary.storage.conversations_store import ConversationsStore


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    # settings is a frozen dataclass — bypass with object.__setattr__.
    object.__setattr__(settings, "db_path", tmp_path / "svc.db")
    return settings.db_path


def _convo(cid):
    return Conversation(
        id=cid, created_at=datetime(2026, 5, 1, tzinfo=timezone.utc), updated_at=None,
        state="closed", subject="S",
        assignee=Admin(id="1", name="Ada", email="ada@co.com"),
        contact=Contact(name="Cara"),
        messages=[Message(0, "admin", "Ada", None, "Hi")],
    )


class FakeAnthropic:
    def __init__(self, *a, **k):
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        block = SimpleNamespace(type="tool_use", name="submit_grade", input={
            "overall_score": 80, "summary": "ok", "rule_results": [],
            "violations": ["minor"], "suggestions": [],
        })
        return SimpleNamespace(content=[block])


def test_review_and_store_then_overview(temp_db, monkeypatch):
    cstore = ConversationsStore(temp_db)
    cstore.save(_convo("1"))
    cstore.save(_convo("2"))
    cstore.close()

    # Force the factory to the fake API grader (not the real Claude Code CLI).
    import intercom_summary.qa.backends as backends_mod
    import intercom_summary.qa.grader as grader_mod
    monkeypatch.setattr(grader_mod, "Anthropic", FakeAnthropic)
    monkeypatch.setattr(backends_mod, "get_grader", lambda backend=None, ruleset_id=None: grader_mod.Grader())

    result = service.review_and_store(conversation_ids=["1", "2"])
    assert result == {
        "graded": 2, "skipped": 0, "failed": 0, "total": 2, "ignored": 0,
        "cancelled": False, "backend_unreachable": False,
    }

    # Idempotent: re-running skips both.
    again = service.review_and_store(conversation_ids=["1", "2"])
    assert again["skipped"] == 2

    overview = service.build_overview()
    assert overview["kpis"]["conversations"] == 2
    assert overview["kpis"]["graded"] == 2
    assert overview["kpis"]["avg_score"] == 80.0
    assert overview["agent_leaderboard"][0]["agent"] == "Ada"
    assert overview["top_violations"][0]["text"] == "minor"
