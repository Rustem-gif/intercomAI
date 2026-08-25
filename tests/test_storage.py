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


def test_agent_filter_accepts_email_or_name(tmp_path):
    """The UI's agent picker sends e-mails; conversations are keyed by display name."""
    db = tmp_path / "t.db"
    cstore = ConversationsStore(db)
    ada = _convo("1", "Ada")
    bob = _convo("2", "Bob")
    bob.assignee.email = "bob@co.com"
    cstore.save(ada)
    cstore.save(bob)

    for selector in (["ada@co.com"], ["Ada"], ["ADA@CO.COM"]):
        rows, total = cstore.query(agents=selector)
        assert total == 1 and rows[0]["id"] == "1", selector
        assert cstore.count(agents=selector) == 1, selector

    rows, total = cstore.query(agents=["ada@co.com", "Bob"])
    assert total == 2
    assert cstore.evaluation_counts(agents=["bob@co.com"])["total"] == 1


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

    counts = cstore.evaluation_counts({"default": "v1"})
    assert counts["total"] == 1       # only "plain" is gradeable
    assert counts["ignored"] == 2     # spammy + jira
    assert counts["graded"] == 1      # the ignored "spammy" grade is excluded
    assert counts["graded_current"] == 1
    # Editing the ruleset the grade was scored with makes it stale.
    assert cstore.evaluation_counts({"default": "v2"})["graded_current"] == 0
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


def test_grades_on_ignored_conversations_drop_out_of_aggregates(tmp_path):
    """A Follow-Up tag applied *after* grading still pulls the grade out of every aggregate.

    Support routinely tags a chat "Follow-Up" in Intercom days after we graded it; the next
    fetch refreshes `conversations.tags` but the stored grade stays. Filtering only at grading
    time would leave that grade counting towards the agent's score and the client's export
    forever, which is exactly what "don't grade follow-ups" is meant to prevent.
    """
    db = tmp_path / "t.db"
    cstore = ConversationsStore(db)
    cstore.save(_tagged("plain", ["login"]))
    cstore.save(_tagged("later", ["login"]))          # not a follow-up yet
    gstore = GradesStore(db)
    for cid, score in [("plain", 90), ("later", 50)]:
        gstore.save(ConversationGrade(
            conversation_id=cid, agent_name="Ada", overall_score=score, summary="ok",
            graded_at="2026-06-25T00:00:00+00:00",
        ))
    assert gstore.agent_scores()[0]["count"] == 2      # both count while untagged

    cstore.save(_tagged("later", ["login", "Follow-Up"]))   # Intercom tags it; re-fetched

    scores = {r["agent"]: r for r in gstore.agent_scores()}
    assert scores["Ada"]["count"] == 1                 # the follow-up no longer counts
    assert scores["Ada"]["avg_score"] == 90.0          # and no longer drags the average
    assert [g["conversation_id"] for g in gstore.all()] == ["plain"]          # XLSX export
    assert [g["conversation_id"] for g in gstore.for_agent("Ada")] == ["plain"]
    assert gstore.accuracy_stats()["summary"]["total_graded"] == 1
    cstore.close()
    gstore.close()


def _convo_csat(cid, agent, rating):
    c = _convo(cid, agent)
    c.csat_rating = rating
    return c


def test_query_max_csat_filter(tmp_path):
    db = tmp_path / "t.db"
    cstore = ConversationsStore(db)
    cstore.save(_convo_csat("low", "Ada", 1))
    cstore.save(_convo_csat("mid", "Ada", 3))
    cstore.save(_convo_csat("high", "Bob", 5))
    cstore.save(_convo_csat("none", "Bob", None))

    rows, total = cstore.query(max_csat=1)
    assert total == 1
    assert [r["id"] for r in rows] == ["low"]

    # Unrated conversations never match a csat ceiling.
    rows, total = cstore.query(max_csat=5)
    assert total == 3
    assert "none" not in {r["id"] for r in rows}
    cstore.close()


