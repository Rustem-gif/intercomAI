"""Derive v4.1 verdicts deterministically from what the model itself reported.

Calibration showed the model deciding pass/fail per criterion inconsistently: the same chat
came back Excellent on one run and Major on the next, and a grade could describe a problem in
its own summary while every criterion said pass. The scoring formula was never the issue — the
verdicts feeding it were. So the chain is now:

    detected problem → criterion fail → severity (the criterion's gate) → score → result

and the rules below enforce it against the model's own lower-level answers (its problem list,
its request list, its outcome status) and against facts code can see (internal notes, who
closed the chat). Like `verdict_guard`, every change
returns a flag that ends up in `flags`, so nothing is corrected silently.

Order matters. `problems_to_fails` runs BEFORE `verdict_guard.apply_guards`, so a problem whose
"evidence" is a bot or player line is still thrown out by the guard. Everything else runs
after it, because those rules write evidence that is not an AGENT quote (a request text, a
description of what is missing) and the guard would otherwise mistake it for mis-attribution.
"""
from __future__ import annotations

import re
from typing import Any

from intercom_summary.intercom.models import Conversation
from intercom_summary.logging_setup import get_logger

log = get_logger(__name__)

# Criteria whose PASS must name the system trace behind it (client point 5: an escalation is
# proven by an internal note, never by the agent's word). This once also covered accuracy,
# financial-case-abandoned and severe-business-trust, and accuracy went cannot_determine with no
# knowledge base (client points 4 and 6); both were withdrawn on 2026-10-08 because they sent
# ~85% of the calibration sample to CANNOT DETERMINE.
SOURCED_PASS_CRITERIA: frozenset[str] = frozenset({"action-escalation-missed"})

# Criteria whose FAIL always goes to a QC manager, whatever the model said about manual review.
# QA (2026-10-08): a churn signal the agent did not react to is exactly the case they want to see
# by hand. Since 2026-10-09 a churn signal is only an explicit "I'm leaving / close my account /
# I play elsewhere" — dissatisfaction alone no longer counts (see the prompt's CHURN VERSUS RG).
MANUAL_REVIEW_ON_FAIL: frozenset[str] = frozenset({"churn-rg-signal-ignored"})

# The narrative (`what_happened`) and the verdict are made separately — the verdict from the
# criteria, the narrative from the model's prose — and QA has seen them part ways: "no mistakes
# by the agent" over a FAIL, or "two messages left unanswered" over a PASS (QA feedback,
# Oct 2026). The verdict is not changed on wording alone; the chat goes to a QC manager.
_PASS_CONFLICT = re.compile(
    r"\bno agent (?:reply|response|answer)\b"
    r"|\bwithout (?:an? |any )?agent(?:'s)? (?:reply|response|answer)\b"
    r"|\bagent (?:never|did not|didn't|failed to) (?:answer|reply|respond|acknowledge|pass|escalate|forward)\b"
    r"|\b(?:player|customer)(?:['’]s)? (?:(?:last|final|two|three|further|other|own) )*(?:messages?|questions?|requests?)\b"
    r"[^.]{0,60}?\b(?:went |was |were |remained |left )?unanswered\b"
    r"|\bnot (?:answered|passed on|escalated|forwarded) (?:by the agent|before the chat (?:was )?closed)\b",
    re.I)
_FAIL_CONFLICT = re.compile(
    r"\bno (?:agent )?(?:errors?|mistakes?|defects?)\b"
    r"|\bhandled (?:it |this |the (?:case|request|chat) )?correctly\b"
    r"|\bdid everything (?:right|correctly)\b"
    r"|\bagent (?:acted|responded|reacted) correctly\b", re.I)


def narrative_conflict(narrative: str, result: str) -> str | None:
    """The phrase in the narrative that contradicts a PASS or a FAIL, if any."""
    pattern = _PASS_CONFLICT if result == "PASS" else _FAIL_CONFLICT if result == "FAIL" else None
    m = pattern.search(narrative or "") if pattern else None
    return m.group(0) if m else None

