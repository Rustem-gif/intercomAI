"""Derive v4.1 verdicts deterministically from what the model itself reported.

Calibration showed the model deciding pass/fail per criterion inconsistently: the same chat
came back Excellent on one run and Major on the next, and a grade could describe a problem in
its own summary while every criterion said pass. The scoring formula was never the issue — the
verdicts feeding it were. So the chain is now:

    detected problem → criterion fail → severity (the criterion's gate) → score → result

and the rules below enforce it against the model's own lower-level answers (its problem list,
its request list, its outcome status) and against facts code can see (internal notes, who
closed the chat, whether a knowledge base was supplied). Like `verdict_guard`, every change
returns a flag that ends up in `flags`, so nothing is corrected silently.

Order matters. `problems_to_fails` runs BEFORE `verdict_guard.apply_guards`, so a problem whose
"evidence" is a bot or player line is still thrown out by the guard. Everything else runs
after it, because those rules write evidence that is not an AGENT quote (a request text, a
description of what is missing) and the guard would otherwise mistake it for mis-attribution.
"""
from __future__ import annotations

from typing import Any

from intercom_summary.intercom.models import Conversation
from intercom_summary.logging_setup import get_logger

log = get_logger(__name__)

# Criteria whose PASS depends on evidence outside the conversation's words — the knowledge
# base, an internal note, backend data. A pass on one of these must name its source; a pass
# with nothing behind it is a guess, and the manual forbids guessing (point 4).
SOURCED_PASS_CRITERIA: frozenset[str] = frozenset({
    "accuracy-material", "accuracy-minor", "action-escalation-missed",
    "financial-case-abandoned", "severe-business-trust",
})
ACCURACY_CRITERIA: frozenset[str] = frozenset({"accuracy-material", "accuracy-minor"})

# Outcome statuses that say the player's issue was not dealt with.
_UNRESOLVED_OUTCOMES = frozenset({"Unresolved", "Abandoned-fake-closed"})


def _by_id(data: dict[str, Any]) -> dict[str, dict]:
    return {c.get("id"): c for c in data.get("criteria") or [] if isinstance(c, dict)}


def _set(data: dict[str, Any], cid: str, verdict: str, ev: str) -> dict:
    """Set a criterion's verdict, adding the entry if the model left it out."""
    entry = _by_id(data).get(cid)
    if entry is None:
        entry = {"id": cid}
        data.setdefault("criteria", []).append(entry)
    entry["v"], entry["ev"] = verdict, ev
    return entry


def problems_to_fails(data: dict[str, Any], known: set[str]) -> list[str]:
    """Every problem the model detected becomes a FAIL on the criterion it names.

    This is what stops a grade describing a defect and still scoring 100: the problem list is
    produced before any verdict, and writing a problem down is the same as failing it."""
    flags: list[str] = []
    current = _by_id(data)
    for p in data.get("problems") or []:
        if not isinstance(p, dict):
            continue
        cid, ev = p.get("id", ""), str(p.get("ev") or "")
        if cid not in known:
            continue
        before = (current.get(cid) or {}).get("v")
        if before == "fail":
            continue
        current[cid] = _set(data, cid, "fail", ev)
        flags.append(f"rule:{cid} {before or 'missing'} → fail — the model listed it as a "
                     f"detected problem")
    return flags


def _material_unresolved(data: dict[str, Any]) -> list[str]:
    return [str(r.get("text") or "") for r in data.get("requests") or []
            if isinstance(r, dict) and r.get("material") and r.get("status") == "unresolved"]


def _has_internal_note(conversation: Conversation) -> bool:
    return any(m.author_type == "admin" and m.part_type == "note" and (m.text or "").strip()
               for m in conversation.messages)


def _claims_escalation(data: dict[str, Any]) -> bool:
    return (data.get("expected_handling") == "Internal escalation"
            or data.get("outcome_status") == "Escalated correctly"
            or any(isinstance(r, dict) and r.get("status") == "escalated"
                   for r in data.get("requests") or []))


def derive_after_guards(conversation: Conversation, data: dict[str, Any], major: set[str],
                        has_kb: bool) -> list[str]:
    """The rules that read the model's request list and outcome, and the facts code can see.
    `major` is the ruleset's Gate 2 Major ids."""
    flags: list[str] = []
    crit = _by_id(data)

    def verdict(cid: str) -> str | None:
        return (crit.get(cid) or {}).get("v")

    def change(cid: str, new: str, ev: str, why: str) -> None:
        before = verdict(cid)
        crit[cid] = _set(data, cid, new, ev)
        flags.append(f"rule:{cid} {before or 'missing'} → {new} — {why}")
        log.info("Rule set %s %s→%s on %s: %s", cid, before, new, conversation.id, why)

    # b. A material request left unresolved IS resp-no-ghost, by the criterion's own wording.
    unresolved = _material_unresolved(data)
    if unresolved and verdict("resp-no-ghost") != "fail":
        change("resp-no-ghost", "fail",
               "Unresolved material request: " + "; ".join(unresolved)[:200],
               "a material request is marked unresolved")

    # c. The model's outcome says the issue was not dealt with, but no verdict says so.
    outcome = data.get("outcome_status")
    if outcome in _UNRESOLVED_OUTCOMES and not any(verdict(cid) == "fail" for cid in major):
        if outcome == "Abandoned-fake-closed" and conversation.closed_by == "admin":
            change("res-no-fake-close", "fail",
                   "Agent closed the chat; outcome Abandoned-fake-closed",
                   "the outcome is a fake close by the agent")
        elif verdict("resp-no-ghost") != "fail":
            change("resp-no-ghost", "cannot_determine",
                   f"Outcome {outcome} but no request marked unresolved",
                   f"outcome {outcome!r} contradicts the verdicts")

    # d. An escalation needs a system trace. The agent telling the player is not one.
    if (_claims_escalation(data) and not _has_internal_note(conversation)
            and verdict("action-escalation-missed") in ("pass", "n/a", None)):
        change("action-escalation-missed", "cannot_determine",
               "Escalation only stated to the player; no internal note",
               "escalation claimed with no internal note in the chat")

    # e. A pass that depends on outside evidence must name it.
    for cid in sorted(SOURCED_PASS_CRITERIA):
        if verdict(cid) == "pass" and not str(crit[cid].get("ev") or "").strip():
            change(cid, "cannot_determine", "Pass with no source cited",
                   "a pass on this criterion must cite its source")

    # f. Accuracy cannot pass with nothing to check it against.
    if not has_kb:
        for cid in sorted(ACCURACY_CRITERIA):
            if verdict(cid) == "pass":
                change(cid, "cannot_determine", "No knowledge base for this brand",
                       "no knowledge base to verify accuracy against")
    return flags


def check_verdicts(conversation: Conversation, data: dict[str, Any], ruleset,
                   has_kb: bool) -> list[str]:
    """The whole correction pass a grader runs on a raw response, in place, before the grade
    is built: problems → fails, the transcript guards, then the derivation rules. A flat
    ruleset gets the guards only. Returns every flag raised."""
    from intercom_summary.qa.rulesets import SCORING_GATED
    from intercom_summary.qa.verdict_guard import apply_guards

    if ruleset.scoring_model != SCORING_GATED:
        return apply_guards(conversation, data)
    flags = problems_to_fails(data, set(ruleset.deductions))
    flags += apply_guards(conversation, data)
    flags += derive_after_guards(conversation, data, set(ruleset.ids_in_gate(2, "major")),
                                 has_kb)
    return flags
