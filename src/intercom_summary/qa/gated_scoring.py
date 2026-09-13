"""The QA Manual v4.1 scoring model — three gates, caps and floors.

The flat model (`qa/schema.py:_compute_score`) treats every criterion as a subtraction, so
enough small untidiness can outweigh failing the player outright. v4.1 refuses that: a failed
outcome must always cost more than any amount of process noise, and a real compliance incident
must always cost more than a failed outcome. That ordering is what the caps and the floor are
for — they are not decoration on a sum, they are the model.

    Gate 1  a real compliance / security / RG incident  → 0
    Gate 2  the player did not get their outcome         → cap 75 (flat: one Major == three)
    Gate 3  process and tone                             → ordinary deductions off the cap

Two guardrails from the flat model are kept deliberately, because they were written against
real misbehaviour: a `fail` on an id the catalogue does not define is ignored (the model
invents criteria), and the points always come from the catalogue, never from the model.
"""
from __future__ import annotations

from dataclasses import dataclass

from intercom_summary.logging_setup import get_logger
from intercom_summary.qa.rulesets import QaRuleset

log = get_logger(__name__)

# Severity ranking, worst first — used to report the single worst thing that happened.
SEVERITY_CRITICAL = "Critical"
SEVERITY_MAJOR = "Major"
SEVERITY_MINOR = "Minor"


@dataclass
class GatedResult:
    score: int
    band: str
    result: str                            # "PASS" | "FAIL"
    severity: str = ""                     # Critical | Major | Minor | "" when nothing failed
    critical_fail: bool = False
    catastrophic_service_failure: bool = False


def _failed_ids(criteria: list[dict], known: set[str]) -> set[str]:
    """Ids the model marked `fail` that the ruleset actually defines.

    `pass`, `n/a` and `cannot_determine` are all score-neutral: only a proven failure costs
    anything, and `cannot_determine` exists precisely so the model never has to guess a fail.
    """
    out: set[str] = set()
    for c in criteria or []:
        if c.get("v") != "fail":
            continue
        cid = c.get("id", "")
        if cid not in known:
            log.warning("Ignoring fail on %r — not a criterion in this ruleset", cid)
            continue
        out.add(cid)
    return out


def score_gated(criteria: list[dict], ruleset: QaRuleset) -> GatedResult:
    """Compute the v4.1 score from the criterion verdicts. See the module docstring."""
    deductions = ruleset.deductions
    failed = _failed_ids(criteria, set(deductions))

    scoring = ruleset.scoring
    pass_threshold = int(scoring.get("pass_threshold", 85))
    major_cap = int(scoring.get("major_cap", 75))
    no_major_floor = int(scoring.get("no_major_floor", major_cap + 1))
    catastrophic_score = int(scoring.get("catastrophic_score", 25))

    # ── Gate 1 — a real incident stops the evaluation ────────────────────────────────
    # Derived from the verdicts, never from the model's own critical_fail boolean: it sets
    # that flag on its own initiative (24 of 30 zeroed grades had no critical criterion
    # failing at all), so the flag is not evidence of anything.
    if failed & ruleset.critical:
        return GatedResult(0, "Critical", "FAIL", SEVERITY_CRITICAL, critical_fail=True)

    groups = ruleset.groups
    major_ids = set(ruleset.ids_in_gate(2, "major"))
    quality_ids = set(ruleset.ids_in_gate(2, "minor"))
    gate3 = ruleset.ids_in_gate(3)
    process_ids = {cid for cid in gate3 if cid not in groups}

    major = bool(failed & major_ids)
    cap = major_cap if major else 100

    quality = sum(abs(deductions[cid]) for cid in failed & quality_ids)
    process = sum(abs(deductions[cid]) for cid in failed & process_ids)

    # Style penalties are capped as a block, not individually — the manual's
    # "together no more than −5".
    style = 0
    for group, group_cap in ruleset.group_caps.items():
        raw = sum(abs(deductions[cid]) for cid in failed if groups.get(cid) == group)
        style += min(group_cap, raw)
    style_maxed = all(
        sum(abs(deductions[cid]) for cid in failed if groups.get(cid) == group) >= group_cap
        for group, group_cap in ruleset.group_caps.items()
    ) if ruleset.group_caps else False

    # ── Catastrophic service failure — everything at once, but NOT a compliance breach ──
    # Kept separate from critical_fail on purpose: both look terrible in a report, but they
    # are different problems with different consequences, so they must not collapse into the
    # same score of 0. Only the complete combination qualifies; a partial one scores normally.
    if major and process_ids and process_ids <= failed and style_maxed:
        return GatedResult(
            catastrophic_score, "Catastrophic", "FAIL", SEVERITY_MAJOR,
            catastrophic_service_failure=True,
        )

    score = cap - quality - process - style
    if not major:
        # Without a failed outcome the score never drops below the Major cap, so a chat that
        # served the player well but untidily can never rank below one that failed them.
        score = max(score, no_major_floor)
    score = max(0, score)

    if major:
        severity = SEVERITY_MAJOR
    elif failed:
        severity = SEVERITY_MINOR
    else:
        severity = ""

    result = "PASS" if score >= pass_threshold else "FAIL"
    if major:
        band = "Major Failure"
    elif score >= 95:
        band = "Excellent"
    elif result == "PASS":
        band = "Good"
    else:
        band = "Needs Work"
    return GatedResult(score, band, result, severity)
