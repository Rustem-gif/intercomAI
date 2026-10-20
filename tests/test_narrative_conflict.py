"""The narrative and the verdict are made separately — the verdict from the criteria, the story
from the model's prose — and QA has seen them part ways (feedback on the 8 Oct calibration run:
"no mistakes by the agent" over a FAIL, "two messages left unanswered" over a PASS). Wording never
changes a verdict; such a chat goes to a QC manager instead."""
from intercom_summary.qa.gated_rules import narrative_conflict
from intercom_summary.qa.schema import ConversationGrade


def _payload(criteria, what_happened, status="answered"):
    return {
        "case_type": "Withdrawal", "risk_flag": "None", "expected_handling": "Answer",
        "data_sufficiency": "Sufficient",
        "requests": [{"text": "Where is my withdrawal?", "status": status, "material": True}],
        "criteria": criteria, "outcome_status": "Resolved", "flags": [], "violations": [],
        "what_happened": what_happened, "manual_review_needed": False,
        "manual_review_reason": "", "confidence": "High",
    }


def test_phrases_that_contradict_a_verdict():
    assert "unanswered" in narrative_conflict(
        "The player's last two messages were left unanswered and the chat was closed.", "PASS")
    assert narrative_conflict("The chat was closed with no agent reply.", "PASS")
    # The player going silent is the opposite case and is no contradiction of a PASS.
    assert narrative_conflict(
        "The agent asked for a bank statement, reminded the player, and the player never answered.",
        "PASS") is None
    assert narrative_conflict("No mistakes by the agent were found.", "FAIL")
    assert narrative_conflict("The agent handled the request correctly and closed.", "FAIL")
    assert narrative_conflict("The agent handled the request correctly and closed.", "PASS") is None
    assert narrative_conflict("anything at all", "CANNOT DETERMINE") is None


def test_a_pass_whose_story_says_the_player_was_left_hanging_goes_to_a_person():
    g = ConversationGrade.from_ollama_output("c1", "Lenny", _payload(
        [{"id": "resp-no-ghost", "v": "pass", "ev": ""}, {"id": "comm-tone", "v": "pass", "ev": ""}],
        "The agent forwarded the card question to finance. The player's last two messages were "
        "left unanswered and the chat was closed by automation."), ruleset_id="kb-v41")
    assert g.overall_result == "PASS"            # the wording alone never changes a verdict
    assert g.manual_review_needed is True
    assert "Narrative contradicts the PASS" in g.manual_review_reason


def test_a_fail_whose_story_says_no_mistakes_goes_to_a_person():
    g = ConversationGrade.from_ollama_output("c1", "Lenny", _payload(
        [{"id": "resp-no-ghost", "v": "fail", "ev": "AGENT: anything else?"}],
        "The agent asked for a bank statement and the player did not reply. No mistakes by the "
        "agent were found.", status="unresolved"), ruleset_id="kb-v41")
    assert g.overall_result == "FAIL" and g.manual_review_needed is True
    assert "no mistakes" in g.manual_review_reason.lower()


def test_a_consistent_grade_is_left_alone():
    g = ConversationGrade.from_ollama_output("c1", "Lenny", _payload(
        [{"id": "resp-no-ghost", "v": "pass", "ev": ""}],
        "The player asked about a withdrawal; the agent gave the status and the player thanked "
        "them."), ruleset_id="kb-v41")
    assert g.overall_result == "PASS" and g.manual_review_needed is False
