"""VIP QA Scorecard v1.6 scoring (`vip-gated`) — every worked example in the scorecard."""
import pytest

from intercom_summary.qa.rulesets import get_ruleset
from intercom_summary.qa.schema import ConversationGrade, score_from_verdicts
from intercom_summary.qa.vip_scoring import NOT_ASSESSABLE, mark_not_assessable, score_vip


@pytest.fixture(scope="module")
def rs():
    return get_ruleset("vip-v16")


def _score(rs, **verdicts):
    return score_vip([{"id": k.replace("_", "-"), "v": v} for k, v in verdicts.items()], rs)


def test_clean_case_is_100(rs):
    r = _score(rs, q_clarity="pass", own_lost="pass", style_tone="n/a")
    assert (r.score, r.result, r.band, r.severity) == (100, "PASS", "Excellent", "")


# §7.2 — three managers refuse a bonus legitimately.
def test_refusal_rude_without_reason_or_step_is_83(rs):
    r = _score(rs, q_clarity="fail", style_tone="fail")
    assert (r.score, r.result) == (83, "FAIL")


def test_refusal_with_reason_but_no_next_step_is_93(rs):
    r = _score(rs, q_clarity="partial")
    assert (r.score, r.result) == (93, "PASS")


def test_unclear_decision_alone_is_86_still_pass(rs):
    r = _score(rs, q_clarity="fail")
    assert (r.score, r.result) == (86, "PASS")


# §5 — follow-up missed + Payments ignored = 2 Majors → cap 65; no notes −5; dry tone −3 → 57.
def test_two_majors_notes_and_tone_is_57(rs):
    r = _score(rs, own_lost="fail", pay_handoff="fail", proc_notes="fail", style_tone="fail")
    assert (r.score, r.result, r.severity) == (57, "FAIL", "Major")
    assert r.breakdown == {"cap": 65, "quality": 0, "process": 5, "style": 3}
    assert not r.service_failure_flag


# §13 — before / after.
def test_polite_but_two_days_silent_is_75(rs):
    assert _score(rs, own_lost="fail").score == 75


def test_payments_waiting_for_context_is_75(rs):
    assert _score(rs, pay_handoff="fail").score == 75


def test_three_majors_is_55_with_service_failure_flag(rs):
    r = _score(rs, own_lost="fail", pay_handoff="fail", action_missed="fail")
    assert (r.score, r.result, r.service_failure_flag) == (55, "FAIL", True)
    assert not r.critical_fail


def test_formal_template_but_resolved_is_96(rs):
    r = _score(rs, style_personal="fail", style_writing="fail")
    assert (r.score, r.result) == (96, "PASS")


def test_birthday_spins_to_self_excluded_player_is_provisional_zero(rs):
    r = _score(rs, crit_rg_commerce="fail", q_clarity="pass")
    assert (r.score, r.result, r.critical_fail) == (0, "FAIL", True)
    assert r.critical_ids == ["crit-rg-commerce"] and r.intent_check == []


def test_intent_criticals_are_listed_for_the_reviewer(rs):
    r = _score(rs, crit_false_status="fail")
    assert r.score == 0 and r.intent_check == ["crit-false-status"]


def test_cap_stops_at_45(rs):
    four = _score(rs, own_lost="fail", pay_handoff="fail", action_missed="fail", own_info="fail")
    five = _score(rs, own_lost="fail", pay_handoff="fail", action_missed="fail", own_info="fail",
                  bonus_unjustified="fail")
    assert four.score == five.score == 45
    assert five.service_failure_flag


def test_style_is_capped_at_6(rs):
    r = _score(rs, style_tone="fail", style_personal="fail", style_writing="fail")
    assert r.score == 94 and r.breakdown["style"] == 6


def test_partial_costs_half_on_two_level_criteria(rs):
    assert _score(rs, q_expectations="partial").score == 96
    assert _score(rs, q_expectations="fail").score == 92


def test_partial_on_a_single_level_criterion_costs_the_full_deduction(rs):
    assert _score(rs, style_tone="partial").score == 97


def test_score_never_goes_below_zero(rs):
    r = _score(rs, own_lost="fail", pay_handoff="fail", action_missed="fail", own_info="fail",
               q_clarity="fail", q_effort="fail", q_complaint="fail", proc_outbound="fail",
               proc_notes="fail", proc_handover="fail")
    assert r.score == 0 and not r.critical_fail


