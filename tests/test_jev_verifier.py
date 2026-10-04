import json
from types import SimpleNamespace

import pytest

from intercom_summary.qa.grader import Grader
from intercom_summary.qa.jev_verifier import (
    DOUBLE_COUNT_PAIRS,
    GATE1,
    JevVerifier,
    build_request,
)
from test_grader import FakeClaude, _convo, _v41, reply

AGENT_LINE = "Hello, how can I help?"   # the only line in _convo()


class FakeJev:
    """Mimics TypeSafeClient.system_one. `nouls` maps a question id (or its prefix before the
    dot) to a probability; anything unspecified answers 'no' (0.02). Choices echo the grader."""

    def __init__(self, nouls=None, choices=None, fail=None):
        self.nouls = nouls or {}
        self.choices = choices or {}
        self.fail = fail
        self.calls: list[dict] = []

    def system_one(self, *, state, questions, model):
        self.calls.append({"state": state, "questions": questions, "model": model})
        if self.fail:
            raise self.fail
        answers = {}
        for qid, q in questions.items():
            if q["type"] == "noul":
                p = self.nouls.get(qid, self.nouls.get(qid.split(".")[0], 0.02))
                answers[qid] = SimpleNamespace(type="noul", noul=p)
            else:
                choice, conf = self.choices.get(qid, (next(iter(q["criteria"])), 0.3))
                answers[qid] = SimpleNamespace(type="choice", choice=choice, confidence=conf)
        return SimpleNamespace(answers=answers, model="jev-1.13.0",
                               usage=SimpleNamespace(input_tokens=4200, output_tokens=40))


def grade_with(jev: FakeJev, mode: str, *replies):
    claude = FakeClaude(*(replies or (reply(_v41()),)))
    grader = Grader(ruleset_id="kb-v41", client=claude,
                    jev=JevVerifier(client=jev, mode=mode))
    return grader.grade(_convo()), claude


# ── the request ────────────────────────────────────────────────────────────────────────
def test_request_asks_blacklist_always_and_support_only_for_gate1_and_major_fails():
    criteria = [
        {"id": "resp-no-ghost", "v": "fail", "ev": "x"},
        {"id": "info-completeness", "v": "fail", "ev": "y"},   # a deduction: no support question
        {"id": "crit-pii-leak", "v": "pass", "ev": "z"},
    ]
    state, questions, plan = build_request("T", criteria)
    assert {f"blacklist.{c}" for c in GATE1} <= set(questions)
    assert {"rg_signal", "case_type", "risk_flag"} <= set(questions)
    assert plan["support"] == {"support.0": "resp-no-ghost"}
    assert state["graded"][0]["criterion"] == "resp-no-ghost"
    assert not plan["same_episode"]
    # Every question refers to the state by path, never by restating it.
    assert "`graded[0].definition`" in questions["support.0"]["instructions"]


def test_double_count_question_only_when_both_halves_of_a_pair_fail():
    a, b = DOUBLE_COUNT_PAIRS[0]
    criteria = [{"id": a, "v": "fail", "ev": "q1"}, {"id": b, "v": "fail", "ev": "q2"}]
    _, questions, plan = build_request("T", criteria)
    assert plan["same_episode"] == {"same_episode.0": [a, b]}
    assert "same_episode.0" in questions


# ── modes ──────────────────────────────────────────────────────────────────────────────
def test_shadow_mode_records_findings_and_changes_nothing():
    plain, _ = grade_with(FakeJev(), "off")
    shadow, _ = grade_with(FakeJev(nouls={"support": 0.05}), "shadow")

    assert shadow.overall_score == plain.overall_score == 72
    assert shadow.manual_review_needed == plain.manual_review_needed
    rules = {f["rule"] for f in shadow.jev["findings"]}
    assert "major_unsupported" in rules
    assert shadow.jev["mode"] == "shadow" and shadow.jev["model"] == "jev-1.13.0"
    assert shadow.jev["usage"]["input_tokens"] == 4200


