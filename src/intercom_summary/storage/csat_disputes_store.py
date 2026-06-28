"""Persistence for support-agent disputes of a conversation's Intercom CSAT rating.

One active record per conversation. An 'accepted' dispute means a manager agreed the
rating was unfair, so it is excluded from the agent's CSAT stats (see
ConversationsStore.agent_csat / query).
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from intercom_summary.settings import settings


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class CsatDisputesStore:
    def __init__(self, db_path: str | Path | None = None) -> None:
        from intercom_summary.storage.db import connect
        self._conn = connect(db_path or settings.db_path)

    def close(self) -> None:
        self._conn.close()

    def create(
        self,
        conversation_id: str,
        agent_name: str,
        reason: str,
        created_via: str,
        created_by: str,
    ) -> bool:
        """Open a new dispute. Returns False if one is already open/accepted for this
        conversation (a rejected dispute may be re-raised)."""
        existing = self.get(conversation_id)
        if existing and existing["status"] in ("open", "accepted"):
            return False
        self._conn.execute(
            """INSERT OR REPLACE INTO csat_disputes
               (conversation_id, agent_name, reason, created_via, created_by, created_at,
                status, resolution_note, resolved_by, resolved_at)
               VALUES (?, ?, ?, ?, ?, ?, 'open', NULL, NULL, NULL)""",
            (conversation_id, agent_name, reason.strip(), created_via, created_by, _now()),
        )
        self._conn.commit()
        return True

    def get(self, conversation_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM csat_disputes WHERE conversation_id=?", (conversation_id,)
        ).fetchone()
        return dict(row) if row else None

    def resolve(
        self, conversation_id: str, status: str, note: str, resolved_by: str
    ) -> bool:
        """Set an open dispute to 'accepted' or 'rejected'."""
        cur = self._conn.execute(
            """UPDATE csat_disputes
               SET status=?, resolution_note=?, resolved_by=?, resolved_at=?
               WHERE conversation_id=?""",
            (status, note.strip(), resolved_by, _now(), conversation_id),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def list(self, status: str | None = None, agent: str | None = None) -> list[dict]:
        """Disputes joined with conversation context (subject, csat_rating) for the
        manager queue. Newest first."""
        where: list[str] = []
        args: list[object] = []
        if status:
            where.append("d.status = ?")
            args.append(status)
        if agent:
            where.append("d.agent_name = ?")
            args.append(agent)
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        rows = self._conn.execute(
            f"""SELECT d.*, c.subject, c.csat_rating, c.created_at AS conversation_created_at
                FROM csat_disputes d
                LEFT JOIN conversations c ON c.id = d.conversation_id
                {clause}
                ORDER BY d.created_at DESC""",
            args,
        ).fetchall()
        return [dict(r) for r in rows]
