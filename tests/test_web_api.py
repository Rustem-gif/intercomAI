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
            display_name: The Boss
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


def test_display_name_is_returned_by_login_and_me(client):
    assert _login(client).json()["display_name"] == "The Boss"
    assert client.get("/api/auth/me").json()["display_name"] == "The Boss"


def test_display_name_falls_back_to_username(client):
    # `ana` has no display_name in the users file — she is still called something.
    assert _login(client, "ana").json()["display_name"] == "ana"
    assert client.get("/api/auth/me").json()["display_name"] == "ana"


def test_me_resolves_display_name_from_the_user_file_not_the_session(client):
    """A session signed before a name was set (or changed) must not pin the old name."""
    _login(client, "ana")
    from intercom_summary.web import auth as auth_mod
    auth_mod.users._users["ana"]["display_name"] = "Anastasia"
    assert client.get("/api/auth/me").json()["display_name"] == "Anastasia"


def test_display_names_map(client):
    # Logged out it is not readable at all.
    assert client.get("/api/users/display-names").status_code == 401

    _login(client, "looker")  # read-only role is enough: these are names, not secrets
    r = client.get("/api/users/display-names")
    assert r.status_code == 200
    assert r.json() == {"boss": "The Boss", "ana": "ana", "looker": "looker"}
    assert "password_hash" not in r.text and "$2b$" not in r.text

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


def test_grade_exposes_the_full_criteria_checklist(client):
    """The seeded grade holds 2 criteria; the panel must still get all 27 to toggle."""
    from intercom_summary.qa.rulesets import get_ruleset

    _seed_casino_grade()
    _login(client, "ana", "pw")
    grade = client.get("/api/conversations/42").json()["grade"]
    assert [r["rule_id"] for r in grade["rule_results"]] == \
        [c["id"] for c in get_ruleset("default").criteria]


def test_criteria_override_accepts_a_criterion_the_model_never_emitted(client):
    """Qwen omits the crit-* rules on ~91% of grades. An analyst must still be able to fail
    one — before the rule_results backfill this returned 422 and compliance breaches were
    ungradeable."""
    _seed_casino_grade()  # contains only res-no-fake-close + open-greet
    _login(client, "ana", "pw")
    r = client.post("/api/conversations/42/override", json={
        "criteria": {"crit-data-care": "fail"},
        "reason": "Agent asked the player for their full card number",
    })
    assert r.status_code == 200
    assert r.json()["human_score"] == 0          # a critical fail forces 0

    grade = client.get("/api/conversations/42").json()["grade"]
    assert grade["human_criteria"] == {"crit-data-care": "fail"}


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


# ── Per-brand filtering ──────────────────────────────────────────────────────────
def _seed_brands(client):
    """Add a second-brand conversation next to the fixture's unbranded '42'."""
    from intercom_summary.storage.conversations_store import ConversationsStore

    store = ConversationsStore(settings.db_path)
    for cid, agent, brand in (("100", "Ada", "Betncare"), ("200", "Bob", "Tomb Riches")):
        store.save(Conversation(
            id=cid, created_at=datetime(2026, 8, 22, tzinfo=timezone.utc), updated_at=None,
            state="closed", subject=f"Chat {cid}",
            assignee=Admin(id="1", name=agent, email=f"{agent.lower()}@co.com"),
            contact=Contact(name="Cara"),
            messages=[Message(0, "admin", agent, None, "Hi")],
            brand=brand,
        ))
    store.close()


def test_brands_endpoint_lists_what_is_in_the_cache(client):
    from intercom_summary.intercom.brands import UNBRANDED

    _login(client)
    _seed_brands(client)

    brands = client.get("/api/brands").json()["brands"]
    by_value = {b["value"]: b for b in brands}

    # The default brand is stored as "Betncare" but shown as the product name.
    assert by_value["Betncare"]["label"] == "King Billy"
    assert by_value["Betncare"]["count"] == 1
    assert by_value["Tomb Riches"]["label"] == "Tomb Riches"
    # The fixture's conversation has no brand, so it is reachable under its own token.
    assert by_value[UNBRANDED]["count"] == 1