def test_unknown_criteria_and_not_assessable_cost_nothing(rs):
    r = score_vip([{"id": "made-up", "v": "fail"}, {"id": "proc-notes", "v": NOT_ASSESSABLE}], rs)
    assert r.score == 100


# ── not assessable ──────────────────────────────────────────────────────────────────────────
def test_external_and_timing_questions_become_not_assessable(rs):
    crit = [{"id": "proc-notes", "v": "cannot_determine"},          # external
            {"id": "proc-first-response", "v": "cannot_determine"},  # timing, no shifts
            {"id": "q-clarity", "v": "cannot_determine"},            # Intercom — stays open
            {"id": "pay-handoff", "v": "fail"}]                      # a visible fail stays
    assert mark_not_assessable(crit, rs, shifts_known=False) == ["proc-notes", "proc-first-response"]
    assert [c["v"] for c in crit] == [NOT_ASSESSABLE, NOT_ASSESSABLE, "cannot_determine", "fail"]


def test_timing_is_assessable_once_shifts_are_configured(rs):
    crit = [{"id": "proc-first-response", "v": "cannot_determine"}]
    assert mark_not_assessable(crit, rs, shifts_known=True) == []
    assert crit[0]["v"] == "cannot_determine"


def _model_output(criteria, **over):
    data = {
        "step0": {"case_type": "Bonus request", "channel": "email", "segment_state": "unknown",
                  "risk_flag": "None", "expected_handling": "Explain refusal",
                  "bonus_eligibility": "unknown", "data_sufficiency": "Sufficient"},
        "requests": [{"text": "Any bonus?", "from": "player", "status": "resolved"}],
        "criteria": criteria, "outcome_status": "Resolved", "summary": "ok",
        "manual_review_needed": False, "manual_review_reason": "", "confidence": "high",
    }
    data.update(over)
    return data


def test_not_assessable_alone_does_not_send_to_manual_review(monkeypatch):
    import intercom_summary.vip_staff as vs

    monkeypatch.setattr(vs, "shifts_known", lambda: False)
    g = ConversationGrade.from_model_output("vc-1", "Vera", _model_output([
        {"id": "proc-notes", "v": "cannot_determine", "ev": "no BO access"},
        {"id": "proc-first-response", "v": "cannot_determine", "ev": "no shifts"},
        {"id": "q-clarity", "v": "partial", "ev": "no next step", "episode": "msg 2",
         "who": "Vera"},
    ]), ruleset_id="vip-v16")
    assert g.overall_score == 93 and g.overall_result == "PASS"
    assert sorted(g.not_assessable) == ["proc-first-response", "proc-notes"]
    assert not g.manual_review_needed
    assert g.step0["case_type"] == "Bonus request" and g.case_type == "Bonus request"
    q = next(r for r in g.rule_results if r.rule_id == "q-clarity")
    assert (q.verdict, q.episode, q.who) == ("partial", "msg 2", "Vera")
    assert g.score_breakdown["quality"] == 7


def test_intercom_cannot_determine_critical_and_service_failure_need_review():
    g = ConversationGrade.from_model_output("vc-1", "Vera", _model_output([
        {"id": "q-clarity", "v": "cannot_determine", "ev": "?"}]), ruleset_id="vip-v16")
    assert g.manual_review_needed and "q-clarity" in g.manual_review_reason

    g = ConversationGrade.from_model_output("vc-1", "Vera", _model_output([
        {"id": "crit-records", "v": "fail", "ev": "x"}]), ruleset_id="vip-v16")
    assert g.overall_score == 0 and g.critical_fail and g.manual_review_needed
    assert g.outcome_status == "Critical" and g.critical_intent_check == ["crit-records"]
    assert "provisional" in g.manual_review_reason and "intent check" in g.manual_review_reason

    g = ConversationGrade.from_model_output("vc-1", "Vera", _model_output([
        {"id": "own-lost", "v": "fail", "ev": "a"}, {"id": "own-info", "v": "fail", "ev": "b"},
        {"id": "pay-handoff", "v": "fail", "ev": "c"}]), ruleset_id="vip-v16")
    assert g.overall_score == 55 and g.service_failure_flag and g.major_count == 3
    assert g.manual_review_needed and not g.critical_fail


def test_reviewer_rescore_uses_the_same_model():
    assert score_from_verdicts({"q-clarity": "partial"}, ruleset_id="vip-v16") == (93, "Good", "PASS")
    assert score_from_verdicts({"own-lost": "fail"}, extra_deduction=5,
                               ruleset_id="vip-v16")[0] == 70
