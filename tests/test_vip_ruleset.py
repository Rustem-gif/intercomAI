"""VIP group + per-ruleset grading.

The load-bearing rule these guard: a grade is judged against the ruleset that *produced* it,
not the ruleset its agent would get today. Without that, adding an agent to the VIP group
would silently invalidate and re-grade their whole back catalogue.
"""
from datetime import datetime, timezone

import pytest

from intercom_summary.intercom.models import Admin, Contact, Conversation, Message
from intercom_summary.qa.rulesets import (
    GROUP_VIP,
    get_ruleset,
    ruleset_id_for_group,
    validate_ruleset,
)
from intercom_summary.qa.schema import ConversationGrade, RuleResult, score_from_verdicts
from intercom_summary.storage.agent_groups_store import AgentGroupsStore
from intercom_summary.storage.grades_store import GradesStore


def _convo(cid="42", agent="Ada"):
    return Conversation(
        id=cid,
        created_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
        updated_at=datetime(2026, 5, 2, tzinfo=timezone.utc),
        state="closed",
        subject="Withdrawal",
        assignee=Admin(id="1", name=agent, email=f"{agent.lower()}@co.com"),
        contact=Contact(name="Cara", email="cara@x.com"),
        messages=[Message(0, "user", "Cara", datetime(2026, 5, 1, tzinfo=timezone.utc), "Help")],
    )


def _grade(cid="42", agent="Ada", ruleset_id="default", rules_version="v1"):
    return ConversationGrade(
        conversation_id=cid, agent_name=agent, overall_score=80, summary="ok",
        rules_version=rules_version, ruleset_id=ruleset_id, model="ollama/test",
        graded_at="2026-05-03T00:00:00+00:00",
    )


# ── membership ────────────────────────────────────────────────────────────────────────
def test_agent_group_membership_and_ruleset_resolution(tmp_path):
    store = AgentGroupsStore(tmp_path / "t.db")
    assert store.get_group("Ada") == "standard"          # no row = standard
    assert ruleset_id_for_group(store.get_group("Ada")) == "default"

    store.set_group("Ada", GROUP_VIP, agent_email="ada@co.com", updated_by="admin")
    assert store.get_group("Ada") == "vip"
    assert store.get_group("ADA") == "vip"               # names are matched case-insensitively
    assert store.get_group(None, agent_email="ADA@co.com") == "vip"   # email wins over name
    assert ruleset_id_for_group(store.get_group("Ada")) == "vip"
    assert store.members(GROUP_VIP) == ["Ada"]

    store.remove("Ada")
    assert store.get_group("Ada") == "standard"
    store.close()


# ── staleness ─────────────────────────────────────────────────────────────────────────
def test_grade_is_current_only_for_its_own_ruleset_version(tmp_path):
    gs = GradesStore(tmp_path / "t.db")
    gs.save(_grade(ruleset_id="default", rules_version="v1"))

    assert gs.is_current("42", "default", "v1") is True    # same ruleset, same version
    assert gs.is_current("42", "default", "v2") is False   # its ruleset was edited → re-grade
    assert gs.is_current("99", "default", "v1") is False   # never graded
    gs.close()


def test_moving_an_agent_to_vip_does_not_invalidate_their_history(tmp_path):
    """The whole point of ruleset-of-record staleness: an agent's pre-VIP grades were correct
    when they were made, so they are left alone rather than re-graded under the VIP ruleset."""
    gs = GradesStore(tmp_path / "t.db")
    gs.save(_grade(ruleset_id="default", rules_version="v1"))

    # Ada is now VIP: the live ruleset for her conversations is 'vip' at some other version.
    assert gs.is_current("42", "vip", "vip-version") is True
    gs.close()


def test_review_grades_each_agent_with_their_own_ruleset(tmp_path, monkeypatch):
    """A mixed batch is bucketed per ruleset and each grade is stamped with the one used."""
    import intercom_summary.qa.backends as backends_mod
    from intercom_summary import service
    from intercom_summary.settings import settings
    from intercom_summary.storage.conversations_store import ConversationsStore

    db = tmp_path / "t.db"
    # settings is a frozen dataclass — bypass with object.__setattr__ (as tests/test_service.py).
    object.__setattr__(settings, "db_path", db)

    cs = ConversationsStore(db)
    cs.save(_convo("1", agent="Vic"))     # VIP agent
    cs.save(_convo("2", agent="Stan"))    # standard agent
    cs.close()

    ags = AgentGroupsStore(db)
    ags.set_group("Vic", GROUP_VIP)
    ags.close()

    class _FakeGrader:
        def __init__(self, ruleset_id):
            self.ruleset_id = ruleset_id
            self.rules_version = f"{ruleset_id}-v1"

        def grade(self, convo):
            return _grade(convo.id, convo.assignee_name, self.ruleset_id, self.rules_version)

    monkeypatch.setattr(
        backends_mod, "get_grader",
        lambda backend=None, ruleset_id=None: _FakeGrader(ruleset_id or "default"),
    )
    monkeypatch.setattr(service, "get_grader", _FakeGrader, raising=False)

    res = service.review_and_store(conversation_ids=["1", "2"])
    assert res["graded"] == 2

    gs = GradesStore(db)
    assert gs.get("1")["ruleset_id"] == "vip"       # Vic is VIP
    assert gs.get("2")["ruleset_id"] == "default"   # Stan is not
    gs.close()