def test_conversations_filtered_by_brand(client):
    _login(client)
    _seed_brands(client)

    assert client.get("/api/conversations").json()["total"] == 3      # unfiltered
    tr = client.get("/api/conversations?brand=Tomb Riches").json()
    assert tr["total"] == 1 and tr["items"][0]["id"] == "200"
    assert tr["items"][0]["brand"] == "Tomb Riches"


def test_overview_and_agents_are_brand_scoped(client):
    _login(client)
    _seed_brands(client)

    assert client.get("/api/overview").json()["kpis"]["conversations"] == 3
    assert client.get("/api/overview?brand=Betncare").json()["kpis"]["conversations"] == 1
    assert client.get("/api/agents?brand=Tomb Riches").json()["agents"] == ["Bob"]


def test_unknown_brand_matches_nothing_rather_than_erroring(client):
    # The brand set is data-driven, so an unrecognised value must not 4xx the way an unknown
    # agent group does — a brand can appear or disappear without a deploy.
    _login(client)
    r = client.get("/api/conversations?brand=Olympia")
    assert r.status_code == 200 and r.json()["total"] == 0


def test_filtered_delete_cannot_reach_across_brands(client):
    """The safety property: a filtered delete resolves its ids server-side, so it must stay
    inside the brand the caller is scoped to."""
    _login(client)
    _seed_brands(client)

    r = client.post("/api/conversations/delete", json={"state": "closed", "brand": "Tomb Riches"})
    assert r.status_code == 200
    assert r.json()["ids"] == ["200"]

    remaining = {c["id"] for c in client.get("/api/conversations").json()["items"]}
    assert remaining == {"42", "100"}


def test_delete_all_is_still_bounded_by_the_active_brand(client):
    _login(client)
    _seed_brands(client)

    r = client.post("/api/conversations/delete", json={"all": True, "brand": "Betncare"})
    assert r.json()["ids"] == ["100"]
    assert client.get("/api/conversations").json()["total"] == 2


# ── SPA cache policy ─────────────────────────────────────────────────────────────
def test_index_html_is_revalidated_but_hashed_assets_are_immutable(client):
    """index.html names the content-hashed bundles, so a cached copy pins the app to the
    previous deploy's JavaScript while the API serves current data — a deploy that looks
    like it did nothing. Starlette sets no Cache-Control at all, which lets browsers cache
    heuristically, so the header has to be explicit."""
    from intercom_summary.web.api import FRONTEND_DIST, SPAStaticFiles

    if not FRONTEND_DIST.exists():
        pytest.skip("frontend not built")

    index = client.get("/")
    assert index.status_code == 200
    assert "no-cache" in index.headers.get("cache-control", "")

    # A client-side route falls back to index.html and must not be cached either.
    spa_route = client.get("/conversations")
    assert "no-cache" in spa_route.headers.get("cache-control", "")

    assets = list((FRONTEND_DIST / "assets").glob("index-*.js"))
    if assets:
        r = client.get(f"/assets/{assets[0].name}")
        assert r.status_code == 200
        assert "immutable" in r.headers.get("cache-control", "")


def test_api_404s_are_not_swallowed_by_the_spa_fallback(client):
    # The cache-header change touches the fallback path; make sure it still lets API
    # 404s through as JSON instead of returning the SPA shell.
    _login(client)
    r = client.get("/api/definitely-not-a-route")
    assert r.status_code == 404
    assert "text/html" not in r.headers.get("content-type", "")


# ── review links are scoped to a date range and frozen at creation ───────────────
def _seed_for_link(cid: str, when: datetime, agent="Ada", score: int | None = 90):
    """A conversation on a given date, graded unless score is None."""
    cstore = ConversationsStore(settings.db_path)
    cstore.save(Conversation(
        id=cid, created_at=when, updated_at=when, state="closed", subject=f"Chat {cid}",
        assignee=Admin(id="1", name=agent, email="a@co.com"), contact=Contact(name="Cara"),
        messages=[Message(0, "admin", agent, when, "Hi")],
    ))
    cstore.close()
    if score is None:
        return
    from intercom_summary.qa.schema import ConversationGrade
    from intercom_summary.storage.grades_store import GradesStore
    gstore = GradesStore(settings.db_path)
    gstore.save(ConversationGrade(
        conversation_id=cid, agent_name=agent, overall_score=score, summary="ok",
        rules_version="v1", model="test", graded_at=when.isoformat(),
    ))
    gstore.close()


