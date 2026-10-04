import json
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
    """Stands in for the Claude client: every grade fails `info-actionable` (8 points) → 92/100."""

    def __init__(self, *a, **k):
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        body = json.dumps({
            "overall_score": 0, "critical_fail": False,
            "criteria": [{"id": "info-actionable", "v": "fail", "ded": -8, "ev": "Hi"}],
            "summary": "ok", "violations": ["minor"], "coaching": [],
        })
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=body)],
            stop_reason="end_turn", model="claude-sonnet-5-5", usage=None,
        )


def test_review_and_store_then_overview(temp_db, monkeypatch):
    cstore = ConversationsStore(temp_db)
    cstore.save(_convo("1"))
    cstore.save(_convo("2"))
    cstore.close()

    # Force the factory to the fake API grader (not the real Claude Code CLI).
    import intercom_summary.qa.backends as backends_mod
    import intercom_summary.qa.grader as grader_mod
    monkeypatch.setattr(grader_mod, "Anthropic", FakeAnthropic)
    monkeypatch.setattr(backends_mod, "get_grader",
                        lambda backend=None, ruleset_id=None: grader_mod.Grader(ruleset_id="default"))

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
    assert overview["kpis"]["avg_score"] == 92.0
    assert overview["agent_leaderboard"][0]["agent"] == "Ada"
    assert overview["top_violations"][0]["text"] == "minor"


def test_fetch_and_store_reports_blacklisted_skips(temp_db, monkeypatch):
    """A fetch whose conversations are all blacklisted used to report `fetched: N` and look
    like it succeeded while storing nothing. It must now report the skipped count."""
    import asyncio
    from intercom_summary.storage.trash_store import TrashStore

    convos = [_convo("1"), _convo("2"), _convo("3")]

    cstore = ConversationsStore(temp_db)
    cstore.save(_convo("2"))
    cstore.close()
    ts = TrashStore(temp_db)
    ts.move_to_trash(["2"], "boss")          # blacklisted → must not come back
    ts.close()

    async def fake_fetch(*, agents, since, until, state, limit, on_conversation, stats):
        stats["matched"] = len(convos)
        stats["tickets_skipped"] = 0
        for i, c in enumerate(convos, start=1):
            on_conversation(c, i, len(convos))
        return convos

    monkeypatch.setattr(service, "fetch_conversations_for_agents", fake_fetch)
    result = asyncio.run(service.fetch_and_store(agents=["Ada"]))

    assert result["fetched"] == 3
    assert result["saved"] == 2
    assert result["skipped_deleted"] == 1
    assert result["skipped_tickets"] == 0

    cstore = ConversationsStore(temp_db)
    assert cstore.get("1") is not None and cstore.get("3") is not None
    assert cstore.get("2") is None
    cstore.close()


def test_build_overview_scopes_kpis_to_one_brand(temp_db):
    """Two brands are two products — the dashboard must be able to describe one at a time."""
    store = ConversationsStore(temp_db)
    for cid, agent, brand in (("1", "Ada", "Betncare"),
                              ("2", "Ada", "Betncare"),
                              ("3", "Bob", "Tomb Riches")):
        c = _convo(cid)
        c.assignee = Admin(id="1", name=agent, email=f"{agent.lower()}@co.com")
        c.brand = brand
        store.save(c)
    store.close()

    assert service.build_overview()["kpis"]["conversations"] == 3
    assert service.build_overview()["kpis"]["agents"] == 2

    kb = service.build_overview(brand="Betncare")["kpis"]
    assert kb["conversations"] == 2 and kb["agents"] == 1

    tr = service.build_overview(brand="Tomb Riches")["kpis"]
    assert tr["conversations"] == 1 and tr["agents"] == 1


def test_claude_api_outage_aborts_the_run_instead_of_skipping_every_chat(temp_db, monkeypatch):
    import anthropic
    import httpx

    import intercom_summary.qa.backends as backends_mod

    cstore = ConversationsStore(temp_db)
    for cid in ("1", "2", "3"):
        cstore.save(_convo(cid))
    cstore.close()

    calls = 0

    class DownGrader:
        ruleset_id, rules_version = "default", "v"

        def grade(self, c):
            nonlocal calls
            calls += 1
            raise anthropic.APIConnectionError(request=httpx.Request("POST", "https://api"))

    monkeypatch.setattr(backends_mod, "get_grader", lambda backend=None, ruleset_id=None: DownGrader())
    result = service.review_and_store(conversation_ids=["1", "2", "3"], backend="api")
    assert result["backend_unreachable"] is True
    assert result["graded"] == 0
    assert calls < 3 or result["failed"] == 0
