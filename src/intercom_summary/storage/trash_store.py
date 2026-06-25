"""Soft-delete trash for conversations.

Deleting a conversation moves it (and its grade) here as a JSON snapshot of the raw rows,
so it can be restored verbatim. Purging removes it permanently. This keeps the main
conversations/grades queries untouched — deleted rows simply leave those tables.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from intercom_summary.settings import settings
from intercom_summary.storage.db import connect


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _reinsert(conn, table: str, row: dict) -> None:
    cols = ",".join(row.keys())
    ph = ",".join("?" * len(row))
    conn.execute(
        f"INSERT OR REPLACE INTO {table} ({cols}) VALUES ({ph})", list(row.values())
    )


class TrashStore:
    def __init__(self, db_path: str | Path | None = None) -> None:
        self._conn = connect(db_path or settings.db_path)

    def close(self) -> None:
        self._conn.close()

    def move_to_trash(self, conversation_ids: list[str], deleted_by: str) -> int:
        """Move conversations (and their grades) into the trash. Returns count moved."""
        moved = 0
        now = _now()
        for cid in conversation_ids:
            crow = self._conn.execute(
                "SELECT * FROM conversations WHERE id=?", (cid,)
            ).fetchone()
            if not crow:
                continue
            grow = self._conn.execute(
                "SELECT * FROM grades WHERE conversation_id=?", (cid,)
            ).fetchone()
            snapshot = {
                "conversation": dict(crow),
                "grade": dict(grow) if grow else None,
            }
            self._conn.execute(
                """INSERT OR REPLACE INTO deleted_conversations
                   (conversation_id, agent_name, subject, created_at,
                    deleted_at, deleted_by, snapshot_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (cid, crow["agent_name"], crow["subject"], crow["created_at"],
                 now, deleted_by, json.dumps(snapshot)),
            )
            self._conn.execute("DELETE FROM grades WHERE conversation_id=?", (cid,))
            self._conn.execute("DELETE FROM conversations WHERE id=?", (cid,))
            moved += 1
        self._conn.commit()
        return moved

    def restore(self, conversation_ids: list[str]) -> int:
        """Re-insert trashed conversations (and their grades). Returns count restored."""
        restored = 0
        for cid in conversation_ids:
            row = self._conn.execute(
                "SELECT snapshot_json FROM deleted_conversations WHERE conversation_id=?",
                (cid,),
            ).fetchone()
            if not row:
                continue
            snap = json.loads(row["snapshot_json"])
            _reinsert(self._conn, "conversations", snap["conversation"])
            if snap.get("grade"):
                _reinsert(self._conn, "grades", snap["grade"])
            self._conn.execute(
                "DELETE FROM deleted_conversations WHERE conversation_id=?", (cid,)
            )
            restored += 1
        self._conn.commit()
        return restored

    def purge(self, conversation_ids: list[str] | None = None) -> int:
        """Permanently remove trashed items. None/empty = empty the whole trash."""
        if conversation_ids:
            ph = ",".join("?" * len(conversation_ids))
            cur = self._conn.execute(
                f"DELETE FROM deleted_conversations WHERE conversation_id IN ({ph})",
                conversation_ids,
            )
        else:
            cur = self._conn.execute("DELETE FROM deleted_conversations")
        self._conn.commit()
        return cur.rowcount

    def list_all(self, limit: int = 500) -> list[dict]:
        rows = self._conn.execute(
            """SELECT conversation_id, agent_name, subject, created_at, deleted_at, deleted_by
               FROM deleted_conversations ORDER BY deleted_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def count(self) -> int:
        return self._conn.execute(
            "SELECT COUNT(*) AS n FROM deleted_conversations"
        ).fetchone()["n"]