def _aug_link(client, **body):
    """Create an August-scoped link for Ada and return its portal payload."""
    _login(client)
    resp = client.post("/api/agent-links", json={
        "agent_name": "Ada", "label": "Ada — August",
        "since": "2026-08-01", "until": "2026-08-31", **body,
    })
    assert resp.status_code == 200, resp.text
    token = resp.json()["token"]
    return token, client.get(f"/api/review/{token}").json()


def test_a_link_shows_only_conversations_inside_its_range(client):
    # The reported bug: a link generated for August also listed September, because the token
    # carried nothing but an agent name and the portal re-queried the whole history each open.
    _seed_for_link("aug-1", datetime(2026, 8, 5, tzinfo=timezone.utc))
    _seed_for_link("aug-2", datetime(2026, 8, 31, 23, 30, tzinfo=timezone.utc))
    _seed_for_link("sep-1", datetime(2026, 9, 1, tzinfo=timezone.utc))

    _, portal = _aug_link(client)
    ids = {c["id"] for c in portal["conversations"]}

    assert ids == {"aug-1", "aug-2"}          # aug-2 proves `until` covers the whole last day
    assert portal["since"] == "2026-08-01" and portal["until"] == "2026-08-31"


def test_a_link_excludes_conversations_with_no_grade(client):
    _seed_for_link("graded", datetime(2026, 8, 5, tzinfo=timezone.utc), score=70)
    _seed_for_link("ungraded", datetime(2026, 8, 6, tzinfo=timezone.utc), score=None)

    _, portal = _aug_link(client)
    assert {c["id"] for c in portal["conversations"]} == {"graded"}


def test_a_conversation_fetched_after_the_link_was_made_does_not_appear(client):
    # The link is a record of what was handed over, so its contents must not drift.
    _seed_for_link("aug-1", datetime(2026, 8, 5, tzinfo=timezone.utc))
    token, portal = _aug_link(client)
    assert len(portal["conversations"]) == 1

    _seed_for_link("aug-late", datetime(2026, 8, 6, tzinfo=timezone.utc))
    after = client.get(f"/api/review/{token}").json()

    assert {c["id"] for c in after["conversations"]} == {"aug-1"}


def test_total_matches_the_rows_actually_returned(client):
    _seed_for_link("aug-1", datetime(2026, 8, 5, tzinfo=timezone.utc))
    _seed_for_link("aug-2", datetime(2026, 8, 6, tzinfo=timezone.utc))

    _, portal = _aug_link(client)
    assert portal["total"] == len(portal["conversations"]) == 2


def test_a_link_with_no_membership_shows_nothing_rather_than_widening(client):
    """An unfrozen link used to fall back to an unscoped, undated, 500-row query over the
    agent's whole history. That is how an "August review" link came to list September chats,
    and it was indistinguishable from a link whose freeze legitimately resolved to nothing.
    Legacy links are frozen in place by scripts/backfill_token_items.py instead."""
    _seed_for_link("aug-1", datetime(2026, 8, 5, tzinfo=timezone.utc))
    _seed_for_link("sep-1", datetime(2026, 9, 1, tzinfo=timezone.utc))
    from intercom_summary.storage.agent_tokens_store import AgentTokensStore
    ts = AgentTokensStore(settings.db_path)
    ts.create("legacy", agent_name="Ada", label="Ada review", created_by="boss")
    ts.close()

    portal = client.get("/api/review/legacy").json()

    assert portal["conversations"] == [] and portal["total"] == 0