# Minor criteria that, when failed, say the agent left part of a request open themselves — so an
# unresolved request in that chat is the agent's, not one waiting on the player.
_EXPLAINS_OPEN_REQUEST = frozenset({"info-completeness", "ownership-effort"})

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


def _unresolved(data: dict[str, Any]) -> list[dict]:
    return [r for r in data.get("requests") or []
            if isinstance(r, dict) and r.get("status") == "unresolved"]


# A player's closing pleasantry needs no reply, so leaving it unanswered is not ghosting.
_COURTESY = re.compile(
    r"^\W*(?:ok(?:ay)?|thanks?|thank you|thx|ty|cheers|bye|goodbye|good ?night|great|perfect|"
    r"cool|alright|fine|danke|vielen dank|tsch[uü]ss)\b[\w\s!.,:;)(❤🙏😊🙂👍-]{0,30}$",
    re.IGNORECASE,
)


def _is_courtesy(text: str | None) -> bool:
    return bool(_COURTESY.match(" ".join((text or "").split())))


def _has_internal_note(conversation: Conversation) -> bool:
    return bool(_internal_notes(conversation))


def _internal_notes(conversation: Conversation) -> list[str]:
    return [m.text.strip() for m in conversation.messages
            if m.author_type == "admin" and m.part_type == "note" and (m.text or "").strip()]


def _escalation_required(data: dict[str, Any]) -> bool:
    """The model's Step 0 answer, read against the MANDATORY ESCALATION list in the prompt."""
    return data.get("expected_handling") == "Internal escalation"


def _agent_claimed_escalation(data: dict[str, Any]) -> bool:
    """Something in the model's own answer says the case was passed on."""
    aem = _by_id(data).get("action-escalation-missed") or {}
    return (data.get("outcome_status") == "Escalated correctly"
            or any(isinstance(r, dict) and r.get("status") == "escalated"
                   for r in data.get("requests") or [])
            or (aem.get("v") == "pass" and bool(str(aem.get("ev") or "").strip())))


