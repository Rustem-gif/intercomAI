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


def _run(rs, data, convo=None, has_kb=True):
    flags = check_verdicts(convo or _convo(), data, rs, has_kb=has_kb)
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
    check_verdicts(_convo(), data, rs, has_kb=True)
    g = ConversationGrade.from_model_output("7", "Ada", data, ruleset_id="kb-v41")
    assert (g.overall_score, g.overall_result) == (75, "FAIL")
    assert g.major_outcome_failure is True
    assert g.violations[0].startswith("Business Trust Breach")
    assert g.summary.startswith("FAIL · Major: Business Trust Breach.")


# ── b. requests decide resp-no-ghost ────────────────────────────────────────────────────
def test_an_unresolved_material_request_fails_resp_no_ghost(rs):
    data = _data(requests=[{"text": "Where is my withdrawal?", "status": "unresolved",
                            "material": True}])
    _, v = _run(rs, data)
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


# ── e. f. a pass that needs outside evidence ───────────────────────────────────────────
def test_a_sourced_criterion_cannot_pass_without_a_source(rs):
    data = _data(criteria=[{"id": "financial-case-abandoned", "v": "pass", "ev": ""},
                           {"id": "comm-tone", "v": "pass", "ev": ""}])
    _, v = _run(rs, data)
    assert v["financial-case-abandoned"] == "cannot_determine"
    assert v["comm-tone"] == "pass"          # ordinary criteria still pass with no ev


def test_accuracy_cannot_pass_without_a_knowledge_base(rs):
    data = _data(criteria=[{"id": "accuracy-material", "v": "pass", "ev": "KB: Withdrawals"}])
    _, v = _run(rs, data, has_kb=False)
    assert v["accuracy-material"] == "cannot_determine"
    data = _data(criteria=[{"id": "accuracy-material", "v": "pass", "ev": "KB: Withdrawals"}])
    _, v = _run(rs, data, has_kb=True)
    assert v["accuracy-material"] == "pass"


def test_flat_rulesets_get_only_the_guards():
    data = {"criteria": [{"id": "cf-friendly", "v": "pass", "ev": ""}],
            "problems": [{"id": "cf-friendly", "ev": AGENT_LINE}]}
    check_verdicts(_convo(), data, get_ruleset("default"), has_kb=False)
    assert data["criteria"][0]["v"] == "pass"


# ── the result rule ────────────────────────────────────────────────────────────────────
def _all_pass(rs, **over):
    criteria = [{"id": c["id"], "v": "pass", "ev": ""} for c in rs.criteria]
    for c in criteria:
        if c["id"] in over:
            c["v"] = over[c["id"]]
    return criteria


def test_cannot_determine_never_passes(rs):
    r = score_gated(_all_pass(rs, **{"accuracy-material": "cannot_determine"}), rs)
    assert (r.score, r.result, r.band) == (100, "CANNOT DETERMINE", "Cannot Determine")


def test_any_cannot_determine_counts_even_in_gate_three(rs):
    r = score_gated(_all_pass(rs, **{"tag-chat": "cannot_determine"}), rs)
    assert r.result == "CANNOT DETERMINE"


def test_insufficient_data_never_passes(rs):
    assert score_gated(_all_pass(rs), rs, insufficient=True).result == "CANNOT DETERMINE"


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


def test_cannot_determine_always_goes_to_manual_review():
    data = _data(data_sufficiency="Insufficient", manual_review_needed=False)
    g = ConversationGrade.from_model_output("7", "Ada", data, ruleset_id="kb-v41")
    assert g.overall_result == "CANNOT DETERMINE"
    assert g.manual_review_needed is True and g.manual_review_reason


def test_the_analyst_rescore_resolves_cannot_determine(rs):
    verdicts = {c["id"]: "pass" for c in rs.criteria}
    verdicts["accuracy-material"] = "cannot_determine"
    assert score_from_verdicts(verdicts, ruleset_id="kb-v41")[2] == "CANNOT DETERMINE"
    assert score_from_verdicts(verdicts, extra_deduction=30,
                               ruleset_id="kb-v41")[2] == "CANNOT DETERMINE"
    verdicts["accuracy-material"] = "pass"
    assert score_from_verdicts(verdicts, ruleset_id="kb-v41")[2] == "PASS"


def test_an_analysts_deduction_never_turns_a_major_into_a_pass(rs):
    verdicts = {c["id"]: "pass" for c in rs.criteria}
    verdicts["resp-no-ghost"] = "fail"
    assert score_from_verdicts(verdicts, extra_deduction=1, ruleset_id="kb-v41")[2] == "FAIL"


# ── the grammar ────────────────────────────────────────────────────────────────────────
def test_problems_come_before_verdicts_and_the_summary_comes_last(rs):
    schema = strict_schema(rs)
    keys = list(schema["properties"])
    assert keys.index("problems") < keys.index("criteria")
    assert keys[-1] == "summary"
    assert "violations" not in keys
    ids = schema["properties"]["problems"]["items"]["properties"]["id"]["enum"]
    assert set(ids) == set(rs.deductions)
