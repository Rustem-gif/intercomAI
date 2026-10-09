"""The deterministic v4.1 chain: detected problem → fail → severity → score → result.

Client calibration (2026-10-08) found the model's own pass/fail calls inconsistent — Excellent
on one run, Major on the next — and grades that described a problem yet scored 100. Each test
here pins one rule that takes such a call away from the model.
"""
from datetime import datetime, timezone

import pytest

from intercom_summary.intercom.models import Admin, Conversation, Message
from intercom_summary.qa.gated_rules import check_verdicts
from intercom_summary.qa.gated_scoring import score_gated
from intercom_summary.qa.rulesets import get_ruleset, strict_schema
from intercom_summary.qa.schema import ConversationGrade, score_from_verdicts

AGENT_LINE = "Your withdrawal is approved and will arrive tomorrow."


@pytest.fixture(scope="module")
def rs():
    return get_ruleset("kb-v41")


def _convo(*extra: Message, closed_by: str | None = None) -> Conversation:
    msgs = [
        Message(0, "user", "Kim", None, "Where is my withdrawal?"),
        Message(1, "admin", "Ada", None, AGENT_LINE),
        Message(2, "bot", "Billy Jr.", None, "Rate our conversation!"),
        *extra,
    ]
    if closed_by:
        msgs.append(Message(len(msgs), closed_by, "x", None, "", part_type="close"))
    return Conversation(
        id="7", created_at=datetime(2026, 10, 6, tzinfo=timezone.utc), updated_at=None,
        state="closed", assignee=Admin(id="1", name="Ada", email="ada@co.com"), messages=msgs,
    )


def _data(**over):
    data = {
        "case_type": "Withdrawal", "risk_flag": "Financial",
        "expected_handling": "Answer", "data_sufficiency": "Sufficient",
        "requests": [{"text": "Where is my withdrawal?", "status": "answered", "material": True}],
        "problems": [],
        "criteria": [{"id": c, "v": "pass", "ev": ""} for c in (
            "resp-no-ghost", "res-no-fake-close", "comm-tone", "crit-data-care")],
        "outcome_status": "Resolved",
        "summary": "All good.",
        "manual_review_needed": False,
    }
    data.update(over)
    return data


def _verdicts(data) -> dict:
    return {c["id"]: c["v"] for c in data["criteria"]}


def _run(rs, data, convo=None):
    flags = check_verdicts(convo or _convo(), data, rs)
    return flags, _verdicts(data)


# ── a. a detected problem is a fail ─────────────────────────────────────────────────────
def test_a_listed_problem_fails_its_criterion_even_when_the_model_said_pass(rs):
    data = _data(problems=[{"id": "comm-tone", "ev": AGENT_LINE}])
    flags, v = _run(rs, data)
    assert v["comm-tone"] == "fail"
    assert any(f.startswith("rule:comm-tone pass → fail") for f in flags)


def test_a_problem_quoting_the_bot_is_still_thrown_out_by_the_guard(rs):
    data = _data(problems=[{"id": "comm-tone", "ev": "Rate our conversation!"}])
    _, v = _run(rs, data)
    assert v["comm-tone"] == "n/a"


def test_a_problem_on_an_unknown_criterion_is_ignored(rs):
    data = _data(problems=[{"id": "made-up", "ev": AGENT_LINE}])
    _, v = _run(rs, data)
    assert "made-up" not in v


def test_a_grade_that_lists_a_major_problem_cannot_score_100(rs):
    """The calibration bug: the problem was written down, every verdict said pass."""
    data = _data(problems=[{"id": "severe-business-trust", "ev": AGENT_LINE}])
    check_verdicts(_convo(), data, rs)
    g = ConversationGrade.from_model_output("7", "Ada", data, ruleset_id="kb-v41")
    assert (g.overall_score, g.overall_result) == (75, "FAIL")
    assert g.major_outcome_failure is True
    assert g.violations[0].startswith("Business Trust Breach")
    assert g.summary.startswith("FAIL · Major: Business Trust Breach.")


# ── b. requests decide resp-no-ghost ────────────────────────────────────────────────────
def _player_last(*extra: Message) -> Conversation:
    """The agent answered once, then the player's last questions got no reply; the bot closed."""
    return _convo(*extra, Message(5, "user", "Kim", datetime(2026, 10, 6, 12, 5, tzinfo=timezone.utc),
                                  "Are you still there? I sent the statement."),
                  closed_by="bot")


