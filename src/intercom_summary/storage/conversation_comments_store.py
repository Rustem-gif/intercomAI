"""Manager comments on individual conversations."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path

from intercom_summary.settings import settings
from intercom_summary.storage.db import connect


class ConversationCommentsStore:
    def __init__(self, db_path=None) -> None:
        self._conn = connect(db_path or settings.db_path)

    def close(self) -> None:
        self._conn.close()

    def add(self, conversation_id: str, author: str, text: str) -> dict:
        comment_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "INSERT INTO conversation_comments (id, conversation_id, author, text, created_at) VALUES (?,?,?,?,?)",
            (comment_id, conversation_id, author, text, now),
        )
        self._conn.commit()
        return {"id": comment_id, "conversation_id": conversation_id, "author": author, "text": text, "created_at": now}

    def list(self, conversation_id: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT id, conversation_id, author, text, created_at FROM conversation_comments WHERE conversation_id=? ORDER BY created_at ASC",
            (conversation_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def delete(self, comment_id: str) -> bool:
        cur = self._conn.execute("DELETE FROM conversation_comments WHERE id=?", (comment_id,))
        self._conn.commit()
        return cur.rowcount > 0

    def get(self, comment_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT id, conversation_id, author, text, created_at FROM conversation_comments WHERE id=?",
            (comment_id,),
        ).fetchone()
        return dict(row) if row else None
