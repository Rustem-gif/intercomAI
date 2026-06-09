"""Persistence for coaching sessions and their linked conversations."""
from __future__ import annotations

import secrets
from datetime import datetime, timezone
from pathlib import Path

from intercom_summary.settings import settings


class CoachingStore:
    def __init__(self, db_path: str | Path | None = None) -> None:
        from intercom_summary.storage.db import connect
        self._conn = connect(db_path or settings.db_path)

    def close(self) -> None:
        self._conn.close()

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def create_session(
        self,
        agent_name: str,
        title: str,
        notes: str,
        due_date: str | None,
        created_by: str,
    ) -> str:
        session_id = secrets.token_urlsafe(12)
        now = self._now()
        self._conn.execute(
            """INSERT INTO coaching_sessions
               (id, agent_name, title, notes, due_date, status, created_by, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, 'open', ?, ?, ?)""",
            (session_id, agent_name, title, notes, due_date, created_by, now, now),
        )
        self._conn.commit()
        return session_id

    def get_session(self, session_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM coaching_sessions WHERE id=?", (session_id,)
        ).fetchone()
        return dict(row) if row else None

    def list_sessions(self, agent_name: str | None = None) -> list[dict]:
        if agent_name:
            rows = self._conn.execute(
                """SELECT cs.*, COUNT(ci.conversation_id) AS item_count
                   FROM coaching_sessions cs
                   LEFT JOIN coaching_items ci ON ci.session_id = cs.id
                   WHERE cs.agent_name=?
                   GROUP BY cs.id ORDER BY cs.created_at DESC""",
                (agent_name,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                """SELECT cs.*, COUNT(ci.conversation_id) AS item_count
                   FROM coaching_sessions cs
                   LEFT JOIN coaching_items ci ON ci.session_id = cs.id
                   GROUP BY cs.id ORDER BY cs.created_at DESC"""
            ).fetchall()
        return [dict(r) for r in rows]

    def update_session(
        self,
        session_id: str,
        title: str | None = None,
        notes: str | None = None,
        due_date: str | None = None,
        status: str | None = None,
    ) -> bool:
        session = self.get_session(session_id)
        if not session:
            return False
        self._conn.execute(
            """UPDATE coaching_sessions
               SET title=?, notes=?, due_date=?, status=?, updated_at=?
               WHERE id=?""",
            (
                title if title is not None else session["title"],
                notes if notes is not None else session["notes"],
                due_date if due_date is not None else session["due_date"],
                status if status is not None else session["status"],
                self._now(),
                session_id,
            ),
        )
        self._conn.commit()
        return True

    def delete_session(self, session_id: str) -> bool:
        self._conn.execute("DELETE FROM coaching_items WHERE session_id=?", (session_id,))
        cur = self._conn.execute("DELETE FROM coaching_sessions WHERE id=?", (session_id,))
        self._conn.commit()
        return cur.rowcount > 0

    def add_item(self, session_id: str, conversation_id: str, note: str = "") -> None:
        self._conn.execute(
            """INSERT OR REPLACE INTO coaching_items (session_id, conversation_id, note)
               VALUES (?, ?, ?)""",
            (session_id, conversation_id, note),
        )
        self._conn.execute(
            "UPDATE coaching_sessions SET updated_at=? WHERE id=?",
            (self._now(), session_id),
        )
        self._conn.commit()

    def remove_item(self, session_id: str, conversation_id: str) -> bool:
        cur = self._conn.execute(
            "DELETE FROM coaching_items WHERE session_id=? AND conversation_id=?",
            (session_id, conversation_id),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def get_items(self, session_id: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM coaching_items WHERE session_id=?", (session_id,)
        ).fetchall()
        return [dict(r) for r in rows]