def _agent_last() -> Conversation:
    """The agent asked for a document, reminded, closed; the player never answered."""
    t = lambda m: datetime(2026, 10, 6, 12, m, tzinfo=timezone.utc)  # noqa: E731
    return _convo(
        Message(5, "user", "Kim", t(1), "One moment"),
        Message(6, "admin", "Ada", t(2), "Can you please provide the bank statement?"),
        Message(7, "admin", "Ada", t(5), "Are we still connected?"),
        Message(8, "admin", "Ada", t(8), "I see no reply from your side and I will close the chat."),
        closed_by="admin",
    )


def test_an_unresolved_material_request_fails_resp_no_ghost_when_the_player_was_ghosted(rs):
    data = _data(requests=[{"text": "Where is my withdrawal?", "status": "unresolved",
                            "material": True}])
    flags, v = _run(rs, data, _player_last())
    assert v["resp-no-ghost"] == "fail"
    ev = next(c["ev"] for c in data["criteria"] if c["id"] == "resp-no-ghost")
    assert "12:05:00" in ev and "automation closed the chat" in ev


def test_ghosting_the_players_last_messages_is_a_major_whatever_the_topic(rs):
    """№103: the player's last two messages got no reply; the model called them secondary."""
    data = _data(requests=[{"text": "Confirm the agent is still connected",
                            "status": "unresolved", "material": False}])
    _, v = _run(rs, data, _player_last())
    assert v["resp-no-ghost"] == "fail"


def test_an_unanswered_thanks_is_not_ghosting(rs):
    convo = _convo(Message(5, "user", "Kim", None, "ok thanks!"), closed_by="bot")
    data = _data(requests=[{"text": "Small talk", "status": "unresolved", "material": False}])
    _, v = _run(rs, data, convo)
    assert v["resp-no-ghost"] == "pass"


def test_a_stray_message_the_agent_never_saw_is_left_to_the_model(rs):
    convo = Conversation(
        id="8", created_at=datetime(2026, 10, 6, tzinfo=timezone.utc), updated_at=None,
        state="closed", messages=[Message(0, "user", "Lead", None, "123456 is your code")])
    data = _data(requests=[{"text": "No real request", "status": "unresolved",
                            "material": False}])
    _, v = _run(rs, data, convo)
    assert v["resp-no-ghost"] == "pass"


def test_a_player_who_went_silent_after_the_agents_request_is_pending_not_a_fail(rs):
    """№116: the agent asked for a bank statement, waited, reminded and closed."""
    data = _data(requests=[{"text": "Credit the missing deposit", "status": "unresolved",
                            "material": True}], outcome_status="Pending-legitimate")
    flags, v = _run(rs, data, _agent_last())
    assert v["resp-no-ghost"] == "pass"
    assert data["requests"][0]["status"] == "pending"
    assert any("unresolved → pending" in f for f in flags)


def test_an_unresolved_outcome_still_goes_to_a_person_when_the_player_went_silent(rs):
    data = _data(requests=[{"text": "Credit the missing deposit", "status": "unresolved",
                            "material": True}], outcome_status="Unresolved")
    _, v = _run(rs, data, _agent_last())
    assert v["resp-no-ghost"] == "cannot_determine"


def test_a_ghosting_fail_the_model_listed_is_kept_when_the_agent_wrote_last(rs):
    data = _data(requests=[{"text": "Where is my withdrawal?", "status": "unresolved",
                            "material": True}],
                 problems=[{"id": "resp-no-ghost", "ev": AGENT_LINE}])
    _, v = _run(rs, data, _agent_last())
    assert v["resp-no-ghost"] == "fail"
    assert data["requests"][0]["status"] == "unresolved"


@pytest.mark.parametrize("failed", ["action-escalation-missed", "info-completeness"])
def test_a_request_the_agents_own_failure_left_open_stays_unresolved(rs, failed):
    """A VIP-manager request answered in that team's place is open because of the agent."""
    data = _data(requests=[{"text": "VIP manager request", "status": "unresolved",
                            "material": True}],
                 problems=[{"id": failed, "ev": AGENT_LINE}])
    _run(rs, data, _agent_last())
    assert data["requests"][0]["status"] == "unresolved"


def test_a_material_request_the_agent_skipped_is_a_major_not_a_minor(rs):
    """"I cannot get promo bonus" never addressed: the model filed it as info-completeness."""
    data = _data(requests=[{"text": "Cannot get promo bonus", "status": "unresolved",
                            "material": True}],
                 problems=[{"id": "info-completeness", "ev": AGENT_LINE}])
    _, v = _run(rs, data, _agent_last())
    assert v["resp-no-ghost"] == "fail"


