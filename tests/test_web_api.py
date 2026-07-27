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


def _seed_casino_grade():
    """Persist an Ollama-style grade for conversation 42 with known QA criteria.
    AI score 85 = 100 − 15 (res-no-fake-close failed)."""
    from intercom_summary.qa.schema import ConversationGrade, RuleResult
    from intercom_summary.storage.grades_store import GradesStore

    gstore = GradesStore(settings.db_path)
    gstore.save(ConversationGrade(
        conversation_id="42", agent_name="Ada", overall_score=85, summary="ok",
        rule_results=[
            RuleResult("res-no-fake-close", "No Fake Closure", "fail", "closed early"),
            RuleResult("open-greet", "Greeting", "pass", "hi"),
        ],
        rules_version="v1", model="ollama/test",
        graded_at="2026-05-01T00:00:00+00:00",
    ))
    gstore.close()


def test_criteria_override_recomputes_score(client):
    _seed_casino_grade()
    _login(client, "ana", "pw")
    # Analyst flips the failed criterion to pass → score recomputes to 100.
    r = client.post("/api/conversations/42/override", json={
        "criteria": {"res-no-fake-close": "pass", "open-greet": "pass"},
        "reason": "Issue was actually resolved in chat",
    })
    assert r.status_code == 200
    assert r.json()["human_score"] == 100

    grade = client.get("/api/conversations/42").json()["grade"]
    assert grade["human_score"] == 100
    # Only the changed criterion is stored (diff vs the AI verdicts).
    assert grade["human_criteria"] == {"res-no-fake-close": "pass"}
    # Rule checks are annotated with canonical deductions for the toggle UI.
    by_id = {x["rule_id"]: x for x in grade["rule_results"]}
    assert by_id["res-no-fake-close"]["deduction"] == 15


def test_criteria_override_rejects_unknown_criterion(client):
    _seed_casino_grade()
    _login(client, "ana", "pw")
    r = client.post("/api/conversations/42/override", json={
        "criteria": {"made-up-id": "fail"}, "reason": "x",
    })
    assert r.status_code == 422


def test_criteria_override_requires_a_change(client):
    _seed_casino_grade()
    _login(client, "ana", "pw")
    # Submitting the AI's own verdicts unchanged is a no-op and rejected.
    r = client.post("/api/conversations/42/override", json={
        "criteria": {"res-no-fake-close": "fail", "open-greet": "pass"}, "reason": "x",
    })
    assert r.status_code == 422


def test_manual_deduction_only(client):
    _seed_casino_grade()  # AI score 85 (res-no-fake-close failed −15)
    _login(client, "ana", "pw")
    r = client.post("/api/conversations/42/override", json={
        "manual_deductions": [{"category": "info-correctness", "points": 20, "note": "wrong bonus"}],
        "reason": "Agent added the wrong bonus",
    })
    assert r.status_code == 200
    assert r.json()["human_score"] == 65  # 85 − 20, no criterion change
    grade = client.get("/api/conversations/42").json()["grade"]
    assert grade["human_score"] == 65
    assert grade["human_criteria"] is None
    assert grade["human_deductions"][0]["category"] == "info-correctness"
    assert grade["human_deductions"][0]["points"] == 20


def test_criteria_plus_manual_deduction(client):
    _seed_casino_grade()
    _login(client, "ana", "pw")
    # Flip the failed criterion to pass (→100) and deduct 10 for info correctness → 90.
    r = client.post("/api/conversations/42/override", json={
        "criteria": {"res-no-fake-close": "pass"},
        "manual_deductions": [{"category": "info-correctness", "points": 10}],
        "reason": "Resolved, but gave slightly wrong info",
    })
    assert r.status_code == 200
    assert r.json()["human_score"] == 90


def test_manual_deduction_validation(client):
    _seed_casino_grade()
    _login(client, "ana", "pw")
    assert client.post("/api/conversations/42/override", json={
        "manual_deductions": [{"category": "made-up", "points": 5}], "reason": "x",
    }).status_code == 422
    assert client.post("/api/conversations/42/override", json={
        "manual_deductions": [{"category": "info-correctness", "points": 0}], "reason": "x",
    }).status_code == 422


def test_manual_deduction_catalog(client):
    _login(client)
    items = client.get("/api/qa/manual-deductions").json()["items"]
    assert any(i["id"] == "info-correctness" for i in items)


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


