"""VIP cases: grouping threads into cases, the case transcript, the guard, storage, exceptions."""
from intercom_summary import vip_cases
from intercom_summary.qa.schema import ConversationGrade
from intercom_summary.qa.verdict_guard import apply_vip_guards
from intercom_summary.qa.vip_transcript import case_block, reply_gaps
from intercom_summary.storage.vip_cases_store import VipCasesStore
from intercom_summary.vip_cases import CaseConversation, case_conversation, group_cases

from tests.vip_case_fixtures import STAFF, msg, pair, save_threads, thread, vip_env  # noqa: F401


# ── grouping ────────────────────────────────────────────────────────────────────────────────
def test_same_player_and_topic_within_7_days_is_one_case():
    rows = [pair("1", 0), pair("2", 24 * 3), pair("3", 24 * 6)]
    cases = group_cases(rows, STAFF)
    assert [c.thread_ids for c in cases] == [["1", "2", "3"]]
    assert cases[0].case_id == "vc-1" and cases[0].primary_manager == "Vera VIP"


def test_the_7_days_count_from_the_previous_threads_last_message():
    # Thread 2 starts 8 days after thread 1 started but 6 days after its last message.
    t1 = thread("1", 0, [msg(0, "user", "Pat", 0, "Where is my withdrawal please?"),
                         msg(1, "admin", "Vera VIP", 24 * 2, "Sent today.")])
    cases = group_cases([t1, pair("2", 24 * 8)], STAFF)
    assert len(cases) == 1
    cases = group_cases([t1, pair("2", 24 * 9 + 1)], STAFF)
    assert [c.thread_ids for c in cases] == [["1"], ["2"]]


def test_other_player_or_topic_starts_another_case():
    rows = [pair("1", 0), pair("2", 1, contact="c2"), pair("3", 2, topic="Deposit")]
    assert len(group_cases(rows, STAFF)) == 3


def test_topic_other_is_never_chained():
    rows = [pair("1", 0, topic="Other"), pair("2", 2, topic="Other")]
    assert len(group_cases(rows, STAFF)) == 2


def test_case_version_changes_when_a_thread_grows():
    a = group_cases([pair("1", 0)], STAFF)[0]
    grown = pair("1", 0)
    grown["payload"]["messages"].append(msg(2, "user", "Pat", 3, "Any news?"))
    b = group_cases([grown], STAFF)[0]
    assert a.case_id == b.case_id and a.case_version != b.case_version


def test_primary_manager_is_who_replied_most_not_the_assignee():
    t = thread("1", 0, [msg(0, "user", "Pat", 0, "Hello I need help with my account"),
                        msg(1, "admin", "Max VIP", 1, "On it."),
                        msg(2, "admin", "Max VIP", 2, "Done."),
                        msg(3, "admin", "Pay Dep", 3, "Docs please.")], agent="Vera VIP")
    c = group_cases([t], STAFF)[0]
    assert c.primary_manager == "Max VIP" and c.managers == ["Max VIP"]


# ── merged conversation + transcript ────────────────────────────────────────────────────────
def _case():
    t1 = thread("1", 0, [
        msg(0, "user", "Pat Player", 0, "My withdrawal is stuck, please check it today"),
        msg(1, "bot", "Billy Jr.", 0.01, "Help Vera understand how they're doing:"),
        msg(2, "admin", "Pay Dep", 2, "Please upload a bank statement."),
        msg(3, "admin", "Vera VIP", 3, "checked BO, pending docs", part_type="note"),
        msg(4, "admin", "Vera VIP", 3.5, "Hi Pat, Payments need one document, then it is paid."),
        msg(5, "admin", "Vera VIP", 3.6, "", part_type="close"),
    ])
    t2 = thread("2", 30, [
        msg(0, "user", "Pat Player", 30, "Uploaded the statement, when will it arrive?"),
    ], subject="statement")
    return group_cases([t1, t2], STAFF)[0]