def test_freezing_a_legacy_link_makes_it_show_exactly_what_was_frozen(client):
    """The migration path for those links: resolve what they should have shown, write it down."""
    _seed_for_link("aug-1", datetime(2026, 8, 5, tzinfo=timezone.utc))
    _seed_for_link("sep-1", datetime(2026, 9, 1, tzinfo=timezone.utc))
    from intercom_summary.storage.agent_tokens_store import AgentTokensStore
    ts = AgentTokensStore(settings.db_path)
    ts.create("legacy", agent_name="Ada", label="Ada review", created_by="boss",
              conversation_ids=["aug-1"])
    ts.close()

    portal = client.get("/api/review/legacy").json()
    assert {c["id"] for c in portal["conversations"]} == {"aug-1"}


def test_an_unfrozen_link_cannot_reach_a_conversation_by_id(client):
    """The access rule had the same hole as the listing: with no membership the detail endpoint
    fell back to "does this chat belong to the agent?", so every chat they ever handled was one
    guessed id away through any of their links."""
    _seed_for_link("aug-1", datetime(2026, 8, 5, tzinfo=timezone.utc))
    from intercom_summary.storage.agent_tokens_store import AgentTokensStore
    ts = AgentTokensStore(settings.db_path)
    ts.create("legacy", agent_name="Ada", label="Ada review", created_by="boss")
    ts.close()

    assert client.get("/api/review/legacy/conversations/aug-1").status_code == 403


def test_the_detail_endpoint_refuses_a_conversation_outside_the_link(client):
    # Without a membership check, an agent could read straight through the link's date range
    # by asking for an id — every conversation of theirs is one guess away.
    _seed_for_link("aug-1", datetime(2026, 8, 5, tzinfo=timezone.utc))
    _seed_for_link("sep-1", datetime(2026, 9, 1, tzinfo=timezone.utc))

    token, _ = _aug_link(client)

    assert client.get(f"/api/review/{token}/conversations/aug-1").status_code == 200
    assert client.get(f"/api/review/{token}/conversations/sep-1").status_code == 403


def test_a_link_serves_its_members_and_refuses_everything_else(client):
    """Membership is the whole access rule — the agent's own other chats included."""
    _seed_for_link("sep-1", datetime(2026, 9, 1, tzinfo=timezone.utc))
    _seed_for_link("sep-2", datetime(2026, 9, 2, tzinfo=timezone.utc))
    _seed_for_link("other", datetime(2026, 9, 1, tzinfo=timezone.utc), agent="Bob")
    from intercom_summary.storage.agent_tokens_store import AgentTokensStore
    ts = AgentTokensStore(settings.db_path)
    ts.create("scoped", agent_name="Ada", label="Ada review", created_by="boss",
              conversation_ids=["sep-1"])
    ts.close()

    assert client.get("/api/review/scoped/conversations/sep-1").status_code == 200
    # Ada's, but not part of this review.
    assert client.get("/api/review/scoped/conversations/sep-2").status_code == 403
    assert client.get("/api/review/scoped/conversations/other").status_code == 403


# ── QA Manual v4.1: the gated ruleset reaches the UI ─────────────────────────────────
def _grade_with_v41(conversation_id="42"):
    """Store a real v4.1 grade the way the grader would."""
    from intercom_summary.qa.schema import ConversationGrade
    from intercom_summary.storage.grades_store import GradesStore

    g = ConversationGrade.from_ollama_output(conversation_id, "Ada", {
        "case_type": "Withdrawal",
        "risk_flag": "Financial",
        "expected_handling": "Internal escalation",
        "data_sufficiency": "Sufficient",
        "requests": [{"text": "Where is my payout?", "status": "unresolved", "material": True}],
        "criteria": [
            {"id": "resp-no-ghost", "v": "fail", "ev": "AGENT: anything else?"},
            {"id": "tag-chat", "v": "cannot_determine", "ev": "tags are CRM metadata"},
        ],
        "outcome_status": "Unresolved",
        "summary": "Payout question never answered.",
        "manual_review_needed": True,
        "manual_review_reason": "tag not visible",
    }, ruleset_id="kb-v41")
    g.ruleset_id = "kb-v41"
    g.rules_version = "test"
    store = GradesStore(settings.db_path)
    store.save(g)
    store.close()
    return g


