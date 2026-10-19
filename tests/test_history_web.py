"""The history index in the web app: stats, the refresh job, the single-job guard, the scheduler."""
import textwrap

import pytest
from fastapi.testclient import TestClient

from intercom_summary import service
from intercom_summary.settings import settings
from intercom_summary.storage.history_store import HistoryStore
from intercom_summary.storage.jobs_store import JobsStore
from intercom_summary.web import api as api_mod
from test_history_store import _row


@pytest.fixture
def client(tmp_path):
    object.__setattr__(settings, "db_path", tmp_path / "web.db")
    object.__setattr__(settings, "web_basic_auth", "")
    object.__setattr__(settings, "anthropic_api_key", "sk-test")
    object.__setattr__(settings, "export_dir", tmp_path / "exports")
    users_file = tmp_path / "web_users.yaml"
    from intercom_summary.web.auth import hash_password
    users_file.write_text(textwrap.dedent(f"""
        users:
          ann:
            password_hash: "{hash_password('pw')}"
            role: analyst
          looker:
            password_hash: "{hash_password('pw')}"
            role: viewer
    """))
    from intercom_summary.web import auth as auth_mod
    auth_mod.users = auth_mod.UserStore(users_file)
    c = TestClient(api_mod.create_app())
    assert c.post("/api/auth/login", json={"username": "ann", "password": "pw"}).status_code == 200
    return c


def test_stats_reflect_the_index(client):
    hs = HistoryStore(settings.db_path)
    hs.save_label(_row("1"))
    hs.close()
    s = client.get("/api/history/stats").json()
    assert s["labels"] == 1 and s["mode"] == "off" and s["unlabelled_cache"] == 0
    assert s["by_case_type"] == [{"key": "Withdrawal", "n": 1}] and len(s["label_version"]) == 12


def test_refresh_runs_as_a_job_and_records_the_result(client, monkeypatch):
    calls = []

    def fake_refresh(**kw):
        calls.append(kw)
        kw["on_progress"](1, 1)
        return {"labelled": 1, "failed": 0, "cost_usd": 0.0006, "indexed": 1,
                "verdicts": {}, "index_version": "abc", "batch_ids": []}

    monkeypatch.setattr(service, "refresh_history", fake_refresh)
    r = client.post("/api/history/refresh").json()
    assert r["kind"] == "history_refresh"
    job = client.get(f"/api/jobs/{r['id']}").json()           # TestClient runs the task inline
    assert job["status"] == "done" and job["result"]["labelled"] == 1
    assert calls and "cancel_event" in calls[0]


def test_refresh_is_refused_while_a_grading_run_is_active_and_vice_versa(client, monkeypatch):
    js = JobsStore(settings.db_path)
    running = js.create("review", {})
    js.update(running, status="running")
    js.close()
    assert client.post("/api/history/refresh").json()["id"] == running
    js = JobsStore(settings.db_path)
    js.update(running, status="done")
    refreshing = js.create("history_refresh", {})
    js.update(refreshing, status="running")
    assert api_mod._active_grading_job(js)["id"] == refreshing
    js.close()


def test_viewers_cannot_refresh(client):
    client.post("/api/auth/logout")
    assert client.post("/api/auth/login", json={"username": "looker", "password": "pw"}).status_code == 200
    assert client.post("/api/history/refresh").status_code == 403


def test_scheduler_refreshes_only_when_due_and_idle(client, monkeypatch):
    object.__setattr__(settings, "history_refresh_days", 14)
    ran = []

    def fake_run(job_id, params):            # the real runner marks its job done when it ends
        ran.append(job_id)
        js = JobsStore(settings.db_path)
        js.update(job_id, status="done", result={})
        js.close()

    monkeypatch.setattr(api_mod, "_run_history_refresh_job", fake_run)
    assert api_mod._maybe_refresh_history() is not None          # never refreshed → due
    assert len(ran) == 1
    hs = HistoryStore(settings.db_path)
    hs.set_meta("history.last_refresh_at", "2026-01-01T00:00:00+00:00")
    hs.close()
    assert api_mod._maybe_refresh_history() is not None          # too old → due
    from datetime import datetime, timezone
    hs = HistoryStore(settings.db_path)
    hs.set_meta("history.last_refresh_at", datetime.now(timezone.utc).isoformat())
    hs.close()
    assert api_mod._maybe_refresh_history() is None              # fresh → not due
    js = JobsStore(settings.db_path)
    js.update(js.create("review", {}), status="running")
    js.close()
    hs = HistoryStore(settings.db_path)
    hs.set_meta("history.last_refresh_at", "2026-01-01T00:00:00+00:00")
    hs.close()
    assert api_mod._maybe_refresh_history() is None              # due, but grading → wait
    assert len(ran) == 2