def test_eval_stats_current_ruleset_not_stale(client):
    """A grade stamped with the live grader's rules_version must NOT be flagged stale."""
    from intercom_summary.qa.backends import get_grader
    _seed_grade(rules_version=get_grader().rules_version)
    _login(client)
    stats = client.get("/api/evaluation/stats").json()
    assert stats["graded"] == 1
    assert stats["stale"] == 0


def test_search_matches_conversation_id(client):
    """Search box also matches the Intercom conversation id (chat number)."""
    _login(client)
    r = client.get("/api/conversations", params={"search": "42"}).json()
    assert r["total"] == 1 and r["items"][0]["id"] == "42"
    # A non-matching id returns nothing (subject is "Login", customer "Cara").
    assert client.get("/api/conversations", params={"search": "9999"}).json()["total"] == 0


def test_agent_scores_endpoint(client):
    _seed_grade(rules_version="v1")  # grade for conversation 42 (Ada), overall_score 80
    _login(client)
    r = client.get("/api/agents/scores?period=all").json()
    assert r["start"] is None and r["end"] is None
    assert r["since"] is None and r["until"] is None
    agents = {a["agent"]: a for a in r["agents"]}
    assert agents["Ada"]["avg_score"] == 80.0 and agents["Ada"]["count"] == 1


def test_agent_scores_custom_range(client):
    # Conversation 42 (Ada) is dated 2026-05-01.
    _seed_grade(rules_version="v1")
    _login(client)

    def agents_for(qs):
        return {a["agent"] for a in client.get(f"/api/agents/scores?{qs}").json()["agents"]}

    # End date is inclusive: a range that starts and ends on the conversation date includes it.
    assert "Ada" in agents_for("start=2026-05-01&end=2026-05-01")
    # Ranges that exclude 2026-05-01 drop the agent.
    assert "Ada" not in agents_for("start=2026-05-02")
    assert "Ada" not in agents_for("end=2026-04-30")


def test_agent_scores_rejects_bad_period(client):
    _login(client)
    assert client.get("/api/agents/scores?period=decade").status_code == 422


def test_agent_scores_rejects_bad_date(client):
    _login(client)
    assert client.get("/api/agents/scores?start=not-a-date").status_code == 422


def _save_convo(cid, agent="Ada", created="2026-05-01", subject="S"):
    cstore = ConversationsStore(settings.db_path)
    cstore.save(Conversation(
        id=cid, created_at=datetime.fromisoformat(created + "T00:00:00+00:00"), updated_at=None,
        state="closed", subject=subject,
        assignee=Admin(id="1", name=agent, email=f"{agent}@co.com"),
        contact=Contact(name="Cara"),
        messages=[Message(0, "admin", agent, None, "Hi")],
    ))
    cstore.close()


def test_soft_delete_and_restore(client):
    _seed_grade()  # grade for conversation 42
    _login(client)
    # Delete moves to trash and removes from the live list.
    assert client.delete("/api/conversations/42").json()["deleted"] == 1
    assert client.get("/api/conversations").json()["total"] == 0
    trash = client.get("/api/trash").json()
    assert trash["total"] == 1 and trash["items"][0]["conversation_id"] == "42"
    # Restore brings the conversation AND its grade back intact.
    assert client.post("/api/trash/restore", json={"ids": ["42"]}).json()["restored"] == 1
    assert client.get("/api/conversations").json()["total"] == 1
    assert client.get("/api/conversations/42").json()["grade"]["overall_score"] == 80
    assert client.get("/api/trash").json()["total"] == 0


def test_filter_based_delete(client):
    _save_convo("100", agent="Bob")
    _login(client)
    # Delete everything matching the agent filter (Bob) — 42 (Ada) stays.
    r = client.post("/api/conversations/delete", json={"agent": ["Bob"]}).json()
    assert r["deleted"] == 1 and r["ids"] == ["100"]
    assert client.get("/api/conversations").json()["total"] == 1
    # No ids, no filter, not all → rejected (guards against accidental delete-all).
    assert client.post("/api/conversations/delete", json={}).status_code == 400


