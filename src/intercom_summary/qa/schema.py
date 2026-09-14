"""Structured shapes for a QA grade, plus the JSON schema we hand to Claude.

The grader asks Claude to call a single tool whose input matches GRADE_TOOL_SCHEMA, so we
get back validated, machine-readable grades instead of free text.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any

from intercom_summary.logging_setup import get_logger

log = get_logger(__name__)


def _compute_score(
    criteria: list[dict],
    critical_fail: bool,
    deductions: dict[str, int] | None = None,
    critical: "frozenset[str] | None" = None,
) -> tuple[int, str, str]:
    """Compute (score, band, overall_result) from the deduction-based criteria list.

    Done in code rather than trusting the model's arithmetic. Formula:
        score = max(0, 100 − sum of deductions for failed criteria)
    Critical fail overrides everything to 0/Critical/FAIL.

    `deductions` is the ruleset's catalogue (criterion id → points). When given it is the
    authority twice over:

    * a `fail` on an id the ruleset does not define is **ignored**. The model invents
      criteria — a client was docked 20 points by a `first-response-time` that exists in no
      prompt, no ruleset file and no catalogue, whose "evidence" was the prompt's own timing
      header. Eight such verdicts carried deductions of −1, −2, −9, −10, −20 and even +10:
      not a scale, a fresh number each time.
    * the points come from the catalogue, not from the model. It already agrees on 99.9% of
      real verdicts, so this changes almost nothing — it just removes the model's ability to
      choose how much a mistake costs.

    `critical` is the ruleset's set of criteria that zero a score. When given, a critical fail is
    **derived from the verdicts** and the model's own `critical_fail` boolean is ignored. The
    model sets that flag on its own initiative: of 30 grades stored at 0/Critical, 24 had no
    critical criterion failing at all, and for 16 of those the deductions alone would not have
    reached zero — the flag is what zeroed them. This mirrors `score_from_verdicts` below, which
    has always derived it this way, so an analyst's manual re-score and the AI score finally
    agree on what "critical" means.

    Omit `deductions` / `critical` only where no ruleset is in scope; the model's own numbers and
    flag are then used as before.
    """
    if critical is not None:
        derived = any(
            c.get("v") == "fail" and c.get("id") in critical for c in (criteria or [])
        )
        if critical_fail and not derived:
            log.warning(
                "Ignoring critical_fail=true — no critical criterion (%s) failed; "
                "scoring from deductions instead", ", ".join(sorted(critical)) or "none",
            )
        critical_fail = derived

    if critical_fail:
        return 0, "Critical", "FAIL"

    total_ded = 0
    for c in criteria or []:
        if c.get("v") != "fail":
            continue
        cid = c.get("id", "")
        if deductions is None:
            total_ded += abs(c.get("ded", 0))
            continue
        if cid not in deductions:
            log.warning("Ignoring fail on %r — not a criterion in this ruleset "
                        "(model-invented; deduction %s discarded)", cid, c.get("ded"))
            continue
        total_ded += abs(deductions[cid])
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
    from intercom_summary.qa.rulesets import SCORING_GATED, get_ruleset

    rs = get_ruleset(ruleset_id)

    if rs.scoring_model == SCORING_GATED:
        # The gated model cannot be expressed as a sum, so it is computed on the verdicts
        # themselves. The analyst's extra deduction comes off the finished score: it is an
        # explicit human judgement about something the AI could not see, so it is allowed to
        # take a chat below the model's own no-Major floor.
        from intercom_summary.qa.gated_scoring import score_gated

        criteria = [{"id": cid, "v": v} for cid, v in verdicts.items()]
        r = score_gated(criteria, rs)
        if extra_deduction and not r.critical_fail:
            score = max(0, r.score - abs(extra_deduction))
            result = "PASS" if score >= rs.pass_threshold else "FAIL"
            return score, r.band, result
        return r.score, r.band, r.result

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
    # QA Manual v4.1 (gated rulesets only; empty on flat-ruleset grades):
    case_type: str = ""                      # Step 0 — Deposit/Withdrawal/Bonus/KYC/...
    risk_flag: str = ""                      # Step 0 — None/Financial/RG/Security/Churn
    expected_handling: str = ""              # Step 0 — Answer/Agent action/Internal escalation
    data_sufficiency: str = ""               # Step 0 — Sufficient/Insufficient
    requests: list = field(default_factory=list)   # multi-intent coverage [{text,status,material}]
    outcome_status: str = ""                 # what became of the case, separate from the score
    severity: str = ""                       # Critical / Major / Minor — the worst thing that failed
    catastrophic_service_failure: bool = False   # NOT critical_fail; see qa/gated_scoring.py
    critical_fail: bool = False
    manual_review_needed: bool = False
    manual_review_reason: str = ""
    confidence: str = ""                     # High/Medium/Low — the model's own certainty

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
        from intercom_summary.qa.rulesets import SCORING_GATED, get_ruleset

        ruleset = get_ruleset(ruleset_id)
        titles = ruleset.titles

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

        # Recompute score from the verdicts — the model's arithmetic, its criterion ids and
        # its point values are all unreliable; the ruleset is the authority. Which formula
        # applies is the ruleset's own declaration: the two original rulesets are flat, the
        # v4.1 ruleset is gated (caps and floors, which no sum can express).
        gated = None
        if ruleset.scoring_model == SCORING_GATED:
            from intercom_summary.qa.gated_scoring import score_gated

            gated = score_gated(criteria, ruleset)
            score, band, result = gated.score, gated.band, gated.result
        else:
            score, band, result = _compute_score(
                criteria, critical_fail, ruleset.deductions, ruleset.critical
            )

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
        grade.confidence = _as_text(data.get("confidence", ""))

        if gated is not None:
            grade.severity = gated.severity
            grade.critical_fail = gated.critical_fail
            grade.catastrophic_service_failure = gated.catastrophic_service_failure
            grade.case_type = _as_text(data.get("case_type", ""))
            grade.risk_flag = _as_text(data.get("risk_flag", ""))
            grade.expected_handling = _as_text(data.get("expected_handling", ""))
            grade.data_sufficiency = _as_text(data.get("data_sufficiency", ""))
            grade.requests = [r for r in (data.get("requests") or []) if isinstance(r, dict)]
            # The model reports the outcome it saw, but a Gate 1 breach overrides it: the
            # manual pairs that score with exactly one status.
            grade.outcome_status = (
                "Critical Fail" if gated.critical_fail
                else _as_text(data.get("outcome_status", ""))
            )
            # Any verdict the model could not determine is a question for the QC manager,
            # whether or not the model thought to ask for one.
            undetermined = [
                c.get("id", "") for c in criteria if c.get("v") == "cannot_determine"
            ]
            grade.manual_review_needed = bool(
                data.get("manual_review_needed") or undetermined
                or _as_text(data.get("data_sufficiency", "")) == "Insufficient"
            )
            reason = _as_text(data.get("manual_review_reason", ""))
            if not reason and undetermined:
                reason = "Cannot determine: " + ", ".join(sorted(undetermined))
            grade.manual_review_reason = reason
        else:
            grade.critical_fail = bool(critical_fail and score == 0)
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
        g.case_type = d.get("case_type", "")
        g.risk_flag = d.get("risk_flag", "")
        g.expected_handling = d.get("expected_handling", "")
        g.data_sufficiency = d.get("data_sufficiency", "")
        g.requests = d.get("requests", []) or []
        g.outcome_status = d.get("outcome_status", "")
        g.severity = d.get("severity", "")
        g.catastrophic_service_failure = bool(d.get("catastrophic_service_failure"))
        g.critical_fail = bool(d.get("critical_fail"))
        g.manual_review_needed = bool(d.get("manual_review_needed"))
        g.manual_review_reason = d.get("manual_review_reason", "")
        g.confidence = d.get("confidence", "")
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
                        "verdict": {"type": "string", "enum": ["pass", "fail", "n/a", "cannot_determine"]},
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