def derive_after_guards(conversation: Conversation, data: dict[str, Any],
                        major: set[str]) -> list[str]:
    """The rules that read the model's request list and outcome, and the facts code can see.
    `major` is the ruleset's Gate 2 Major ids."""
    from intercom_summary.qa.prompt import closing_clause

    flags: list[str] = []
    crit = _by_id(data)

    def verdict(cid: str) -> str | None:
        return (crit.get(cid) or {}).get("v")

    def change(cid: str, new: str, ev: str, why: str) -> None:
        before = verdict(cid)
        crit[cid] = _set(data, cid, new, ev)
        flags.append(f"rule:{cid} {before or 'missing'} → {new} — {why}")
        log.info("Rule set %s %s→%s on %s: %s", cid, before, new, conversation.id, why)

    # b. Who stopped responding decides what an unresolved request means (QA, 2026-10-09).
    #    The model marked requests `unresolved` both when the agent ignored the player and when
    #    the player went silent after the agent asked them for something; the old rule failed
    #    both. The comment order tells them apart.
    ending = conversation.ending
    unresolved = _unresolved(data)
    ghost_ids = {"resp-no-ghost", "res-no-fake-close"}
    if unresolved and ending["last_role"] == "CUSTOMER":
        # The player wrote last and nobody answered. Ghosting the player's last messages is a
        # Major whatever their topic — unless they were only a "thanks", or the agent never
        # wrote at all and nothing material was at stake (spam, a lead's stray paste).
        unanswered = ending["unanswered_player"]
        ghosted = (not all(_is_courtesy(m.text) for m in unanswered)) and ending["agent_ever_wrote"]
        hit = [r for r in unresolved if r.get("material") or ghosted]
        if hit and verdict("resp-no-ghost") != "fail":
            times = ", ".join(m.created_at.strftime("%H:%M:%S") if m.created_at else "?"
                              for m in unanswered) or "?"
            texts = "; ".join(str(r.get("text") or "") for r in hit)
            change("resp-no-ghost", "fail",
                   f"The player's last message(s) ({times}) got no agent reply before "
                   f"{closing_clause(conversation)}: {texts}"[:240],
                   "a request is unresolved and the player's last messages went unanswered")
    elif unresolved and ending["last_role"] == "AGENT":
        # Only when the model found nothing that explains the open request: a failed Major (a
        # handoff not made) or a skipped part of the answer means it really is unresolved.
        listed = {p.get("id") for p in data.get("problems") or [] if isinstance(p, dict)}
        explains = ghost_ids | major | _EXPLAINS_OPEN_REQUEST
        if not (listed & ghost_ids) and all(verdict(cid) != "fail" for cid in explains):
            # The agent did their step and the player went quiet: the case waits on the player
            # (or a team), which is never the agent's failure. Verdicts are left alone; rule c
            # still sends an "Unresolved" outcome to a QC manager.
            for r in unresolved:
                r["status"] = "pending"
                flags.append(f"rule:requests \"{str(r.get('text') or '')[:60]}\" unresolved → "
                             f"pending — the agent wrote last; the player did not reply")
        else:
            # Something the agent did (or skipped) leaves the request open. A MATERIAL request
            # left open is resp-no-ghost by the criterion's own wording, however the model filed
            # it — unless a Major already covers it (one mistake, one penalty).
            material = [str(r.get("text") or "") for r in unresolved if r.get("material")]
            if material and not any(verdict(cid) == "fail" for cid in major):
                change("resp-no-ghost", "fail",
                       "Unresolved material request: " + "; ".join(material)[:200],
                       "a material request is marked unresolved and the agent left it open")

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

    # d / f. An escalation needs a system trace; a required one that never happened is a fail.
    #    The agent telling the player is not a trace (d). A case on the prompt's MANDATORY
    #    ESCALATION list that the agent neither passed on nor said they passed on is a missed
    #    escalation (f, QA 2026-10-09: the model used to decide on its own that none was needed).
    #    A model `cannot_determine` is the "unsure → QC manager" path and is left alone; a model
    #    `fail` is never overturned because some note exists — notes are free text.
    if not _has_internal_note(conversation) and verdict("action-escalation-missed") in (
            "pass", "n/a", None):
        claimed = _agent_claimed_escalation(data)
        required = _escalation_required(data)
        if claimed or (required and outcome == "Pending-legitimate"):
            change("action-escalation-missed", "cannot_determine",
                   "Escalation only stated to the player; no internal note",
                   "escalation claimed with no internal note in the chat")
        elif required:
            case = str(data.get("escalation_case") or "").strip() or "internal escalation expected"
            change("action-escalation-missed", "fail",
                   f"Required escalation not performed ({case}): the agent answered or closed "
                   f"the chat without passing it on, and no internal note shows a handoff"[:240],
                   "a mandatory escalation was neither performed nor claimed")

    # e. A pass that depends on a system trace must name it. When the chat has an internal
    #    note, the note is that trace: cite it rather than sending the chat to manual review.
    notes = _internal_notes(conversation)
    for cid in sorted(SOURCED_PASS_CRITERIA):
        if verdict(cid) == "pass" and not str(crit[cid].get("ev") or "").strip():
            if notes:
                crit[cid]["ev"] = "note: " + " ".join(notes[0].split()[:15])
                flags.append(f"rule:{cid} pass — cited the internal note in the chat")
            else:
                change(cid, "cannot_determine", "Pass with no source cited",
                       "a pass on this criterion must cite its source")
    return flags


def check_verdicts(conversation: Conversation, data: dict[str, Any], ruleset) -> list[str]:
    """The whole correction pass a grader runs on a raw response, in place, before the grade
    is built: problems → fails, the transcript guards, then the derivation rules. A flat
    ruleset gets the guards only. Returns every flag raised."""
    from intercom_summary.qa.rulesets import SCORING_GATED
    from intercom_summary.qa.verdict_guard import apply_guards

    if ruleset.scoring_model != SCORING_GATED:
        return apply_guards(conversation, data)
    flags = problems_to_fails(data, set(ruleset.deductions))
    flags += apply_guards(conversation, data)
    flags += derive_after_guards(conversation, data, set(ruleset.ids_in_gate(2, "major")))
    return flags
