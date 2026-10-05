"""Calibration runs: grade a frozen sample into its own tables, never into live grades."""
import sqlite3
from datetime import datetime, timezone

import pytest

from intercom_summary import service
from intercom_summary.intercom.models import Admin, Contact, Conversation, Message
from intercom_summary.qa.schema import ConversationGrade, RuleResult
from intercom_summary.settings import settings
from intercom_summary.storage.calibration_store import CalibrationStore
from intercom_summary.storage.conversations_store import ConversationsStore
from intercom_summary.storage.grades_store import GradesStore


@pytest.fixture
def temp_db(tmp_path):
    object.__setattr__(settings, "db_path", tmp_path / "cal.db")
    return settings.db_path


def _convo(cid, ticket=False):
    return Conversation(
        id=cid, created_at=datetime(2026, 8, 1, tzinfo=timezone.utc), updated_at=None,
        state="closed", subject="S", is_ticket=ticket,
        assignee=Admin(id="1", name="Ada", email="ada@co.com"),
        contact=Contact(name="Cara"),
        messages=[Message(0, "admin", "Ada", None, "Hi")],
    )


class FakeGrader:
    """Fails res-no-fake-close (−15) on every chat → 85."""
    ruleset_id, rules_version = "default", "v-cal"

    def __init__(self):
        self.graded: list[str] = []

    def grade(self, c):
        self.graded.append(c.id)
        return ConversationGrade(
            conversation_id=c.id, agent_name="Ada", overall_score=85, summary="ai",
            rule_results=[RuleResult("res-no-fake-close", "No Fake Closure", "fail", "x")],
            rules_version="v-cal", model="fake", graded_at="2026-10-05T00:00:00+00:00",
            ruleset_id="default",
        )


def _seed(temp_db):
    cal = CalibrationStore(temp_db)
    cal.create("s1", "Sample")
    cal.replace_items("s1", [
        {"conversation_id": cid, "seq": i + 1, "pilot": i == 0}
        for i, cid in enumerate(["1", "2", "3", "4", "5"])
    ])
    cal.close()
    # "1" is cached with a live grade carrying a human override; "2" is cached, ungraded.
    cstore = ConversationsStore(temp_db)
    cstore.save(_convo("1"))
    cstore.save(_convo("2"))
    cstore.close()
    g = GradesStore(temp_db)
    g.save(ConversationGrade(conversation_id="1", agent_name="Ada", overall_score=70,
                             summary="live", rules_version="old", model="qwen",
                             graded_at="2026-06-01T00:00:00+00:00"))
    g.save_override("1", 60, "human", "qa")
    g.close()


def _count(db, table):
    with sqlite3.connect(db) as c:
        return c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def test_run_grades_into_its_own_tables_and_fetches_the_rest(temp_db, monkeypatch):
    _seed(temp_db)
    import intercom_summary.qa.backends as backends_mod

    fake = FakeGrader()
    monkeypatch.setattr(backends_mod, "get_grader", lambda backend=None, ruleset_id=None: fake)
    fetched_ids: list[str] = []

    def fetch(ids):
        fetched_ids.extend(ids)
        return {"3": _convo("3"), "4": _convo("4", ticket=True), "5": "not_found"}

    grades_before = _count(temp_db, "grades")
    history_before = _count(temp_db, "grade_history")
    result = service.run_calibration("s1", ruleset_id="default", fetch=fetch)

    assert sorted(fetched_ids) == ["3", "4", "5"]
    assert sorted(fake.graded) == ["1", "2", "3"]
    assert result["graded"] == 3 and result["tickets"] == 1 and result["unavailable"] == 1

    # Live grades untouched: same rows, same score, human override intact, no history added.
    assert _count(temp_db, "grades") == grades_before
    assert _count(temp_db, "grade_history") == history_before
    g = GradesStore(temp_db)
    live = g.get("1")
    g.close()
    assert live["overall_score"] == 70 and live["human_score"] == 60
    # Fetched chats are not written into the live cache.
    cstore = ConversationsStore(temp_db)
    assert cstore.get("3") is None
    cstore.close()

    cal = CalibrationStore(temp_db)
    rows = {r["conversation_id"]: r for r in cal.results(result["run_id"])}
    detail = cal.result(result["run_id"], "3")
    cal.close()
    assert {k: r["status"] for k, r in rows.items()} == {
        "1": "graded", "2": "graded", "3": "graded", "4": "ticket", "5": "not_found"}
    assert rows["3"]["source"] == "intercom" and rows["1"]["source"] == "cache"
    assert rows["1"]["overall_score"] == 85 and rows["1"]["live_human_score"] == 60
    # The transcript is kept so a chat that was never cached stays viewable.
    assert detail["conversation_json"]["id"] == "3"


def test_pilot_only_restricts_the_run(temp_db, monkeypatch):
    _seed(temp_db)
    import intercom_summary.qa.backends as backends_mod

    fake = FakeGrader()
    monkeypatch.setattr(backends_mod, "get_grader", lambda backend=None, ruleset_id=None: fake)
    service.run_calibration("s1", ruleset_id="default", pilot_only=True, fetch=lambda ids: {})
    assert fake.graded == ["1"]


def test_review_keeps_ai_side_on_regrade(temp_db):
    _seed(temp_db)
    cal = CalibrationStore(temp_db)
    run = cal.start_run("s1", "default")
    grade = FakeGrader().grade(_convo("2"))
    cal.save_result(run, "2", "graded", source="cache", grade=grade, conversation=_convo("2"))
    assert cal.save_review(run, "2", 100, "fine", "qa", human_criteria={"res-no-fake-close": "pass"})
    # A retried AI grade must not wipe the manager's verdict.
    cal.save_result(run, "2", "graded", source="cache", grade=grade)
    r = cal.result(run, "2")
    cal.close()
    assert r["human_score"] == 100 and r["human_criteria"] == {"res-no-fake-close": "pass"}
    assert r["conversation_json"]["id"] == "2"
