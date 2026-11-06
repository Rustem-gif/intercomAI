"""A readable copy of the grades and of QA's work in Google Sheets.

Not a restorable backup — the SQLite snapshots (backup.py) and the journal (storage/journal.py)
are — but a copy anyone can open without the app, which survives the app, the Mac and its
disks. Two tabs:

  Grades & QA  one row per graded chat: the AI grade and everything QA did on it (re-score,
               reason, Agree, comments, dispute, tags, last QA action). Rewritten hourly.
  Status       when it was last written, and what the copy holds.

Times are shown in Kyiv time. The tab is formatted on every write (coloured header, banding,
PASS/FAIL colours, score colour scale, filters), so the look survives the rewrite.

Auth is a Google service account (GOOGLE_SERVICE_ACCOUNT_FILE) the sheet is shared with.
Every outcome is recorded in `meta`; a failure turns the backup badge red (backup.health).
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from intercom_summary.settings import settings

log = logging.getLogger(__name__)

META_GRADES_AT = "sheets.grades_at"
META_ERROR = "sheets.last_error"
META_ERROR_AT = "sheets.last_error_at"

TAB = "Grades & QA"
OLD_TABS = ("Grades", "QA actions")          # earlier layout: renamed / removed on next write
KYIV = ZoneInfo("Europe/Kyiv")
CHUNK = 5000           # rows per update call — keeps each request well under the API's size cap

# (header, width px, align)
COLUMNS = [
    ("Chat id", 130, "LEFT"), ("Created", 118, "LEFT"), ("Brand", 90, "LEFT"),
    ("Agent", 110, "LEFT"), ("Ruleset", 80, "CENTER"), ("AI score", 72, "CENTER"),
    ("AI result", 78, "CENTER"), ("AI graded", 118, "LEFT"), ("QA score", 72, "CENTER"),
    ("QA reason", 260, "LEFT"), ("QA by", 90, "LEFT"), ("QA at", 118, "LEFT"),
    ("Agreed by", 90, "LEFT"), ("Agreed at", 118, "LEFT"), ("Comments", 80, "CENTER"),
    ("Last comment", 260, "LEFT"), ("Dispute", 90, "CENTER"), ("Tags", 140, "LEFT"),
    ("Last QA action", 118, "LEFT"), ("Note", 120, "LEFT"),
]
HEADER = [c[0] for c in COLUMNS]
COL = {name: i for i, name in enumerate(HEADER)}
RESTORED_NOTE = "restored from log"


def _rgb(hex_: str) -> dict:
    h = hex_.lstrip("#")
    return {"red": int(h[0:2], 16) / 255, "green": int(h[2:4], 16) / 255,
            "blue": int(h[4:6], 16) / 255}


def enabled() -> bool:
    return bool(settings.gsheet_id)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def local(iso: str | None) -> str:
    """UTC ISO → 'YYYY-MM-DD HH:MM' Kyiv time; '' for nothing."""
    if not iso:
        return ""
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return iso
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(KYIV).strftime("%Y-%m-%d %H:%M")


def _open(client=None):
    if client is None:
        import gspread

        client = gspread.service_account(filename=str(settings.google_service_account_file))
    return client.open_by_key(settings.gsheet_id)


def _tab(book, title: str, cols: int):
    import gspread

    try:
        return book.worksheet(title)
    except gspread.WorksheetNotFound:
        return book.add_worksheet(title=title, rows=100, cols=cols)


def _meta(conn: sqlite3.Connection, values: dict[str, str] | None = None,
          keys: tuple[str, ...] = ()) -> dict[str, str]:
    if values:
        now = _now()
        for k, v in values.items():
            conn.execute("""INSERT INTO meta (key, value, updated_at) VALUES (?,?,?)
                            ON CONFLICT(key) DO UPDATE SET value=excluded.value,
                                                           updated_at=excluded.updated_at""",
                         (k, v, now))
        conn.commit()
    if not keys:
        return {}
    rows = conn.execute(f"SELECT key, value FROM meta WHERE key IN ({','.join('?' * len(keys))})",
                        keys).fetchall()
    return {r[0]: r[1] for r in rows}


def grade_rows(conn: sqlite3.Connection) -> list[list[Any]]:
    """One row per graded chat: the AI grade plus all of QA's work on it."""
    rows = conn.execute(
        """SELECT g.conversation_id, c.created_at, c.brand, COALESCE(c.agent_name, g.agent_name),
                  g.ruleset_id, g.overall_score, g.payload_json, g.graded_at, g.human_score,
                  g.override_reason, g.overridden_by, g.overridden_at, g.human_confirmed_by,
                  g.human_confirmed_at, c.custom_tags, g.summary,
                  cm.n, cm.last_text, cm.last_at,
                  d.status, d.created_at, d.resolved_at
             FROM grades g
             LEFT JOIN conversations c ON c.id = g.conversation_id
             LEFT JOIN (SELECT conversation_id, COUNT(*) AS n, MAX(created_at) AS last_at,
                               (SELECT author || ': ' || text FROM conversation_comments x
                                 WHERE x.conversation_id = cc.conversation_id
                                 ORDER BY created_at DESC LIMIT 1) AS last_text
                          FROM conversation_comments cc GROUP BY conversation_id) cm
                    ON cm.conversation_id = g.conversation_id
             LEFT JOIN grade_disputes d ON d.conversation_id = g.conversation_id
            ORDER BY c.created_at DESC""").fetchall()
    out = []
    for r in rows:
        try:
            result = json.loads(r[6] or "{}").get("overall_result", "")
        except json.JSONDecodeError:
            result = ""
        last_qa = max((t for t in (r[11], r[13], r[18], r[20], r[21]) if t), default="")
        out.append([
            str(r[0]), local(r[1]), r[2] or "", r[3] or "", r[4] or "", r[5], result,
            local(r[7]), "" if r[8] is None else r[8], r[9] or "", r[10] or "", local(r[11]),
            r[12] or "", local(r[13]), r[16] or "", r[17] or "", r[19] or "", r[14] or "",
            local(last_qa),
            RESTORED_NOTE if (r[15] or "").startswith("Restored from the service log") else "",
        ])
    return out


