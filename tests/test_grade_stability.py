"""The September incident, as a regression test.

What happened: the QA rulebook was corrected on 3 and 4 September. `rules_version` hashes the
prompt text, so both edits marked every stored grade stale, and the next review runs silently
re-graded them with different scores. QA had already done their manual pass over the month's
red/yellow chats, and agents had disputed specific numbers. Those numbers then moved, and
because nothing recorded what a grade used to be, nobody could show what had changed.

Two guarantees follow, and both are load-bearing:
  1. a chat an analyst has re-graded is never re-graded by an ordinary run;
  2. any grade that *is* overwritten leaves its predecessor behind.
"""
from datetime import datetime, timezone

import pytest

from intercom_summary.intercom.models import Admin, Contact, Conversation, Message
from intercom_summary.qa import backends as backends_mod
from intercom_summary.qa.schema import ConversationGrade
from intercom_summary.settings import settings
from intercom_summary.storage.conversations_store import ConversationsStore
from intercom_summary.storage.grades_store import GradesStore
from intercom_summary import service


def _convo(cid, agent="Ada"):
    return Conversation(
        id=cid, created_at=datetime(2026, 8, 27, tzinfo=timezone.utc),
        updated_at=datetime(2026, 8, 27, tzinfo=timezone.utc),
        state="closed", subject="Withdrawal",
        assignee=Admin(id="1", name=agent, email="ada@co.com"),
        contact=Contact(name="Cara", email="cara@x.com"),
        messages=[Message(0, "user", "Cara", datetime(2026, 8, 27, tzinfo=timezone.utc), "Help")],
    )


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "stability.db"
    object.__setattr__(settings, "db_path", path)
    cstore = ConversationsStore(path)
    cstore.save(_convo("reviewed"))
    cstore.save(_convo("untouched"))
    cstore.close()
    return path


def _seed_grade(db, cid, score, version):
    store = GradesStore(db)
    g = ConversationGrade(conversation_id=cid, agent_name="Ada", overall_score=score,
                          summary="s", rules_version=version, ruleset_id="default",
                          model="ollama/test", graded_at="2026-09-01T00:00:00+00:00")
    store.save(g)
    store.close()


def _run_with_new_rules(monkeypatch, new_version="v2", new_score=41):
    """Re-run grading after a rulebook edit. The model returns a different score, as it did."""
    class _Grader:
        def __init__(self, ruleset_id=None, **_):
            self.ruleset_id = ruleset_id or "default"
            self.rules_version = new_version

        def grade(self, convo):
            return ConversationGrade(
                conversation_id=convo.id, agent_name=convo.assignee_name,
                overall_score=new_score, summary="re-graded", rules_version=new_version,
                ruleset_id="default", model="ollama/test",
                graded_at="2026-09-04T00:00:00+00:00",
            )

    monkeypatch.setattr(backends_mod, "get_grader",
                        lambda backend=None, ruleset_id=None: _Grader(ruleset_id))
    return service.review_and_store(conversation_ids=["reviewed", "untouched"])


def test_a_rules_change_does_not_move_a_score_qa_signed_off_on(db, monkeypatch):
    _seed_grade(db, "reviewed", 92, "v1")
    _seed_grade(db, "untouched", 92, "v1")

    store = GradesStore(db)
    store.save_override("reviewed", 88, "checked by hand against the transcript", "ana")
    store.close()

    _run_with_new_rules(monkeypatch)

    store = GradesStore(db)
    reviewed, untouched = store.get("reviewed"), store.get("untouched")
    store.close()

    # The chat QA signed off on is untouched, AI score and all.
    assert reviewed["overall_score"] == 92
    assert reviewed["human_score"] == 88
    assert reviewed["rules_version"] == "v1"
    # The one nobody reviewed is re-graded under the new rules, which is the point of the edit.
    assert untouched["overall_score"] == 41
    assert untouched["rules_version"] == "v2"


def test_an_explicit_regrade_still_reaches_a_reviewed_chat(db, monkeypatch):
    """Protection, not a lock — a deliberate re-grade after a rules change must still work."""
    _seed_grade(db, "reviewed", 92, "v1")
    store = GradesStore(db)
    store.save_override("reviewed", 88, "checked", "ana")
    store.close()

    class _Grader:
        ruleset_id, rules_version = "default", "v2"

        def grade(self, convo):
            return ConversationGrade(
                conversation_id=convo.id, agent_name="Ada", overall_score=41, summary="s",
                rules_version="v2", ruleset_id="default", model="ollama/test",
                graded_at="2026-09-04T00:00:00+00:00")

    monkeypatch.setattr(backends_mod, "get_grader",
                        lambda backend=None, ruleset_id=None: _Grader())
    service.review_and_store(conversation_ids=["reviewed"], regrade=True)

    store = GradesStore(db)
    g = store.get("reviewed")
    store.close()
    assert g["overall_score"] == 41
    # and the analyst's verdict survives the re-grade, as it always has
    assert g["human_score"] == 88


def test_the_superseded_score_is_still_on_record(db, monkeypatch):
    """"Why is this chat red now when it was green last week?" must have an answer."""
    _seed_grade(db, "untouched", 92, "v1")

    _run_with_new_rules(monkeypatch, new_version="v2", new_score=41)

    store = GradesStore(db)
    hist = store.history("untouched")
    store.close()

    assert len(hist) == 1
    assert hist[0]["overall_score"] == 92
    assert hist[0]["rules_version"] == "v1"
