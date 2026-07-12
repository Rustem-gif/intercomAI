"""review_and_store must skip a conversation whose grading fails (e.g. an unparseable
model response) instead of aborting the whole batch or persisting a bogus 0/100."""
from datetime import datetime, timezone

import pytest

from intercom_summary import service
from intercom_summary.intercom.models import Admin, Contact, Conversation, Message
from intercom_summary.qa.ollama_grader import GradeParseError
from intercom_summary.qa.schema import ConversationGrade
from intercom_summary.settings import settings
from intercom_summary.storage.conversations_store import ConversationsStore
from intercom_summary.storage.grades_store import GradesStore


@pytest.fixture
def temp_db(tmp_path):
    object.__setattr__(settings, "db_path", tmp_path / "r.db")
    return tmp_path


def _conv(cid: str) -> Conversation:
    return Conversation(
        id=cid, created_at=datetime(2026, 5, 1, tzinfo=timezone.utc), updated_at=None,
        state="closed", subject="S", assignee=Admin(id="1", name="Ada", email="a@co.com"),
        contact=Contact(name="Cara"), messages=[Message(0, "admin", "Ada", None, "hi")],
    )


class _FakeGrader:
    """Grades 'good' fine; raises on 'bad' (simulating an unusable model response)."""
    rules_version = "v1"

    def grade(self, convo):
        if convo.id == "bad":
            raise GradeParseError("no usable grade after retries")
        return ConversationGrade(
            conversation_id=convo.id, agent_name="Ada", overall_score=80, summary="ok",
            rules_version="v1", model="ollama/test",
            graded_at=datetime.now(timezone.utc).isoformat(),
        )


def _conv_tagged(cid: str, tags: list[str]) -> Conversation:
    c = _conv(cid)
    c.tags = tags
    return c


def test_ignored_tag_conversations_are_not_graded(temp_db, monkeypatch):
    cs = ConversationsStore()
    cs.save(_conv("plain"))
    cs.save(_conv_tagged("spammy", ["Spam"]))            # case-insensitive match
    cs.save(_conv_tagged("followup", ["KYC", "Follow-Up"]))
    cs.close()

    monkeypatch.setattr(
        "intercom_summary.qa.backends.get_grader", lambda backend=None, ruleset_id=None: _FakeGrader()
    )

    result = service.review_and_store(regrade=True)  # bulk path
    assert result["ignored"] == 2
    assert result["graded"] == 1
    assert result["total"] == 1

    gs = GradesStore()
    try:
        assert gs.get("plain") is not None
        assert gs.get("spammy") is None      # never graded
        assert gs.get("followup") is None    # never graded
    finally:
        gs.close()


def test_failed_grade_is_skipped_not_saved(temp_db, monkeypatch):
    cs = ConversationsStore()
    cs.save(_conv("good"))
    cs.save(_conv("bad"))
    cs.close()

    monkeypatch.setattr(service, "get_grader", lambda backend=None, ruleset_id=None: _FakeGrader(), raising=False)
    monkeypatch.setattr(
        "intercom_summary.qa.backends.get_grader", lambda backend=None, ruleset_id=None: _FakeGrader()
    )

    result = service.review_and_store(conversation_ids=["good", "bad"], regrade=True)

    assert result["graded"] == 1
    assert result["failed"] == 1
    assert result["graded"] + result["skipped"] == result["total"]  # progress reaches 100%

    gs = GradesStore()
    try:
        assert gs.get("good") is not None        # good grade persisted
        assert gs.get("bad") is None             # failed grade NOT saved as a 0/100
    finally:
        gs.close()
