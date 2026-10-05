"""Persistence for frozen calibration samples — the fixed chat sets the AI is measured on.

A calibration sample is not a saved search. Its whole value is that it does not move: two
measurement runs are only comparable if they graded the same chats, so membership is written
down once and `replace_items` refuses to touch a sample already marked frozen.
"""
from __future__ import annotations

import json
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

    # ── runs & results ───────────────────────────────────────────────────────────────
    # A run's grades are kept here and nowhere else: the same chats are on the dashboards with
    # their own live grade (and sometimes a human override), which a measurement run must not
    # replace.
    def start_run(self, sample_id: str, ruleset_id: str, rules_version: str = "",
                  model: str = "", effort: str = "", job_id: str | None = None,
                  created_by: str = "") -> str:
        import uuid

        run_id = uuid.uuid4().hex[:12]
        self._conn.execute(
            """INSERT INTO calibration_runs
               (id, sample_id, ruleset_id, rules_version, model, effort, started_at, job_id,
                created_by)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (run_id, sample_id, ruleset_id, rules_version, model, effort, _now(), job_id,
             created_by),
        )
        self._conn.commit()
        return run_id

    def finish_run(self, run_id: str, cost_usd: float | None = None) -> None:
        self._conn.execute(
            "UPDATE calibration_runs SET finished_at=?, cost_usd=? WHERE id=?",
            (_now(), cost_usd, run_id),
        )
        self._conn.commit()

    def get_run(self, run_id: str) -> dict | None:
        row = self._conn.execute("SELECT * FROM calibration_runs WHERE id=?", (run_id,)).fetchone()
        return dict(row) if row else None

    def latest_run(self, sample_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM calibration_runs WHERE sample_id=? ORDER BY started_at DESC LIMIT 1",
            (sample_id,),
        ).fetchone()
        return dict(row) if row else None

    def list_runs(self, sample_id: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM calibration_runs WHERE sample_id=? ORDER BY started_at DESC",
            (sample_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def save_result(self, run_id: str, conversation_id: str, status: str, *,
                    source: str | None = None, grade=None, conversation=None,
                    error: str | None = None) -> None:
        """Record one member's outcome. A re-save (e.g. a retried chat) replaces the AI side
        and keeps whatever a QA manager has already recorded."""
        payload = json.dumps(grade.to_dict()) if grade is not None else None
        convo = json.dumps(conversation.to_dict()) if conversation is not None else None
        self._conn.execute(
            """INSERT INTO calibration_results
               (run_id, conversation_id, source, status, error, overall_score, payload_json,
                conversation_json)
               VALUES (?,?,?,?,?,?,?,?)
               ON CONFLICT(run_id, conversation_id) DO UPDATE SET
                   source = COALESCE(excluded.source, calibration_results.source),
                   status = excluded.status,
                   error = excluded.error,
                   overall_score = excluded.overall_score,
                   payload_json = excluded.payload_json,
                   conversation_json = COALESCE(excluded.conversation_json,
                                                calibration_results.conversation_json)""",
            (run_id, str(conversation_id), source, status, error,
             grade.overall_score if grade is not None else None, payload, convo),
        )
        self._conn.commit()

    def results(self, run_id: str) -> list[dict]:
        """Every sample member with this run's outcome (NULL status = not reached yet), in
        document order, plus the chat's live grade for reference."""
        run = self.get_run(run_id)
        if run is None:
            return []
        rows = self._conn.execute(
            """SELECT i.seq, i.conversation_id, i.chat_date, i.agent_name, i.category, i.pilot,
                      r.source, r.status, r.error, r.overall_score, r.payload_json,
                      r.human_score, r.human_criteria, r.human_note, r.reviewed_by,
                      r.reviewed_at,
                      g.overall_score AS live_score, g.human_score AS live_human_score,
                      g.ruleset_id AS live_ruleset_id
                 FROM calibration_sample_items i
                 LEFT JOIN calibration_results r
                        ON r.conversation_id = i.conversation_id AND r.run_id = ?
                 LEFT JOIN grades g ON g.conversation_id = i.conversation_id
                WHERE i.sample_id = ?
                ORDER BY i.seq""",
            (run_id, run["sample_id"]),
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["pilot"] = bool(d["pilot"])
            d["payload"] = json.loads(d.pop("payload_json")) if d["payload_json"] else None
            d["human_criteria"] = json.loads(d["human_criteria"]) if d["human_criteria"] else None
            out.append(d)
        return out

    def result(self, run_id: str, conversation_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM calibration_results WHERE run_id=? AND conversation_id=?",
            (run_id, conversation_id),
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        for k in ("payload_json", "conversation_json", "human_criteria", "human_deductions"):
            d[k] = json.loads(d[k]) if d[k] else None
        return d

    def save_review(self, run_id: str, conversation_id: str, human_score: int, note: str,
                    reviewed_by: str, human_criteria: dict | None = None,
                    human_deductions: list | None = None) -> bool:
        cur = self._conn.execute(
            """UPDATE calibration_results
                  SET human_score=?, human_note=?, reviewed_by=?, reviewed_at=?,
                      human_criteria=?, human_deductions=?
                WHERE run_id=? AND conversation_id=? AND status='graded'""",
            (human_score, note, reviewed_by, _now(),
             json.dumps(human_criteria) if human_criteria else None,
             json.dumps(human_deductions) if human_deductions else None,
             run_id, conversation_id),
        )
        self._conn.commit()
        return cur.rowcount > 0


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
