"""QA feedback of 2026-10-28: the EMPTY tag read as "no tag", BREACHED reply times kept their
points, and the review queue of chats with an internal note and other problem chats."""
from datetime import datetime, timedelta, timezone
import json

import pytest

from intercom_summary.intercom.models import Admin, Conversation, Message
from intercom_summary.qa.fail_verifier import failed_criteria
from intercom_summary.qa.gated_rules import check_verdicts
from intercom_summary.qa.prompt import transcript_block
from intercom_summary.qa.review_queue import NOTE_REASON, reasons
from intercom_summary.qa.rulesets import get_ruleset
from intercom_summary.qa.schema import ConversationGrade
from intercom_summary.qa.verdict_guard import apply_guards

T0 = datetime(2026, 10, 20, 9, 0, tzinfo=timezone.utc)


def _at(sec: int) -> datetime:
    return T0 + timedelta(seconds=sec)


def _convo(*msgs: Message, tags=()) -> Conversation:
    return Conversation(id="9", created_at=T0, updated_at=None, state="closed",
                        assignee=Admin(id="1", name="Ada", email="a@co.com"),
                        messages=list(msgs), tags=list(tags))


def _chat(first_reply_after: int = 30, later_gap: int = 60, note: bool = False,
          tags=("Bonus",)) -> Conversation:
    msgs = [
        Message(0, "user", "Kim", _at(0), "Where is my bonus?"),
        Message(1, "admin", "Ada", _at(0), "", part_type="assignment"),
        Message(2, "admin", "Ada", _at(first_reply_after), "Hello Kim, let me check."),
        Message(3, "user", "Kim", _at(first_reply_after + 20), "Any news?"),
        Message(4, "admin", "Ada", _at(first_reply_after + 20 + later_gap), "It is credited."),
    ]
    if note:
        msgs.append(Message(5, "admin", "Ada", _at(first_reply_after + 30),
                            "passed to finance", part_type="note"))
    return _convo(*msgs, tags=tags)


# ── the follow-up reply time ──────────────────────────────────────────────────────────────
def test_the_longest_later_reply_is_measured_from_the_players_line():
    c = _chat(later_gap=420)
    assert c.longest_followup_seconds == 420
    sla = c.sla_summary(120, 300)
    assert sla["followup_breached"] and not sla["first_response_breached"]


def test_a_help_center_article_before_the_player_wrote_is_not_the_first_reply():
    """The article is posted under the agent's name in the same second as the player's line."""
    c = _convo(
        Message(0, "admin", "Ada", _at(0), '[Article: "How do I activate my Free Spins?"]'),
        Message(1, "user", "Kim", _at(0), "I got 50 free spins by text"),
        Message(2, "admin", "Ada", _at(10800), "", part_type="assignment"),
        Message(3, "admin", "Ada", _at(10810), "Hello! Let me check."),
    )
    assert c.agent_first_reply_seconds == 10
    assert c.longest_followup_seconds is None


def test_the_timing_header_names_the_sla_criterion_and_the_later_reply():
    text = transcript_block(_chat(later_gap=420))
    assert "Longest later agent reply after a player message: 7m 00s" in text
    assert "BREACHED" in text and "resp-sla" in text


# ── EMPTY is a tag ────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("tag", ["EMPTY", "no request"])
def test_the_tags_line_says_a_placeholder_tag_is_a_tag(tag):
    text = transcript_block(_chat(tags=(tag,)))
    assert f"Chat tags: {tag} (" in text and "a tag IS set" in text


def test_a_tag_fail_that_only_says_none_is_set_is_not_kept_on_a_tagged_chat():
    c = _chat(tags=("EMPTY",))
    data = {"criteria": [{"id": "tag-chat", "v": "fail", "ev": "Chat tags: EMPTY"}]}
    flags = apply_guards(c, data)
    assert data["criteria"][0]["v"] == "cannot_determine"
    assert any(f.startswith("guard:tag-chat fail re-read") for f in flags)


