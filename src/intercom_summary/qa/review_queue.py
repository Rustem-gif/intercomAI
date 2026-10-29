"""Why a chat is in the QC manager's review queue (the Needs Attention page).

QA (2026-10-28) asked for every chat with an internal note to be checked by hand until agents
are used to writing them — from 1 November a note is how an escalation is confirmed — and for
the other problem chats to sit in the same queue: the ones the AI itself sent to review, FAILs
and critical fails, and a low player rating. The reasons are read from the stored grade and the
chat as they are, so a grade made before this rule joins the queue without being re-graded.
"""
from __future__ import annotations

from typing import Any

# The player's post-chat rating at or below this is a problem chat (the UI's CSAT_LOW_MAX).
LOW_CSAT = 2

NOTE_REASON = "Internal note — QC checks"


def has_internal_note(conversation) -> bool:
    from intercom_summary.qa.gated_rules import _has_internal_note

    return _has_internal_note(conversation)


def reasons(*, has_note: bool, manual_review: bool, manual_reason: str = "",
            overall_result: str | None = None, critical: bool = False,
            csat: int | None = None) -> list[str]:
    """The reasons in the order a QC manager should read them; empty when none applies."""
    out: list[str] = []
    if critical:
        out.append("Critical fail")
    elif overall_result == "FAIL":
        out.append("FAIL")
    if has_note:
        out.append(NOTE_REASON)
    if manual_review:
        reason = (manual_reason or "").strip()
        if reason and reason != NOTE_REASON:
            out.append(f"AI: {reason}")
        elif not reason:
            out.append("AI: flagged for review")
    if csat is not None and csat <= LOW_CSAT:
        out.append(f"Low CSAT ({csat}/5)")
    return out


def for_grade(conversation, payload: dict[str, Any]) -> list[str]:
    """The reasons for one graded chat, from its stored grade payload."""
    return reasons(
        has_note=has_internal_note(conversation),
        manual_review=bool(payload.get("manual_review_needed")),
        manual_reason=str(payload.get("manual_review_reason") or ""),
        overall_result=payload.get("overall_result"),
        critical=bool(payload.get("critical_fail")),
        csat=getattr(conversation, "csat_rating", None),
    )
