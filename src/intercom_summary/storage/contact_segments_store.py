"""Cache of the player's VIP segment per Intercom contact (see intercom/segments.py).

A player's segment changes slowly, and the same player writes many chats, so one GET
/contacts/{id} a week per player is plenty. A stale row is refetched, never deleted.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from intercom_summary.settings import settings
from intercom_summary.storage.db import connect

MAX_AGE = timedelta(days=7)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ContactSegmentsStore:
    def __init__(self, db_path: str | Path | None = None, max_age: timedelta = MAX_AGE) -> None:
        self._conn: sqlite3.Connection = connect(db_path or settings.db_path)
        self._max_age = max_age

    def close(self) -> None:
        self._conn.close()

    def get(self, contact_id: str) -> tuple[str, str] | None:
        """(segment, loyalty_level) when a fresh row exists, else None (= look it up)."""
        row = self._conn.execute(
            "SELECT segment, loyalty_level, fetched_at FROM contact_segments WHERE contact_id=?",
            (contact_id,),
        ).fetchone()
        if not row:
            return None
        try:
            fetched = datetime.fromisoformat(row["fetched_at"])
        except (TypeError, ValueError):
            return None
        if _now() - fetched > self._max_age:
            return None
        return row["segment"], row["loyalty_level"]

    def known(self, contact_id: str) -> tuple[str, str] | None:
        """The last segment recorded for the player, however old."""
        row = self._conn.execute(
            "SELECT segment, loyalty_level FROM contact_segments WHERE contact_id=?",
            (contact_id,),
        ).fetchone()
        return (row["segment"], row["loyalty_level"]) if row else None

    def put(self, contact_id: str, segment: str, loyalty_level: str, groups_raw: str) -> None:
        self._conn.execute(
            """INSERT OR REPLACE INTO contact_segments
               (contact_id, segment, loyalty_level, groups_raw, fetched_at)
               VALUES (?,?,?,?,?)""",
            (contact_id, segment, loyalty_level, groups_raw, _now().isoformat()),
        )
        self._conn.commit()

    def counts(self) -> dict[str, int]:
        rows = self._conn.execute(
            "SELECT segment, COUNT(*) AS n FROM contact_segments GROUP BY segment").fetchall()
        return {r["segment"]: r["n"] for r in rows}
