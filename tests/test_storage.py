from datetime import datetime, timezone

from intercom_summary.intercom.models import Admin, Contact, Conversation, Message
from intercom_summary.qa.schema import ConversationGrade, RuleResult
from intercom_summary.storage.conversations_store import ConversationsStore
from intercom_summary.storage.grades_store import GradesStore
from intercom_summary.storage.jobs_store import JobsStore


def _convo(cid="42", agent="Ada", score_state="closed"):
    return Conversation(
        id=cid,
        created_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
        updated_at=datetime(2026, 5, 2, tzinfo=timezone.utc),
        state=score_state,
        subject="Login issue",
        assignee=Admin(id="1", name=agent, email="ada@co.com"),
        contact=Contact(name="Cara", email="cara@x.com"),
        messages=[
            Message(0, "user", "Cara", datetime(2026, 5, 1, tzinfo=timezone.utc), "Help"),
            Message(1, "admin", agent, datetime(2026, 5, 1, tzinfo=timezone.utc), "Sure"),
        ],
        tags=["login"],
        csat_rating=5,
    )


def test_conversation_roundtrip_via_store(tmp_path):
    db = tmp_path / "t.db"
    store = ConversationsStore(db)
    store.save(_convo())
    got = store.get("42")
    assert got is not None
    assert got.assignee_name == "Ada"
    assert got.message_count == 2
    assert got.messages[0].text == "Help"
    assert got.tags == ["login"]
    assert store.count() == 1
    assert store.agents() == ["Ada"]


def test_query_filters_and_score_join(tmp_path):
    db = tmp_path / "t.db"
    cstore = ConversationsStore(db)
    cstore.save(_convo("1", "Ada"))
    cstore.save(_convo("2", "Bob"))

    gstore = GradesStore(db)
    gstore.save(ConversationGrade(
        conversation_id="1", agent_name="Ada", overall_score=90, summary="good",
        rule_results=[RuleResult("tone-greeting", "Greeting", "pass")],
        graded_at="2026-05-03T00:00:00+00:00",
    ))

    rows, total = cstore.query(agents=["Ada"])
    assert total == 1 and rows[0]["id"] == "1"
    assert rows[0]["score"] == 90

    rows, total = cstore.query(min_score=50)
    assert total == 1  # only the graded one passes a score filter


def _tagged(cid, tags):
    c = _convo(cid)
    c.tags = tags
    return c


def test_tags_are_ignored_helper():
    from intercom_summary.storage.conversations_store import tags_are_ignored
    assert tags_are_ignored(["Spam"])               # case-insensitive
    assert tags_are_ignored(["KYC", "Follow-Up"])   # any match
    assert not tags_are_ignored(["login"])
    assert not tags_are_ignored([])
    assert not tags_are_ignored(None)


def test_evaluation_counts_excludes_ignored(tmp_path):
    db = tmp_path / "t.db"
    cstore = ConversationsStore(db)
    cstore.save(_tagged("plain", ["login"]))
    cstore.save(_tagged("spammy", ["Spam"]))
    cstore.save(_tagged("jira", ["Jira"]))
    gstore = GradesStore(db)
    # Grade one gradeable and one ignored conversation, both on ruleset "v1".
    for cid in ("plain", "spammy"):
        gstore.save(ConversationGrade(
            conversation_id=cid, agent_name="Ada", overall_score=90, summary="ok",
            rules_version="v1", graded_at="2026-05-03T00:00:00+00:00",
        ))

    counts = cstore.evaluation_counts("v1")
    assert counts["total"] == 1       # only "plain" is gradeable
    assert counts["ignored"] == 2     # spammy + jira
    assert counts["graded"] == 1      # the ignored "spammy" grade is excluded
    assert counts["graded_current"] == 1
    # A grade under a different ruleset is "stale" (graded but not graded_current).
    assert cstore.evaluation_counts("v2")["graded_current"] == 0
    cstore.close()
    gstore.close()


def _convo_on(cid, agent, created_iso):
    c = _convo(cid, agent)
    c.created_at = datetime.fromisoformat(created_iso)
    return c


def test_agent_scores_period_and_override(tmp_path):
    db = tmp_path / "t.db"
    cstore = ConversationsStore(db)
    cstore.save(_convo_on("a1", "Ada", "2026-06-20T00:00:00+00:00"))
    cstore.save(_convo_on("a2", "Ada", "2026-01-01T00:00:00+00:00"))
    cstore.save(_convo_on("b1", "Bob", "2026-06-21T00:00:00+00:00"))
    gstore = GradesStore(db)
    for cid, agent, score in [("a1", "Ada", 80), ("a2", "Ada", 60), ("b1", "Bob", 90)]:
        gstore.save(ConversationGrade(
            conversation_id=cid, agent_name=agent, overall_score=score, summary="ok",
            graded_at="2026-06-25T00:00:00+00:00",
        ))
    # A human override must win over the AI score in the average.
    gstore.save_override("a1", 100, "manager call", "boss")

    # All-time: Ada = (100 + 60)/2 = 80 over 2 grades; Bob = 90.
    all_time = {r["agent"]: r for r in gstore.agent_scores()}
    assert all_time["Ada"]["avg_score"] == 80.0 and all_time["Ada"]["count"] == 2
    assert all_time["Bob"]["avg_score"] == 90.0

    # Period (conversations since 2026-06-01): only a1 (Ada, overridden 100) and b1.
    recent = {r["agent"]: r for r in gstore.agent_scores("2026-06-01T00:00:00+00:00")}
    assert recent["Ada"]["avg_score"] == 100.0 and recent["Ada"]["count"] == 1
    assert recent["Bob"]["avg_score"] == 90.0
    cstore.close()
    gstore.close()


def test_trash_round_trip_preserves_override(tmp_path):
    from intercom_summary.storage.trash_store import TrashStore
    db = tmp_path / "t.db"
    cstore = ConversationsStore(db)
    cstore.save(_convo("9", "Ada"))
    cstore.close()
    gstore = GradesStore(db)
    gstore.save(ConversationGrade(
        conversation_id="9", agent_name="Ada", overall_score=70, summary="s",
        graded_at="2026-05-03T00:00:00+00:00",
    ))
    gstore.save_override("9", 95, "manager call", "boss")
    gstore.close()

    ts = TrashStore(db)
    assert ts.move_to_trash(["9"], "boss") == 1
    cs = ConversationsStore(db)
    assert cs.get("9") is None        # gone from live tables
    cs.close()
    assert ts.count() == 1
    assert ts.restore(["9"]) == 1     # restore re-inserts conversation + grade
    ts.close()

    gstore = GradesStore(db)
    g = gstore.get("9")
    gstore.close()
    # The grade comes back verbatim, including the human override columns.
    assert g["overall_score"] == 70 and g["human_score"] == 95
    assert g["override_reason"] == "manager call"
    cs = ConversationsStore(db)
    assert cs.get("9") is not None
    cs.close()


def test_jobs_lifecycle(tmp_path):
    js = JobsStore(tmp_path / "t.db")
    jid = js.create("fetch", {"agents": ["Ada"]})
    assert js.get(jid)["status"] == "queued"
    js.update(jid, status="running")
    js.update(jid, status="done", result={"fetched": 3})
    job = js.get(jid)
    assert job["status"] == "done"
    assert job["result"]["fetched"] == 3
    assert job["params"]["agents"] == ["Ada"]