def test_agent_csat_summary(tmp_path):
    db = tmp_path / "t.db"
    cstore = ConversationsStore(db)
    cstore.save(_convo_csat("a1", "Ada", 1))   # low
    cstore.save(_convo_csat("a2", "Ada", 5))
    cstore.save(_convo_csat("a3", "Ada", None))  # ignored (no rating)
    cstore.save(_convo_csat("b1", "Bob", 4))

    by_agent = {r["agent"]: r for r in cstore.agent_csat()}
    assert by_agent["Ada"]["csat_count"] == 2          # a3 excluded
    assert by_agent["Ada"]["avg_csat"] == 3.0          # (1 + 5) / 2
    assert by_agent["Ada"]["low_csat_count"] == 1      # only a1 (<= csat_low_max=1)
    assert by_agent["Bob"]["csat_count"] == 1
    assert by_agent["Bob"]["low_csat_count"] == 0
    cstore.close()


def test_grade_dispute_lifecycle(tmp_path):
    from intercom_summary.storage.grade_disputes_store import GradeDisputesStore
    db = tmp_path / "t.db"
    cstore = ConversationsStore(db)
    cstore.save(_convo("g1", "Ada"))
    gstore = GradesStore(db)
    gstore.save(ConversationGrade(
        conversation_id="g1", agent_name="Ada", overall_score=40, summary="harsh",
        graded_at="2026-05-03T00:00:00+00:00",
    ))

    dstore = GradeDisputesStore(db)
    assert dstore.create("g1", "Ada", "score too low", "portal", "Ada") is True
    # A second open dispute on the same conversation is rejected.
    assert dstore.create("g1", "Ada", "again", "portal", "Ada") is False
    assert dstore.get("g1")["status"] == "open"

    # The open dispute surfaces on the conversation list row for the badge.
    rows, _ = cstore.query(agents=["Ada"])
    assert rows[0]["grade_dispute_status"] == "open"

    # Manager rejects → a rejected dispute can be re-raised.
    assert dstore.resolve("g1", "rejected", "stands", "boss") is True
    assert dstore.get("g1")["status"] == "rejected"
    assert dstore.create("g1", "Ada", "still unfair", "portal", "Ada") is True

    # Manager accepts; the queue listing carries conversation + score context.
    assert dstore.resolve("g1", "accepted", "agreed", "boss") is True
    queue = dstore.list(status="accepted")
    assert queue[0]["conversation_id"] == "g1"
    assert queue[0]["subject"] == "Login issue" and queue[0]["score"] == 40
    cstore.close()
    gstore.close()
    dstore.close()


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


# ── Blacklist: deleted conversations must not be silently resurrected, but a bulk
# cache clear must not permanently block re-import either (that made every Intercom
# fetch import nothing while still reporting success).
def test_blacklisted_conversation_is_not_reimported(tmp_path):
    from intercom_summary.storage.trash_store import TrashStore
    db = tmp_path / "t.db"
    cstore = ConversationsStore(db)
    cstore.save(_convo("7"))
    ts = TrashStore(db)
    assert ts.move_to_trash(["7"], "boss") == 1          # explicit delete → blacklisted
    ts.close()

    assert cstore.save(_convo("7")) is False              # re-fetch is refused
    assert cstore.get("7") is None
    cstore.close()


def test_bulk_cleared_conversation_can_be_reimported(tmp_path):
    from intercom_summary.storage.trash_store import TrashStore
    db = tmp_path / "t.db"
    cstore = ConversationsStore(db)
    cstore.save(_convo("8"))
    ts = TrashStore(db)
    assert ts.move_to_trash(["8"], "boss", blacklist=False) == 1
    ts.close()

    assert cstore.save(_convo("8")) is True               # re-fetch brings it back
    assert cstore.get("8") is not None
    cstore.close()


def test_save_many_counts_only_stored(tmp_path):
    from intercom_summary.storage.trash_store import TrashStore
    db = tmp_path / "t.db"
    cstore = ConversationsStore(db)
    cstore.save(_convo("1"))
    ts = TrashStore(db)
    ts.move_to_trash(["1"], "boss")
    ts.close()

    # 3 fetched, 1 blacklisted → save_many must report 2, not 3.
    assert cstore.save_many([_convo("1"), _convo("2"), _convo("3")]) == 2
    cstore.close()