def test_off_mode_never_calls_jev():
    jev = FakeJev()
    grader = Grader(ruleset_id="kb-v41", client=FakeClaude(reply(_v41())), jev=False)
    assert grader.grade(_convo()).jev == {}
    assert jev.calls == []


def test_flag_mode_sends_an_unsupported_major_fail_to_a_qc_manager():
    grade, _ = grade_with(FakeJev(nouls={"support": 0.05}), "flag")
    assert grade.manual_review_needed is True
    assert "jev:major_unsupported(resp-no-ghost)" in grade.manual_review_reason
    assert grade.overall_score == 72          # Jev never moves the score itself


def test_supported_fails_raise_no_finding():
    grade, _ = grade_with(FakeJev(nouls={"support": 0.95}), "flag")
    assert not any(f["rule"] == "major_unsupported" for f in grade.jev["findings"])


def test_a_quote_that_is_not_in_the_conversation_is_caught_in_code():
    """Even if Jev were fooled, whatever the grader quotes must actually have been said."""
    data = _v41(criteria=[
        {"id": "resp-no-ghost", "v": "fail",
         "ev": 'Agent replied "I will not help you with your withdrawal today" and left.'},
        {"id": "crit-data-care", "v": "pass", "ev": "-"},
    ])
    grade, _ = grade_with(FakeJev(nouls={"support": 0.99}), "flag", reply(data))
    f = next(f for f in grade.jev["findings"] if f["rule"] == "major_unsupported")
    assert "not in the conversation" in f["detail"]


@pytest.mark.parametrize("cid,ev,holds", [
    # An omission is evidenced by describing the gap — there is nothing to quote.
    ("resp-no-ghost", "The player's withdrawal question was never answered.", True),
    # …but a quote it does include must be real (apostrophes don't open quotes).
    ("resp-no-ghost", "Agent's only reply was 'Hello, how can I help?' and nothing else.", True),
    ("resp-no-ghost", "Agent's only reply was 'We never pay bonuses to anyone' and left.", False),
    # Ellipses inside a quote are cuts: each fragment is checked, too-short ones are skipped.
    ("resp-no-ghost", 'Agent: "Hello, how can I help? … We never pay bonuses here"', False),
    ("resp-no-ghost", 'Agent: "Hello, how … can I help?"', True),
    # A commission failure must cite real text, quoted or not.
    ("accuracy-material", "Agent said withdrawals take 24 hours.", False),
    ("accuracy-material", "Hello, how can I help?", True),
])
def test_evidence_holds(cid, ev, holds):
    from intercom_summary.qa.jev_verifier import evidence_holds

    assert evidence_holds(cid, ev, _convo()) is holds


def test_blacklisted_action_the_grader_passed_is_flagged_not_zeroed():
    grade, _ = grade_with(FakeJev(nouls={"blacklist.crit-data-care": 0.97}), "flag")
    assert any(f["rule"] == "gate1_missed" and f["criterion"] == "crit-data-care"
               for f in grade.jev["findings"])
    assert grade.manual_review_needed is True
    assert grade.overall_score == 72 and grade.critical_fail is False


def test_double_count_is_flagged():
    data = _v41(criteria=[
        {"id": "resp-no-ghost", "v": "fail", "ev": AGENT_LINE},
        {"id": "ownership-effort", "v": "fail", "ev": AGENT_LINE},
    ])
    grade, _ = grade_with(FakeJev(nouls={"support": 0.9, "same_episode": 0.88}), "flag",
                          reply(data))
    assert any(f["rule"] == "double_count" and f["criterion"] == "resp-no-ghost+ownership-effort"
               for f in grade.jev["findings"])


def test_rg_signal_the_grader_missed_is_flagged():
    grade, _ = grade_with(FakeJev(nouls={"support": 0.9, "rg_signal": 0.93}), "flag")
    assert any(f["rule"] == "rg_signal_missed" for f in grade.jev["findings"])


