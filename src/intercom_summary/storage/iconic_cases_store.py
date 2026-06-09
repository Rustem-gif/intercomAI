"""Persistent store for the knowledge-base of iconic / representative cases."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from intercom_summary.settings import settings
from intercom_summary.storage.db import connect


class IconicCasesStore:
    def __init__(self, db_path: str | Path | None = None) -> None:
        self._conn = connect(db_path or settings.db_path)

    def close(self) -> None:
        self._conn.close()

    def add(self, conversation_id: str, added_by: str, comment: str = "") -> None:
        self._conn.execute(
            """INSERT OR REPLACE INTO iconic_cases
               (conversation_id, added_by, added_at, manager_comment)
               VALUES (?, ?, ?, ?)""",
            (conversation_id, added_by, datetime.now(timezone.utc).isoformat(), comment),
        )
        self._conn.commit()

    def remove(self, conversation_id: str) -> bool:
        cur = self._conn.execute(
            "DELETE FROM iconic_cases WHERE conversation_id=?", (conversation_id,)
        )
        self._conn.commit()
        return cur.rowcount > 0

    def update_comment(self, conversation_id: str, comment: str) -> bool:
        cur = self._conn.execute(
            "UPDATE iconic_cases SET manager_comment=? WHERE conversation_id=?",
            (comment, conversation_id),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def get(self, conversation_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM iconic_cases WHERE conversation_id=?", (conversation_id,)
        ).fetchone()
        return dict(row) if row else None

    def list_all(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM iconic_cases ORDER BY added_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    def is_iconic(self, conversation_id: str) -> bool:
        return self._conn.execute(
            "SELECT 1 FROM iconic_cases WHERE conversation_id=?", (conversation_id,)
        ).fetchone() is not None
