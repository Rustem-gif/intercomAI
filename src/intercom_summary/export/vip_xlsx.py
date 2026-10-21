"""Export the VIP managers' emails to XLSX — the threads, or the cases with their grades.

Two workbooks, matching the two views of the VIP emails page:

  • threads_workbook — one row per thread (`vip_conversations`) with its transcript. With the
    filtered-out threads included, a "Kept" column says why each one was dropped.
  • cases_workbook — one row per VIP case (`vip_cases`) with its Scorecard v1.6 grade and the
    transcript exactly as the grader reads it, plus a "Criteria" sheet with every verdict.

Both take the rows already filtered by the caller, so the file holds what the page shows.
"""
from __future__ import annotations

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from intercom_summary.intercom.models import Conversation

_HEADER_FILL = PatternFill("solid", fgColor="1F2937")
_HEADER_FONT = Font(color="FFFFFF", bold=True)
# Excel caps a cell at 32,767 characters.
_CELL_MAX = 32000

THREAD_COLS = [
    "Conversation ID", "Date (UTC)", "VIP manager", "Player", "Player email", "Brand",
    "Started by", "Topic", "Messages", "Subject", "Kept", "Transcript",
]
CASE_COLS = [
    "Case ID", "Player", "Player email", "Brand", "Topic", "VIP manager", "Other managers",
    "First message (UTC)", "Last message (UTC)", "Threads", "Thread IDs",
    "Score", "Result", "AI score", "AI result", "Re-scored by", "Re-score reason", "Agreed by",
    "Critical", "Service failure", "Needs review", "Review reason", "Outcome", "Case type",
    "Failed criteria", "Summary", "Grade out of date", "Graded (UTC)", "Transcript",
]
CRITERIA_COLS = ["Case ID", "Criterion", "Title", "Verdict", "Severity", "Evidence", "Comment"]


def _style_header(ws: Worksheet, ncols: int, freeze: str = "B2") -> None:
    for c in range(1, ncols + 1):
        cell = ws.cell(row=1, column=c)
        cell.fill = _HEADER_FILL
        cell.font = _HEADER_FONT
    ws.freeze_panes = freeze
    ws.auto_filter.ref = f"A1:{get_column_letter(ncols)}1"


def _wrap_last(ws: Worksheet, ncols: int) -> None:
    """The transcript is the last column: wide and wrapped, so a row reads like a chat."""
    ws.column_dimensions[get_column_letter(ncols)].width = 120
    for row in ws.iter_rows(min_row=2, min_col=ncols, max_col=ncols):
        for cell in row:
            cell.alignment = Alignment(wrap_text=True, vertical="top")


def _when(s: str | None) -> str:
    return (s or "")[:16].replace("T", " ")


def _yes(v) -> str:
    return "yes" if v else ""


def threads_workbook(rows: list[dict]) -> Workbook:
    """`rows` are `vip_conversations` rows with their `payload` (VipStore.query(payload=True))."""
    wb = Workbook()
    ws = wb.active
    ws.title = "VIP threads"
    ws.append(THREAD_COLS)
    for r in rows:
        convo = Conversation.from_dict(r["payload"])
        kept = "yes" if r.get("keep") == 1 else (
            f"no — {r.get('filter_stage') or 'rule'}: {r.get('reason') or ''}"
            if r.get("keep") == 0 else "not decided")
        ws.append([r["id"], _when(r["created_at"]), r["agent_name"], r["customer_name"],
                   r["customer_email"], r["brand"],
                   "player" if r["delivered_as"] == "customer_initiated" else "manager",
                   r["topic"], r["message_count"], r["subject"], kept,
                   convo.transcript_text()[:_CELL_MAX]])
    _style_header(ws, len(THREAD_COLS))
    _wrap_last(ws, len(THREAD_COLS))
    return wb


def _effective(grade: dict | None) -> tuple[int | None, str]:
    """The score and PASS/FAIL that count: the analyst's re-score when there is one."""
    if not grade:
        return None, ""
    if grade.get("human_score") is not None:
        score = grade["human_score"]
        return score, "PASS" if score >= 85 and not grade.get("critical_fail") else "FAIL"
    return grade.get("overall_score"), grade.get("overall_result") or ""


def cases_workbook(cases: list[dict], grades: dict[str, dict], transcripts: dict[str, str],
                   display_names: dict[str, str] | None = None) -> Workbook:
    """`cases` as VipCasesStore.cases() lists them; `grades` = case_id → VipCasesStore.get_grade;
    `transcripts` = case_id → the grader's case block. `display_names` maps a login to the
    name to print, as in the QA report."""
    names = display_names or {}
    wb = Workbook()
    ws = wb.active
    ws.title = "VIP cases"
    ws.append(CASE_COLS)
    crit = wb.create_sheet("Criteria")
    crit.append(CRITERIA_COLS)
    for c in cases:
        g = grades.get(c["case_id"])
        score, result = _effective(g)
        others = [m for m in c.get("managers") or [] if m != c.get("primary_manager")]
        failed = [r["rule_id"] for r in (g or {}).get("rule_results") or []
                  if r.get("verdict") in ("fail", "partial")]
        row_grade = c.get("grade") or {}
        ws.append([
            c["case_id"], c.get("contact_name"), c.get("contact_email"), c.get("brand"),
            c.get("topic"), c.get("primary_manager"), ", ".join(others),
            _when(c.get("first_at")), _when(c.get("last_at")), c.get("thread_count"),
            ", ".join(c.get("thread_ids") or []),
            score, result,
            g.get("overall_score") if g else None, g.get("overall_result") if g else "",
            names.get(g.get("overridden_by") or "", g.get("overridden_by") or "") if g else "",
            (g.get("override_reason") or "") if g else "",
            names.get(g.get("human_confirmed_by") or "", g.get("human_confirmed_by") or "") if g else "",
            _yes(g and g.get("critical_fail")), _yes(g and g.get("service_failure_flag")),
            _yes(g and g.get("manual_review_needed")),
            (g.get("manual_review_reason") or "") if g else "",
            (g.get("outcome_status") or "") if g else "", (g.get("case_type") or "") if g else "",
            ", ".join(failed), (g.get("summary") or "")[:_CELL_MAX] if g else "",
            _yes(row_grade.get("stale")), _when(g.get("graded_at")) if g else "",
            (transcripts.get(c["case_id"]) or "")[:_CELL_MAX],
        ])
        for r in (g or {}).get("rule_results") or []:
            # n/a carries nothing a reviewer can check; every other verdict does.
            if r.get("verdict") == "n/a":
                continue
            crit.append([c["case_id"], r.get("rule_id"), r.get("title"), r.get("verdict"),
                         r.get("severity") or "", (r.get("evidence") or "")[:_CELL_MAX],
                         (r.get("comment") or "")[:_CELL_MAX]])
    _style_header(ws, len(CASE_COLS))
    _wrap_last(ws, len(CASE_COLS))
    _style_header(crit, len(CRITERIA_COLS), freeze="A2")
    crit.column_dimensions["F"].width = 80
    crit.column_dimensions["G"].width = 60
    return wb
