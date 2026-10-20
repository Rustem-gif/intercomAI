"""The VIP case as the vip-v16 grader reads it (ruleset `transcript: vip_case`).

The chat transcript (qa/prompt.py) does not fit a VIP case on four counts, each of which the
scorecard depends on:

* a case spans days and threads, so every line carries a full UTC date, not a clock time, and
  a separator marks where a new thread starts;
* other departments write inside VIP threads ("Pay Dep", "Head of CS", support agents), and
  the scorecard forbids charging their words or delays to a VIP manager — so a staff line is
  labelled VIP MANAGER or OTHER STAFF (config/vip_staff.yaml), never a generic AGENT;
* internal notes are system traces the scorecard accepts as proof ("there is a trace"), so
  they stay in, marked "(internal note)";
* SLA criteria need the reply gaps, which are computed here — the model is not asked to do
  date arithmetic — and they only count inside a shift, so the header says whether a shift
  schedule exists. Without one, the timing criteria are not assessable (qa/vip_scoring.py).

Automation (bots, rating requests, SLA bookkeeping) and empty parts are dropped.
"""
from __future__ import annotations

from datetime import datetime

from intercom_summary import vip_staff
from intercom_summary.intercom.models import Message, fmt_duration
from intercom_summary.qa.grading_common import trim_transcript

# A case can hold a dozen threads; the chat limit (15k) would cut most of a long one away.
# ~60k characters is ~15k tokens — still a fraction of the prompt it sits under.
VIP_CASE_TRANSCRIPT_CHARS = 60_000

_PLAYER = frozenset({"user", "contact", "lead"})

EVIDENCE_SCOPE = (
    "Evidence scope: only these Intercom emails and the internal notes written in them are "
    "visible. Slack, the back office, Jira, the VIP tables, daily_report and bonus history "
    "are not."
)


def _when(dt: datetime | None) -> str:
    return dt.strftime("%Y-%m-%d %H:%M UTC") if dt else "?"


def _visible(m: Message) -> bool:
    return m.author_type != "bot" and bool((m.text or "").strip())


def role_of(m: Message, case) -> str:
    if m.author_type in _PLAYER:
        return "PLAYER"
    if m.author_type == "admin":
        return "VIP MANAGER" if case.is_vip_manager(m.author_name) else "OTHER STAFF"
    return m.author_type.upper()


def _label(m: Message, case) -> str:
    name = f" {m.author_name}" if m.author_name else ""
    note = " (internal note)" if m.part_type == "note" else ""
    return f"{role_of(m, case)}{name}{note}"


def reply_gaps(case) -> list[dict]:
    """Each run of player messages → the next VIP MANAGER public reply in the case (any
    thread), measured from the first message of the run. `reply_at` None = never answered."""
    gaps: list[dict] = []
    waiting: tuple[Message, str] | None = None
    for m, tid in zip(case.messages, case.message_thread):
        if not _visible(m) or m.created_at is None:
            continue
        if m.author_type in _PLAYER:
            if waiting is None:
                waiting = (m, tid)
        elif (m.author_type == "admin" and m.part_type != "note"
              and case.is_vip_manager(m.author_name) and waiting is not None):
            asked, asked_tid = waiting
            gaps.append({"asked_at": asked.created_at, "thread": asked_tid,
                         "reply_at": m.created_at, "reply_thread": tid,
                         "manager": m.author_name,
                         "seconds": int((m.created_at - asked.created_at).total_seconds())})
            waiting = None
    if waiting is not None:
        asked, asked_tid = waiting
        gaps.append({"asked_at": asked.created_at, "thread": asked_tid, "reply_at": None,
                     "reply_thread": None, "manager": None, "seconds": None})
    return gaps


