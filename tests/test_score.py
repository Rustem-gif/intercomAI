"""The ruleset — not the model — decides which criteria exist and what they cost.

A client was docked 20 points by `first-response-time`, a criterion that appears in no prompt,
no ruleset file and no catalogue. The model invented the id, invented the deduction, and cited
the prompt's own timing header as evidence. `_compute_score` subtracted it without question.
"""
from intercom_summary.qa.schema import _compute_score

# A miniature catalogue; the real one comes from qa/rulesets.py.
CATALOGUE = {"open-greet": 2, "res-no-fake-close": 15, "resp-first-reply": 5}


def _score(criteria, catalogue=CATALOGUE):
    return _compute_score(criteria, False, catalogue)[0]


def test_a_criterion_the_ruleset_does_not_define_is_ignored():
    invented = [{"id": "first-response-time", "v": "fail", "ded": -20}]
    assert _score(invented) == 100


def test_the_catalogue_beats_the_models_own_number():
    # Seen in real output: −1, −2, −9, −10, −20 and +10 for the same invented criterion.
    assert _score([{"id": "open-greet", "v": "fail", "ded": -99}]) == 98
    assert _score([{"id": "open-greet", "v": "fail", "ded": 2}]) == 98
    assert _score([{"id": "open-greet", "v": "fail"}]) == 98


def test_real_failures_still_score_exactly_as_before():
    assert _score([
        {"id": "open-greet", "v": "fail", "ded": -2},
        {"id": "res-no-fake-close", "v": "fail", "ded": -15},
    ]) == 83


def test_pass_and_na_never_deduct():
    assert _score([
        {"id": "open-greet", "v": "pass", "ded": -2},
        {"id": "res-no-fake-close", "v": "n/a", "ded": -15},
    ]) == 100


def test_without_a_catalogue_the_models_numbers_are_used_as_before():
    # The manual re-scoring path passes no catalogue; behaviour there must not change.
    assert _compute_score([{"id": "whatever", "v": "fail", "ded": -20}], False)[0] == 80


def test_a_critical_fail_still_overrides_everything():
    assert _compute_score([], True, CATALOGUE) == (0, "Critical", "FAIL")


# ── a critical fail must come from a critical criterion ──────────────────────────
CRITICAL = frozenset({"crit-data-care", "crit-rg-care"})


def _score_c(criteria, critical_fail):
    return _compute_score(criteria, critical_fail, {**CATALOGUE, "crit-rg-care": 0,
                                                    "cf-friendly": 5}, CRITICAL)


def test_the_models_critical_flag_alone_cannot_zero_a_score():
    # 30 grades sat at 0/Critical; 24 had no critical criterion failing, and for 16 of those
    # the deductions alone would not have reached zero. The flag was doing the zeroing.
    assert _score_c([{"id": "cf-friendly", "v": "fail", "ded": -5}], True) == (95, "Excellent", "PASS")


def test_a_real_critical_criterion_still_zeroes_the_score():
    assert _score_c([{"id": "crit-rg-care", "v": "fail", "ded": 0}], True) == (0, "Critical", "FAIL")


def test_a_critical_criterion_zeroes_even_when_the_model_forgot_the_flag():
    # Derived from the verdicts, so a model that fails crit-rg-care but reports
    # critical_fail=false cannot smuggle a passing score through.
    assert _score_c([{"id": "crit-rg-care", "v": "fail", "ded": 0}], False) == (0, "Critical", "FAIL")


def test_a_passing_critical_criterion_is_not_a_critical_fail():
    assert _score_c([{"id": "crit-rg-care", "v": "n/a", "ded": 0},
                     {"id": "crit-data-care", "v": "pass", "ded": 0}], True)[0] == 100


def test_deductions_can_still_reach_zero_on_their_own():
    # This is the honest route to 0 — enough real failures, no flag involved.
    heavy = [{"id": "res-no-fake-close", "v": "fail", "ded": -15} for _ in range(7)]
    assert _score_c(heavy, False) == (0, "Critical", "FAIL")


def test_without_a_critical_set_the_flag_is_trusted_as_before():
    # The manual re-scoring path derives its own flag and passes no criterion ids.
    assert _compute_score([{"v": "fail", "ded": -5}], True) == (0, "Critical", "FAIL")
