"""Persistence for frozen calibration samples — the fixed chat sets the AI is measured on.

A calibration sample is not a saved search. Its whole value is that it does not move: two
measurement runs are only comparable if they graded the same chats, so membership is written
down once and `replace_items` refuses to touch a sample already marked frozen.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from intercom_summary.settings import settings


class SampleFrozenError(Exception):
    """Refused to change the membership of a frozen sample."""


class CalibrationStore:
    def __init__(self, db_path: str | Path | None = None) -> None:
        from intercom_summary.storage.db import connect
        self._conn = connect(db_path or settings.db_path)

    def close(self) -> None:
        self._conn.close()

    # ── samples ──────────────────────────────────────────────────────────────────────
    def create(self, sample_id: str, name: str, source: str = "",
               created_by: str = "", frozen: bool = True) -> None:
        self._conn.execute(
            """INSERT INTO calibration_samples (id, name, source, created_at, created_by, frozen)
               VALUES (?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET name=excluded.name, source=excluded.source""",
            (sample_id, name, source, datetime.now(timezone.utc).isoformat(),
             created_by, int(frozen)),
        )
        self._conn.commit()

    def get(self, sample_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM calibration_samples WHERE id=?", (sample_id,)
        ).fetchone()
        return dict(row) if row else None

    def list_samples(self) -> list[dict]:
        rows = self._conn.execute(
            """SELECT s.*,
                      (SELECT COUNT(*) FROM calibration_sample_items i
                        WHERE i.sample_id = s.id) AS size,
                      (SELECT COUNT(*) FROM calibration_sample_items i
                        WHERE i.sample_id = s.id AND i.pilot = 1) AS pilot_size
                 FROM calibration_samples s ORDER BY s.created_at DESC"""
        ).fetchall()
        return [dict(r) for r in rows]

    # ── membership ───────────────────────────────────────────────────────────────────
    def replace_items(self, sample_id: str, items: list[dict], force: bool = False) -> int:
        """Set the sample's membership. Refuses on a frozen sample unless forced.

        `items` are dicts of conversation_id, seq, chat_date, agent_name, category, pilot.
        """
        sample = self.get(sample_id)
        if sample is None:
            raise KeyError(f"No calibration sample {sample_id!r}")
        existing = self._conn.execute(
            "SELECT COUNT(*) AS n FROM calibration_sample_items WHERE sample_id=?",
            (sample_id,),
        ).fetchone()["n"]
        # Freezing protects membership that exists; it does not stop a sample being populated
        # for the first time.
        if existing and sample["frozen"] and not force:
            raise SampleFrozenError(
                f"Sample {sample_id!r} is frozen. Re-collecting it would make earlier "
                f"measurement runs incomparable; pass force=True only if that is intended."
            )
        self._conn.execute(
            "DELETE FROM calibration_sample_items WHERE sample_id=?", (sample_id,)
        )
        self._conn.executemany(
            """INSERT INTO calibration_sample_items
               (sample_id, conversation_id, seq, chat_date, agent_name, category, pilot)
               VALUES (?,?,?,?,?,?,?)""",
            [
                (sample_id, str(i["conversation_id"]), int(i.get("seq", 0) or 0),
                 i.get("chat_date"), i.get("agent_name"), i.get("category"),
                 int(bool(i.get("pilot"))))
                for i in items
            ],
        )
        self._conn.commit()
        return len(items)

    def items(self, sample_id: str, pilot_only: bool = False) -> list[dict]:
        sql = "SELECT * FROM calibration_sample_items WHERE sample_id=?"
        if pilot_only:
            sql += " AND pilot=1"
        rows = self._conn.execute(sql + " ORDER BY seq", (sample_id,)).fetchall()
        return [dict(r) for r in rows]

    def conversation_ids(self, sample_id: str, pilot_only: bool = False) -> list[str]:
        return [i["conversation_id"] for i in self.items(sample_id, pilot_only)]

    def missing_conversations(self, sample_id: str) -> list[str]:
        """Sample members that are not in the conversations cache, so cannot be graded.

        A member can also be missing because it was soft-deleted: a conversation sitting in
        the trash silently blocks its own re-import from Intercom, so a caller checking why
        a sample is short has to look there too (see `trashed_conversations`).
        """
        rows = self._conn.execute(
            """SELECT i.conversation_id FROM calibration_sample_items i
                LEFT JOIN conversations c ON c.id = i.conversation_id
               WHERE i.sample_id=? AND c.id IS NULL
               ORDER BY i.seq""",
            (sample_id,),
        ).fetchall()
        return [r["conversation_id"] for r in rows]

    def trashed_conversations(self, sample_id: str) -> list[str]:
        rows = self._conn.execute(
            """SELECT i.conversation_id FROM calibration_sample_items i
                JOIN deleted_conversations d ON d.conversation_id = i.conversation_id
               WHERE i.sample_id=? ORDER BY i.seq""",
            (sample_id,),
        ).fetchall()
        return [r["conversation_id"] for r in rows]
