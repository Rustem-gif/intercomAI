"""The majority vote on borderline grades (qa/grader.py). Two runs of the same rules flip the
PASS/FAIL of about one QA-reviewed chat in ten; a borderline grade is made again and the
majority decides."""
from intercom_summary.qa.grader import Grader
from intercom_summary.settings import Settings, settings
from tests.test_grader import FakeClaude, _convo, _v41, reply

ALL_PASS = dict(
    requests=[{"text": "Where is my withdrawal?", "status": "answered", "material": True}],
    criteria=[{"id": "resp-no-ghost", "v": "pass", "ev": ""},
              {"id": "comm-tone", "v": "pass", "ev": ""},
              {"id": "crit-data-care", "v": "pass", "ev": "no card data requested"}],
    outcome_status="Resolved", summary="Answered the withdrawal question.",
)


def _grader(fake, votes=3):
    return Grader(ruleset_id="kb-v41", client=fake, jev=False, knowledge={}, history=False,
                  fail_verifier=False, votes=votes)


def test_a_borderline_fail_is_outvoted_by_two_passes():
    fake = FakeClaude(reply(_v41()), reply(_v41(**ALL_PASS)), reply(_v41(**ALL_PASS)))
    g = _grader(fake).grade(_convo())
    assert len(fake.calls) == 3
    assert g.overall_result == "PASS" and g.overall_score == 100
    assert g.votes["n"] == 3 and g.votes["majority"] == "PASS" and g.votes["changed"] is True
    assert [v["result"] for v in g.votes["results"]] == ["FAIL", "PASS", "PASS"]
    assert g.usage["calls"] == 3                 # the cost of every vote is on the grade


def test_the_majority_can_confirm_the_first_grade():
    fake = FakeClaude(reply(_v41()), reply(_v41(**ALL_PASS)), reply(_v41()))
    g = _grader(fake).grade(_convo())
    assert g.overall_result == "FAIL" and g.votes["changed"] is False
    assert g.votes["results"][1]["result"] == "PASS"


def test_a_clear_grade_is_not_voted_on():
    fake = FakeClaude(reply(_v41(**ALL_PASS)))
    g = _grader(fake).grade(_convo())
    assert len(fake.calls) == 1 and g.overall_score == 100 and g.votes == {}


def test_votes_off_means_one_grading():
    fake = FakeClaude(reply(_v41()), reply(_v41(**ALL_PASS)))
    g = _grader(fake, votes=1).grade(_convo())
    assert len(fake.calls) == 1 and g.overall_result == "FAIL" and g.votes == {}


def test_unusable_votes_leave_the_first_grade_as_it_was():
    fake = FakeClaude(reply(_v41()), reply(text="not json", stop_reason="max_tokens"))
    g = _grader(fake).grade(_convo())
    assert g.overall_result == "FAIL" and g.votes == {}


def test_votes_default_on_for_the_gated_ruleset_and_the_band_is_configurable():
    assert Settings().qa_votes == 3 and Settings().qa_vote_band == (70, 89)
    old = settings.qa_vote_band
    object.__setattr__(settings, "qa_vote_band", (90, 99))
    try:
        fake = FakeClaude(reply(_v41()), reply(_v41(**ALL_PASS)))
        g = _grader(fake).grade(_convo())          # 75 is outside 90–99: no vote
        assert len(fake.calls) == 1 and g.overall_result == "FAIL"
    finally:
        object.__setattr__(settings, "qa_vote_band", old)
