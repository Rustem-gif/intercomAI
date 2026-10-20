"""QA lessons: collect QA's corrections, draft rules, approve them into the prompt."""
import json
from types import SimpleNamespace

import pytest

from intercom_summary.qa import lessons
from intercom_summary.qa.fail_verifier import FailVerifier, verification_text
from intercom_summary.qa.schema import ConversationGrade, RuleResult
from intercom_summary.settings import settings
from intercom_summary.storage.grades_store import GradesStore
from intercom_summary.storage.lessons_store import LessonsStore


@pytest.fixture
def db(tmp_path):
    old = settings.db_path, settings.anthropic_api_key
    path = tmp_path / "lessons.db"
    object.__setattr__(settings, "db_path", path)
    yield path
    object.__setattr__(settings, "db_path", old[0])
    object.__setattr__(settings, "anthropic_api_key", old[1])


def _grade(cid, score, verdicts):
    g = ConversationGrade(conversation_id=cid, agent_name="Ada", overall_score=score, summary="s",
                          rules_version="v1", ruleset_id="kb-v41", model="m",
                          graded_at="2026-10-10T00:00:00+00:00")
    g.rule_results = [RuleResult(rule_id=k, title=k, verdict=v, evidence=f"ev {k}") for k, v in verdicts.items()]
    return g


def _rescore(db, cid, ai, verdicts, human, reason, criteria=None):
    store = GradesStore(db)
    store.save(_grade(cid, ai, verdicts))
    store.save_override(cid, human, reason, "kate", human_criteria=criteria)
    store.close()


def test_feedback_counts_named_and_inferred_flips(db):
    _rescore(db, "1", 75, {"resp-no-ghost": "fail", "comm-tone": "pass"}, 100, "no mistakes")
    _rescore(db, "2", 75, {"resp-no-ghost": "fail"}, 95, "answered indirectly",
             criteria={"resp-no-ghost": "pass"})
    fb = lessons.collect_feedback("2000-01-01")
    by = {c["conversation_id"]: c for c in fb["chats"]}
    assert by["1"]["flips"] == [{"criterion_id": "resp-no-ghost", "ai": "fail", "human": "pass",
                                 "ai_evidence": "ev resp-no-ghost", "inferred": True}]
    assert by["2"]["flips"][0]["inferred"] is False
    groups = lessons.by_criterion(fb["chats"])
    assert list(groups) == ["resp-no-ghost"] and len(groups["resp-no-ghost"]) == 2
    s = lessons.summary(fb["chats"], fb["agreed"])
    assert s["false_fail"] == 2 and s["false_pass"] == 0


def test_feedback_respects_since(db):
    _rescore(db, "1", 75, {"resp-no-ghost": "fail"}, 100, "ok")
    assert lessons.collect_feedback("2999-01-01")["chats"] == []


def _fake_client(payload):
    class _Messages:
        def __init__(self):
            self.calls = []

        def create(self, **params):
            self.calls.append(params)
            return SimpleNamespace(content=[SimpleNamespace(type="text", text=json.dumps(payload))],
                                   usage=None, stop_reason="end_turn")

    m = _Messages()
    return SimpleNamespace(beta=SimpleNamespace(messages=m)), m


def test_sync_drafts_only_criteria_corrected_often_enough(db, monkeypatch):
    for i in range(3):
        _rescore(db, f"g{i}", 75, {"resp-no-ghost": "fail"}, 100, "no mistakes")
    _rescore(db, "t", 75, {"comm-tone": "fail"}, 100, "fine")
    client, msgs = _fake_client({"lessons": [{
        "kind": "not_a_fail", "text": "A menu click is routing, not a request.",
        "rationale": "QA passed these", "conversation_ids": ["g0", "g1"], "already_covered": False}]})
    object.__setattr__(settings, "anthropic_api_key", "test")
    r = lessons.sync(since="2000-01-01", client=client)
    assert len(msgs.calls) == 1                      # resp-no-ghost only: comm-tone has 1 < 3
    assert "resp-no-ghost" in msgs.calls[0]["messages"][0]["content"]
    store = LessonsStore()
    rows = store.list(status="draft")
    last = json.loads(store.get_meta("lessons.last_sync"))
    store.close()
    assert len(rows) == 1 and rows[0]["criterion_id"] == "resp-no-ghost"
    assert [e["conversation_id"] for e in rows[0]["evidence"]] == ["g0", "g1"]
    assert r["drafted"] == [rows[0]["id"]] and last["criteria"]["resp-no-ghost"] == 3