# ── formatting ──────────────────────────────────────────────────────────────────
def _cf(sheet_id: int, col: int, n: int, *, cond: dict, bg: str | None = None,
        fg: str | None = None, bold: bool = False, italic: bool = False,
        cols: tuple[int, int] | None = None) -> dict:
    fmt: dict[str, Any] = {}
    if bg:
        fmt["backgroundColor"] = _rgb(bg)
    text: dict[str, Any] = {}
    if fg:
        text["foregroundColor"] = _rgb(fg)
    if bold:
        text["bold"] = True
    if italic:
        text["italic"] = True
    if text:
        fmt["textFormat"] = text
    c0, c1 = cols or (col, col + 1)
    return {"addConditionalFormatRule": {"index": 0, "rule": {
        "ranges": [{"sheetId": sheet_id, "startRowIndex": 1, "endRowIndex": n + 1,
                    "startColumnIndex": c0, "endColumnIndex": c1}],
        "booleanRule": {"condition": cond, "format": fmt}}}}


def _eq(v: str) -> dict:
    return {"type": "TEXT_EQ", "values": [{"userEnteredValue": v}]}


NOT_BLANK = {"type": "NOT_BLANK"}


def _style(book, ws, n_rows: int, columns: list[tuple[str, int, str]], *, header: str,
           band: str, tab_color: str, index: int, rules: list[dict] | None = None,
           gradient_col: int | None = None) -> None:
    """(Re)apply the tab's look. Idempotent: old banding and conditional rules are replaced."""
    sid = ws.id
    meta = book.fetch_sheet_metadata(params={
        "fields": "sheets(properties(sheetId),bandedRanges(bandedRangeId),conditionalFormats)"})
    sheet = next((s for s in meta.get("sheets", []) if s["properties"]["sheetId"] == sid), {})
    ncols = len(columns)
    end = n_rows + 1
    reqs: list[dict] = []
    reqs += [{"deleteBanding": {"bandedRangeId": b["bandedRangeId"]}}
             for b in sheet.get("bandedRanges", [])]
    reqs += [{"deleteConditionalFormatRule": {"sheetId": sid, "index": 0}}
             for _ in sheet.get("conditionalFormats", [])]
    reqs.append({"updateSheetProperties": {
        "properties": {"sheetId": sid, "index": index, "tabColorStyle": {"rgbColor": _rgb(tab_color)},
                       "gridProperties": {"frozenRowCount": 1}},
        "fields": "index,tabColorStyle,gridProperties.frozenRowCount"}})
    # Body: readable size, vertically centred, long text clipped (not wrapped) so rows stay even.
    reqs.append({"repeatCell": {
        "range": {"sheetId": sid, "startRowIndex": 1, "endRowIndex": end,
                  "startColumnIndex": 0, "endColumnIndex": ncols},
        "cell": {"userEnteredFormat": {"verticalAlignment": "MIDDLE", "wrapStrategy": "CLIP",
                                       "textFormat": {"fontSize": 10}}},
        "fields": "userEnteredFormat(verticalAlignment,wrapStrategy,textFormat.fontSize)"}})
    for i, (_, width, align) in enumerate(columns):
        reqs.append({"updateDimensionProperties": {
            "range": {"sheetId": sid, "dimension": "COLUMNS", "startIndex": i, "endIndex": i + 1},
            "properties": {"pixelSize": width}, "fields": "pixelSize"}})
        reqs.append({"repeatCell": {
            "range": {"sheetId": sid, "startRowIndex": 1, "endRowIndex": end,
                      "startColumnIndex": i, "endColumnIndex": i + 1},
            "cell": {"userEnteredFormat": {"horizontalAlignment": align}},
            "fields": "userEnteredFormat.horizontalAlignment"}})
    # Header: coloured, bold white, taller.
    reqs.append({"repeatCell": {
        "range": {"sheetId": sid, "startRowIndex": 0, "endRowIndex": 1,
                  "startColumnIndex": 0, "endColumnIndex": ncols},
        "cell": {"userEnteredFormat": {
            "backgroundColor": _rgb(header), "horizontalAlignment": "CENTER",
            "verticalAlignment": "MIDDLE", "wrapStrategy": "WRAP",
            "textFormat": {"bold": True, "fontSize": 10, "foregroundColor": _rgb("#FFFFFF")}}},
        "fields": "userEnteredFormat(backgroundColor,horizontalAlignment,verticalAlignment,"
                  "wrapStrategy,textFormat)"}})
    reqs.append({"updateDimensionProperties": {
        "range": {"sheetId": sid, "dimension": "ROWS", "startIndex": 0, "endIndex": 1},
        "properties": {"pixelSize": 36}, "fields": "pixelSize"}})
    reqs.append({"addBanding": {"bandedRange": {
        "range": {"sheetId": sid, "startRowIndex": 0, "endRowIndex": end,
                  "startColumnIndex": 0, "endColumnIndex": ncols},
        "rowProperties": {"headerColor": _rgb(header), "firstBandColor": _rgb("#FFFFFF"),
                          "secondBandColor": _rgb(band)}}}})
    reqs.append({"setBasicFilter": {"filter": {"range": {
        "sheetId": sid, "startRowIndex": 0, "endRowIndex": end,
        "startColumnIndex": 0, "endColumnIndex": ncols}}}})
    if gradient_col is not None:
        reqs.append({"addConditionalFormatRule": {"index": 0, "rule": {
            "ranges": [{"sheetId": sid, "startRowIndex": 1, "endRowIndex": end,
                        "startColumnIndex": gradient_col, "endColumnIndex": gradient_col + 1}],
            "gradientRule": {
                "minpoint": {"type": "NUMBER", "value": "50", "color": _rgb("#F8B4B4")},
                "midpoint": {"type": "NUMBER", "value": "80", "color": _rgb("#FDE68A")},
                "maxpoint": {"type": "NUMBER", "value": "100", "color": _rgb("#A7F3D0")}}}}})
    # Rules are inserted at index 0, so the LAST one added wins: add row-wide rules first.
    reqs += rules or []
    book.batch_update({"requests": reqs})


