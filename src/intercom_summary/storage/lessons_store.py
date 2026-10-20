"""Persistence for "QA lessons" — grading-rule changes drafted from QA's re-scores.

A lesson is a draft until an admin approves it (qa/lessons.py). Only an approved lesson reaches
the grading rules, and then only for chats graded after the approval: existing grades are never
re-graded (GradesStore.is_current).
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from intercom_summary.settings import settings

STATUSES = ("draft", "approved", "rejected")
KINDS = ("not_a_fail", "is_a_fail")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class LessonsStore:
    def __init__(self, db_path: str | Path | None = None) -> None:
        from intercom_summary.storage.db import connect
        self._conn = connect(db_path or settings.db_path)

    def close(self) -> None:
        self._conn.close()

    def add(self, *, ruleset_id: str, criterion_id: str, kind: str, text: str,
            rationale: str = "", evidence: list[dict] | None = None,
            sync_id: str | None = None) -> str:
        if kind not in KINDS:
            raise ValueError(f"lesson kind must be one of {KINDS} (got {kind!r})")
        lesson_id = "ql-" + uuid.uuid4().hex[:10]
        self._conn.execute(
            """INSERT INTO qa_lessons (id, created_at, ruleset_id, criterion_id, kind, text,
                                       rationale, evidence_json, sync_id)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (lesson_id, _now(), ruleset_id, criterion_id, kind, text.strip(), rationale.strip(),
             json.dumps(evidence or [], ensure_ascii=False), sync_id),
        )
        self._conn.commit()
        return lesson_id

    def get(self, lesson_id: str) -> dict | None:
        row = self._conn.execute("SELECT * FROM qa_lessons WHERE id=?", (lesson_id,)).fetchone()
        return self._row(row) if row else None

    def list(self, status: str | None = None, ruleset_id: str | None = None) -> list[dict]:
        where, args = [], []
        if status:
            where.append("status = ?")
            args.append(status)
        if ruleset_id:
            where.append("ruleset_id = ?")
            args.append(ruleset_id)
        sql = "SELECT * FROM qa_lessons" + (f" WHERE {' AND '.join(where)}" if where else "")
        rows = self._conn.execute(sql + " ORDER BY created_at DESC", args).fetchall()
        return [self._row(r) for r in rows]

    def edit_text(self, lesson_id: str, text: str) -> bool:
        """Reword a draft before deciding on it. A decided lesson is a record and stays as it was."""
        cur = self._conn.execute(
            "UPDATE qa_lessons SET text=? WHERE id=? AND status='draft'", (text.strip(), lesson_id))
        self._conn.commit()
        return cur.rowcount > 0

    def decide(self, lesson_id: str, status: str, decided_by: str) -> bool:
        if status not in ("approved", "rejected"):
            raise ValueError("a lesson is decided as 'approved' or 'rejected'")
        cur = self._conn.execute(
            "UPDATE qa_lessons SET status=?, decided_by=?, decided_at=? WHERE id=? AND status='draft'",
            (status, decided_by, _now(), lesson_id))
        self._conn.commit()
        return cur.rowcount > 0

    def set_eval(self, lesson_id: str, result: dict) -> None:
        self._conn.execute("UPDATE qa_lessons SET eval_json=? WHERE id=?",
                           (json.dumps(result, ensure_ascii=False), lesson_id))
        self._conn.commit()

    def approved_boundaries(self, ruleset_id: str) -> dict[str, list[str]]:
        """{criterion_id: [approved NOT-A-FAIL texts]} — what the fail verifier adds to its own
        hand-written boundaries."""
        out: dict[str, list[str]] = {}
        for r in self._conn.execute(
                "SELECT criterion_id, text FROM qa_lessons WHERE status='approved' "
                "AND kind='not_a_fail' AND ruleset_id=? ORDER BY decided_at", (ruleset_id,)):
            out.setdefault(r["criterion_id"], []).append(r["text"])
        return out

    # ── sync bookkeeping (the shared `meta` table) ──────────────────────────────────────
    def get_meta(self, key: str) -> str | None:
        row = self._conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        self._conn.execute(
            """INSERT INTO meta (key, value, updated_at) VALUES (?,?,?)
               ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at""",
            (key, value, _now()))
        self._conn.commit()

    @staticmethod
    def _row(row) -> dict:
        d = dict(row)
        d["evidence"] = json.loads(d.pop("evidence_json") or "[]")
        d["eval"] = json.loads(d.pop("eval_json")) if d.get("eval_json") else None
        return d
