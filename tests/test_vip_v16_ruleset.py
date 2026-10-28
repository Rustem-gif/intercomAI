"""The vip-v16 ruleset end to end: catalogue ↔ prompt, the grader on a VIP case, the grading
service, calibration over VIP cases and the web endpoints. Offline — fake Claude, fake grader."""
import pytest
from fastapi.testclient import TestClient

from intercom_summary import service, vip_cases
from intercom_summary.qa.grader import Grader
from intercom_summary.qa.rulesets import (
    SCORING_VIP_GATED,
    TRANSCRIPT_VIP_CASE,
    get_ruleset,
    output_schema_for,
    strict_schema,
    validate_ruleset,
)
from intercom_summary.qa.schema import ConversationGrade, RuleResult
from intercom_summary.settings import settings
from intercom_summary.storage.calibration_store import CalibrationStore
from intercom_summary.storage.vip_cases_store import VipCasesStore

from tests.test_grader import FakeClaude, reply
from tests.vip_case_fixtures import msg, pair, save_threads, thread, vip_env  # noqa: F401


def test_catalogue_and_prompt_agree():
    rs = get_ruleset("vip-v16")
    assert rs.scoring_model == SCORING_VIP_GATED and rs.transcript == TRANSCRIPT_VIP_CASE
    assert validate_ruleset(rs) == []
    assert len(rs.criteria) == 34 and rs.pass_threshold == 85
    assert rs.partial_deductions == {"q-clarity": 7, "q-expectations": 4, "q-effort": 4,
                                     "q-complaint": 4}
    assert rs.group_caps == {"style": 6}


def test_output_schema_is_strict_and_allows_partial():
    rs = get_ruleset("vip-v16")
    schema = strict_schema(rs)
    item = schema["properties"]["criteria"]["items"]
    assert "partial" in item["properties"]["v"]["enum"]
    assert "not_assessable" not in item["properties"]["v"]["enum"]   # code-set only
    assert set(item["properties"]["id"]["enum"]) == {c["id"] for c in rs.criteria}
    assert output_schema_for("vip-v16")["properties"]["step0"]


def _case_rows():
    return [
        thread("1", 0, [
            msg(0, "user", "Pat Player", 0, "My withdrawal is stuck, please check it today"),
            msg(1, "admin", "Pay Dep", 2, "Please upload a bank statement."),
            msg(2, "admin", "Vera VIP", 3, "Hi Pat, Payments need one document, then it is paid."),
        ]),
        pair("2", 30),
    ]


def _grade_json():
    return {
        "step0": {"case_type": "Withdrawal", "channel": "email", "segment_state": "unknown",
                  "risk_flag": "Financial", "expected_handling": "Keep informed",
                  "bonus_eligibility": "n/a", "data_sufficiency": "Sufficient"},
        "requests": [{"text": "withdrawal stuck", "from": "player", "status": "pending"}],
        "criteria": [
            {"id": "q-expectations", "v": "partial", "ev": "Payments need one document",
             "episode": "thread 1 12:00", "who": "Vera VIP", "counted_in": ""},
            # Blames Vera for Pay Dep's words — the guard must drop it.
            {"id": "style-tone", "v": "fail", "ev": "Please upload a bank statement.",
             "episode": "thread 1 11:00", "who": "Vera VIP", "counted_in": ""},
            {"id": "proc-notes", "v": "cannot_determine", "ev": "records live in the VIP table",
             "episode": "", "who": "", "counted_in": ""},
        ],
        "outcome_status": "Pending-legitimate", "summary": "Kept the player informed.",
        "manual_review_needed": False, "manual_review_reason": "", "confidence": "high",
    }


