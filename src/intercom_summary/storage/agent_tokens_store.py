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
        session_id: str | None = None,
        since: str | None = None,
        until: str | None = None,
        conversation_ids: "list[str] | None" = None,
    ) -> None:
        """Create a review link.

        `since` / `until` record the range the link covers, for display. `conversation_ids` is
        the set it actually shows, resolved by the caller at creation time and frozen here — a
        link is a record of what was handed to the agent, so its contents must not drift as new
        conversations are fetched. Both default to None, which reproduces the old unscoped
        behaviour for any caller that does not care.
        """
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            """INSERT INTO agent_review_tokens
               (token, agent_name, tag, label, created_by, created_at, expires_at, session_id,
                since, until)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (token, agent_name, tag, label, created_by, now, expires_at, session_id,
             since, until),
        )
        if conversation_ids:
            self._conn.executemany(
                "INSERT OR IGNORE INTO agent_review_token_items (token, conversation_id) "
                "VALUES (?, ?)",
                [(token, cid) for cid in conversation_ids],
            )
        self._conn.commit()

    def item_ids(self, token: str) -> list[str]:
        """The conversation ids frozen into this link. Empty means the link covers nothing."""
        rows = self._conn.execute(
            "SELECT conversation_id FROM agent_review_token_items WHERE token=?", (token,)
        ).fetchall()
        return [r["conversation_id"] for r in rows]

    def covers(self, token: str, conversation_id: str) -> bool:
        """Whether the link includes this conversation.

        A link with no membership covers nothing. It used to return None for that case and the
        caller fell back to "does this conversation belong to the agent?", which made an
        agent's entire history reachable through any one of their links.
        """
        return conversation_id in set(self.item_ids(token))

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
        self._conn.execute(
            "DELETE FROM agent_review_token_items WHERE token=?", (token,)
        )
        cur = self._conn.execute(
            "DELETE FROM agent_review_tokens WHERE token=?", (token,)
        )
        self._conn.commit()
        return cur.rowcount > 0
