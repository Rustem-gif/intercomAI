import textwrap
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from intercom_summary.intercom.models import Admin, Contact, Conversation, Message
from intercom_summary.settings import settings
from intercom_summary.storage.conversations_store import ConversationsStore


@pytest.fixture
def client(tmp_path, monkeypatch):
    # Point DB + users file at temp locations; disable Basic Auth so tests can hit
    # the login endpoint without needing to send Authorization headers.
    object.__setattr__(settings, "db_path", tmp_path / "web.db")
    object.__setattr__(settings, "eval_dir", tmp_path / "eval")
    object.__setattr__(settings, "web_basic_auth", "")

    users_file = tmp_path / "web_users.yaml"
    from intercom_summary.web.auth import hash_password
    users_file.write_text(textwrap.dedent(f"""
        users:
          boss:
            password_hash: "{hash_password('pw')}"
            role: admin
          ana:
            password_hash: "{hash_password('pw')}"
            role: analyst
          looker:
            password_hash: "{hash_password('pw')}"
            role: viewer
    """))

    from intercom_summary.web import auth as auth_mod
    auth_mod.users = auth_mod.UserStore(users_file)

    # Seed a conversation so list/detail have data.
    cstore = ConversationsStore(settings.db_path)
    cstore.save(Conversation(
        id="42", created_at=datetime(2026, 5, 1, tzinfo=timezone.utc), updated_at=None,
        state="closed", subject="Login",
        assignee=Admin(id="1", name="Ada", email="ada@co.com"),
        contact=Contact(name="Cara"),
        messages=[Message(0, "admin", "Ada", None, "Hi")],
    ))
    cstore.close()

    from intercom_summary.web.api import create_app
    return TestClient(create_app())


def _login(client, user="boss", pw="pw"):
    r = client.post("/api/auth/login", json={"username": user, "password": pw})
    assert r.status_code == 200
    return r


def test_requires_auth(client):
    assert client.get("/api/overview").status_code == 401


def test_login_and_read(client):
    _login(client)
    assert client.get("/api/auth/me").json()["role"] == "admin"

    ov = client.get("/api/overview")
    assert ov.status_code == 200
    assert ov.json()["kpis"]["conversations"] == 1

    convos = client.get("/api/conversations").json()
    assert convos["total"] == 1 and convos["items"][0]["id"] == "42"

    detail = client.get("/api/conversations/42").json()
    assert "Ada" in detail["transcript"]


def test_bad_login_rejected(client):
    r = client.post("/api/auth/login", json={"username": "boss", "password": "nope"})
    assert r.status_code == 401


def test_viewer_cannot_write(client):
    _login(client, "looker", "pw")
    # viewer may read overview
    assert client.get("/api/overview").status_code == 200
    # but not trigger fetch or edit rules
    assert client.post("/api/fetch", json={"agents": ["Ada"]}).status_code == 403
    assert client.put("/api/rules", json={"text": "x"}).status_code == 403


def test_intercom_admins_listing(client, monkeypatch):
    _login(client)
    object.__setattr__(settings, "intercom_token", "tok")

    async def fake_list_agents():
        return [{"id": "1", "name": "Ada", "email": "ada@co.com"},
                {"id": "2", "name": "Bob", "email": "bob@co.com"}]

    monkeypatch.setattr("intercom_summary.service.list_agents", fake_list_agents)
    r = client.get("/api/intercom/admins")
    assert r.status_code == 200
    admins = r.json()["admins"]
    assert {a["name"] for a in admins} == {"Ada", "Bob"}


def test_review_rejects_claude_code_backend(client):
    _login(client)
    # Claude Code is no longer a selectable grading engine.
    r = client.post("/api/review", json={"conversation_ids": ["42"], "backend": "claude_code"})
    assert r.status_code == 400


def test_viewer_cannot_fetch(client):
    _login(client, "looker", "pw")
    # A write-gated endpoint must reject viewers before running.
    assert client.post("/api/fetch", json={"agents": ["a@co.com"]}).status_code == 403


def _seed_grade(rules_version="v-old"):
    """Persist a grade for conversation 42 under a given ruleset version."""
    from intercom_summary.qa.schema import ConversationGrade
    from intercom_summary.storage.grades_store import GradesStore

    gstore = GradesStore(settings.db_path)
    gstore.save(ConversationGrade(
        conversation_id="42", agent_name="Ada", overall_score=80,
        summary="ok", rules_version=rules_version, model="test",
        graded_at="2026-05-01T00:00:00+00:00",
    ))
    gstore.close()


def test_analyst_can_override_grade(client):
    _seed_grade()
    _login(client, "ana", "pw")
    r = client.post("/api/conversations/42/override",
                    json={"score": 95, "reason": "Manager judged higher"})
    assert r.status_code == 200
    assert r.json()["human_score"] == 95
    # The override is persisted and surfaced on the grade.
    grade = client.get("/api/conversations/42").json()["grade"]
    assert grade["human_score"] == 95
    assert grade["overridden_by"] == "ana"


def test_viewer_cannot_override_grade(client):
    _seed_grade()
    _login(client, "looker", "pw")
    r = client.post("/api/conversations/42/override",
                    json={"score": 95, "reason": "nope"})
    assert r.status_code == 403


def test_eval_stats_counts_grades_under_older_ruleset(client):
    """A grade stored under a previous ruleset must still count as 'graded' —
    editing the rules used to zero the count (regression)."""
    _seed_grade(rules_version="some-old-version")
    _login(client)
    stats = client.get("/api/evaluation/stats").json()
    assert stats["graded"] == 1
    assert stats["pending"] == 0
    # And it is flagged as graded under an older ruleset.
    assert stats["stale"] == 1


def test_admin_fetch_enqueues_job(client, monkeypatch):
    _login(client)

    # Avoid real Intercom + run the background task inline.
    async def fake_fetch_and_store(**kwargs):
        return {"fetched": 0, "agents": kwargs.get("agents", [])}

    monkeypatch.setattr("intercom_summary.service.fetch_and_store", fake_fetch_and_store)
    object.__setattr__(settings, "intercom_token", "tok")  # pass require_intercom()

    r = client.post("/api/fetch", json={"agents": ["Ada"], "since": "2026-05-01"})
    assert r.status_code == 200
    job = r.json()
    assert job["kind"] == "fetch"
    # TestClient runs BackgroundTasks synchronously after the response.
    status = client.get(f"/api/jobs/{job['id']}").json()
    assert status["status"] == "done"
    assert status["result"]["fetched"] == 0