def test_grader_grades_a_case_on_the_case_transcript(vip_env):
    db = vip_env / "vip.db"
    save_threads(db, _case_rows())
    vip_cases.rebuild(db)
    convo = vip_cases.load_case_conversations(["vc-1"], db)["vc-1"]
    fake = FakeClaude(reply(_grade_json()))
    grader = Grader(ruleset_id="vip-v16", client=fake, jev=False, knowledge={})
    grade = grader.grade(convo)

    sent = fake.calls[0]["messages"][0]["content"]
    assert sent.startswith("=== BACK OFFICE")  # the player's BO record, then the case
    assert "\n=== VIP CASE vc-1 ===" in sent and "OTHER STAFF Pay Dep" in sent
    assert grade.conversation_id == "vc-1" and grade.ruleset_id == "vip-v16"
    assert grade.overall_score == 96 and grade.overall_result == "PASS"
    verdicts = {r.rule_id: r.verdict for r in grade.rule_results}
    assert verdicts["style-tone"] == "n/a" and verdicts["proc-notes"] == "not_assessable"
    assert any("other staff line" in f for f in grade.signal_flags)
    assert not grade.manual_review_needed


class FakeGrader:
    ruleset_id, rules_version = "vip-v16", "v-test"

    def __init__(self):
        self.graded: list[str] = []

    def grade(self, c):
        self.graded.append(c.id)
        return ConversationGrade(
            conversation_id=c.id, agent_name=c.assignee_name, overall_score=75, summary="ai",
            rule_results=[RuleResult("own-lost", "Lost case", "fail", "x")],
            rules_version="v-test", model="fake", graded_at="2026-10-20T00:00:00+00:00",
            ruleset_id="vip-v16", overall_result="FAIL",
        )


@pytest.fixture
def fake_grader(monkeypatch):
    import intercom_summary.qa.backends as backends_mod

    fake = FakeGrader()
    monkeypatch.setattr(backends_mod, "get_grader", lambda backend=None, ruleset_id=None: fake)
    return fake


def test_grade_vip_cases_writes_vip_grades_only_and_skips_current(vip_env, fake_grader):
    import sqlite3

    save_threads(settings.db_path, _case_rows() + [pair("3", 24 * 30, contact="c9")])
    result = service.grade_vip_cases()
    assert sorted(fake_grader.graded) == ["vc-1", "vc-3"] and result["graded"] == 2
    with sqlite3.connect(settings.db_path) as c:
        assert c.execute("SELECT COUNT(*) FROM grades").fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM vip_grades").fetchone()[0] == 2

    # Graded with the current prompt? The stored stamp is the fake's, so pin it.
    import intercom_summary.qa.rulesets as rulesets_mod
    real = rulesets_mod.get_ruleset("vip-v16")
    store = VipCasesStore()
    store._conn.execute("UPDATE vip_grades SET rules_version = ?", (real.version,))
    store._conn.commit()
    store.close()
    fake_grader.graded.clear()
    assert service.grade_vip_cases()["graded"] == 0 and fake_grader.graded == []


def test_calibration_over_vip_cases_grades_each_case_once(vip_env, fake_grader):
    save_threads(settings.db_path, _case_rows())
    cal = CalibrationStore()
    cal.create("vip-s", "VIP sample", default_ruleset="vip-v16")
    cal.replace_items("vip-s", [{"conversation_id": cid, "seq": i + 1}
                                for i, cid in enumerate(["1", "2", "999"])])
    assert cal.missing_conversations("vip-s") == ["999"]
    cal.close()

    result = service.run_calibration("vip-s")     # ruleset from the sample's default
    assert fake_grader.graded == ["vc-1"]          # threads 1 and 2 are one case
    assert result["cases"] == 1 and result["unavailable"] == 1
    cal = CalibrationStore()
    rows = {r["conversation_id"]: r for r in cal.results(result["run_id"])}
    detail = cal.result(result["run_id"], "2")
    run = cal.get_run(result["run_id"])
    cal.close()
    assert {k: r["status"] for k, r in rows.items()} == {
        "1": "graded", "2": "graded", "999": "not_found"}
    assert rows["1"]["source"] == "vip-case" and rows["2"]["overall_score"] == 75
    assert detail["conversation_json"]["case_id"] == "vc-1" and run["ruleset_id"] == "vip-v16"