def test_trash_expire_respects_cutoff(tmp_path):
    from datetime import timedelta
    from intercom_summary.storage.trash_store import TrashStore
    db = tmp_path / "t.db"
    cstore = ConversationsStore(db)
    cstore.save(_convo("old"))
    cstore.save(_convo("new"))
    cstore.close()

    ts = TrashStore(db)
    ts.move_to_trash(["old", "new"], "boss")
    stale = (datetime.now(timezone.utc) - timedelta(days=100)).isoformat()
    ts._conn.execute(
        "UPDATE deleted_conversations SET deleted_at=? WHERE conversation_id='old'", (stale,)
    )
    ts._conn.commit()

    assert ts.count_expiring_before(90) == 1
    assert ts.expire(0) == 0                    # 0 disables expiry
    assert ts.expire(90) == 1
    assert ts.count() == 1
    assert ts.list_all()[0]["conversation_id"] == "new"
    ts.close()


# ── rule_results normalization ────────────────────────────────────────────────────────
# The Qwen grader emits only the criteria it chooses to — usually 24 of the standard
# ruleset's 27, sometimes far fewer. GradesStore.get() fills the gaps so the Grade panel
# always shows the full checklist and every criterion stays re-scorable.
def _ollama_grade(cid="42", ruleset_id="default", rule_results=None):
    return ConversationGrade(
        conversation_id=cid, agent_name="Ada", overall_score=85, summary="ok",
        rule_results=rule_results if rule_results is not None else [
            RuleResult("res-no-fake-close", "No Fake Closure", "fail", "closed early"),
            RuleResult("open-greet", "Greeting", "pass", "hi"),
        ],
        ruleset_id=ruleset_id, rules_version="v1", model="ollama/test",
        graded_at="2026-05-01T00:00:00+00:00",
    )


def test_partial_grade_is_backfilled_to_the_full_ruleset(tmp_path):
    from intercom_summary.qa.rulesets import get_ruleset

    gstore = GradesStore(tmp_path / "t.db")
    gstore.save(_ollama_grade())
    got = gstore.get("42")
    gstore.close()

    criteria = get_ruleset("default").criteria
    # Every criterion is present, in catalogue order — crit-* first.
    assert [r["rule_id"] for r in got["rule_results"]] == [c["id"] for c in criteria]

    by_id = {r["rule_id"]: r for r in got["rule_results"]}
    # What the model actually said survives untouched...
    assert by_id["res-no-fake-close"]["verdict"] == "fail"
    assert by_id["res-no-fake-close"]["evidence"] == "closed early"
    assert by_id["open-greet"]["verdict"] == "pass"
    # ...and the criteria it never reported come back as score-neutral n/a.
    assert by_id["crit-data-care"]["verdict"] == "n/a"
    assert by_id["crit-data-care"]["title"] == "Data Security"
    assert by_id["crit-data-care"]["evidence"] == ""
    # Backfilled rows carry deduction/critical too, or the UI drops to the manual slider.
    assert all(isinstance(r["deduction"], int) for r in got["rule_results"])
    assert by_id["crit-data-care"]["critical"] is True


def test_backfill_does_not_change_the_score(tmp_path):
    """n/a deducts nothing, so a backfilled grade must score exactly as it did before."""
    from intercom_summary.qa.schema import score_from_verdicts

    gstore = GradesStore(tmp_path / "t.db")
    gstore.save(_ollama_grade())
    got = gstore.get("42")
    gstore.close()

    verdicts = {r["rule_id"]: r["verdict"] for r in got["rule_results"]}
    score, _band, _result = score_from_verdicts(verdicts, ruleset_id="default")
    assert score == got["overall_score"] == 85     # 100 − 15 for res-no-fake-close


