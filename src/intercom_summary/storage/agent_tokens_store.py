"""Persistence for agent review tokens (shareable links)."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from intercom_summary.settings import settings


class AgentTokensStore:
    def __init__(self, db_path: str | Path | None = None) -> None:
        from intercom_summary.storage.db import connect

        self._conn = connect(db_path or settings.db_path)

    def close(self) -> None:
        self._conn.close()

    def create(
        self,
        token: str,
        agent_name: str,
        label: str,
        created_by: str,
        tag: str | None = None,
        expires_at: str | None = None,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            """INSERT INTO agent_review_tokens
               (token, agent_name, tag, label, created_by, created_at, expires_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (token, agent_name, tag, label, created_by, now, expires_at),
        )
        self._conn.commit()

    def get(self, token: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM agent_review_tokens WHERE token=?", (token,)
        ).fetchone()
        if not row:
            return None
        result = dict(row)
        # Treat as expired if expires_at is set and in the past.
        if result.get("expires_at"):
            now = datetime.now(timezone.utc).isoformat()
            if result["expires_at"] < now:
                return None
        return result

    def list_all(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM agent_review_tokens ORDER BY created_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    def list_by_agent(self, agent_name: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM agent_review_tokens WHERE agent_name=? ORDER BY created_at DESC",
            (agent_name,),
        ).fetchall()
        return [dict(r) for r in rows]

    def delete(self, token: str) -> bool:
        cur = self._conn.execute(
            "DELETE FROM agent_review_tokens WHERE token=?", (token,)
        )
        self._conn.commit()
        return cur.rowcount > 0