def test_a_skipped_request_already_covered_by_a_major_is_not_penalised_twice(rs):
    data = _data(requests=[{"text": "VIP manager request", "status": "unresolved",
                            "material": True}],
                 problems=[{"id": "action-escalation-missed", "ev": AGENT_LINE}])
    _, v = _run(rs, data, _agent_last())
    assert v["resp-no-ghost"] == "pass"


def test_a_policy_refusal_of_a_bonus_is_not_an_escalation_case(rs):
    assert "is NOT an escalation case (see T&C COMPLIANCE)" in rs.prompt_text


def test_a_ghosting_fail_may_quote_the_player(rs):
    data = _data(problems=[{"id": "resp-no-ghost",
                            "ev": "Are you still there? I sent the statement."}])
    _, v = _run(rs, data, _player_last())
    assert v["resp-no-ghost"] == "fail"


def test_an_unresolved_secondary_request_does_not(rs):
    data = _data(requests=[{"text": "Nice day?", "status": "unresolved", "material": False}])
    _, v = _run(rs, data)
    assert v["resp-no-ghost"] == "pass"


# ── c. an outcome that contradicts the verdicts ────────────────────────────────────────
def test_unresolved_outcome_with_no_failure_is_undetermined(rs):
    _, v = _run(rs, _data(outcome_status="Unresolved"))
    assert v["resp-no-ghost"] == "cannot_determine"


def test_a_fake_close_by_the_agent_fails_res_no_fake_close(rs):
    _, v = _run(rs, _data(outcome_status="Abandoned-fake-closed"), _convo(closed_by="admin"))
    assert v["res-no-fake-close"] == "fail"


# ── d. an escalation needs an internal note ────────────────────────────────────────────
def test_an_escalation_with_no_internal_note_cannot_pass(rs):
    data = _data(expected_handling="Internal escalation",
                 criteria=[{"id": "action-escalation-missed", "v": "pass",
                            "ev": "I have passed this to my colleagues"}])
    _, v = _run(rs, data)
    assert v["action-escalation-missed"] == "cannot_determine"


def test_an_escalation_with_an_internal_note_keeps_its_pass(rs):
    note = Message(3, "admin", "Ada", None, "Escalated to Payments, ticket KB-1234",
                   part_type="note")
    data = _data(expected_handling="Internal escalation",
                 criteria=[{"id": "action-escalation-missed", "v": "pass",
                            "ev": "note: Escalated to Payments"}])
    _, v = _run(rs, data, _convo(note))
    assert v["action-escalation-missed"] == "pass"


# ── e. an escalation pass must cite its note ───────────────────────────────────────────
def test_an_escalation_pass_must_cite_its_note(rs):
    data = _data(criteria=[{"id": "action-escalation-missed", "v": "pass", "ev": ""},
                           {"id": "comm-tone", "v": "pass", "ev": ""}])
    _, v = _run(rs, data)
    assert v["action-escalation-missed"] == "cannot_determine"
    assert v["comm-tone"] == "pass"          # ordinary criteria still pass with no ev


def test_other_passes_need_no_source(rs):
    # Client points 4 and 6 (no data / no KB / admin data → cannot_determine) were withdrawn
    # on 2026-10-08: accuracy and the money criteria keep a pass that cites nothing.
    data = _data(criteria=[{"id": cid, "v": "pass", "ev": ""} for cid in (
        "accuracy-material", "accuracy-minor", "financial-case-abandoned",
        "severe-business-trust")])
    flags, v = _run(rs, data)
    assert set(v.values()) == {"pass"} and not flags


def test_flat_rulesets_get_only_the_guards():
    data = {"criteria": [{"id": "cf-friendly", "v": "pass", "ev": ""}],
            "problems": [{"id": "cf-friendly", "ev": AGENT_LINE}]}
    check_verdicts(_convo(), data, get_ruleset("default"))
    assert data["criteria"][0]["v"] == "pass"


# ── the result rule ────────────────────────────────────────────────────────────────────
def _all_pass(rs, **over):
    criteria = [{"id": c["id"], "v": "pass", "ev": ""} for c in rs.criteria]
    for c in criteria:
        if c["id"] in over:
            c["v"] = over[c["id"]]
    return criteria


def test_cannot_determine_does_not_block_a_pass(rs):
    r = score_gated(_all_pass(rs, **{"accuracy-material": "cannot_determine"}), rs)
    assert (r.score, r.result, r.band) == (100, "PASS", "Excellent")


