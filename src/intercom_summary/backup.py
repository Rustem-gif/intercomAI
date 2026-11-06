"""Backups of the database (+ config/rules), made by the web app itself.

Why in-process: the old launchd job (scripts/backup.sh) ran `/bin/bash` on a script under
~/Desktop, which macOS privacy protection refuses. It failed every night from 26 Aug 2026 with
"Operation not permitted", nobody saw it, and when the DB was deleted on 6 Nov the newest copy
was ten weeks old. The web process already has access to the data, and it records every run's
outcome in `meta`, so the Storage page can show a backup that has stopped working.

Each run:
  1. SQLite online backup of DB_PATH into BACKUP_DIR/<stamp>/grades.db (safe while the app
     writes), then `quick_check` on the copy — a backup that cannot be opened is no backup.
  2. config/ + rules/ into config.tar.gz.
  3. Prune local snapshots older than BACKUP_KEEP_DAYS, never the newest one.
  4. `rclone copy` the snapshot to RCLONE_REMOTE/<stamp> when set. Remote copies are never
     deleted here.
"""
from __future__ import annotations

import json
import logging
import shutil
import sqlite3
import subprocess
import tarfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from intercom_summary.settings import settings

log = logging.getLogger(__name__)

STAMP_FORMAT = "%Y%m%d_%H%M%S"
META_OK = "backup.last_ok_at"
META_RESULT = "backup.last_result"
META_ERROR = "backup.last_error"
META_ERROR_AT = "backup.last_error_at"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _meta_conn(db_path: Path) -> sqlite3.Connection:
    from intercom_summary.storage.db import connect

    return connect(db_path)


def _set_meta(db_path: Path, values: dict[str, str]) -> None:
    conn = _meta_conn(db_path)
    try:
        now = _now().isoformat()
        for k, v in values.items():
            conn.execute(
                """INSERT INTO meta (key, value, updated_at) VALUES (?,?,?)
                   ON CONFLICT(key) DO UPDATE SET value=excluded.value,
                                                  updated_at=excluded.updated_at""",
                (k, v, now))
        conn.commit()
    finally:
        conn.close()


def status(db_path: Path | None = None) -> dict[str, Any]:
    """The last run's outcome, for the Storage page. `stale` = no good backup within
    twice the interval (or ever), which is what the page shows in red."""
    db_path = Path(db_path or settings.db_path)
    conn = _meta_conn(db_path)
    try:
        rows = dict(conn.execute(
            "SELECT key, value FROM meta WHERE key IN (?,?,?,?)",
            (META_OK, META_RESULT, META_ERROR, META_ERROR_AT)).fetchall())
    finally:
        conn.close()
    last_ok = rows.get(META_OK)
    stale = True
    if last_ok:
        stale = _now() - datetime.fromisoformat(last_ok) > timedelta(hours=2 * settings.backup_hours)
    error_at = rows.get(META_ERROR_AT)
    failing = bool(error_at and (not last_ok or error_at > last_ok))
    return {
        "dir": str(settings.backup_dir),
        "remote": settings.rclone_remote,
        "interval_hours": settings.backup_hours,
        "last_ok_at": last_ok,
        "last_result": json.loads(rows[META_RESULT]) if rows.get(META_RESULT) else None,
        "last_error": rows.get(META_ERROR) if failing else None,
        "last_error_at": error_at if failing else None,
        "stale": stale,
    }


def _rclone() -> str | None:
    # launchd starts the app with a bare PATH, so look in Homebrew's prefixes too.
    return (settings.rclone_bin or shutil.which("rclone")
            or next((p for p in ("/opt/homebrew/bin/rclone", "/usr/local/bin/rclone")
                     if Path(p).exists()), None))


def _prune(backup_dir: Path, keep_days: int, keep: str) -> list[str]:
    cutoff = _now() - timedelta(days=keep_days)
    removed = []
    for d in sorted(backup_dir.iterdir()):
        if not d.is_dir() or d.name == keep:
            continue
        try:
            made = datetime.strptime(d.name, STAMP_FORMAT).replace(tzinfo=timezone.utc)
        except ValueError:
            continue                      # not ours — leave it alone
        if made < cutoff:
            shutil.rmtree(d, ignore_errors=True)
            removed.append(d.name)
    return removed


def run_backup(db_path: Path | None = None, backup_dir: Path | None = None,
               root: Path | None = None) -> dict[str, Any]:
    """Make one snapshot. Records the outcome in `meta` and re-raises on failure."""
    db_path = Path(db_path or settings.db_path)
    backup_dir = Path(backup_dir or settings.backup_dir)
    root = Path(root or Path.cwd())
    stamp = _now().strftime(STAMP_FORMAT)
    snap = backup_dir / stamp
    try:
        if not db_path.exists():
            raise FileNotFoundError(f"database not found at {db_path}")
        snap.mkdir(parents=True, exist_ok=False)

        target = snap / "grades.db"
        src = sqlite3.connect(db_path)
        dst = sqlite3.connect(target)
        try:
            src.backup(dst)
            ok = dst.execute("PRAGMA quick_check").fetchone()[0]
            conversations = dst.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
            grades = dst.execute("SELECT COUNT(*) FROM grades").fetchone()[0]
        finally:
            dst.close()
            src.close()
        if ok != "ok":
            raise RuntimeError(f"backup copy failed its integrity check: {ok}")

        with tarfile.open(snap / "config.tar.gz", "w:gz") as tar:
            for name in ("config", "rules"):
                if (root / name).exists():
                    tar.add(root / name, arcname=name)

        result: dict[str, Any] = {
            "stamp": stamp, "path": str(snap), "bytes": target.stat().st_size,
            "conversations": conversations, "grades": grades, "uploaded": None,
        }
        result["pruned"] = _prune(backup_dir, settings.backup_keep_days, keep=stamp)

        if settings.rclone_remote:
            rclone = _rclone()
            if not rclone:
                raise RuntimeError("RCLONE_REMOTE is set but rclone is not installed")
            dest = f"{settings.rclone_remote}/{stamp}"
            proc = subprocess.run([rclone, "copy", str(snap), dest, "--transfers", "4"],
                                  capture_output=True, text=True, timeout=3600)
            if proc.returncode != 0:
                raise RuntimeError(f"rclone copy to {dest} failed: "
                                   f"{(proc.stderr or proc.stdout).strip()[-500:]}")
            result["uploaded"] = dest

        _set_meta(db_path, {META_OK: _now().isoformat(), META_RESULT: json.dumps(result)})
        log.info("Backup %s: %d conversations, %d grades, %.1f MB%s", stamp, conversations,
                 grades, result["bytes"] / 1e6,
                 f" → {result['uploaded']}" if result["uploaded"] else " (local only)")
        return result
    except Exception as exc:
        log.error("BACKUP FAILED (%s): %s", stamp, exc)
        try:
            _set_meta(db_path, {META_ERROR: str(exc), META_ERROR_AT: _now().isoformat()})
        except Exception:  # noqa: BLE001 — a missing DB cannot record its own failure
            log.exception("could not record the backup failure")
        raise


def backup_due(db_path: Path | None = None) -> bool:
    st = status(db_path)
    if not st["last_ok_at"]:
        return True
    return _now() - datetime.fromisoformat(st["last_ok_at"]) >= timedelta(hours=settings.backup_hours)
