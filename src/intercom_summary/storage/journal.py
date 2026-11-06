"""Append-only journal of QA's work: re-scores, Agree, comments, disputes, tags, …

Why: on 6 Nov 2026 the database was deleted, and every re-score QA had made since the last
usable backup was gone for good — the server had kept them nowhere else. The DB is backed up
once a day; this journal closes the gap. Every human action is written here the moment it is
saved, to a file on a *different* disk from the DB (JOURNAL_DIR, internal disk by default),
and backup.sync_journal() copies it offsite every hour. After a restore,
scripts/replay_journal.py re-applies everything newer than the snapshot.

One JSON object per line, in monthly files (human-actions-YYYY-MM.jsonl):
    {"ts": "<UTC ISO>", "action": "grade.override", "conversation_id": "…", …}

`record()` never raises: a journal problem must not cost QA the action they just took. A
failure is logged and kept in `last_error`, which the Storage page shows in red.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from intercom_summary.settings import settings

log = logging.getLogger(__name__)

_lock = threading.Lock()
# Process-local status for the Storage page; the files themselves are the source of truth.
_state: dict[str, Any] = {"last_ok_at": None, "last_error": None, "last_error_at": None}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def journal_dir() -> Path:
    return Path(settings.journal_dir)


def record(action: str, *, at: str | None = None, **fields: Any) -> None:
    """Append one action. `at` is the time the action happened (defaults to now)."""
    if not settings.journal_enabled:
        return
    entry = {"ts": at or _now(), "action": action, **fields}
    line = json.dumps(entry, ensure_ascii=False, default=str) + "\n"
    try:
        d = journal_dir()
        path = d / f"human-actions-{entry['ts'][:7]}.jsonl"
        with _lock:
            d.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(line)
                fh.flush()
                os.fsync(fh.fileno())
        _state["last_ok_at"] = entry["ts"]
    except Exception as exc:  # noqa: BLE001 — never fail the user's action over the journal
        _state["last_error"] = f"{type(exc).__name__}: {exc}"
        _state["last_error_at"] = _now()
        log.error("QA ACTION JOURNAL WRITE FAILED (%s on %s): %s", action,
                  fields.get("conversation_id") or fields.get("case_id") or "?", exc)


def entries(since: str | None = None, until: str | None = None) -> Iterator[dict]:
    """Every journal entry with since <= ts < until, oldest first."""
    d = journal_dir()
    if not d.exists():
        return
    out = []
    for path in sorted(d.glob("human-actions-*.jsonl")):
        month = path.stem.removeprefix("human-actions-")
        if since and month < since[:7]:
            continue
        if until and month > until[:7]:
            continue
        with open(path, encoding="utf-8") as fh:
            for n, raw in enumerate(fh, 1):
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    e = json.loads(raw)
                except json.JSONDecodeError:
                    log.warning("Skipping unreadable journal line %s:%d", path.name, n)
                    continue
                if since and e["ts"] < since:
                    continue
                if until and e["ts"] >= until:
                    continue
                out.append(e)
    out.sort(key=lambda e: e["ts"])
    yield from out


def last_ts() -> str | None:
    """Timestamp of the newest entry (read from the newest file), or None."""
    d = journal_dir()
    files = sorted(d.glob("human-actions-*.jsonl")) if d.exists() else []
    for path in reversed(files):
        last = None
        with open(path, encoding="utf-8") as fh:
            for raw in fh:
                if raw.strip():
                    last = raw
        if last:
            try:
                return json.loads(last)["ts"]
            except (json.JSONDecodeError, KeyError):
                return None
    return None


def status() -> dict[str, Any]:
    d = journal_dir()
    files = sorted(d.glob("human-actions-*.jsonl")) if d.exists() else []
    this_month = d / f"human-actions-{_now()[:7]}.jsonl"
    month_entries = 0
    if this_month.exists():
        with open(this_month, encoding="utf-8") as fh:
            month_entries = sum(1 for raw in fh if raw.strip())
    return {
        "enabled": settings.journal_enabled,
        "dir": str(d),
        "files": len(files),
        "bytes": sum(f.stat().st_size for f in files),
        "entries_this_month": month_entries,
        "last_entry_at": last_ts(),
        "last_error": _state["last_error"],
        "last_error_at": _state["last_error_at"],
    }
