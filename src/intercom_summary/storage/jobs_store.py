"""Track background jobs (fetch / review) so the UI and Slack can poll progress."""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from intercom_summary.settings import settings
from intercom_summary.storage.db import connect


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class JobsStore:
    def __init__(self, db_path: str | Path | None = None) -> None:
        self._conn: sqlite3.Connection = connect(db_path or settings.db_path)

    def close(self) -> None:
        self._conn.close()

    def create(self, kind: str, params: dict) -> str:
        job_id = uuid.uuid4().hex[:12]
        now = _now()
        self._conn.execute(
            """INSERT INTO jobs (id, kind, status, params_json, result_json, error,
                                 created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (job_id, kind, "queued", json.dumps(params), None, None, now, now),
        )
        self._conn.commit()
        return job_id

    def update(self, job_id: str, *, status: str | None = None,
               result: dict | None = None, error: str | None = None) -> None:
        sets, args = ["updated_at = ?"], [_now()]
        if status is not None:
            sets.append("status = ?")
            args.append(status)
        if result is not None:
            sets.append("result_json = ?")
            args.append(json.dumps(result))
        if error is not None:
            sets.append("error = ?")
            args.append(error)
        args.append(job_id)
        self._conn.execute(f"UPDATE jobs SET {', '.join(sets)} WHERE id = ?", args)
        self._conn.commit()

    def get(self, job_id: str) -> dict | None:
        row = self._conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        d["params"] = json.loads(d.pop("params_json") or "{}")
        d["result"] = json.loads(d.pop("result_json") or "null")
        return d

    def list_recent(self, kind: str | None = None, limit: int = 20) -> list[dict]:
        if kind:
            rows = self._conn.execute(
                "SELECT * FROM jobs WHERE kind = ? ORDER BY created_at DESC LIMIT ?",
                (kind, limit),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        result = []
        for row in rows:
            d = dict(row)
            d["params"] = json.loads(d.pop("params_json") or "{}")
            d["result"] = json.loads(d.pop("result_json") or "null")
            result.append(d)
        return result