def _grade_rules(sid: int, n: int) -> list[dict]:
    restored_col = chr(ord("A") + COL["Note"])
    return [
        # Row-wide (lowest priority): grades rebuilt from the log after the 6 Nov loss.
        _cf(sid, 0, n, cols=(0, len(HEADER)), fg="#6B7280", italic=True,
            cond={"type": "CUSTOM_FORMULA",
                  "values": [{"userEnteredValue": f'=${restored_col}2="{RESTORED_NOTE}"'}]}),
        _cf(sid, COL["AI result"], n, cond=_eq("PASS"), bg="#DCFCE7", fg="#166534", bold=True),
        _cf(sid, COL["AI result"], n, cond=_eq("FAIL"), bg="#FEE2E2", fg="#991B1B", bold=True),
        _cf(sid, COL["QA score"], n, cond=NOT_BLANK, bg="#FEF3C7", fg="#92400E", bold=True),
        _cf(sid, COL["QA reason"], n, cond=NOT_BLANK, bg="#FFFBEB"),
        _cf(sid, COL["Agreed by"], n, cond=NOT_BLANK, bg="#DCFCE7", fg="#166534"),
        _cf(sid, COL["Comments"], n, cond=NOT_BLANK, bg="#DBEAFE", fg="#1E40AF", bold=True),
        _cf(sid, COL["Dispute"], n, cond=_eq("open"), bg="#FEE2E2", fg="#991B1B", bold=True),
        _cf(sid, COL["Dispute"], n, cond=_eq("accepted"), bg="#FEF3C7", fg="#92400E"),
        _cf(sid, COL["Dispute"], n, cond=_eq("rejected"), bg="#F3F4F6", fg="#4B5563"),
        _cf(sid, COL["Tags"], n, cond=NOT_BLANK, fg="#6D28D9"),
    ]


