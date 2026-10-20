"""The fail verifier: a second look at every grade heading for FAIL (qa/fail_verifier.py)."""
import json

import pytest

from intercom_summary.qa.fail_verifier import VERIFIER_SCHEMA, FailVerifier, verification_text
from intercom_summary.qa.grader import Grader
from intercom_summary.settings import settings

from tests.test_grader import FakeClaude, _convo, _v41, reply


def _checks(**verdicts):
    return reply({"checks": [{"id": cid.replace("_", "-"), "verdict": v, "quote": "q",
                              "reason": f"{cid} reason"} for cid, v in verdicts.items()]})


def _grade(mode, *replies, data=None):
    fake = FakeClaude(reply(data or _v41()), *replies)
    grader = Grader(ruleset_id="kb-v41", client=fake, jev=False, knowledge={}, history=False,
                    fail_verifier=FailVerifier(mode))
    return grader.grade(_convo()), fake


def test_on_mode_overturns_an_unproven_major_and_the_chat_passes():
    grade, fake = _grade("on", _checks(resp_no_ghost="not_proven", comm_tone="upheld"))
    assert len(fake.calls) == 2
    sent = fake.calls[1]
    assert sent["output_config"]["format"]["schema"] == VERIFIER_SCHEMA
    assert "SECOND REVIEW" in sent["messages"][0]["content"]
    assert sent["system"] == fake.calls[0]["system"]          # the cached prefix is reused
    verdicts = {r.rule_id: r.verdict for r in grade.rule_results}
    assert verdicts["resp-no-ghost"] == "pass" and verdicts["comm-tone"] == "fail"
    assert grade.overall_result == "PASS" and grade.overall_score == 97
    assert grade.fail_verifier["overturned"] == ["resp-no-ghost"]
    assert any(f.startswith("verifier:resp-no-ghost") for f in grade.signal_flags)
    assert grade.usage["calls"] == 2


def test_shadow_mode_records_and_changes_nothing():
    grade, _ = _grade("shadow", _checks(resp_no_ghost="not_proven", comm_tone="not_proven"))
    assert grade.overall_result == "FAIL"
    assert {r.rule_id: r.verdict for r in grade.rule_results}["resp-no-ghost"] == "fail"
    assert sorted(grade.fail_verifier["overturned"]) == ["comm-tone", "resp-no-ghost"]
    assert grade.fail_verifier["mode"] == "shadow"


def test_a_passing_chat_is_not_verified():
    data = _v41(criteria=[{"id": "comm-tone", "v": "fail", "ev": "Hello, how can I help?"}],
                requests=[{"text": "hi", "status": "answered", "material": False}],
                outcome_status="Resolved")
    grade, fake = _grade("on", _checks(comm_tone="not_proven"), data=data)
    assert len(fake.calls) == 1 and grade.fail_verifier == {}
    assert grade.overall_result == "PASS"


def test_a_critical_is_never_lifted_only_sent_to_review():
    data = _v41(criteria=[{"id": "crit-data-care", "v": "fail", "ev": "Hello, how can I help?"}],
                requests=[{"text": "hi", "status": "answered", "material": False}],
                outcome_status="Critical Fail")
    grade, _ = _grade("on", _checks(crit_data_care="not_proven"), data=data)
    assert grade.overall_score == 0 and grade.critical_fail
    assert grade.fail_verifier["disputed_critical"] == ["crit-data-care"]
    assert grade.manual_review_needed and "verifier disputes critical" in grade.manual_review_reason


def test_a_verifier_failure_leaves_the_grade_as_it_was():
    grade, _ = _grade("on", reply(text="not json at all"))
    assert grade.overall_result == "FAIL"
    assert "error" in grade.fail_verifier and "overturned" not in grade.fail_verifier


def test_on_by_default_for_gated_rulesets_and_switchable_off():
    from intercom_summary.settings import Settings

    assert Settings().qa_fail_verifier == "on"          # conftest switches it off for the suite
    object.__setattr__(settings, "qa_fail_verifier", "on")
    fake = FakeClaude(reply(_v41()), _checks(resp_no_ghost="upheld", comm_tone="upheld"))
    Grader(ruleset_id="kb-v41", client=fake, jev=False, knowledge={}, history=False).grade(_convo())
    assert len(fake.calls) == 2
    object.__setattr__(settings, "qa_fail_verifier", "off")
    fake = FakeClaude(reply(_v41()))
    Grader(ruleset_id="kb-v41", client=fake, jev=False, knowledge={}, history=False).grade(_convo())
    assert len(fake.calls) == 1


def test_bad_mode_is_rejected():
    with pytest.raises(ValueError):
        FailVerifier("loud")


def test_verification_text_lists_each_fail_with_its_boundaries():
    text = verification_text([{"id": "resp-no-ghost", "v": "fail", "ev": "\"where?\" no reply"},
                              {"id": "made-up", "v": "fail", "ev": "x"}])
    assert '- resp-no-ghost — the first pass wrote: ""where?" no reply"' in text
    assert text.count("NOT A FAIL:") >= 3 and "- made-up" in text