def test_step0_disagreement_is_only_a_note():
    grade, _ = grade_with(FakeJev(nouls={"support": 0.9},
                                  choices={"case_type": ("Bonus", 0.9)}), "flag")
    assert any(f["rule"] == "case_type_mismatch" for f in grade.jev["findings"])
    assert grade.manual_review_needed is False


def test_a_jev_failure_never_blocks_the_grade():
    grade, _ = grade_with(FakeJev(fail=ConnectionError("jev down")), "flag")
    assert grade.overall_score == 72
    assert "jev down" in grade.jev["error"]
    assert grade.jev["findings"] == []


def test_jev_runs_only_for_gated_rulesets_and_only_when_configured():
    from intercom_summary.settings import settings

    claude = FakeClaude(reply(_v41()))
    old = settings.jev_mode, settings.jev_api_key
    try:
        object.__setattr__(settings, "jev_mode", "shadow")
        object.__setattr__(settings, "jev_api_key", "test-key")
        assert Grader(ruleset_id="kb-v41", client=claude)._jev is not None
        assert Grader(ruleset_id="default", client=claude)._jev is None   # flat: no definitions
        assert Grader(ruleset_id="vip", client=claude)._jev is None
        object.__setattr__(settings, "jev_api_key", "")
        assert Grader(ruleset_id="kb-v41", client=claude)._jev is None    # no key, no Jev
        object.__setattr__(settings, "jev_api_key", "test-key")
        object.__setattr__(settings, "jev_mode", "off")
        assert Grader(ruleset_id="kb-v41", client=claude)._jev is None
    finally:
        object.__setattr__(settings, "jev_mode", old[0])
        object.__setattr__(settings, "jev_api_key", old[1])


# ── reconcile ──────────────────────────────────────────────────────────────────────────
def test_reconcile_asks_the_grader_once_to_re_examine_the_disputed_criteria():
    revised = _v41(criteria=[
        {"id": "resp-no-ghost", "v": "pass", "ev": "answered"},
        {"id": "comm-tone", "v": "fail", "ev": AGENT_LINE},
    ], outcome_status="Resolved")

    class TwoStep(FakeJev):
        def system_one(self, *, state, questions, model):
            # First check disputes resp-no-ghost; after the revision nothing is left to dispute.
            self.nouls = {"support": 0.05} if not self.calls else {}
            return super().system_one(state=state, questions=questions, model=model)

    grade, claude = grade_with(TwoStep(), "reconcile", reply(_v41()), reply(revised))

    assert len(claude.calls) == 2
    follow_up = claude.calls[1]
    assert follow_up["messages"][1]["role"] == "assistant"
    assert "resp-no-ghost" in follow_up["messages"][2]["content"]
    assert follow_up["output_config"]["effort"] == "high"
    # The revised verdicts are scored: no Major any more → 100 − comm-tone 3 = 97.
    assert grade.overall_score == 97
    assert grade.jev["reconcile"]["changed"]["resp-no-ghost"] == ["fail", "pass"]
    assert grade.manual_review_needed is False


def test_reconcile_that_still_disagrees_ends_in_manual_review():
    grade, claude = grade_with(FakeJev(nouls={"support": 0.05}), "reconcile",
                               reply(_v41()), reply(_v41()))
    assert len(claude.calls) == 2
    assert grade.manual_review_needed is True
    assert "jev:major_unsupported" in grade.manual_review_reason


def test_jev_block_survives_storage_round_trip(tmp_path):
    from intercom_summary.storage.grades_store import GradesStore

    grade, _ = grade_with(FakeJev(nouls={"support": 0.05}), "shadow")
    store = GradesStore(tmp_path / "g.db")
    try:
        store.save(grade)
        stored = store.get(grade.conversation_id)
    finally:
        store.close()
    assert stored["jev"]["findings"] == json.loads(json.dumps(grade.jev["findings"]))