STATUS_COLUMNS = [("What", 230, "LEFT"), ("Value", 170, "LEFT"), ("About", 520, "LEFT")]


def _write_status(book, conn: sqlite3.Connection, rows: list[list[Any]]) -> None:
    m = _meta(conn, keys=(META_GRADES_AT,))
    rescored = sum(1 for r in rows if r[COL["QA score"]] != "")
    agreed = sum(1 for r in rows if r[COL["Agreed by"]])
    commented = sum(1 for r in rows if r[COL["Comments"]])
    disputed = sum(1 for r in rows if r[COL["Dispute"]])
    restored = sum(1 for r in rows if r[COL["Note"]])
    data = [
        [c[0] for c in STATUS_COLUMNS],
        ["Last updated (Kyiv)", local(m.get(META_GRADES_AT)),
         f"The '{TAB}' tab is rewritten every hour from the QA app."],
        ["Graded chats", len(rows), "One row per chat: the AI grade and everything QA did on it."],
        ["Re-scored by QA", rescored, "QA score / reason / by / at filled in."],
        ["Agreed by QA", agreed, "QA confirmed the AI grade as it stands."],
        ["With comments", commented, "Count and the latest comment per chat."],
        ["With a dispute", disputed, "open / accepted / rejected."],
        ["Restored from log", restored,
         "Grades rebuilt after the 6 Nov 2026 data loss: score and PASS/FAIL only (grey rows)."],
        ["This is a copy", "", "Full backups (restorable) are in Google Drive: database every 6 h, "
                               "QA actions journal every hour."],
    ]
    ws = _tab(book, "Status", len(STATUS_COLUMNS))
    ws.clear()
    ws.update(range_name="A1", values=data, value_input_option="RAW")
    _style(book, ws, len(data) - 1, STATUS_COLUMNS, header="#334155", band="#F1F5F9",
           tab_color="#334155", index=1)


def _retire_old_tabs(book) -> None:
    import gspread

    for name in OLD_TABS:
        try:
            ws = book.worksheet(name)
        except gspread.WorksheetNotFound:
            continue
        try:
            book.worksheet(TAB)
            book.del_worksheet(ws)              # the unified tab exists: drop the old one
        except gspread.WorksheetNotFound:
            ws.update_title(TAB)                # first run of the new layout: reuse it


def export_grades(db_path: Path | None = None, client=None) -> int:
    """Rewrite the 'Grades & QA' tab (and Status) from the DB. Returns the number of rows."""
    if not enabled():
        return 0
    from intercom_summary.storage.db import connect

    conn = connect(db_path or settings.db_path)
    try:
        try:
            rows = grade_rows(conn)
            book = _open(client)
            _retire_old_tabs(book)
            ws = _tab(book, TAB, len(HEADER))
            ws.clear()
            ws.resize(rows=len(rows) + 1, cols=len(HEADER))
            data = [HEADER] + rows
            for i in range(0, len(data), CHUNK):
                ws.update(range_name=f"A{i + 1}", values=data[i:i + CHUNK],
                          value_input_option="RAW")
            sid = ws.id
            _style(book, ws, len(rows), COLUMNS, header="#4F46E5", band="#EEF2FF",
                   tab_color="#4F46E5", index=0, rules=_grade_rules(sid, len(rows)),
                   gradient_col=COL["AI score"])
            _meta(conn, {META_GRADES_AT: _now(), META_ERROR: ""})
            _write_status(book, conn, rows)
            log.info("Google Sheet: '%s' rewritten (%d rows)", TAB, len(rows))
            return len(rows)
        except Exception as exc:
            _record_error(conn, f"Sheet export: {exc}")
            raise
    finally:
        conn.close()


def _record_error(conn: sqlite3.Connection, msg: str) -> None:
    log.error("GOOGLE SHEET SYNC FAILED: %s", msg)
    try:
        _meta(conn, {META_ERROR: msg[:500], META_ERROR_AT: _now()})
    except Exception:  # noqa: BLE001
        log.exception("could not record the sheet sync failure")


def status(db_path: Path | None = None) -> dict[str, Any]:
    from intercom_summary.storage.db import connect

    if not enabled():
        return {"enabled": False}
    conn = connect(db_path or settings.db_path)
    try:
        m = _meta(conn, keys=(META_GRADES_AT, META_ERROR, META_ERROR_AT))
    finally:
        conn.close()
    return {"enabled": True, "grades_at": m.get(META_GRADES_AT),
            "last_error": m.get(META_ERROR) or None, "last_error_at": m.get(META_ERROR_AT)}
