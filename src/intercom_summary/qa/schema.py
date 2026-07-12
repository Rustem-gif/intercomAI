"""Structured shapes for a QA grade, plus the JSON schema we hand to Claude.

The grader asks Claude to call a single tool whose input matches GRADE_TOOL_SCHEMA, so we
get back validated, machine-readable grades instead of free text.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any


def _compute_score(criteria: list[dict], critical_fail: bool) -> tuple[int, str, str]:
    """Compute (score, band, overall_result) from the deduction-based criteria list.

    Done in code rather than trusting the model's arithmetic. Formula:
        score = max(0, 100 − sum of absolute deductions for failed criteria)
    Critical fail overrides everything to 0/Critical/FAIL.
    """
    if critical_fail:
        return 0, "Critical", "FAIL"

    total_ded = sum(abs(c.get("ded", 0)) for c in (criteria or []) if c.get("v") == "fail")
    score = max(0, 100 - total_ded)

    if score >= 90:
        band, result = "Excellent", "PASS"
    elif score >= 75:
        band, result = "Good", "PASS"
    elif score >= 60:
        band, result = "Acceptable", "PASS"
    elif score >= 40:
        band, result = "Poor", "FAIL"
    else:
        band, result = "Critical", "FAIL"
    return score, band, result


def score_from_verdicts(
    verdicts: dict[str, str], extra_deduction: int = 0, ruleset_id: str | None = None
) -> tuple[int, str, str]:
    """Recompute (score, band, overall_result) from a {criterion_id: verdict} map using the
    canonical per-criterion deductions. Used for manual ScoreBuddy-style re-scoring: an
    analyst flips criteria pass↔fail and the score follows the same formula the AI uses.

    `extra_deduction` is an additional point total the analyst applies for things the AI
    cannot verify (e.g. information correctness — see the ruleset's manual_deductions); it is
    subtracted on top of the criteria deductions.

    `ruleset_id` must be the ruleset the grade was originally scored with (grades.ruleset_id),
    NOT the agent's current group: re-scoring an old standard grade for an agent who has since
    moved to VIP has to use the standard points, or the score would change under them.

    A FAIL on any critical criterion forces 0 (matches the grader's CRITICAL FAIL rule).
    """
    from intercom_summary.qa.rulesets import get_ruleset

    rs = get_ruleset(ruleset_id)
    deductions, critical = rs.deductions, rs.critical

    critical_fail = any(v == "fail" and cid in critical for cid, v in verdicts.items())
    criteria = [{"v": v, "ded": deductions.get(cid, 0)} for cid, v in verdicts.items()]
    if extra_deduction:
        criteria.append({"v": "fail", "ded": extra_deduction})
    return _compute_score(criteria, critical_fail)


def _as_text(value: Any) -> str:
    """Coerce a model-produced field to a string.

    Local models (Qwen et al.) sometimes return a field the schema declares as a
    string as a nested object or list instead (e.g. a structured ``summary``).
    Persisting that raw would crash the SQLite write ("type 'dict' is not supported"),
    so flatten anything non-scalar to readable text.
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, dict):
        return "; ".join(f"{k}: {_as_text(v)}" for k, v in value.items())
    if isinstance(value, (list, tuple)):
        return "; ".join(_as_text(v) for v in value)
    return str(value)


@dataclass
class RuleResult:
    rule_id: str
    title: str
    verdict: str           # "pass" | "fail" | "n/a"
    evidence: str = ""     # quote / reference from the conversation
    comment: str = ""


@dataclass
class ConversationGrade:
    conversation_id: str
    agent_name: str
    overall_score: int                       # 0-100
    summary: str
    rule_results: list[RuleResult] = field(default_factory=list)
    violations: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)
    # filled in by the grader, not the model:
    agent_email: str = ""
    rules_version: str = ""
    ruleset_id: str = "default"              # which ruleset scored this ('default' | 'vip')
    model: str = ""
    graded_at: str = ""
    # Analyst override of the AI's score, if any. Set when a grade is read back from the store;
    # a freshly produced grade has none. Reports score on `effective_score`, matching the
    # dashboard's COALESCE(human_score, overall_score).
    human_score: int | None = None
    overridden_by: str = ""
    # iGaming QA enrichment (ollama backend only; empty for legacy grades):
    classification: dict = field(default_factory=dict)   # kept for backwards compat
    scorecard_raw: dict = field(default_factory=dict)    # stores criteria dict keyed by id
    overall_result: str = ""                 # "PASS" | "FAIL"
    band: str = ""                           # Excellent/Good/Acceptable/Poor/Critical
    signal_flags: list = field(default_factory=list)     # active signal flags from the grade
    business_risk: str = ""                  # low/medium/high/critical

    @property
    def effective_score(self) -> int:
        """The score that counts: the analyst's override if there is one, else the AI's."""
        return self.human_score if self.human_score is not None else self.overall_score

    @property
    def is_overridden(self) -> bool:
        return self.human_score is not None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_tool_input(cls, conversation_id: str, agent_name: str, data: dict[str, Any]) -> "ConversationGrade":
        results = [
            RuleResult(
                rule_id=str(r.get("rule_id", "")),
                title=_as_text(r.get("title", "")),
                verdict=r.get("verdict", "n/a"),
                evidence=_as_text(r.get("evidence", "")),
                comment=_as_text(r.get("comment", "")),
            )
            for r in data.get("rule_results", [])
        ]
        return cls(
            conversation_id=conversation_id,
            agent_name=agent_name,
            overall_score=int(data.get("overall_score", 0) or 0),
            summary=_as_text(data.get("summary", "")),
            rule_results=results,
            violations=[_as_text(v) for v in data.get("violations", [])],
            suggestions=[_as_text(s) for s in data.get("suggestions", [])],
        )

    @classmethod
    def from_ollama_output(
        cls,
        conversation_id: str,
        agent_name: str,
        data: dict[str, Any],
        ruleset_id: str | None = None,
    ) -> "ConversationGrade":
        """Build a ConversationGrade from the deduction-based QA JSON produced by the Ollama grader."""
        from intercom_summary.qa.rulesets import get_ruleset

        titles = get_ruleset(ruleset_id).titles

        criteria = data.get("criteria") or []
        critical_fail = bool(data.get("critical_fail"))

        rule_results = [
            RuleResult(
                rule_id=c.get("id", ""),
                title=titles.get(c.get("id", ""), c.get("id", "").replace("-", " ").title()),
                verdict=c.get("v", "n/a"),
                evidence=_as_text(c.get("ev", "")),
                comment="",
            )
            for c in criteria
        ]

        # Recompute score from deductions — the model's arithmetic is unreliable.
        score, band, result = _compute_score(criteria, critical_fail)

        grade = cls(
            conversation_id=conversation_id,
            agent_name=agent_name,
            overall_score=score,
            summary=_as_text(data.get("summary", "")),
            rule_results=rule_results,
            violations=[_as_text(v) for v in data.get("violations", [])],
            suggestions=[_as_text(s) for s in data.get("coaching", [])],
        )
        grade.ruleset_id = ruleset_id or "default"
        grade.scorecard_raw = {c["id"]: c for c in criteria if "id" in c}
        grade.overall_result = result
        grade.band = band
        grade.signal_flags = data.get("flags") or []
        grade.business_risk = data.get("risk", "")
        return grade

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ConversationGrade":
        """Rebuild a grade from a stored payload dict (inverse of to_dict)."""
        g = cls.from_tool_input(d["conversation_id"], d.get("agent_name", ""), d)
        g.agent_email = d.get("agent_email", "")
        g.rules_version = d.get("rules_version", "")
        g.ruleset_id = d.get("ruleset_id", "default")
        # Carry the analyst override through, so reports built from stored grades score on it
        # rather than silently reporting the AI's superseded number.
        g.human_score = d.get("human_score")
        g.overridden_by = d.get("overridden_by") or ""
        g.model = d.get("model", "")
        g.graded_at = d.get("graded_at", "")
        g.classification = d.get("classification", {})
        g.scorecard_raw = d.get("scorecard_raw", {})
        g.overall_result = d.get("overall_result", "")
        g.band = d.get("band", "")
        g.signal_flags = d.get("signal_flags", [])
        g.business_risk = d.get("business_risk", "")
        return g


# Tool schema given to Claude (structured output).
GRADE_TOOL_SCHEMA: dict[str, Any] = {
    "name": "submit_grade",
    "description": "Submit the QA evaluation of a single support conversation.",
    "input_schema": {
        "type": "object",
        "properties": {
            "overall_score": {
                "type": "integer",
                "minimum": 0,
                "maximum": 100,
                "description": "Overall compliance score 0-100.",
            },
            "summary": {
                "type": "string",
                "description": "2-4 sentence summary of how the agent handled this conversation.",
            },
            "rule_results": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "rule_id": {"type": "string"},
                        "title": {"type": "string"},
                        "verdict": {"type": "string", "enum": ["pass", "fail", "n/a"]},
                        "evidence": {"type": "string", "description": "Short quote or reference."},
                        "comment": {"type": "string"},
                    },
                    "required": ["rule_id", "verdict"],
                },
            },
            "violations": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Concrete rule violations, most important first.",
            },
            "suggestions": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Actionable coaching suggestions for the agent.",
            },
        },
        "required": ["overall_score", "summary", "rule_results"],
    },
}