def test_resolving_many_agents_opens_one_connection(tmp_path, monkeypatch):
    """Regression: ruleset resolution used to open a DB connection per conversation and never
    close it. A full review run then exhausted the process's file descriptors and SQLite
    started failing every open with "unable to open database file" — including unrelated
    queries like /api/jobs. Membership must be read once per run."""
    import intercom_summary.storage.db as db_mod
    from intercom_summary.qa.rulesets import agent_ruleset_resolver
    from intercom_summary.settings import settings

    object.__setattr__(settings, "db_path", tmp_path / "t.db")

    opened = 0
    real_connect = db_mod.connect

    def counting_connect(path):
        nonlocal opened
        opened += 1
        return real_connect(path)

    monkeypatch.setattr(db_mod, "connect", counting_connect)
    monkeypatch.setattr("intercom_summary.storage.agent_groups_store.connect", counting_connect)

    resolve = agent_ruleset_resolver()
    for _ in range(2000):
        resolve("Ada")
        resolve("Vic")

    assert opened == 1, f"resolution opened {opened} connections — it must load membership once"


# ── scoring ───────────────────────────────────────────────────────────────────────────
def test_manual_rescore_uses_the_rulesets_own_deductions():
    """Flipping the same criterion costs a different number of points in each ruleset, so a
    re-score must be told which ruleset the grade came from."""
    std = get_ruleset("default")
    vip = get_ruleset("vip")
    assert std.deductions["req-understanding"] == 8
    assert vip.deductions["req-understanding"] == 8   # shared criterion, same weight

    # A criterion that only exists in the VIP ruleset scores nothing under the standard one.
    verdicts = {"vip-host-escalation": "fail"}
    assert score_from_verdicts(verdicts, ruleset_id="vip")[0] == 100 - 15
    assert score_from_verdicts(verdicts, ruleset_id="default")[0] == 100


def test_critical_criteria_force_zero_in_both_rulesets():
    for rid in ("default", "vip"):
        score, band, result = score_from_verdicts({"crit-data-care": "fail"}, ruleset_id=rid)
        assert (score, band, result) == (0, "Critical", "FAIL")


# ── stored grades round-trip ──────────────────────────────────────────────────────────
def test_stored_grade_rebuilds_despite_the_extra_keys_the_store_adds(tmp_path):
    """GradesStore.get() enriches a grade with human_score, override fields and per-criterion
    `deduction`/`critical` annotations. Rebuilding the dataclass must ignore those rather than
    choke on them — `cli review` hit exactly this when re-reading an already-graded chat."""
    gs = GradesStore(tmp_path / "t.db")
    grade = _grade(ruleset_id="default", rules_version="v1")
    grade.rule_results = [
        RuleResult(rule_id="open-greet", title="Greeting", verdict="fail", evidence="—")
    ]
    gs.save(grade)
    gs.save_override("42", 70, "too harsh", "analyst")

    cached = gs.get("42")
    assert cached["human_score"] == 70                  # extra key from the store
    by_id = {r["rule_id"]: r for r in cached["rule_results"]}
    assert by_id["open-greet"]["deduction"] == 2        # extra key from _normalize_criteria

    rebuilt = ConversationGrade.from_dict(cached)
    assert rebuilt.conversation_id == "42"
    assert rebuilt.ruleset_id == "default"
    assert {r.rule_id for r in rebuilt.rule_results} >= {"open-greet"}
    gs.close()


def test_reports_score_on_the_analyst_override_not_the_superseded_ai_score(tmp_path):
    """Both exports (CLI `review` and /api/export/qa.xlsx) build grades from the store via
    from_dict. If the override didn't survive that, a report would show the AI's number while
    the dashboard — which does COALESCE(human_score, overall_score) — shows the analyst's."""
    from intercom_summary.qa.report import aggregate, report_markdown

    gs = GradesStore(tmp_path / "t.db")
    gs.save(_grade("42", "Ada"))              # AI scored 80
    gs.save(_grade("43", "Ada"))
    gs.save_override("42", 55, "missed an RG signal", "analyst")
    grades = [ConversationGrade.from_dict(d) for d in gs.all()]
    gs.close()

    by_id = {g.conversation_id: g for g in grades}
    assert by_id["42"].effective_score == 55 and by_id["42"].overall_score == 80
    assert by_id["43"].effective_score == 80 and not by_id["43"].is_overridden

    data = aggregate(grades)["Ada"]
    assert data["avg_score"] == 67.5          # (55 + 80) / 2, not (80 + 80) / 2
    assert data["min_score"] == 55
    assert data["overridden"] == 1

    md = report_markdown(grades)
    assert "**55/100** _(AI scored 80, overridden)_" in md


# ── drift ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("ruleset_id", ["default", "vip"])
def test_shipped_rulesets_have_no_prompt_criteria_drift(ruleset_id):
    """The deduction points in the prompt text must match the criteria catalogue — otherwise
    the model and a manual re-score would disagree on what the same criterion costs."""
    assert validate_ruleset(get_ruleset(ruleset_id)) == []
