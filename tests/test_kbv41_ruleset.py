"""The v4.1 ruleset is wired end to end: catalogue, prompt, grammar and grade shape agree.

A ruleset can be internally consistent and still be useless — if the prompt's points drift from
the catalogue the analyst's re-score disagrees with the AI's score, and if the output grammar
omits a field the model never produces it. Both failures are silent at runtime, so they are
checked here instead.
"""
import json

import pytest

from intercom_summary.qa.kbv41_prompt import KBV41_OUTPUT_SCHEMA, KBV41_QA_SYSTEM_PROMPT
from intercom_summary.qa.rulesets import (
    KBV41_RULESET_ID,
    SCORING_GATED,
    get_ruleset,
    output_schema_for,
    validate_ruleset,
)
from intercom_summary.qa.schema import ConversationGrade, score_from_verdicts


@pytest.fixture(scope="module")
def rs():
    return get_ruleset(KBV41_RULESET_ID)


def test_the_prompt_and_the_catalogue_agree_on_every_point(rs):
    assert validate_ruleset(rs) == []


def test_it_declares_the_gated_model_and_the_manuals_numbers(rs):
    assert rs.scoring_model == SCORING_GATED
    assert rs.pass_threshold == 85
    assert rs.scoring["major_cap"] == 75
    assert rs.scoring["no_major_floor"] == 76
    assert rs.group_caps == {"communication": 5}


def test_every_criterion_declares_a_gate_and_a_severity(rs):
    for c in rs.criteria:
        assert c.get("gate") in (1, 2, 3), c["id"]
        assert c.get("severity") in ("critical", "major", "minor"), c["id"]


def test_gate_one_is_exactly_the_critical_set(rs):
    assert set(rs.ids_in_gate(1)) == set(rs.critical)


def test_the_other_rulesets_are_untouched_and_still_flat():
    for rid in ("default", "vip"):
        other = get_ruleset(rid)
        assert other.scoring_model == "flat"
        assert validate_ruleset(other) == []


def test_the_grammar_declares_every_field_the_grade_reads(rs):
    props = KBV41_OUTPUT_SCHEMA["properties"]
    for field in ("case_type", "risk_flag", "expected_handling", "data_sufficiency",
                  "requests", "criteria", "outcome_status", "summary",
                  "manual_review_needed", "confidence"):
        assert field in props, field
    assert output_schema_for(KBV41_RULESET_ID) is KBV41_OUTPUT_SCHEMA
    # and the flat rulesets keep their own grammar
    assert output_schema_for("default") is not KBV41_OUTPUT_SCHEMA


def test_cannot_determine_is_offered_to_the_model():
    verdicts = KBV41_OUTPUT_SCHEMA["properties"]["criteria"]["items"]["properties"]["v"]["enum"]
    assert verdicts == ["pass", "fail", "n/a", "cannot_determine"]
    assert "cannot_determine" in KBV41_QA_SYSTEM_PROMPT


def test_the_prompt_never_asks_the_model_to_do_the_arithmetic():
    """The score is computed from verdicts. Asking for it invites the model to invent one."""
    assert "overall_score" not in KBV41_OUTPUT_SCHEMA["properties"]
    assert "Do NOT calculate a score" in KBV41_QA_SYSTEM_PROMPT


# ── a whole model response, through the real parsing path ────────────────────────────
def _payload(**over):
    data = {
        "case_type": "Withdrawal",
        "risk_flag": "Financial",
        "expected_handling": "Internal escalation",
        "data_sufficiency": "Sufficient",
        "requests": [
            {"text": "Where is my withdrawal?", "status": "unresolved", "material": True},
            {"text": "Can I change my email?", "status": "answered", "material": False},
        ],
        "criteria": [
            {"id": "resp-no-ghost", "v": "fail", "ev": "AGENT: anything else?"},
            {"id": "tag-chat", "v": "cannot_determine", "ev": "tags are CRM metadata"},
            {"id": "comm-tone", "v": "pass", "ev": ""},
        ],
        "outcome_status": "Unresolved",
        "flags": ["payment_sensitive_case"],
        "violations": ["Withdrawal question never answered"],
        "summary": "The agent closed without answering the withdrawal question.",
        "manual_review_needed": False,
        "manual_review_reason": "",
        "confidence": "High",
    }
    data.update(over)
    return data


def test_a_v41_grade_carries_the_new_fields():
    g = ConversationGrade.from_ollama_output("c1", "Lenny", _payload(),
                                             ruleset_id=KBV41_RULESET_ID)
    assert g.overall_score == 75 and g.overall_result == "FAIL"
    assert g.severity == "Major" and not g.critical_fail
    assert g.case_type == "Withdrawal" and g.risk_flag == "Financial"
    assert g.outcome_status == "Unresolved"
    assert g.expected_handling == "Internal escalation"
    assert len(g.requests) == 2 and g.requests[0]["status"] == "unresolved"
    assert g.confidence == "High"


def test_a_cannot_determine_verdict_always_asks_for_a_human():
    """The model said manual_review_needed=false while leaving a verdict undetermined.
    That is exactly the case a QC manager exists for, so the flag is derived, not trusted."""
    g = ConversationGrade.from_ollama_output("c1", "Lenny", _payload(),
                                             ruleset_id=KBV41_RULESET_ID)
    assert g.manual_review_needed is True
    assert "tag-chat" in g.manual_review_reason


def test_a_gate_one_breach_overrides_the_models_outcome_status():
    g = ConversationGrade.from_ollama_output("c1", "Lenny", _payload(
        criteria=[{"id": "crit-data-care", "v": "fail", "ev": "AGENT: send me your CVV"}],
        outcome_status="Resolved",
    ), ruleset_id=KBV41_RULESET_ID)
    assert g.overall_score == 0 and g.critical_fail is True
    assert g.outcome_status == "Critical Fail"
    assert g.catastrophic_service_failure is False


def test_the_grade_survives_a_round_trip_through_storage():
    g = ConversationGrade.from_ollama_output("c1", "Lenny", _payload(),
                                             ruleset_id=KBV41_RULESET_ID)
    back = ConversationGrade.from_dict(json.loads(json.dumps(g.to_dict())))
    for f in ("case_type", "risk_flag", "outcome_status", "severity", "requests",
              "manual_review_needed", "critical_fail", "confidence"):
        assert getattr(back, f) == getattr(g, f), f


# ── the analyst's re-score uses the same formula as the AI ────────────────────────────
def test_a_manual_rescore_reproduces_the_ai_score(rs):
    verdicts = {c["id"]: "pass" for c in rs.criteria}
    verdicts["resp-no-ghost"] = "fail"
    score, _band, result = score_from_verdicts(verdicts, ruleset_id=KBV41_RULESET_ID)
    assert (score, result) == (75, "FAIL")


def test_an_analysts_extra_deduction_may_go_below_the_no_major_floor(rs):
    """The floor protects the model's own criteria from out-ranking a failed outcome. A QC
    manager subtracting points for something the AI could not see is a deliberate human act
    and is not bound by it."""
    verdicts = {c["id"]: "pass" for c in rs.criteria}
    score, _band, result = score_from_verdicts(
        verdicts, extra_deduction=40, ruleset_id=KBV41_RULESET_ID
    )
    assert (score, result) == (60, "FAIL")


def test_a_critical_fail_cannot_be_softened_by_arithmetic(rs):
    verdicts = {c["id"]: "pass" for c in rs.criteria}
    verdicts["crit-pii-leak"] = "fail"
    assert score_from_verdicts(verdicts, extra_deduction=10,
                               ruleset_id=KBV41_RULESET_ID)[0] == 0