def test_delete_ungraded_preset(client):
    _seed_grade()        # 42 is graded
    _save_convo("100")   # 100 is ungraded
    _login(client)
    r = client.post("/api/conversations/delete", json={"ungraded": True}).json()
    assert r["ids"] == ["100"]
    assert client.get("/api/conversations").json()["total"] == 1


def test_purge_trash(client):
    _login(client)
    client.delete("/api/conversations/42")
    assert client.get("/api/trash").json()["total"] == 1
    assert client.post("/api/trash/purge", json={"all": True}).json()["purged"] == 1
    assert client.get("/api/trash").json()["total"] == 0


def test_viewer_cannot_delete_or_use_trash(client):
    _login(client, "looker", "pw")
    assert client.delete("/api/conversations/42").status_code == 403
    assert client.post("/api/conversations/delete", json={"all": True}).status_code == 403
    assert client.get("/api/trash").status_code == 403
    assert client.post("/api/trash/restore", json={"all": True}).status_code == 403


def test_iconic_case_survives_deletion(client):
    _seed_grade(rules_version="v1")  # grade for conversation 42 (Ada, score 80)
    _login(client)
    assert client.post(
        "/api/iconic-cases", json={"conversation_id": "42", "comment": "great handling"}
    ).status_code == 200

    # Deleting the source conversation + grade must NOT make the KB case disappear.
    assert client.delete("/api/conversations/42").status_code == 200
    assert client.get("/api/conversations/42").status_code == 404  # source is gone

    items = client.get("/api/iconic-cases").json()["items"]
    assert len(items) == 1
    case = items[0]
    assert case["archived"] is True
    assert case["conversation"]["agent_name"] == "Ada"
    assert case["conversation"]["score"] == 80

    # The frozen exemplar is still fully viewable.
    detail = client.get("/api/iconic-cases/42").json()
    assert detail["grade"]["overall_score"] == 80
    assert detail["conversation"]["subject"] == "Login"
    assert detail["transcript"]


def test_review_portal_exposes_agent_kb(client):
    _seed_grade(rules_version="v1")
    _login(client)
    client.post("/api/iconic-cases", json={"conversation_id": "42", "comment": "exemplar"})

    from intercom_summary.storage.agent_tokens_store import AgentTokensStore
    ts = AgentTokensStore(settings.db_path)
    ts.create("tok-ada", agent_name="Ada", label="Ada review", created_by="boss")
    ts.create("tok-bob", agent_name="Bob", label="Bob review", created_by="boss")
    ts.close()

    # Public portal (no login) lists the agent's exemplars and serves the snapshot.
    listing = client.get("/api/review/tok-ada/iconic-cases").json()
    assert listing["total"] == 1 and listing["items"][0]["conversation_id"] == "42"
    detail = client.get("/api/review/tok-ada/iconic-cases/42").json()
    assert detail["grade"]["overall_score"] == 80

    # Another agent's token cannot see Ada's exemplar.
    assert client.get("/api/review/tok-bob/iconic-cases").json()["total"] == 0
    assert client.get("/api/review/tok-bob/iconic-cases/42").status_code == 403


def _seed_graded_convo(cid="77", agent="Ada", score=40):
    cstore = ConversationsStore(settings.db_path)
    cstore.save(Conversation(
        id=cid, created_at=datetime(2026, 5, 1, tzinfo=timezone.utc), updated_at=None,
        state="closed", subject="Graded chat",
        assignee=Admin(id="1", name=agent, email="a@co.com"),
        contact=Contact(name="Cara"),
        messages=[Message(0, "admin", agent, None, "Hi")],
    ))
    cstore.close()
    from intercom_summary.qa.schema import ConversationGrade
    from intercom_summary.storage.grades_store import GradesStore
    gstore = GradesStore(settings.db_path)
    gstore.save(ConversationGrade(
        conversation_id=cid, agent_name=agent, overall_score=score, summary="ok",
        graded_at="2026-05-03T00:00:00+00:00",
    ))
    gstore.close()


