import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from intercom_summary import backup
from intercom_summary.settings import settings
from intercom_summary.storage import db as dbmod
from intercom_summary.storage.db import DatabaseMissingError, connect


@pytest.fixture
def live_db(tmp_path):
    path = tmp_path / "live" / "grades.db"
    conn = connect(path)
    conn.execute("INSERT INTO conversations (id, payload_json) VALUES ('c1', '{}')")
    conn.commit()
    conn.close()
    return path


@pytest.fixture(autouse=True)
def _no_remote():
    old = settings.rclone_remote
    object.__setattr__(settings, "rclone_remote", "")
    yield
    object.__setattr__(settings, "rclone_remote", old)


def test_backup_copies_db_and_config_and_records_status(live_db, tmp_path):
    root = tmp_path / "repo"
    (root / "config").mkdir(parents=True)
    (root / "config" / "roles.yaml").write_text("x: 1")
    result = backup.run_backup(db_path=live_db, backup_dir=tmp_path / "bk", root=root)

    snap = tmp_path / "bk" / result["stamp"]
    copy = sqlite3.connect(snap / "grades.db")
    assert copy.execute("SELECT id FROM conversations").fetchall() == [("c1",)]
    assert (snap / "config.tar.gz").exists()
    assert result["conversations"] == 1 and result["uploaded"] is None

    st = backup.status(live_db)
    assert st["last_ok_at"] and not st["stale"] and st["last_error"] is None
    assert not backup.backup_due(live_db)


def test_failed_backup_is_recorded_and_shown(live_db, tmp_path):
    object.__setattr__(settings, "rclone_remote", "nowhere:bucket")
    object.__setattr__(settings, "rclone_bin", "/nonexistent/rclone")
    try:
        with pytest.raises(Exception):
            backup.run_backup(db_path=live_db, backup_dir=tmp_path / "bk")
    finally:
        object.__setattr__(settings, "rclone_bin", "")
    st = backup.status(live_db)
    assert st["last_error"] and st["stale"]
    assert backup.backup_due(live_db)


def test_prune_keeps_newest_and_ignores_foreign_dirs(tmp_path):
    old = (datetime.now(timezone.utc) - timedelta(days=30)).strftime(backup.STAMP_FORMAT)
    (tmp_path / old).mkdir()
    (tmp_path / "keep-me").mkdir()
    removed = backup._prune(tmp_path, keep_days=7, keep="nothing")
    assert removed == [old]
    assert (tmp_path / "keep-me").exists()
    # The newest snapshot is never pruned, however old.
    (tmp_path / old).mkdir()
    assert backup._prune(tmp_path, keep_days=7, keep=old) == []


def test_connect_refuses_to_recreate_a_guarded_missing_db(live_db, monkeypatch):
    monkeypatch.setattr(dbmod, "REQUIRE_EXISTING", live_db)
    connect(live_db).close()                       # present: fine
    live_db.unlink()
    with pytest.raises(DatabaseMissingError):
        connect(live_db)
    assert not live_db.exists()