def test_preview_score_is_the_single_source_of_the_formula(client):
    _login(client)
    r = client.post("/api/qa/preview-score", json={
        "ruleset_id": "kb-v41",
        "criteria": {"resp-no-ghost": "fail", "tag-chat": "pass"},
    })
    assert r.status_code == 200
    body = r.json()
    # One Major caps at 75, which is below the 85 threshold, so it is a guaranteed FAIL.
    assert body["score"] == 75 and body["result"] == "FAIL"
    assert body["pass_threshold"] == 85 and body["scoring_model"] == "gated"


def test_preview_score_uses_the_flat_formula_for_a_flat_ruleset(client):
    _login(client)
    r = client.post("/api/qa/preview-score", json={
        "ruleset_id": "default", "criteria": {"open-greet": "fail"},
    })
    assert r.json()["score"] == 98


def test_preview_score_rejects_an_unknown_deduction_category(client):
    _login(client)
    r = client.post("/api/qa/preview-score", json={
        "ruleset_id": "kb-v41", "criteria": {"tag-chat": "pass"},
        "manual_deductions": [{"category": "made-up", "points": 5, "note": ""}],
    })
    assert r.status_code == 422


def test_rulesets_expose_their_scoring_model(client):
    _login(client)
    items = {r["id"]: r for r in client.get("/api/rulesets").json()["items"]}
    assert items["kb-v41"]["scoring"]["model"] == "gated"
    assert items["kb-v41"]["scoring"]["major_cap"] == 75
    assert items["default"]["scoring"]["model"] == "flat"
    # The new ruleset must not have introduced prompt↔catalogue drift anywhere.
    assert all(not r["warnings"] for r in items.values())


def test_a_v41_grade_reaches_the_api_with_its_case_state(client):
    _login(client)
    _grade_with_v41()
    grade = client.get("/api/conversations/42").json()["grade"]
    assert grade["overall_score"] == 75 and grade["overall_result"] == "FAIL"
    assert grade["outcome_status"] == "Unresolved"
    assert grade["severity"] == "Major"
    assert grade["manual_review_needed"] is True
    assert grade["requests"][0]["status"] == "unresolved"
    # The criteria the model never reported are backfilled, carrying their gate and severity
    # so the panel can show what a failure would actually cost.
    by_id = {r["rule_id"]: r for r in grade["rule_results"]}
    assert by_id["resp-no-ghost"]["severity"] == "major"
    assert by_id["comm-tone"]["group"] == "communication"
    assert by_id["tag-chat"]["verdict"] == "cannot_determine"


def test_an_analyst_may_override_a_criterion_to_cannot_determine(client):
    _login(client, "ana")
    _grade_with_v41()
    r = client.post("/api/conversations/42/override", json={
        "reason": "No CRM access to confirm the escalation.",
        "criteria": {"resp-no-ghost": "cannot_determine"},
    })
    assert r.status_code == 200
    # Undoing the only Major lifts the 75 cap, and a cannot_determine costs nothing.
    assert r.json()["human_score"] == 100


def test_a_review_run_may_be_forced_onto_one_ruleset(client):
    _login(client)
    r = client.post("/api/review", json={"ruleset_id": "kb-v41", "conversation_ids": ["42"]})
    assert r.status_code in (200, 503)     # 503 when no QA backend is configured in the test env


def test_a_review_run_rejects_an_unknown_ruleset(client):
    _login(client)
    r = client.post("/api/review", json={"ruleset_id": "nope"})
    assert r.status_code == 400


def test_calibration_samples_report_what_cannot_be_graded(client):
    _login(client)
    from intercom_summary.storage.calibration_store import CalibrationStore

    store = CalibrationStore(settings.db_path)
    store.create("s1", "Sample", source="doc.docx")
    store.replace_items("s1", [
        {"conversation_id": "42", "seq": 1, "pilot": True},
        {"conversation_id": "not-fetched", "seq": 2},
    ])
    store.close()

    items = client.get("/api/calibration/samples").json()["items"]
    assert items[0]["size"] == 2 and items[0]["pilot_size"] == 1
    # A sample that cannot be graded in full is a finding, not something to quietly drop.
    assert items[0]["missing"] == 1

    listed = client.get("/api/conversations?sample=s1").json()
    assert [c["id"] for c in listed["items"]] == ["42"]
