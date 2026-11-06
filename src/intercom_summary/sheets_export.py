"""A readable copy of the grades and of QA's work in Google Sheets.

Not a restorable backup — the SQLite snapshots (backup.py) and the journal (storage/journal.py)
are — but a copy anyone can open without the app, which survives the app, the Mac and its
disks. Three tabs:

  Grades      one row per graded chat (AI score/result + QA's re-score or Agree), rewritten in
              full after every backup.
  QA actions  every journal entry (re-scores, Agree, comments, disputes, tags…), appended as
              they come, hourly. Never rewritten, so it keeps the history even if the DB is lost.
  Status      when each tab was last written.

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

from intercom_summary.settings import settings

log = logging.getLogger(__name__)

META_GRADES_AT = "sheets.grades_at"
META_ACTIONS_THROUGH = "sheets.actions_through"
META_ACTIONS_AT = "sheets.actions_synced_at"
META_ERROR = "sheets.last_error"
META_ERROR_AT = "sheets.last_error_at"

GRADE_HEADER = ["Chat id", "Created", "Brand", "Agent", "Ruleset", "AI score", "AI result",
                "Graded at", "QA score", "QA reason", "QA by", "QA at", "Agreed by", "Agreed at",
                "Custom tags", "Restored from log"]
ACTION_HEADER = ["Time (UTC)", "Action", "Chat / case id", "By", "Score", "Text / reason",
                 "Details"]
CHUNK = 5000           # rows per update call — keeps each request well under the API's size cap


def enabled() -> bool:
    return bool(settings.gsheet_id)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


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
    rows = conn.execute(
        """SELECT g.conversation_id, c.created_at, c.brand, COALESCE(c.agent_name, g.agent_name),
                  g.ruleset_id, g.overall_score, g.payload_json, g.graded_at, g.human_score,
                  g.override_reason, g.overridden_by, g.overridden_at, g.human_confirmed_by,
                  g.human_confirmed_at, c.custom_tags, g.summary
             FROM grades g LEFT JOIN conversations c ON c.id = g.conversation_id
            ORDER BY c.created_at DESC""").fetchall()
    out = []
    for r in rows:
        try:
            result = json.loads(r[6] or "{}").get("overall_result", "")
        except json.JSONDecodeError:
            result = ""
        out.append([str(r[0]), r[1] or "", r[2] or "", r[3] or "", r[4] or "", r[5],
                    result, r[7] or "", "" if r[8] is None else r[8], r[9] or "", r[10] or "",
                    r[11] or "", r[12] or "", r[13] or "", r[14] or "",
                    "yes" if (r[15] or "").startswith("Restored from the service log") else ""])
    return out


def action_row(e: dict) -> list[Any]:
    who = (e.get("overridden_by") or e.get("confirmed_by") or e.get("author")
           or e.get("created_by") or e.get("resolved_by") or e.get("reviewed_by")
           or e.get("updated_by") or "")
    text = e.get("reason") or e.get("text") or e.get("note") or ""
    if e["action"] == "tags.set":
        text = ", ".join(e.get("tags") or [])
    shown = {"ts", "action", "conversation_id", "case_id", "human_score", "reason", "text",
             "note", "overridden_by", "confirmed_by", "author", "created_by", "resolved_by",
             "reviewed_by", "updated_by"}
    details = {k: v for k, v in e.items() if k not in shown and v not in (None, "", [], {})}
    return [e["ts"], e["action"], str(e.get("conversation_id") or e.get("case_id") or ""), who,
            "" if e.get("human_score") is None else e["human_score"], text,
            json.dumps(details, ensure_ascii=False) if details else ""]


def _write_status(book, conn: sqlite3.Connection) -> None:
    m = _meta(conn, keys=(META_GRADES_AT, META_ACTIONS_AT, META_ACTIONS_THROUGH))
    ws = _tab(book, "Status", 2)
    ws.update(range_name="A1", values=[
        ["What", "Last written (UTC)"],
        ["Grades tab rewritten", m.get(META_GRADES_AT, "")],
        ["QA actions tab synced", m.get(META_ACTIONS_AT, "")],
        ["Newest QA action in the sheet", m.get(META_ACTIONS_THROUGH, "")],
    ])


def export_grades(db_path: Path | None = None, client=None) -> int:
    """Rewrite the Grades tab from the DB. Returns the number of rows written."""
    if not enabled():
        return 0
    from intercom_summary.storage.db import connect

    conn = connect(db_path or settings.db_path)
    try:
        try:
            rows = grade_rows(conn)
            book = _open(client)
            ws = _tab(book, "Grades", len(GRADE_HEADER))
            ws.clear()
            ws.resize(rows=len(rows) + 1, cols=len(GRADE_HEADER))
            data = [GRADE_HEADER] + rows
            for i in range(0, len(data), CHUNK):
                ws.update(range_name=f"A{i + 1}", values=data[i:i + CHUNK],
                          value_input_option="RAW")
            ws.freeze(rows=1)
            _meta(conn, {META_GRADES_AT: _now(), META_ERROR: ""})
            _write_status(book, conn)
            log.info("Google Sheet: Grades tab rewritten (%d rows)", len(rows))
            return len(rows)
        except Exception as exc:
            _record_error(conn, f"Grades export: {exc}")
            raise
    finally:
        conn.close()


def sync_actions(db_path: Path | None = None, client=None) -> int:
    """Append journal entries newer than the last one already in the sheet."""
    if not enabled():
        return 0
    from intercom_summary.storage import journal
    from intercom_summary.storage.db import connect

    conn = connect(db_path or settings.db_path)
    try:
        try:
            through = _meta(conn, keys=(META_ACTIONS_THROUGH,)).get(META_ACTIONS_THROUGH)
            new = [e for e in journal.entries(since=through) if not through or e["ts"] > through]
            book = _open(client)
            ws = _tab(book, "QA actions", len(ACTION_HEADER))
            # A fresh tab answers [['']] or [], never None: test the cell's text, not the list.
            first = ws.get_values("A1:A1")
            if not (first and first[0] and first[0][0]):
                ws.update(range_name="A1", values=[ACTION_HEADER])
                ws.freeze(rows=1)
            if new:
                rows = [action_row(e) for e in new]
                for i in range(0, len(rows), CHUNK):
                    ws.append_rows(rows[i:i + CHUNK], value_input_option="RAW")
                _meta(conn, {META_ACTIONS_THROUGH: new[-1]["ts"]})
            _meta(conn, {META_ACTIONS_AT: _now(), META_ERROR: ""})
            _write_status(book, conn)
            return len(new)
        except Exception as exc:
            _record_error(conn, f"QA actions sync: {exc}")
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
        m = _meta(conn, keys=(META_GRADES_AT, META_ACTIONS_AT, META_ACTIONS_THROUGH, META_ERROR,
                              META_ERROR_AT))
    finally:
        conn.close()
    return {"enabled": True, "grades_at": m.get(META_GRADES_AT),
            "actions_synced_at": m.get(META_ACTIONS_AT),
            "actions_through": m.get(META_ACTIONS_THROUGH),
            "last_error": m.get(META_ERROR) or None, "last_error_at": m.get(META_ERROR_AT)}
