"""The VIP cases export (export/vip_xlsx.py, GET /api/vip/cases/export.xlsx): one row per case
with the grade that counts, the AI's original beside it, and a Criteria sheet with every
verdict. The threads export is covered in tests/test_vip.py."""
import textwrap
from io import BytesIO

from fastapi.testclient import TestClient
from openpyxl import load_workbook

from intercom_summary import vip_cases
from intercom_summary.export.vip_xlsx import CASE_COLS, CRITERIA_COLS, cases_workbook
from intercom_summary.qa.schema import ConversationGrade, RuleResult
from intercom_summary.settings import settings
from intercom_summary.storage.vip_cases_store import VipCasesStore
from tests.vip_case_fixtures import pair, save_threads, vip_env  # noqa: F401

COL = {name: i for i, name in enumerate(CASE_COLS)}


def _grade(case_id, score, results):
    return ConversationGrade(
        conversation_id=case_id, agent_name="Vera VIP", overall_score=score,
        summary="Payments owned the delay.", rules_version="r1", ruleset_id="vip-v16",
        overall_result="PASS" if score >= 85 else "FAIL", model="fake",
        graded_at="2026-10-20T10:00:00", manual_review_needed=score < 85,
        manual_review_reason="borderline" if score < 85 else "",
        rule_results=[RuleResult(rule_id=i, title=t, verdict=v, evidence=e)
                      for i, t, v, e in results],
    )


def _setup(db):
    """Two cases: vc-1 (two threads) graded FAIL then re-scored to 90; vc-3 graded PASS and
    agreed; vc-5 never graded."""
    save_threads(db, [pair("1", 0), pair("2", 24), pair("3", 24 * 20), pair("5", 24 * 40)])
    vip_cases.rebuild(db)
    store = VipCasesStore(db)
    try:
        v1 = store.get_case("vc-1")["case_version"]
        store.save_grade(_grade("vc-1", 75, [
            ("pay-handoff", "Payments handoff", "fail", "no handoff to Payments"),
            ("q-clarity", "Clarity", "pass", "plain answer"),
            ("proc-notes", "Internal notes", "n/a", ""),
        ]), case_version=v1, primary_manager="Vera VIP")
        assert store.save_override("vc-1", 90, "Payments owned the delay", "qa")
        store.save_grade(_grade("vc-3", 95, [("q-clarity", "Clarity", "pass", "plain answer")]),
                         case_version=store.get_case("vc-3")["case_version"])
        assert store.confirm("vc-3", "kate")
    finally:
        store.close()


def _rows(ws):
    rows = list(ws.iter_rows(values_only=True))
    return rows[0], {r[0]: r for r in rows[1:]}


def test_cases_workbook_prints_the_effective_grade_and_every_verdict(vip_env):
    db = vip_env / "vip.db"
    _setup(db)
    store = VipCasesStore(db)
    try:
        cases = store.cases()
        grades = {c["case_id"]: store.get_grade(c["case_id"]) for c in cases if c["grade"]}
    finally:
        store.close()
    convos = vip_cases.load_case_conversations([c["case_id"] for c in cases], db)
    from intercom_summary.qa.vip_transcript import case_block
    wb = cases_workbook(cases, grades, {k: case_block(v) for k, v in convos.items()},
                        {"qa": "Kateryna"})

    header, rows = _rows(wb["VIP cases"])
    assert list(header) == CASE_COLS and set(rows) == {"vc-1", "vc-3", "vc-5"}
    r1 = rows["vc-1"]
    assert (r1[COL["Score"]], r1[COL["Result"]]) == (90, "PASS")          # the re-score counts
    assert (r1[COL["AI score"]], r1[COL["AI result"]]) == (75, "FAIL")    # the AI's is kept
    assert r1[COL["Re-scored by"]] == "Kateryna" and r1[COL["Re-score reason"]] == "Payments owned the delay"
    assert r1[COL["Threads"]] == 2 and r1[COL["Thread IDs"]] == "1, 2"
    assert r1[COL["VIP manager"]] == "Vera VIP" and r1[COL["Player"]] == "Pat Player"
    assert r1[COL["Failed criteria"]] == "pay-handoff" and r1[COL["Needs review"]] == "yes"
    assert r1[COL["Review reason"]] == "borderline" and r1[COL["Graded (UTC)"]] == "2026-10-20 10:00"
    assert "PLAYER" in r1[COL["Transcript"]] and "Where is my withdrawal" in r1[COL["Transcript"]]
    r3 = rows["vc-3"]
    assert (r3[COL["Score"]], r3[COL["Result"]], r3[COL["Agreed by"]]) == (95, "PASS", "kate")
    assert r3[COL["Re-scored by"]] == "" and r3[COL["Needs review"]] == ""
    r5 = rows["vc-5"]
    assert r5[COL["Score"]] is None and r5[COL["Result"]] == "" and r5[COL["Transcript"]]

    header, crit = _rows(wb["Criteria"])
    assert list(header) == CRITERIA_COLS
    verdicts = [(r[0], r[1], r[3]) for r in wb["Criteria"].iter_rows(min_row=2, values_only=True)]
    assert ("vc-1", "pay-handoff", "fail") in verdicts and ("vc-3", "q-clarity", "pass") in verdicts
    # n/a rows (the ruleset's unreported criteria are padded in as n/a) say nothing: left out.
    assert all(v != "n/a" for _, _, v in verdicts)
    assert [r for r in wb["Criteria"].iter_rows(min_row=2, values_only=True) if r[0] == "vc-1"][0][5] \
        == "no handoff to Payments"


def test_api_export_follows_the_cases_filters(vip_env):
    _setup(vip_env / "vip.db")
    object.__setattr__(settings, "web_basic_auth", "")
    users_file = vip_env / "web_users.yaml"
    from intercom_summary.web.auth import hash_password
    users_file.write_text(textwrap.dedent(f"""
        users:
          looker:
            password_hash: "{hash_password('pw')}"
            role: viewer
    """))
    from intercom_summary.web import auth as auth_mod
    auth_mod.users = auth_mod.UserStore(users_file)
    from intercom_summary.web.api import create_app
    c = TestClient(create_app())
    assert c.post("/api/auth/login", json={"username": "looker", "password": "pw"}).status_code == 200

    x = c.get("/api/vip/cases/export.xlsx")                 # not read as case id "export.xlsx"
    assert x.status_code == 200 and x.content[:2] == b"PK"
    assert x.headers["content-disposition"].endswith('filename="vip_cases.xlsx"')
    _, rows = _rows(load_workbook(BytesIO(x.content))["VIP cases"])
    assert set(rows) == {"vc-1", "vc-3", "vc-5"}

    def ids(query):
        return set(_rows(load_workbook(BytesIO(c.get(f"/api/vip/cases/export.xlsx?{query}").content))["VIP cases"])[1])

    assert ids("result=ungraded") == {"vc-5"}
    assert ids("result=PASS") == {"vc-1", "vc-3"}            # the re-score made vc-1 a PASS
    assert ids("flag=manual") == {"vc-1"}
    assert ids("search=nobody") == set()
    assert c.get("/api/vip/cases/vc-1").status_code == 200   # the detail route still works
