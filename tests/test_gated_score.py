"""The v4.1 three-gate model: caps and floors, not a sum.

The whole point of the model is an ordering the flat model could not express — a failed
outcome must always cost more than any amount of process untidiness, and a compliance
incident must always cost more than a failed outcome. Every test here defends one edge of
that ordering, and the worked examples are the ones the manual itself states.
"""
import pytest

from intercom_summary.qa.gated_scoring import score_gated
from intercom_summary.qa.rulesets import get_ruleset


@pytest.fixture(scope="module")
def rs():
    return get_ruleset("kb-v41")


def _score(rs, *failed_ids, **verdicts):
    """Score a chat where the named criteria failed and everything else passed."""
    criteria = [{"id": cid, "v": "fail", "ev": "quote"} for cid in failed_ids]
    criteria += [{"id": cid, "v": v, "ev": ""} for cid, v in verdicts.items()]
    return score_gated(criteria, rs)


# ── Gate 1 ────────────────────────────────────────────────────────────────────────────
def test_a_critical_criterion_zeroes_the_chat(rs):
    r = _score(rs, "crit-data-care")
    assert (r.score, r.result, r.critical_fail, r.severity) == (0, "FAIL", True, "Critical")


def test_critical_wins_over_everything_else(rs):
    r = _score(rs, "crit-rg-care", "resp-no-ghost", "tag-chat")
    assert r.score == 0 and r.critical_fail and not r.catastrophic_service_failure


def test_all_five_gate_one_criteria_zero(rs):
    for cid in rs.ids_in_gate(1):
        assert _score(rs, cid).score == 0, cid


# ── Gate 2 — the cap is flat ──────────────────────────────────────────────────────────
def test_one_major_caps_at_75(rs):
    r = _score(rs, "resp-no-ghost")
    assert (r.score, r.result, r.severity) == (75, "FAIL", "Major")


def test_three_majors_cost_exactly_the_same_as_one(rs):
    """v3.0 had a ladder where several failures deducted from each other. v4.1 removed it."""
    assert _score(rs, "resp-no-ghost", "accuracy-material", "res-no-fake-close").score == 75


def test_a_major_always_fails_because_the_cap_is_below_the_threshold(rs):
    assert rs.pass_threshold > rs.scoring["major_cap"]


def test_quality_defects_do_not_cap_but_do_subtract(rs):
    # info-completeness −5 on top of a Major: 75 − 5.
    assert _score(rs, "resp-no-ghost", "info-completeness").score == 70


# ── the floor ─────────────────────────────────────────────────────────────────────────
def test_without_a_major_the_score_never_falls_below_the_cap(rs):
    """Every non-Major penalty at once still outranks any chat that failed the outcome."""
    r = _score(rs, "info-completeness", "accuracy-minor", "resp-sla", "tag-chat",
               "ownership-effort", "comm-tone", "comm-etiquette", "comm-language")
    raw = 100 - 5 - 5 - 5 - 3 - 6 - 5   # 71 before the floor
    assert raw < 76 and r.score == 76
    assert r.result == "FAIL" and r.severity == "Minor"


def test_a_clean_chat_scores_100(rs):
    r = _score(rs)
    assert (r.score, r.result, r.severity, r.band) == (100, "PASS", "", "Excellent")


# ── Gate 3 ────────────────────────────────────────────────────────────────────────────
def test_process_penalties_apply_in_full(rs):
    assert _score(rs, "tag-chat").score == 100 - 3
    assert _score(rs, "resp-sla", "tag-chat", "ownership-effort").score == 100 - 14


def test_the_communication_block_is_capped_as_a_group(rs):
    # 3 + 2 + 2 = 7 raw, capped at 5.
    assert _score(rs, "comm-tone", "comm-etiquette", "comm-language").score == 95
    assert _score(rs, "comm-tone", "comm-etiquette").score == 95          # 5 exactly
    assert _score(rs, "comm-tone").score == 97                            # under the cap


# ── catastrophic service failure ──────────────────────────────────────────────────────
def test_catastrophic_needs_the_complete_combination(rs):
    r = _score(rs, "resp-no-ghost", "resp-sla", "tag-chat", "ownership-effort",
               "comm-tone", "comm-etiquette")
    assert (r.score, r.catastrophic_service_failure, r.band) == (25, True, "Catastrophic")
    # and it is NOT a compliance breach — the two must stay separately filterable
    assert r.critical_fail is False


def test_the_manuals_partial_example_scores_61_not_25(rs):
    """Section 3: Major + tag-chat + ownership-effort + max style, SLA met → 75−3−6−5."""
    r = _score(rs, "resp-no-ghost", "tag-chat", "ownership-effort",
               "comm-tone", "comm-etiquette")
    assert r.score == 61 and r.catastrophic_service_failure is False


def test_everything_failed_but_style_under_the_cap_is_not_catastrophic(rs):
    r = _score(rs, "resp-no-ghost", "resp-sla", "tag-chat", "ownership-effort", "comm-etiquette")
    assert r.score == 75 - 14 - 2 and not r.catastrophic_service_failure


def test_every_process_criterion_failed_without_a_major_is_not_catastrophic(rs):
    """The same Gate 3 wipeout that scores 25 alongside a Major is an ordinary 81 without one.
    Catastrophic is about failing the player AND the process, not the process alone."""
    r = _score(rs, "resp-sla", "tag-chat", "ownership-effort", "comm-tone", "comm-etiquette")
    assert r.score == 100 - 14 - 5 and not r.catastrophic_service_failure


# ── the threshold ─────────────────────────────────────────────────────────────────────
def test_pass_fail_turns_exactly_on_85(rs):
    # ownership-effort (−6) + comm-tone (−3) + comm-etiquette (−2, capped block = 5) = 89
    assert _score(rs, "ownership-effort").result == "PASS"                 # 94
    assert _score(rs, "ownership-effort", "resp-sla", "tag-chat").score == 86
    assert _score(rs, "ownership-effort", "resp-sla", "tag-chat").result == "PASS"
    r = _score(rs, "ownership-effort", "resp-sla", "tag-chat", "info-completeness")
    assert (r.score, r.result) == (81, "FAIL")


# ── guardrails carried over from the flat model ───────────────────────────────────────
def test_a_criterion_the_ruleset_does_not_define_is_ignored(rs):
    assert _score(rs, "first-response-time").score == 100


def test_only_fail_costs_anything(rs):
    r = score_gated([
        {"id": "resp-no-ghost", "v": "cannot_determine", "ev": "no CRM data"},
        {"id": "tag-chat", "v": "cannot_determine", "ev": "tags not visible"},
        {"id": "res-no-fake-close", "v": "n/a", "ev": ""},
        {"id": "comm-tone", "v": "pass", "ev": ""},
    ], rs)
    assert (r.score, r.result) == (100, "PASS")


def test_the_models_own_deduction_number_is_never_used(rs):
    r = score_gated([{"id": "tag-chat", "v": "fail", "ded": -99, "ev": "q"}], rs)
    assert r.score == 97
