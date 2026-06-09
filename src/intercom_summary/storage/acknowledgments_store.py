"""Persistence for review portal acknowledgments (agent marks conversation as reviewed)."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from intercom_summary.settings import settings


class AcknowledgmentsStore:
    def __init__(self, db_path: str | Path | None = None) -> None:
        from intercom_summary.storage.db import connect
        self._conn = connect(db_path or settings.db_path)

    def close(self) -> None:
        self._conn.close()

    def acknowledge(self, token: str, conversation_id: str) -> bool:
        """Toggle acknowledgment. Returns True if now acknowledged, False if removed."""
        existing = self._conn.execute(
            "SELECT 1 FROM review_acknowledgments WHERE token=? AND conversation_id=?",
            (token, conversation_id),
        ).fetchone()
        if existing:
            self._conn.execute(
                "DELETE FROM review_acknowledgments WHERE token=? AND conversation_id=?",
                (token, conversation_id),
            )
            self._conn.commit()
            return False
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "INSERT INTO review_acknowledgments (token, conversation_id, acknowledged_at) VALUES (?, ?, ?)",
            (token, conversation_id, now),
        )
        self._conn.commit()
        return True

    def get_acknowledged_ids(self, token: str) -> set[str]:
        rows = self._conn.execute(
            "SELECT conversation_id FROM review_acknowledgments WHERE token=?", (token,)
        ).fetchall()
        return {r["conversation_id"] for r in rows}

    def count_for_token(self, token: str) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) FROM review_acknowledgments WHERE token=?", (token,)
        ).fetchone()
        return row[0] if row else 0