def test_sync_without_drafting_only_reports(db):
    object.__setattr__(settings, "anthropic_api_key", "")
    _rescore(db, "1", 75, {"resp-no-ghost": "fail"}, 100, "no mistakes")
    r = lessons.sync(since="2000-01-01", draft=False)
    assert r["drafted"] == [] and r["criteria"] == {"resp-no-ghost": 1}
    assert lessons.last_sync_at() is not None


PROMPT = """# Rules
## QA PRACTICE — WHAT IS NOT A FAILURE
- the inactivity close is fine.

## MANDATORY ESCALATION
1. VIP.
"""


def test_add_to_prompt_creates_then_extends_the_learned_list():
    l1 = {"id": "ql-1", "criterion_id": "resp-no-ghost", "kind": "not_a_fail", "text": "Menu clicks route."}
    l2 = {"id": "ql-2", "criterion_id": "res-no-fake-close", "kind": "is_a_fail",
          "text": "[sharpens an existing rule] Unanswered last message."}
    once = lessons.add_to_prompt(PROMPT, l1)
    twice = lessons.add_to_prompt(once, l2)
    learned = twice.index(lessons.LEARNED_HEADING)
    assert twice.index("## QA PRACTICE") < learned < twice.index("## MANDATORY ESCALATION")
    assert "- resp-no-ghost — NOT a failure: Menu clicks route. (QA lesson ql-1)" in twice
    assert "- res-no-fake-close — IS a failure: Unanswered last message. (QA lesson ql-2)" in twice
    assert twice.count(lessons.LEARNED_HEADING) == 1
    assert twice.index("ql-1") < twice.index("ql-2")


def test_approve_writes_the_prompt_and_reaches_the_verifier_but_regrades_nothing(db, tmp_path,
                                                                                monkeypatch):
    from intercom_summary.qa import rulesets

    prompt = tmp_path / "kb.txt"
    prompt.write_text(PROMPT, encoding="utf-8")
    real = rulesets.get_ruleset

    def fake_get(rid=None):
        rs = real(rid)
        text = prompt.read_text(encoding="utf-8")
        return SimpleNamespace(**{**rs.__dict__, "prompt_path": prompt, "prompt_text": text,
                                  "version": str(hash(text))})

    monkeypatch.setattr(rulesets, "get_ruleset", fake_get)
    _rescore(db, "old", 75, {"resp-no-ghost": "fail"}, 100, "no mistakes")
    store = LessonsStore()
    lid = store.add(ruleset_id="kb-v41", criterion_id="resp-no-ghost", kind="not_a_fail",
                    text="A bot-menu click is routing.")
    store.close()

    out = lessons.approve(lid, "admin")
    assert "A bot-menu click is routing. (QA lesson" in prompt.read_text(encoding="utf-8")
    assert out["rules_version"] == str(hash(prompt.read_text(encoding="utf-8")))
    with pytest.raises(ValueError):
        lessons.approve(lid, "admin")              # decided once

    learned = FailVerifier("on").learned("kb-v41")
    assert learned == {"resp-no-ghost": ["A bot-menu click is routing."]}
    assert "NOT A FAIL: A bot-menu click is routing." in verification_text(
        [{"id": "resp-no-ghost", "ev": "x"}], learned)
    # The approval changes the rules for new chats only: the stored grade stays current.
    gs = GradesStore(db)
    assert gs.is_current("old", "kb-v41", out["rules_version"])
    gs.close()


def test_only_a_draft_can_be_edited_or_decided(db):
    store = LessonsStore()
    lid = store.add(ruleset_id="kb-v41", criterion_id="comm-tone", kind="is_a_fail", text="x")
    assert store.edit_text(lid, "y") and store.get(lid)["text"] == "y"
    assert store.decide(lid, "rejected", "boss")
    assert not store.edit_text(lid, "z") and not store.decide(lid, "approved", "boss")
    with pytest.raises(ValueError):
        store.add(ruleset_id="kb-v41", criterion_id="c", kind="maybe", text="x")
    store.close()