def _shift_lines(case) -> list[str]:
    """The SHIFTS block: each case manager's intervals that touch the case's span."""
    shifts = vip_staff.shifts()
    if not shifts:
        return ["Shift schedule: UNKNOWN — reply gaps cannot be placed in a shift, so answer "
                "cannot_determine for proc-first-response and proc-next-response."]
    first = case.messages[0].created_at if case.messages else None
    last = case.messages[-1].created_at if case.messages else None
    lines = ["=== SHIFTS (UTC) ==="]
    names = {m.author_name for m in case.messages if m.author_type == "admin"
             and case.is_vip_manager(m.author_name)}
    for name in sorted(names):
        spans = []
        for iv in shifts.get(name) or []:
            try:
                a, b = (datetime.fromisoformat(x) for x in str(iv).split("/"))
            except ValueError:
                continue
            if first and last and (b.date() < first.date() or a.date() > last.date()):
                continue
            spans.append(f"{a:%Y-%m-%d %H:%M}–{b:%H:%M}")
        lines.append(f"{name}: {', '.join(spans) if spans else 'no shifts recorded in this period'}")
    return lines


def case_block(case) -> str:
    """The full grader input for one CaseConversation (vip_cases.case_conversation)."""
    index = {t["id"]: i + 1 for i, t in enumerate(case.threads)}
    managers = sorted({m.author_name for m in case.messages if m.author_type == "admin"
                       and _visible(m) and case.is_vip_manager(m.author_name)})
    others = sorted({m.author_name for m in case.messages if m.author_type == "admin"
                     and _visible(m) and not case.is_vip_manager(m.author_name)})
    player = case.contact.name or case.contact.email or "unknown"
    head = [
        f"=== VIP CASE {case.case_id} ===",
        f"Brand: {case.brand or 'unknown'}",
        f"Player: {player}" + (f" <{case.contact.email}>" if case.contact.email else ""),
        "Channel: email",
        f"Topic (pre-label, may be wrong): {case.topic or 'unknown'}",
        f"Primary VIP manager: {case.assignee_name or 'none'}",
        f"VIP managers who wrote: {', '.join(managers) or 'none'}",
        f"Other staff who wrote: {', '.join(others) or 'none'}",
        f"Threads ({len(case.threads)}):",
    ]
    for t in case.threads:
        opened = _when(datetime.fromisoformat(t["created_at"])) if t.get("created_at") else "?"
        head.append(
            f"  {index[t['id']]}. {t['id']} | opened {opened} by {t.get('opened_by') or '?'} | "
            f"assigned to {t.get('agent_name') or 'nobody'} | \"{t.get('subject') or '—'}\""
        )
    head.append(EVIDENCE_SCOPE)
    head += _shift_lines(case)

    gaps = ["=== REPLY GAPS (player message → next VIP MANAGER public reply; wall-clock, "
            "not shift time) ==="]
    for g in reply_gaps(case):
        if g["reply_at"] is None:
            gaps.append(f"  {_when(g['asked_at'])} (thread {index.get(g['thread'], '?')}) → "
                        f"no VIP MANAGER reply after it in this case")
        else:
            gaps.append(f"  {_when(g['asked_at'])} (thread {index.get(g['thread'], '?')}) → "
                        f"{_when(g['reply_at'])} {g['manager']} "
                        f"(thread {index.get(g['reply_thread'], '?')}): "
                        f"{fmt_duration(g['seconds'])}")
    if len(gaps) == 1:
        gaps.append("  none — no player message in this case")

    body = ["=== TRANSCRIPT (automation removed) ==="]
    current = None
    prev: Message | None = None
    for m, tid in zip(case.messages, case.message_thread):
        if not _visible(m):
            continue
        if tid != current:
            t = case.threads[index[tid] - 1]
            body.append(f"--- thread {index[tid]} · {tid} · \"{t.get('subject') or '—'}\" ---")
            current = tid
        gap = ""
        if prev is not None and prev.created_at and m.created_at:
            secs = int((m.created_at - prev.created_at).total_seconds())
            if secs > 0:
                gap = f" | +{fmt_duration(secs)}"
        body.append(f"[{_when(m.created_at)}{gap}] {_label(m, case)}: {m.text.strip()}")
        prev = m
    body.append("=== END ===")

    # Only the transcript is trimmed: the header and the gap table are what every criterion
    # leans on, and a long case's header alone outgrows the trimmer's kept head.
    prefix = "\n".join(head) + "\n\n" + "\n".join(gaps) + "\n\n"
    limit = max(10_000, VIP_CASE_TRANSCRIPT_CHARS - len(prefix))
    return prefix + trim_transcript("\n".join(body), limit=limit)