def test_backfill_uses_the_grade_s_own_ruleset(tmp_path):
    from intercom_summary.qa.rulesets import get_ruleset

    gstore = GradesStore(tmp_path / "t.db")
    gstore.save(_ollama_grade(ruleset_id="vip", rule_results=[
        RuleResult("vip-recognition", "VIP Recognition", "fail", "no tier check"),
    ]))
    got = gstore.get("42")
    gstore.close()

    assert len(got["rule_results"]) == len(get_ruleset("vip").criteria)
    assert len(got["rule_results"]) != len(get_ruleset("default").criteria)
    # A VIP grade must not be backfilled with standard-only criteria.
    ids = {r["rule_id"] for r in got["rule_results"]}
    assert "vip-comp-protocol" in ids and "open-greet" not in ids


def test_legacy_claude_grade_is_left_alone(tmp_path):
    """Old Claude-backend grades use a different criteria vocabulary; grafting the casino
    catalogue onto them would invent checks that were never part of that grade."""
    gstore = GradesStore(tmp_path / "t.db")
    gstore.save(_ollama_grade(rule_results=[RuleResult("tone-greeting", "Greeting", "pass")]))
    got = gstore.get("42")
    gstore.close()

    assert [r["rule_id"] for r in got["rule_results"]] == ["tone-greeting"]


def test_unknown_criterion_survives_alongside_the_backfill(tmp_path):
    """A hallucinated id is kept at the end rather than silently dropped."""
    from intercom_summary.qa.rulesets import get_ruleset

    gstore = GradesStore(tmp_path / "t.db")
    gstore.save(_ollama_grade(rule_results=[
        RuleResult("open-greet", "Greeting", "pass"),
        RuleResult("made-up-rule", "Invented", "fail"),
    ]))
    got = gstore.get("42")
    gstore.close()

    ids = [r["rule_id"] for r in got["rule_results"]]
    assert ids[-1] == "made-up-rule"
    assert len(ids) == len(get_ruleset("default").criteria) + 1


# ── Brand filtering (multi-brand workspace) ──────────────────────────────────────
def _branded(cid: str, brand: str, agent: str = "Ada"):
    c = _convo(cid=cid, agent=agent)
    c.brand = brand
    return c


def test_brand_roundtrips_and_filters(tmp_path):
    store = ConversationsStore(tmp_path / "b.db")
    store.save(_branded("1", "Betncare"))
    store.save(_branded("2", "Tomb Riches"))
    store.save(_branded("3", ""))            # unbranded

    assert store.get("2").brand == "Tomb Riches"

    rows, total = store.query(brand="Tomb Riches")
    assert total == 1 and rows[0]["id"] == "2"
    assert rows[0]["brand"] == "Tomb Riches"

    assert store.count(brand="Betncare") == 1
    assert store.count(brand="Tomb Riches") == 1


def test_unbranded_rows_are_selectable_by_their_own_token(tmp_path):
    from intercom_summary.intercom.brands import UNBRANDED

    store = ConversationsStore(tmp_path / "b.db")
    store.save(_branded("1", "Betncare"))
    store.save(_branded("2", ""))

    rows, total = store.query(brand=UNBRANDED)
    assert total == 1 and rows[0]["id"] == "2"


def test_no_brand_filter_returns_everything(tmp_path):
    # The additive guarantee: an unfiltered query must behave exactly as it did before
    # brands existed, whatever brands the rows happen to carry.
    store = ConversationsStore(tmp_path / "b.db")
    store.save(_branded("1", "Betncare"))
    store.save(_branded("2", "Tomb Riches"))
    store.save(_branded("3", ""))

    assert store.query()[1] == 3
    assert store.query(brand=None)[1] == 3
    assert store.count() == 3


def test_brands_lists_counts_busiest_first(tmp_path):
    store = ConversationsStore(tmp_path / "b.db")
    for i in range(3):
        store.save(_branded(f"kb{i}", "Betncare"))
    store.save(_branded("tr", "Tomb Riches"))

    assert store.brands() == [
        {"brand": "Betncare", "count": 3},
        {"brand": "Tomb Riches", "count": 1},
    ]


def test_save_keeps_known_brand_when_refetch_has_none(tmp_path):
    # A re-fetch that comes back without the Brand attribute must not wipe a backfilled value.
    store = ConversationsStore(tmp_path / "b.db")
    store.save(_branded("1", "Tomb Riches"))
    store.save(_branded("1", ""))            # same conversation, brand missing this time

    assert store.get("1").brand == "Tomb Riches"
    assert store.query(brand="Tomb Riches")[1] == 1


