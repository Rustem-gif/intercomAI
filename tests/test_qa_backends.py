import pytest

from intercom_summary.qa.backends import get_grader
from intercom_summary.qa.ollama_grader import OllamaGrader, _is_valid_grade
from intercom_summary.qa.prompt import extract_grade_dict
from intercom_summary.qa.schema import ConversationGrade, _compute_score


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
        "overall_score": 90,
        "critical_fail": False,
        "summary": {"strengths": "polite", "weaknesses": "slow"},  # object, not string
        "criteria": [{"id": "open-greet", "v": "pass", "ded": 0, "ev": {"turn": 1, "quote": "hi"}}],
        "flags": [],
        "risk": "low",
        "violations": [],
        "coaching": [{"action": "be faster"}],
        "confidence": "High",
    }
    g = ConversationGrade.from_ollama_output("c1", "Ada", data)
    assert isinstance(g.summary, str) and "polite" in g.summary
    assert all(isinstance(s, str) for s in g.suggestions)
    assert all(isinstance(r.evidence, str) for r in g.rule_results)


def test_is_valid_grade_requires_criteria():
    # A real grade has a populated criteria list with at least one evaluated item.
    assert _is_valid_grade({"criteria": [{"v": "pass", "ded": 0, "id": "open-greet", "ev": "hi"}]})
    assert _is_valid_grade({"criteria": [{"v": "fail", "ded": -8, "id": "res-effort", "ev": "no effort"}]})
    assert _is_valid_grade({"criteria": [{"v": "n/a", "ded": 0, "id": "crit-rg-care", "ev": "n/a"}]})
    assert not _is_valid_grade({"criteria": []})
    assert not _is_valid_grade({"weighted_score": 0})
    assert not _is_valid_grade({})


def test_compute_score_deduction_based():
    # No fails → 100 / Excellent / PASS
    assert _compute_score([], False) == (100, "Excellent", "PASS")
    # One high-severity fail
    assert _compute_score([{"v": "fail", "ded": -15}], False) == (85, "Good", "PASS")
    # Multiple fails that drop below 90
    criteria = [{"v": "fail", "ded": -10}, {"v": "fail", "ded": -8}]
    assert _compute_score(criteria, False) == (82, "Good", "PASS")
    # Pass and n/a items contribute 0 deduction
    mixed = [{"v": "pass", "ded": 0}, {"v": "n/a", "ded": 0}, {"v": "fail", "ded": -5}]
    assert _compute_score(mixed, False) == (95, "Excellent", "PASS")
    # Score cannot go below 0
    heavy = [{"v": "fail", "ded": -100}]
    assert _compute_score(heavy, False)[0] == 0
    # Critical fail overrides everything regardless of deductions
    assert _compute_score([], True) == (0, "Critical", "FAIL")
    assert _compute_score([{"v": "pass", "ded": 0}], True) == (0, "Critical", "FAIL")


def test_from_ollama_output_recomputes_score():
    # Model might return a wrong overall_score — we ignore it and recompute from deductions.
    data = {
        "overall_score": 999,  # bogus value that should be ignored
        "critical_fail": False,
        "criteria": [
            {"id": "open-greet", "v": "pass", "ded": 0, "ev": "Hi there"},
            {"id": "res-no-fake-close", "v": "fail", "ded": -15, "ev": "closed without resolution"},
        ],
        "flags": ["fake_closure_signal"],
        "risk": "high",
        "violations": ["Fake closure detected"],
        "summary": "Agent closed without resolving the issue.",
        "coaching": ["Confirm resolution before closing."],
        "confidence": "High",
    }
    g = ConversationGrade.from_ollama_output("c1", "Ada", data)
    assert g.overall_score == 85  # 100 - 15, not the bogus 999
    assert g.band == "Good"
    assert g.overall_result == "PASS"
    assert g.signal_flags == ["fake_closure_signal"]
    assert g.business_risk == "high"
    assert g.rule_results[0].title == "Greeting"
    assert g.rule_results[1].title == "No Fake Closure"
    assert g.rule_results[1].verdict == "fail"


def test_from_ollama_output_critical_fail():
    data = {
        "overall_score": 0,
        "critical_fail": True,
        "criteria": [
            {"id": "crit-data-care", "v": "fail", "ded": 0, "ev": "asked for password"},
        ],
        "flags": [],
        "risk": "critical",
        "violations": ["Agent asked for password"],
        "summary": "Critical data security failure.",
        "coaching": ["Never request passwords."],
        "confidence": "High",
    }
    g = ConversationGrade.from_ollama_output("c2", "Bob", data)
    assert g.overall_score == 0
    assert g.band == "Critical"
    assert g.overall_result == "FAIL"


def test_ollama_stamps_reported_rules_version(monkeypatch):
    """Regression: the version stamped on a saved grade must equal the version the
    grader reports via .rules_version. These previously diverged (the save used the
    support_rules.md hash while the property returns the qa_system_prompt.txt hash),
    which made every Ollama grade count as 'stale' and forced needless re-grading."""
    import json
    from datetime import datetime, timezone

    from intercom_summary.intercom.models import Admin, Contact, Conversation, Message

    grader = get_grader("ollama")
    valid = json.dumps({
        "overall_score": 90,
        "critical_fail": False,
        "criteria": [{"id": "open-greet", "v": "pass", "ded": 0, "ev": "Hi there"}],
        "flags": [],
        "risk": "low",
        "violations": [],
        "summary": "Good chat.",
        "coaching": [],
        "confidence": "High",
    })
    monkeypatch.setattr(grader, "_call", lambda transcript, temp: valid)

    conv = Conversation(
        id="c1", created_at=datetime(2026, 5, 1, tzinfo=timezone.utc), updated_at=None,
        state="closed", subject="Login",
        assignee=Admin(id="1", name="Ada", email="ada@co.com"),
        contact=Contact(name="Cara"),
        messages=[Message(0, "admin", "Ada", None, "Hi")],
    )
    grade = grader.grade(conv)
    assert grade.rules_version == grader.rules_version


def test_extract_grade_dict_variants():
    assert extract_grade_dict('{"overall_score": 90, "summary": "s", "rule_results": []}')["overall_score"] == 90
    fenced = 'sure:\n```json\n{"overall_score": 60, "summary": "s", "rule_results": []}\n```'
    assert extract_grade_dict(fenced)["overall_score"] == 60
    assert extract_grade_dict('noise {"overall_score": 1, "summary":"", "rule_results":[]} tail')["overall_score"] == 1
