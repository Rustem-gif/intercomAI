"""Persistence for support-agent disputes of the QA grade a conversation received.

One active record per conversation. A manager resolves a dispute by accepting it
(and re-scoring the conversation through the override flow) or rejecting it.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from intercom_summary.settings import settings


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class GradeDisputesStore:
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
            """INSERT OR REPLACE INTO grade_disputes
               (conversation_id, agent_name, reason, created_via, created_by, created_at,
                status, resolution_note, resolved_by, resolved_at)
               VALUES (?, ?, ?, ?, ?, ?, 'open', NULL, NULL, NULL)""",
            (conversation_id, agent_name, reason.strip(), created_via, created_by, _now()),
        )
        self._conn.commit()
        return True

    def get(self, conversation_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM grade_disputes WHERE conversation_id=?", (conversation_id,)
        ).fetchone()
        return dict(row) if row else None

    def resolve(
        self, conversation_id: str, status: str, note: str, resolved_by: str
    ) -> bool:
        """Set an open dispute to 'accepted' or 'rejected'. The actual score change (on
        accept) is applied separately via the grade-override flow."""
        cur = self._conn.execute(
            """UPDATE grade_disputes
               SET status=?, resolution_note=?, resolved_by=?, resolved_at=?
               WHERE conversation_id=?""",
            (status, note.strip(), resolved_by, _now(), conversation_id),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def list(self, status: str | None = None, agent: str | None = None) -> list[dict]:
        """Disputes joined with conversation + grade context (subject, current effective
        score) for the manager queue. Newest first."""
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
            f"""SELECT d.*, c.subject,
                       COALESCE(g.human_score, g.overall_score) AS score
                FROM grade_disputes d
                LEFT JOIN conversations c ON c.id = d.conversation_id
                LEFT JOIN grades g ON g.conversation_id = d.conversation_id
                {clause}
                ORDER BY d.created_at DESC""",
            args,
        ).fetchall()
        return [dict(r) for r in rows]