def test_agents_and_csat_are_brand_scoped(tmp_path):
    store = ConversationsStore(tmp_path / "b.db")
    store.save(_branded("1", "Betncare", agent="Ada"))
    store.save(_branded("2", "Tomb Riches", agent="Bob"))

    assert store.agents() == ["Ada", "Bob"]
    assert store.agents(brand="Tomb Riches") == ["Bob"]
    assert [r["agent"] for r in store.agent_csat(brand="Betncare")] == ["Ada"]


def test_grades_are_brand_filtered_through_their_conversation(tmp_path):
    db = tmp_path / "b.db"
    cstore = ConversationsStore(db)
    cstore.save(_branded("1", "Betncare", agent="Ada"))
    cstore.save(_branded("2", "Tomb Riches", agent="Bob"))

    gstore = GradesStore(db)
    for cid, agent, score in (("1", "Ada", 90), ("2", "Bob", 40)):
        gstore.save(ConversationGrade(
            conversation_id=cid, agent_name=agent, overall_score=score,
            summary="s", rule_results=[RuleResult("r", "t", "pass", "", "")],
        ))

    assert len(gstore.all()) == 2                              # unfiltered unchanged
    assert [g["agent_name"] for g in gstore.all(brand="Tomb Riches")] == ["Bob"]
    assert gstore.accuracy_stats()["summary"]["total_graded"] == 2
    assert gstore.accuracy_stats(brand="Betncare")["summary"]["total_graded"] == 1
    assert [r["agent"] for r in gstore.agent_scores(brand="Betncare")] == ["Ada"]


def test_migration_adds_brand_to_a_pre_brand_database(tmp_path):
    """Regression: `_SCHEMA` runs before `_migrate()`, so anything in the schema script that
    references `conversations.brand` blows up on a database created before the column existed
    — every test here builds a *fresh* DB, so only an old-shaped one catches it."""
    import sqlite3

    from intercom_summary.storage.db import connect

    db = tmp_path / "old.db"
    old = sqlite3.connect(db)
    old.execute("""
        CREATE TABLE conversations (
            id TEXT PRIMARY KEY, agent_name TEXT, agent_email TEXT, customer_name TEXT,
            customer_email TEXT, state TEXT, subject TEXT, created_at TEXT, updated_at TEXT,
            message_count INTEGER, csat_rating INTEGER, tags TEXT, payload_json TEXT,
            fetched_at TEXT
        )""")
    old.execute("INSERT INTO conversations (id, agent_name) VALUES ('1', 'Ada')")
    old.commit()
    old.close()

    conn = connect(db)                       # must not raise
    cols = {r[1] for r in conn.execute("PRAGMA table_info(conversations)")}
    assert {"brand", "custom_tags"} <= cols
    indexes = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='conversations'")}
    assert "idx_convo_brand" in indexes
    # Pre-existing rows survive, unbranded until the backfill runs.
    assert conn.execute("SELECT brand FROM conversations WHERE id='1'").fetchone()[0] == ""
    conn.close()

    connect(db).close()                      # idempotent on a second open


def test_grade_without_a_conversation_counts_only_under_all_brands(tmp_path):
    """A conversation deleted after grading leaves its grade behind. There is no brand to
    attribute that grade to, so it counts unfiltered but under no individual brand — real
    databases carry a few of these, and the behaviour should be deliberate, not a surprise."""
    db = tmp_path / "b.db"
    cstore = ConversationsStore(db)
    cstore.save(_branded("1", "Betncare", agent="Ada"))
    cstore.close()

    gstore = GradesStore(db)
    for cid in ("1", "gone"):
        gstore.save(ConversationGrade(
            conversation_id=cid, agent_name="Ada", overall_score=70,
            summary="s", rule_results=[RuleResult("r", "t", "pass", "", "")],
        ))

    assert len(gstore.all()) == 2                      # unfiltered: both
    assert len(gstore.all(brand="Betncare")) == 1      # scoped: only the one we can place