def test_a_tag_fail_naming_a_real_mismatch_stands():
    c = _chat(tags=("EMPTY",))
    ev = "Chat tags: EMPTY — but the player asked where their bonus is"
    data = {"criteria": [{"id": "tag-chat", "v": "fail", "ev": ev}]}
    apply_guards(c, data)
    assert data["criteria"][0]["v"] == "fail"


# ── a BREACHED reply with nothing cited is a fail ─────────────────────────────────────────
def _data(sla_v="pass", sla_ev=""):
    return {
        "case_type": "Bonus", "risk_flag": "None", "expected_handling": "Answer",
        "data_sufficiency": "Sufficient",
        "requests": [{"text": "Where is my bonus?", "status": "answered", "material": True}],
        "problems": [],
        "criteria": [{"id": c, "v": "pass", "ev": ""} for c in
                     ("resp-no-ghost", "res-no-fake-close", "comm-tone", "tag-chat")]
        + [{"id": "resp-sla", "v": sla_v, "ev": sla_ev}],
        "outcome_status": "Resolved", "summary": "Fine.", "manual_review_needed": False,
    }


def _sla(data):
    return next(c for c in data["criteria"] if c["id"] == "resp-sla")


@pytest.mark.parametrize("v", ["pass", "n/a"])
def test_a_breached_first_reply_with_nothing_cited_fails_resp_sla(v):
    data = _data(sla_v=v)
    flags = check_verdicts(_chat(first_reply_after=239), data, get_ruleset("kb-v41"))
    assert _sla(data)["v"] == "fail" and "3m 59s" in _sla(data)["ev"]
    assert any(f.startswith("rule:resp-sla") for f in flags)
    # Settled by the clock: the fail verifier does not re-read it.
    assert all(c["id"] != "resp-sla" for c in failed_criteria(data))


def test_a_pass_that_cites_the_delay_message_is_left_alone():
    data = _data(sla_ev="Agent: 'Thank you for waiting, the queue was long today'")
    check_verdicts(_chat(first_reply_after=239), data, get_ruleset("kb-v41"))
    assert _sla(data)["v"] == "pass"


def test_a_reply_inside_the_target_is_left_alone():
    data = _data()
    check_verdicts(_chat(), data, get_ruleset("kb-v41"))
    assert _sla(data)["v"] == "pass"


# ── the review queue ──────────────────────────────────────────────────────────────────────
def test_an_internal_note_sends_a_new_grade_to_review():
    data = _data()
    check_verdicts(_chat(note=True), data, get_ruleset("kb-v41"))
    g = ConversationGrade.from_model_output("9", "Ada", data, ruleset_id="kb-v41")
    assert g.manual_review_needed and NOTE_REASON in g.manual_review_reason


def test_reasons_are_listed_worst_first():
    assert reasons(has_note=True, manual_review=True, manual_reason="Churn signal ignored",
                   overall_result="FAIL", critical=False, csat=1) == [
        "FAIL", NOTE_REASON, "AI: Churn signal ignored", "Low CSAT (1/5)"]
    assert reasons(has_note=False, manual_review=False) == []


def test_the_review_filter_finds_old_grades_with_a_note(tmp_path):
    from intercom_summary.storage.conversations_store import ConversationsStore

    db = tmp_path / "q.db"
    store = ConversationsStore(db)
    try:
        chats = {"n": _chat(note=True), "ok": _chat(), "fail": _chat(), "seen": _chat(note=True)}
        for cid, c in chats.items():
            c.id = cid
            store.save(c)
        for cid, result, human in (("n", "PASS", None), ("ok", "PASS", None),
                                   ("fail", "FAIL", None), ("seen", "PASS", 90)):
            store._conn.execute(
                "INSERT INTO grades (conversation_id, overall_score, payload_json, human_score, "
                "ruleset_id) VALUES (?, 95, ?, ?, 'kb-v41')",
                (cid, json.dumps({"overall_result": result}), human))
        store._conn.commit()
        rows, total = store.query(review=True)
        got = {r["id"]: r["review_reasons"] for r in rows}
        assert total == 2 and got == {"n": [NOTE_REASON], "fail": ["FAIL"]}
    finally:
        store.close()