# ── web ─────────────────────────────────────────────────────────────────────────────────────
@pytest.fixture
def client(vip_env, monkeypatch):
    from intercom_summary.web import auth
    from intercom_summary.web.api import create_app

    save_threads(settings.db_path, _case_rows())
    app = create_app()
    admin = {"username": "admin", "role": "admin"}
    for dep in (auth.current_user, auth.require_write, auth.require_admin):
        app.dependency_overrides[dep] = lambda admin=admin: admin
    return TestClient(app)


def test_cases_endpoints(client):
    r = client.get("/api/vip/cases").json()
    assert [c["case_id"] for c in r["items"]] == ["vc-1"]
    assert r["summary"]["to_grade"] == 1 and r["summary"]["shifts_known"] is False
    d = client.get("/api/vip/cases/vc-1").json()
    assert d["transcript"].startswith("=== VIP CASE vc-1") and d["grade"] is None
    assert client.get("/api/vip/cases/vc-404").status_code == 404
    assert client.get("/api/vip/conversations/2").json()["vip"]["case_id"] == "vc-1"
    assert client.get("/api/vip/exceptions").json()["unanswered"] == []


def test_review_job_and_override(client, fake_grader, monkeypatch):
    monkeypatch.setattr(type(settings), "require_qa", lambda self, backend=None: None)
    job = client.post("/api/vip/review", json={}).json()
    assert job["kind"] == "vip_review"
    assert client.get(f"/api/jobs/{job['id']}").json()["status"] == "done"
    assert fake_grader.graded == ["vc-1"]

    bad = client.post("/api/vip/grades/vc-1/override", json={"reason": "", "score": 90})
    assert bad.status_code == 422
    r = client.post("/api/vip/grades/vc-1/override",
                    json={"reason": "Payments owned it", "criteria": {"own-lost": "pass",
                                                                       "q-clarity": "partial"}})
    assert r.status_code == 200 and r.json()["human_score"] == 93
    bad = client.post("/api/vip/grades/vc-1/override",
                      json={"reason": "x", "criteria": {"style-tone": "partial"}})
    assert bad.status_code == 422
    g = client.get("/api/vip/cases/vc-1").json()["grade"]
    assert g["overall_score"] == 75 and g["human_score"] == 93


def test_agree_on_a_case_grade_and_the_loop_endpoint(client, fake_grader, monkeypatch):
    monkeypatch.setattr(type(settings), "require_qa", lambda self, backend=None: None)
    assert client.post("/api/vip/grades/vc-1/confirm").status_code == 404   # not graded yet
    client.post("/api/vip/review", json={})
    assert client.post("/api/vip/grades/vc-1/confirm").json()["human_confirmed_by"] == "admin"
    g = client.get("/api/vip/cases/vc-1").json()["grade"]
    assert g["human_confirmed_by"] == "admin" and g["human_score"] is None
    assert client.delete("/api/vip/grades/vc-1/confirm").status_code == 200
    client.post("/api/vip/grades/vc-1/override", json={"reason": "Payments owned it", "score": 93})
    assert client.post("/api/vip/grades/vc-1/confirm").status_code == 409   # re-scored already

    # The loop by hand: collect (faked) → regroup → grade; nothing new, so nothing graded.
    from intercom_summary import service, vip

    async def fake_collect(since, until, reclassify=False):
        return {"fetched": 0, "kept": 0, "since": since, "until": until}

    monkeypatch.setattr(vip, "collect", fake_collect)
    monkeypatch.setattr(type(settings), "require_intercom", lambda self: None)
    monkeypatch.setattr(type(settings), "require_anthropic", lambda self: None)
    job = client.post("/api/vip/auto").json()
    assert job["kind"] == "vip_auto"
    done = client.get(f"/api/jobs/{job['id']}").json()
    assert done["status"] == "done" and done["result"]["grading"]["graded"] == 0
    assert fake_grader.graded == ["vc-1"]                 # graded once; never re-graded
    assert service.vip_auto_cycle.__doc__  # the same function the scheduler runs