def test_case_conversation_merges_threads_in_time_order():
    convo = case_conversation(_case(), STAFF)
    assert isinstance(convo, CaseConversation) and convo.id == "vc-1"
    assert convo.message_thread == ["1"] * 6 + ["2"]
    assert convo.assignee_name == "Vera VIP" and convo.channel == "email"
    restored = CaseConversation.from_dict(convo.to_dict())
    assert restored.message_thread == convo.message_thread and restored.staff == convo.staff


def test_transcript_labels_roles_dates_notes_and_threads():
    text = case_block(case_conversation(_case(), STAFF))
    assert "OTHER STAFF Pay Dep: Please upload" in text
    assert "VIP MANAGER Vera VIP (internal note): checked BO" in text
    assert "[2026-10-01 09:00 UTC] PLAYER Pat Player: My withdrawal" in text
    assert "Billy Jr." not in text                      # automation removed
    assert '--- thread 2 · 2 · "statement" ---' in text
    assert "Shift schedule: UNKNOWN" in text
    assert "VIP managers who wrote: Vera VIP" in text and "Other staff who wrote: Pay Dep" in text


def test_reply_gaps_count_only_vip_manager_public_replies():
    gaps = reply_gaps(case_conversation(_case(), STAFF))
    # Pay Dep's line and Vera's note do not answer the player; Vera's reply does, 3h30 later.
    assert gaps[0]["seconds"] == 3.5 * 3600 and gaps[0]["manager"] == "Vera VIP"
    # The second thread's question is still open.
    assert gaps[1]["reply_at"] is None
    text = case_block(case_conversation(_case(), STAFF))
    assert "no VIP MANAGER reply after it in this case" in text


# ── guard ───────────────────────────────────────────────────────────────────────────────────
def test_guard_drops_fails_evidenced_by_other_staff_bots_or_the_player():
    convo = case_conversation(_case(), STAFF)
    data = {"criteria": [
        {"id": "style-tone", "v": "fail", "ev": "Please upload a bank statement."},
        {"id": "q-clarity", "v": "partial", "ev": "[2026-10-01 09:00 UTC] PLAYER Pat Player: "
                                                  "My withdrawal is stuck, please check it"},
        {"id": "q-complaint", "v": "fail", "ev": "My withdrawal is stuck, please check it today"},
        {"id": "crit-false-status", "v": "fail", "ev": "Please upload a bank statement."},
        {"id": "q-expectations", "v": "partial",
         "ev": "[2026-10-01 12:30 UTC] VIP MANAGER Vera VIP: Hi Pat, Payments need one document"},
    ]}
    flags = apply_vip_guards(convo, data)
    v = {c["id"]: c["v"] for c in data["criteria"]}
    assert v == {"style-tone": "n/a", "q-clarity": "n/a", "q-complaint": "fail",
                 "crit-false-status": "fail", "q-expectations": "partial"}
    assert len(flags) == 2 and "other staff line" in flags[0]


# ── storage + rebuild + exceptions ──────────────────────────────────────────────────────────
def test_rebuild_store_grade_and_staleness(vip_env):
    db = vip_env / "vip.db"
    save_threads(db, [pair("1", 0), pair("2", 24), pair("3", 24 * 20)])
    stats = vip_cases.rebuild(db)
    assert stats["threads"] == 3 and stats["cases"] == 2 and stats["multi_thread_cases"] == 1

    store = VipCasesStore(db)
    assert store.case_for_thread("2") == "vc-1"
    case = store.get_case("vc-1")
    assert case["thread_ids"] == ["1", "2"] and case["primary_manager"] == "Vera VIP"
    convo = vip_cases.load_case_conversations(["vc-1"], db)["vc-1"]
    assert convo.case_version == case["case_version"]

    grade = ConversationGrade(conversation_id="vc-1", agent_name="Vera VIP", overall_score=75,
                              summary="s", rules_version="r1", ruleset_id="vip-v16",
                              overall_result="FAIL", model="fake", graded_at="2026-10-20")
    store.save_grade(grade, case_version=case["case_version"], primary_manager="Vera VIP")
    assert store.is_current("vc-1", "r1", case["case_version"])
    # A rules change never re-grades a graded case (the client's rule); growth does.
    assert store.is_current("vc-1", "r2", case["case_version"])
    assert not store.is_current("vc-1", "r1", "another-version")
    assert store.save_override("vc-1", 90, "Payments owned the delay", "qa")
    g = store.get_grade("vc-1")
    assert g["overall_score"] == 75 and g["human_score"] == 90       # AI original kept
    assert [c["grade"] is not None for c in store.cases()] == [False, True]
    assert store.cases(result="PASS")[0]["case_id"] == "vc-1"        # the human verdict counts
    store.close()

    # A new message in thread 2 makes the grade stale; the grade itself survives the rebuild.
    grown = pair("2", 24)
    grown["payload"]["messages"].append(msg(2, "user", "Pat", 26, "Still nothing arrived"))
    save_threads(db, [grown])
    vip_cases.rebuild(db)
    store = VipCasesStore(db)
    row = next(c for c in store.cases() if c["case_id"] == "vc-1")
    assert row["grade"]["stale"] and store.summary()["stale"] == 1
    store.close()


