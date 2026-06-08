import pytest

from intercom_summary.qa.backends import get_grader
from intercom_summary.qa.ollama_grader import OllamaGrader, _is_valid_grade
from intercom_summary.qa.prompt import extract_grade_dict
from intercom_summary.qa.schema import ConversationGrade, _aggregate_score
from intercom_summary.qa.casino_prompt import DIMENSION_WEIGHTS


def test_factory_selects_backend():
    assert isinstance(get_grader("ollama"), OllamaGrader)
    with pytest.raises(RuntimeError):
        get_grader("nonsense")
    # Claude Code is no longer a valid backend.
    with pytest.raises(RuntimeError):
        get_grader("claude_code")


def test_ollama_output_coerces_non_string_fields():
    """Qwen sometimes returns summary/evidence as objects; these must become strings
    so the SQLite write doesn't fail with 'type dict is not supported'."""
    data = {
        "weighted_score": 80,
        "summary": {"strengths": "polite", "weaknesses": "slow"},  # object, not string
        "scorecard": {"empathy": {"score": 4, "evidence": {"turn": 2, "quote": "hi"}}},
        "improvement_actions": [{"action": "be faster"}],
        "critical_errors": [],
        "major_issues": [],
    }
    g = ConversationGrade.from_ollama_output("c1", "Ada", data)
    assert isinstance(g.summary, str) and "polite" in g.summary
    assert all(isinstance(s, str) for s in g.suggestions)
    assert all(isinstance(r.evidence, str) for r in g.rule_results)


def test_is_valid_grade_requires_scorecard():
    # A real grade has a populated scorecard; empty/wrong JSON must be rejected
    # so it is retried/skipped rather than saved as a bogus 0/100.
    assert _is_valid_grade({"scorecard": {"empathy": {"score": 4}}, "weighted_score": 80})
    assert _is_valid_grade({"scorecard": {"a": {"score": "5"}}})  # numeric string ok
    assert not _is_valid_grade({"scorecard": {}})
    assert not _is_valid_grade({"weighted_score": 0})
    assert not _is_valid_grade({})
    # all dimensions N/A = model declined to evaluate → not a usable grade
    assert not _is_valid_grade({"scorecard": {"a": {"score": "N/A"}, "b": {"score": "N/A"}}})


def test_dimension_schema_forces_numeric_score():
    # Regression: Qwen 2.5 14B, under grammar-constrained structured output, emitted the
    # `score` enum value BEFORE writing any reasoning and grabbed the lazy "N/A" for every
    # dimension — an all-N/A scorecard that collapses to 0/100, fails _is_valid_grade, and
    # gets the conversation skipped (~75% skip rate). The schema must (1) list reasoning &
    # evidence before score so the model commits to a number only after reasoning, and
    # (2) NOT offer "N/A" so the grammar forces a real 1-5 judgement.
    from intercom_summary.qa.casino_prompt import _DIMENSION_SCHEMA

    enum = _DIMENSION_SCHEMA["properties"]["score"]["enum"]
    assert "N/A" not in enum
    assert enum == ["1", "2", "3", "4", "5"]
    order = list(_DIMENSION_SCHEMA["properties"])
    assert order.index("score") > order.index("reasoning")
    assert order.index("score") > order.index("evidence")


def test_aggregate_score_computes_weighted_band():
    all5 = {d: {"score": "5"} for d in DIMENSION_WEIGHTS}
    assert _aggregate_score(all5, False) == (100, "Excellent", "PASS")
    all1 = {d: {"score": "1"} for d in DIMENSION_WEIGHTS}
    assert _aggregate_score(all1, False) == (20, "Critical", "FAIL")
    # N/A dimensions are renormalised out (all-3 except some N/A still averages to 3 -> 60)
    mixed = {d: {"score": "3"} for d in DIMENSION_WEIGHTS}
    mixed["efficiency"] = {"score": "N/A"}
    assert _aggregate_score(mixed, False)[0] == 60
    # Critical error caps the score at 39 / FAIL even with perfect dimensions.
    assert _aggregate_score(all5, True) == (39, "Critical", "FAIL")


def test_from_ollama_output_uses_computed_score_not_model_arithmetic():
    # Model returns weighted_score=0 (bad math) but real per-dimension scores -> we recompute.
    data = {
        "weighted_score": 0, "overall_result": "PASS", "summary": "ok",
        "scorecard": {d: {"score": "5", "reasoning": "", "evidence": ""} for d in DIMENSION_WEIGHTS},
        "critical_errors": [], "major_issues": [], "improvement_actions": [],
    }
    g = ConversationGrade.from_ollama_output("c1", "Ada", data)
    assert g.overall_score == 100 and g.overall_result == "PASS" and g.band == "Excellent"


def test_extract_grade_dict_variants():
    assert extract_grade_dict('{"overall_score": 90, "summary": "s", "rule_results": []}')["overall_score"] == 90
    fenced = 'sure:\n```json\n{"overall_score": 60, "summary": "s", "rule_results": []}\n```'
    assert extract_grade_dict(fenced)["overall_score"] == 60
    assert extract_grade_dict('noise {"overall_score": 1, "summary":"", "rule_results":[]} tail')["overall_score"] == 1