def test_portal_grade_dispute_and_resolution(client):
    _seed_graded_convo("77", "Ada", 40)
    from intercom_summary.storage.agent_tokens_store import AgentTokensStore
    ts = AgentTokensStore(settings.db_path)
    ts.create("tok-ada", agent_name="Ada", label="Ada", created_by="boss")
    ts.create("tok-bob", agent_name="Bob", label="Bob", created_by="boss")
    ts.close()

    # Another agent's token cannot dispute Ada's conversation.
    assert client.post(
        "/api/review/tok-bob/conversations/77/grade-dispute", json={"reason": "x"}
    ).status_code == 403

    # Ada disputes her grade via her portal link (no login).
    r = client.post(
        "/api/review/tok-ada/conversations/77/grade-dispute", json={"reason": "too harsh"}
    )
    assert r.status_code == 200
    # A second open dispute is rejected.
    assert client.post(
        "/api/review/tok-ada/conversations/77/grade-dispute", json={"reason": "again"}
    ).status_code == 409

    # The dispute shows up in the manager queue and on the detail payload.
    _login(client)
    queue = client.get("/api/grade-disputes?status=open").json()["items"]
    assert any(d["conversation_id"] == "77" and d["score"] == 40 for d in queue)
    detail = client.get("/api/conversations/77").json()
    assert detail["grade_dispute"]["status"] == "open"

    # Manager accepts; the corrected score itself is applied via the override endpoint.
    assert client.post(
        "/api/conversations/77/grade-dispute/resolve", json={"status": "accepted"}
    ).status_code == 200
    assert client.get("/api/grade-disputes?status=open").json()["items"] == []


def test_grade_dispute_requires_grade(client):
    # Seeded conversation "42" has no grade.
    from intercom_summary.storage.agent_tokens_store import AgentTokensStore
    ts = AgentTokensStore(settings.db_path)
    ts.create("tok-ada", agent_name="Ada", label="Ada", created_by="boss")
    ts.close()
    assert client.post(
        "/api/review/tok-ada/conversations/42/grade-dispute", json={"reason": "x"}
    ).status_code == 422


def test_grade_dispute_resolve_is_write_gated(client):
    _seed_graded_convo("78", "Ada", 50)
    from intercom_summary.storage.grade_disputes_store import GradeDisputesStore
    ds = GradeDisputesStore(settings.db_path)
    ds.create("78", "Ada", "reason", "dashboard", "boss")
    ds.close()

    _login(client, "looker", "pw")  # viewer
    assert client.post(
        "/api/conversations/78/grade-dispute/resolve", json={"status": "accepted"}
    ).status_code == 403
    assert client.post(
        "/api/conversations/78/grade-dispute", json={"reason": "y"}
    ).status_code == 403


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


def test_explicit_delete_blacklists_but_bulk_clear_does_not(client):
    """A "Delete ALL" is a cache clear, not a blacklist. Blacklisting it silently blocked
    every later Intercom fetch of those dates."""
    _save_convo("100", agent="Bob")
    _login(client)

    # Naming a conversation → blacklisted, blocked from re-import.
    r = client.post("/api/conversations/delete", json={"ids": ["100"]}).json()
    assert r["blacklisted"] is True
    assert client.get("/api/trash").json()["items"][0]["blacklist"] == 1

    # Deleting by filter / all=true → cleared, still restorable, but re-importable.
    r = client.post("/api/conversations/delete", json={"all": True}).json()
    assert r["blacklisted"] is False
    entry = next(i for i in client.get("/api/trash").json()["items"]
                 if i["conversation_id"] == "42")
    assert entry["blacklist"] == 0


def test_trash_pagination_reports_true_total(client):
    _save_convo("100")
    _login(client)
    client.post("/api/conversations/delete", json={"all": True})
    body = client.get("/api/trash?limit=1").json()
    assert body["total"] == 2 and len(body["items"]) == 1
    assert body["limit"] == 1 and body["offset"] == 0
    assert client.get("/api/trash?limit=1&offset=1").json()["items"][0][
        "conversation_id"] != body["items"][0]["conversation_id"]


def test_storage_stats_is_admin_only(client):
    _login(client, "ana", "pw")            # analyst may write but not administer
    assert client.get("/api/storage").status_code == 403
    assert client.post("/api/storage/vacuum", json={}).status_code == 403

    _login(client)                         # boss = admin
    body = client.get("/api/storage").json()
    assert body["db"]["bytes"] > 0
    assert body["trash"]["retention_days"] == settings.trash_retention_days
    assert any(t["table"] == "conversations" for t in body["db"]["tables"])
    assert client.post("/api/storage/vacuum", json={}).json()["before_bytes"] > 0
