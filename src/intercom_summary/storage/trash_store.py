"""Soft-delete trash for conversations.

Deleting a conversation moves it (and its grade) here as a JSON snapshot of the raw rows,
so it can be restored verbatim. Purging removes it permanently. This keeps the main
conversations/grades queries untouched — deleted rows simply leave those tables.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
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

    def move_to_trash(
        self, conversation_ids: list[str], deleted_by: str, blacklist: bool = True
    ) -> int:
        """Move conversations (and their grades) into the trash. Returns count moved.

        `blacklist=True` (an analyst deliberately deleting specific conversations) also bars
        them from ever being re-imported by a later Intercom fetch. Pass False for bulk
        "clear the workspace" deletes, where the intent is to empty the local cache rather
        than to blacklist — those stay restorable but a re-fetch may bring them back.
        """
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
                    deleted_at, deleted_by, snapshot_json, blacklist)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (cid, crow["agent_name"], crow["subject"], crow["created_at"],
                 now, deleted_by, json.dumps(snapshot), 1 if blacklist else 0),
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

    def list_all(self, limit: int = 500, offset: int = 0) -> list[dict]:
        rows = self._conn.execute(
            """SELECT conversation_id, agent_name, subject, created_at, deleted_at,
                      deleted_by, blacklist
               FROM deleted_conversations ORDER BY deleted_at DESC LIMIT ? OFFSET ?""",
            (limit, offset),
        ).fetchall()
        return [dict(r) for r in rows]

    def count(self) -> int:
        return self._conn.execute(
            "SELECT COUNT(*) AS n FROM deleted_conversations"
        ).fetchone()["n"]

    def expire(self, days: int) -> int:
        """Purge tombstones deleted more than `days` ago. Returns the count removed.

        Without this the trash — and the blacklist that comes with it — grows forever.
        `days <= 0` disables expiry.
        """
        if days <= 0:
            return 0
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        cur = self._conn.execute(
            "DELETE FROM deleted_conversations WHERE deleted_at < ?", (cutoff,)
        )
        self._conn.commit()
        return cur.rowcount

    def stats(self) -> dict:
        """Counts and age bounds for the Storage panel."""
        row = self._conn.execute(
            """SELECT COUNT(*) AS total,
                      COALESCE(SUM(blacklist), 0) AS blacklisted,
                      MIN(deleted_at) AS oldest,
                      MAX(deleted_at) AS newest,
                      COALESCE(SUM(LENGTH(snapshot_json)), 0) AS bytes
               FROM deleted_conversations"""
        ).fetchone()
        return dict(row)

    def count_expiring_before(self, days: int) -> int:
        """How many tombstones the next expiry run would remove."""
        if days <= 0:
            return 0
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        return self._conn.execute(
            "SELECT COUNT(*) AS n FROM deleted_conversations WHERE deleted_at < ?", (cutoff,)
        ).fetchone()["n"]