def test_override_requires_a_reason(vip_env):
    import pytest

    store = VipCasesStore(vip_env / "vip.db")
    with pytest.raises(ValueError):
        store.save_override("vc-1", 90, "  ", "qa")
    store.close()


def test_exceptions_unanswered_and_no_owner(vip_env):
    db = vip_env / "vip.db"
    answered_elsewhere = thread("1", 0, [msg(0, "user", "Pat", 0, "Can I get my VIP spins now?")])
    reply_in_sibling = pair("2", 5)
    ignored = thread("3", 10, [msg(0, "user", "Sam", 10, "My deposit vanished, please help me")],
                     contact="c2", agent="")
    thanks = thread("4", 11, [msg(0, "admin", "Vera VIP", 11, "Here are your spins"),
                              msg(1, "user", "Kim", 12, "Thanks! Sent from my iPhone")],
                    contact="c3")
    save_threads(db, [answered_elsewhere, reply_in_sibling, ignored, thanks])
    ex = vip_cases.exceptions(db)
    assert [e["thread_id"] for e in ex["unanswered"]] == ["3"]
    assert [e["thread_id"] for e in ex["no_owner"]] == ["3"]
    assert ex["as_of"]


def test_a_grown_case_is_regraded_unless_a_human_signed_it_off(vip_env):
    db = vip_env / "vip.db"
    save_threads(db, [pair("1", 0)])
    vip_cases.rebuild(db)
    store = VipCasesStore(db)
    v1 = store.get_case("vc-1")["case_version"]

    def grade(score, version):
        return ConversationGrade(conversation_id="vc-1", agent_name="Vera VIP", overall_score=score,
                                 summary="s", rules_version="r1", ruleset_id="vip-v16",
                                 overall_result="PASS" if score >= 85 else "FAIL", model="fake",
                                 graded_at="2026-10-20")

    store.save_grade(grade(75, v1), case_version=v1)
    assert store.confirm("vc-1", "kate")                       # QA agrees with the grade
    assert store.get_grade("vc-1")["human_confirmed_by"] == "kate"
    assert store.is_current("vc-1", "r9", "grown-version")     # agreed → left alone even if grown

    assert store.confirm("vc-1", None)                         # withdrawn
    assert not store.is_current("vc-1", "r1", "grown-version")  # grown, nobody signed off → graded
    store.save_grade(grade(92, "grown-version"), case_version="grown-version")
    g = store.get_grade("vc-1")
    assert g["overall_score"] == 92 and g["human_confirmed_at"] is None
    hist = store.history("vc-1")
    assert [h["overall_score"] for h in hist] == [75] and hist[0]["case_version"] == v1

    assert store.save_override("vc-1", 60, "missed the payments handoff", "qa")
    assert not store.confirm("vc-1", "kate")                   # a re-scored grade can't be agreed
    assert store.is_current("vc-1", "r1", "grown-again")       # a human verdict is final
    store.close()