def test_a_proven_major_beats_an_undetermined_criterion(rs):
    r = score_gated(_all_pass(rs, **{"resp-no-ghost": "fail",
                                     "accuracy-material": "cannot_determine"}), rs)
    assert (r.score, r.result, r.major_outcome_failure) == (75, "FAIL", True)


def test_a_major_fails_even_if_the_threshold_is_lowered(rs):
    from dataclasses import replace

    lenient = replace(rs, scoring={**rs.scoring, "pass_threshold": 70})
    r = score_gated(_all_pass(lenient, **{"resp-no-ghost": "fail"}), lenient)
    assert (r.score, r.result, r.major_outcome_failure) == (75, "FAIL", True)


def test_a_critical_reports_the_major_too(rs):
    r = score_gated(_all_pass(rs, **{"crit-pii-leak": "fail", "resp-no-ghost": "fail"}), rs)
    assert (r.score, r.critical_fail, r.major_outcome_failure) == (0, True, True)


def test_cannot_determine_passes_but_goes_to_manual_review(rs):
    data = _data(criteria=_all_pass(rs, **{"accuracy-material": "cannot_determine"}),
                 manual_review_needed=False)
    g = ConversationGrade.from_model_output("7", "Ada", data, ruleset_id="kb-v41")
    assert g.overall_result == "PASS"
    assert g.manual_review_needed is True and "accuracy-material" in g.manual_review_reason


def test_an_ignored_churn_signal_is_a_major_and_always_goes_to_manual_review(rs):
    criteria = _all_pass(rs, **{"churn-rg-signal-ignored": "fail"})
    data = _data(criteria=criteria, manual_review_needed=False, manual_review_reason="")
    g = ConversationGrade.from_model_output("7", "Ada", data, ruleset_id="kb-v41")
    assert (g.overall_result, g.overall_score, g.major_outcome_failure) == ("FAIL", 75, True)
    assert g.manual_review_needed is True
    assert g.manual_review_reason == "Churn signal ignored — confirm"


def test_the_churn_criterion_counts_only_a_real_reaction(rs):
    text = rs.prompt_text
    assert "Answering the player's main request is NOT a reaction" in text


def test_churn_is_only_an_explicit_statement(rs):
    """QA, 2026-10-09: "my account is jinxed" is a complaint, not a threat to leave. The 10-08
    widening to "open dissatisfaction" is reversed."""
    text = rs.prompt_text
    assert "A churn signal is ONLY an explicit statement" in text
    assert '"my account is jinxed"' in text
    assert "unhappy with its terms, its bonuses or the casino as a whole" not in text
    assert "open dissatisfaction" not in text


def test_insufficient_data_goes_to_manual_review_without_blocking_the_result(rs):
    data = _data(criteria=_all_pass(rs), data_sufficiency="Insufficient",
                 manual_review_needed=False)
    g = ConversationGrade.from_model_output("7", "Ada", data, ruleset_id="kb-v41")
    assert g.overall_result == "PASS" and g.manual_review_needed is True


def test_the_analyst_rescore_follows_the_points(rs):
    verdicts = {c["id"]: "pass" for c in rs.criteria}
    verdicts["accuracy-material"] = "cannot_determine"
    assert score_from_verdicts(verdicts, ruleset_id="kb-v41")[2] == "PASS"
    assert score_from_verdicts(verdicts, extra_deduction=30, ruleset_id="kb-v41")[2] == "FAIL"


def test_an_analysts_deduction_never_turns_a_major_into_a_pass(rs):
    verdicts = {c["id"]: "pass" for c in rs.criteria}
    verdicts["resp-no-ghost"] = "fail"
    assert score_from_verdicts(verdicts, extra_deduction=1, ruleset_id="kb-v41")[2] == "FAIL"


# ── the grammar ────────────────────────────────────────────────────────────────────────
def test_problems_come_before_verdicts_and_the_summary_comes_last(rs):
    schema = strict_schema(rs)
    keys = list(schema["properties"])
    assert keys.index("problems") < keys.index("criteria")
    assert keys[-1] == "what_happened" and "summary" not in keys
    assert "violations" not in keys
    ids = schema["properties"]["problems"]["items"]["properties"]["id"]["enum"]
    assert set(ids) == set(rs.deductions)


