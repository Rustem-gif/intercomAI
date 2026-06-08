"""Structured shapes for a QA grade, plus the JSON schema we hand to Claude.

The grader asks Claude to call a single tool whose input matches GRADE_TOOL_SCHEMA, so we
get back validated, machine-readable grades instead of free text.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any


def _aggregate_score(scorecard: dict, has_critical_error: bool) -> tuple[int, str, str]:
    """Compute (weighted_score, band, overall_result) from per-dimension scores.

    Mirrors the prompt's methodology: weighted average over applicable (non-N/A)
    dimensions, renormalised by the weights that applied; capped at 39 on any critical
    error; then mapped to a band/PASS-FAIL. Done in code because the model is unreliable
    at the arithmetic even when its per-dimension scores are sound.
    """
    from intercom_summary.qa.casino_prompt import DIMENSION_WEIGHTS

    num = den = 0
    for dim, dd in (scorecard or {}).items():
        weight = DIMENSION_WEIGHTS.get(dim)
        if weight is None or not isinstance(dd, dict):
            continue
        raw = dd.get("score")
        try:
            sv = int(raw)
        except (TypeError, ValueError):
            continue  # "N/A" or non-numeric → excluded, weight renormalised out
        if 1 <= sv <= 5:
            num += sv * weight
            den += weight

    score = round((num / den) / 5 * 100) if den else 0
    if has_critical_error:
        score = min(score, 39)

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
    model: str = ""
    graded_at: str = ""
    # casino/iGaming QA enrichment (ollama backend only; empty for legacy grades):
    classification: dict = field(default_factory=dict)
    scorecard_raw: dict = field(default_factory=dict)
    overall_result: str = ""                 # "PASS" | "FAIL"
    band: str = ""                           # Excellent/Good/Acceptable/Poor/Critical

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
    def from_ollama_output(cls, conversation_id: str, agent_name: str, data: dict[str, Any]) -> "ConversationGrade":
        """Build a ConversationGrade from the casino QA JSON produced by the Ollama grader."""
        scorecard = data.get("scorecard", {})
        rule_results = []
        for dim_name, dim_data in scorecard.items():
            score = dim_data.get("score", "N/A")
            if score == "N/A":
                verdict = "n/a"
            else:
                verdict = "pass" if int(score) >= 3 else "fail"
            rule_results.append(
                RuleResult(
                    rule_id=dim_name,
                    title=dim_name.replace("_", " ").title(),
                    verdict=verdict,
                    evidence=_as_text(dim_data.get("evidence", "")),
                    comment=_as_text(dim_data.get("reasoning", "")),
                )
            )

        critical_errors = data.get("critical_errors") or []
        violations = [
            f"[CRITICAL] {e.get('type', '')}: {e.get('quote', '')}"
            for e in critical_errors
        ] + [
            f"[MAJOR] {i.get('dimension', '')}: {i.get('description', '')}"
            for i in data.get("major_issues", [])
        ]

        # Compute the weighted score deterministically from the per-dimension scores.
        # The model scores each dimension reliably but is unreliable at the arithmetic
        # (it often returns weighted_score=0), so we own the aggregation.
        score, band, result = _aggregate_score(scorecard, bool(critical_errors))

        grade = cls(
            conversation_id=conversation_id,
            agent_name=agent_name,
            overall_score=score,
            summary=_as_text(data.get("summary", "")),
            rule_results=rule_results,
            violations=[_as_text(v) for v in violations],
            suggestions=[_as_text(s) for s in data.get("improvement_actions", [])],
        )
        grade.classification = data.get("classification", {})
        grade.scorecard_raw = scorecard
        grade.overall_result = result
        grade.band = band
        return grade

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ConversationGrade":
        """Rebuild a grade from a stored payload dict (inverse of to_dict)."""
        g = cls.from_tool_input(d["conversation_id"], d.get("agent_name", ""), d)
        g.agent_email = d.get("agent_email", "")
        g.rules_version = d.get("rules_version", "")
        g.model = d.get("model", "")
        g.graded_at = d.get("graded_at", "")
        g.classification = d.get("classification", {})
        g.scorecard_raw = d.get("scorecard_raw", {})
        g.overall_result = d.get("overall_result", "")
        g.band = d.get("band", "")
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
