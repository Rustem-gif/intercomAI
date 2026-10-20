"""The VIP QA Scorecard v1.6 scoring model (`vip-gated`).

    Critical failed                → 0 (provisional — only a reviewer makes it final), FAIL
    otherwise   score = cap − help quality − process − min(6, style),   never below 0
                cap   = 100, then 75, 65, 55, 45 for 1, 2, 3, 4+ independent Majors
    PASS        score ≥ 85 and no Critical (any Major caps at 75, so a Major is always a FAIL)
    service_failure_flag  at 3+ independent Majors — reported, does not change the score

Unlike v4.1 (qa/gated_scoring.py) there is no floor and no catastrophic rule, and the Major cap
steps down: one missed follow-up and "player ignored + Payments ignored + escalation missed"
must not score the same. Independent Majors are counted as distinct failed Major criteria —
one verdict per criterion, so "one cause with several consequences" is one Major by
construction.

Two verdicts exist here that v4.1 does not have:

* `partial` — the four two-level help-quality criteria (q-clarity −7/−14 and the −4/−8 ones)
  carry a `partial_deduction`; `fail` costs the full one.
* `not_assessable` — set in code, never by the model: a `cannot_determine` on a criterion whose
  evidence lives outside Intercom (`scope: external`), or on a timing criterion while no shift
  schedule is configured. The grader only sees Intercom, so those questions are expected to be
  open; unlike `cannot_determine` they cost nothing AND send nothing to manual review.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from intercom_summary.logging_setup import get_logger
from intercom_summary.qa.rulesets import QaRuleset

log = get_logger(__name__)

NOT_ASSESSABLE = "not_assessable"


@dataclass
class VipResult:
    score: int
    band: str
    result: str                               # "PASS" | "FAIL"
    severity: str = ""                        # Critical | Major | Minor | ""
    critical_fail: bool = False
    critical_ids: list[str] = field(default_factory=list)
    intent_check: list[str] = field(default_factory=list)   # failed crits that need intent
    major_ids: list[str] = field(default_factory=list)
    service_failure_flag: bool = False
    breakdown: dict = field(default_factory=dict)          # cap / quality / process / style


def mark_not_assessable(criteria: list[dict], ruleset: QaRuleset,
                        shifts_known: bool) -> list[str]:
    """Re-label, in place, the `cannot_determine` verdicts the grader could never have
    answered from Intercom. Returns the ids re-labelled."""
    meta = {c["id"]: c for c in ruleset.criteria}
    out: list[str] = []
    for c in criteria or []:
        if not isinstance(c, dict) or c.get("v") != "cannot_determine":
            continue
        m = meta.get(c.get("id", ""))
        if not m:
            continue
        if m.get("scope") == "external" or (m.get("timing") and not shifts_known):
            c["v"] = NOT_ASSESSABLE
            out.append(c["id"])
    return out


def _verdicts(criteria: list[dict], known: set[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for c in criteria or []:
        cid = c.get("id", "")
        if cid not in known:
            if c.get("v") in ("fail", "partial"):
                log.warning("Ignoring %s on %r — not a criterion in this ruleset", c.get("v"), cid)
            continue
        out[cid] = c.get("v", "n/a")
    return out


def score_vip(criteria: list[dict], ruleset: QaRuleset) -> VipResult:
    meta = {c["id"]: c for c in ruleset.criteria}
    verdicts = _verdicts(criteria, set(meta))
    scoring = ruleset.scoring
    pass_threshold = int(scoring.get("pass_threshold", 85))
    caps = [int(x) for x in scoring.get("major_caps") or [75, 65, 55, 45]]
    service_failure_at = int(scoring.get("service_failure_majors", 3))

    def cost(cid: str) -> int:
        """Points a verdict costs. `partial` is only honoured where the catalogue defines a
        partial deduction; elsewhere the model has misused it and it is read as a fail."""
        v, m = verdicts[cid], meta[cid]
        if v == "fail":
            return abs(int(m.get("deduction", 0)))
        if v == "partial":
            return abs(int(m.get("partial_deduction", m.get("deduction", 0))))
        return 0

    failing = [cid for cid, v in verdicts.items() if v in ("fail", "partial")]

    # ── Gate 1 — Critical: provisional 0 ──────────────────────────────────────────────
    critical = [cid for cid in failing if meta[cid].get("critical")]
    if critical:
        intent = [cid for cid in critical if meta[cid].get("intent")]
        return VipResult(0, "Critical", "FAIL", "Critical", critical_fail=True,
                         critical_ids=critical, intent_check=intent,
                         breakdown={"cap": 0, "quality": 0, "process": 0, "style": 0})

    groups = ruleset.groups
    majors = [cid for cid in failing
              if int(meta[cid].get("gate", 0)) == 2 and meta[cid].get("severity") == "major"]
    quality = sum(cost(cid) for cid in failing
                  if int(meta[cid].get("gate", 0)) == 2 and meta[cid].get("severity") != "major")
    process = sum(cost(cid) for cid in failing
                  if int(meta[cid].get("gate", 0)) == 3 and cid not in groups)
    style = 0
    for group, group_cap in ruleset.group_caps.items():
        style += min(group_cap, sum(cost(cid) for cid in failing if groups.get(cid) == group))

    cap = 100 if not majors else caps[min(len(majors), len(caps)) - 1]
    score = max(0, cap - quality - process - style)
    result = "PASS" if score >= pass_threshold else "FAIL"

    if majors:
        severity, band = "Major", "Major Failure"
    else:
        severity = "Minor" if failing else ""
        band = "Excellent" if score >= 95 else "Good" if result == "PASS" else "Needs Work"
    return VipResult(
        score, band, result, severity, major_ids=majors,
        service_failure_flag=len(majors) >= service_failure_at,
        breakdown={"cap": cap, "quality": quality, "process": process, "style": style},
    )