# ── f. a mandatory escalation that never happened (QA, 2026-10-09) ─────────────────────
def test_a_required_escalation_never_performed_or_claimed_fails(rs):
    """№132: "VIP manager request" answered by the agent and closed — no handoff at all."""
    data = _data(expected_handling="Internal escalation", escalation_case="VIP manager requested",
                 criteria=[{"id": "action-escalation-missed", "v": "n/a", "ev": ""}])
    flags, v = _run(rs, data)
    assert v["action-escalation-missed"] == "fail"
    ev = next(c["ev"] for c in data["criteria"] if c["id"] == "action-escalation-missed")
    assert "VIP manager requested" in ev
    g = ConversationGrade.from_model_output("7", "Ada", data, ruleset_id="kb-v41")
    assert (g.overall_result, g.overall_score) == ("FAIL", 75)


@pytest.mark.parametrize("claim", [
    {"requests": [{"text": "Check my deposit", "status": "escalated", "material": True}]},
    {"outcome_status": "Escalated correctly"},
    {"outcome_status": "Pending-legitimate"},
])
def test_a_required_escalation_the_agent_claimed_without_a_note_cannot_be_determined(rs, claim):
    data = _data(expected_handling="Internal escalation",
                 criteria=[{"id": "action-escalation-missed", "v": "pass", "ev": ""}], **claim)
    _, v = _run(rs, data)
    assert v["action-escalation-missed"] == "cannot_determine"


def test_the_models_own_cannot_determine_on_a_required_escalation_is_left_alone(rs):
    data = _data(expected_handling="Internal escalation",
                 criteria=[{"id": "action-escalation-missed", "v": "cannot_determine",
                            "ev": "unsure whether a listed case applies"}])
    _, v = _run(rs, data)
    assert v["action-escalation-missed"] == "cannot_determine"


def test_no_escalation_expected_and_none_claimed_changes_nothing(rs):
    data = _data(expected_handling="Answer", outcome_status="Pending-legitimate",
                 criteria=[{"id": "action-escalation-missed", "v": "n/a", "ev": ""}])
    flags, v = _run(rs, data)
    assert v["action-escalation-missed"] == "n/a" and not flags


def test_a_pass_with_no_source_cites_the_internal_note_when_there_is_one(rs):
    note = Message(3, "admin", "Ada", None, "forwarded to finance, waiting for reply",
                   part_type="note")
    data = _data(expected_handling="Internal escalation",
                 criteria=[{"id": "action-escalation-missed", "v": "pass", "ev": ""}])
    _, v = _run(rs, data, _convo(note))
    assert v["action-escalation-missed"] == "pass"
    ev = next(c["ev"] for c in data["criteria"] if c["id"] == "action-escalation-missed")
    assert ev == "note: forwarded to finance, waiting for reply"


def test_a_model_fail_is_not_overturned_by_a_note(rs):
    note = Message(3, "admin", "Ada", None, "notifications are broken", part_type="note")
    data = _data(expected_handling="Internal escalation",
                 problems=[{"id": "action-escalation-missed", "ev": AGENT_LINE}])
    _, v = _run(rs, data, _convo(note))
    assert v["action-escalation-missed"] == "fail"


def test_the_prompt_carries_the_mandatory_escalation_list(rs):
    assert "## MANDATORY ESCALATION" in rs.prompt_text
    assert "VIP manager request" in rs.prompt_text
    assert "escalation_case" in strict_schema(rs)["properties"]


# ── the summary cannot contradict the score (QA, 2026-10-09) ───────────────────────────
def test_the_summary_is_the_verdict_line_then_the_models_neutral_narrative(rs):
    data = _data(problems=[{"id": "severe-business-trust", "ev": AGENT_LINE}],
                 what_happened="The player asked about a withdrawal and the agent replied.")
    data.pop("summary")
    check_verdicts(_convo(), data, rs)
    g = ConversationGrade.from_model_output("7", "Ada", data, ruleset_id="kb-v41")
    assert g.summary == ("FAIL · Major: Business Trust Breach. "
                         "The player asked about a withdrawal and the agent replied.")


def test_a_pass_summary_lists_no_undetermined_criteria(rs):
    data = _data(criteria=_all_pass(rs, **{"accuracy-material": "cannot_determine"}),
                 what_happened="Answered.", manual_review_reason="")
    g = ConversationGrade.from_model_output("7", "Ada", data, ruleset_id="kb-v41")
    assert g.summary == "PASS. Answered."
    assert "Cannot determine" not in g.summary
    assert "accuracy-material" in g.manual_review_reason


def test_a_grade_from_the_old_grammar_still_reads_its_summary(rs):
    g = ConversationGrade.from_model_output("7", "Ada", _data(criteria=_all_pass(rs)),
                                            ruleset_id="kb-v41")
    assert g.summary == "PASS. All good."
