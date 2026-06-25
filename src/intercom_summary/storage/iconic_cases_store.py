"""Persistent store for the knowledge-base of iconic / representative cases.

Each case carries a frozen `snapshot` (the conversation transcript + grade at the moment it
was curated) so the exemplar stays viewable even after the source conversation/grade is
deleted from the cache.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from intercom_summary.settings import settings
from intercom_summary.storage.db import connect


class IconicCasesStore:
    def __init__(self, db_path: str | Path | None = None) -> None:
        self._conn = connect(db_path or settings.db_path)

    def close(self) -> None:
        self._conn.close()

    def add(
        self,
        conversation_id: str,
        added_by: str,
        comment: str = "",
        snapshot: dict | None = None,
    ) -> None:
        self._conn.execute(
            """INSERT OR REPLACE INTO iconic_cases
               (conversation_id, added_by, added_at, manager_comment, snapshot_json)
               VALUES (?, ?, ?, ?, ?)""",
            (
                conversation_id, added_by, datetime.now(timezone.utc).isoformat(),
                comment, json.dumps(snapshot) if snapshot else None,
            ),
        )
        self._conn.commit()

    def set_snapshot(self, conversation_id: str, snapshot: dict) -> bool:
        """Attach/refresh the frozen snapshot for a case (used by the backfill)."""
        cur = self._conn.execute(
            "UPDATE iconic_cases SET snapshot_json=? WHERE conversation_id=?",
            (json.dumps(snapshot), conversation_id),
        )
        self._conn.commit()
        return cur.rowcount > 0

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
        """Case metadata only (no snapshot) — used to flag a conversation as iconic."""
        row = self._conn.execute(
            """SELECT conversation_id, added_by, added_at, manager_comment
               FROM iconic_cases WHERE conversation_id=?""",
            (conversation_id,),
        ).fetchone()
        return dict(row) if row else None

    def get_snapshot(self, conversation_id: str) -> dict | None:
        """The frozen exemplar (conversation/transcript/grade) for a case, or None."""
        row = self._conn.execute(
            "SELECT snapshot_json FROM iconic_cases WHERE conversation_id=?",
            (conversation_id,),
        ).fetchone()
        if not row or not row["snapshot_json"]:
            return None
        return json.loads(row["snapshot_json"])

    def list_all(self) -> list[dict]:
        """All cases with their parsed `snapshot` (newest first)."""
        rows = self._conn.execute(
            """SELECT conversation_id, added_by, added_at, manager_comment, snapshot_json
               FROM iconic_cases ORDER BY added_at DESC"""
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            raw = d.pop("snapshot_json", None)
            d["snapshot"] = json.loads(raw) if raw else None
            out.append(d)
        return out

    def is_iconic(self, conversation_id: str) -> bool:
        return self._conn.execute(
            "SELECT 1 FROM iconic_cases WHERE conversation_id=?", (conversation_id,)
        ).fetchone() is not None
