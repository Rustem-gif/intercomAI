"""The backstop that overturns verdicts the conversation itself contradicts.

Every case here is drawn from real grader output in data/grades.db — the model quoting the
rubric back as its own evidence, quoting "Hello, Keely!" while deducting for the name not being
used, quoting the bot's marketing script as the agent's, quoting the player's profanity as the
agent's rudeness, and penalising a closing the bot performed.
"""
from datetime import datetime, timedelta, timezone

from intercom_summary.intercom.models import Admin, Contact, Conversation, Message
from intercom_summary.qa.verdict_guard import apply_guards

_T0 = datetime(2026, 8, 1, 2, 10, 0, tzinfo=timezone.utc)
_GREETING = "Hello, Keely! Thank you for contacting the King Billy Support Department."


def _convo(agent_text: str = _GREETING, customer: str = "Keely Smith") -> Conversation:
    return Conversation(
        id="215561098356176", created_at=_T0, updated_at=_T0 + timedelta(minutes=10),
        state="closed", assignee=Admin(id="1", name="Lenny"),
        contact=Contact(name=customer),
        messages=[
            Message(0, "user", customer, _T0, "Game error"),
            Message(1, "admin", "Lenny", _T0 + timedelta(minutes=6), agent_text),
        ],
    )


def _criteria(*entries: tuple[str, str, str, int]) -> dict:
    return {"criteria": [{"id": i, "v": v, "ev": e, "ded": d} for i, v, e, d in entries]}


def _verdicts(data: dict) -> dict[str, str]:
    return {c["id"]: c["v"] for c in data["criteria"]}


def test_ungrounded_greeting_fail_is_dropped_to_na():
    # 433 of 434 open-greet fails in the real database cited this exact string — the rubric's
    # own FAIL condition — rather than anything from the conversation.
    data = _criteria(("open-greet", "fail", "No greeting at conversation start", -2))
    flags = apply_guards(_convo(), data)

    assert _verdicts(data) == {"open-greet": "n/a"}
    assert flags and "not in the transcript" in flags[0]


def test_a_greeting_fail_backed_by_a_real_quote_is_left_alone():
    convo = _convo(agent_text="what do you want")
    data = _criteria(("open-greet", "fail", "AGENT Lenny: what do you want", -2))
    flags = apply_guards(convo, data)

    assert _verdicts(data) == {"open-greet": "fail"}
    assert flags == []


def test_name_use_fail_is_overturned_when_the_agent_used_the_name():
    # The reported bug: the model quotes the agent greeting the player by name and fails it.
    # The evidence IS in the transcript, so grounding alone would not catch this.
    data = _criteria(("open-name-use", "fail", f"AGENT Lenny: {_GREETING}", -1))
    flags = apply_guards(_convo(), data)

    assert _verdicts(data) == {"open-name-use": "pass"}
    assert "agent used the player's name" in flags[0] and "Keely" in flags[0]


def test_name_use_fail_stands_when_the_agent_never_used_the_name():
    convo = _convo(agent_text="Hello! Please describe the problem.")
    data = _criteria(("open-name-use", "fail",
                      "AGENT Lenny: Hello! Please describe the problem.", -1))
    flags = apply_guards(convo, data)

    assert _verdicts(data) == {"open-name-use": "fail"}
    assert flags == []


def test_a_name_too_short_to_be_evidence_is_not_matched():
    # "Al" inside "Also" must not count as addressing the player.
    convo = _convo(agent_text="Also, please try again later.", customer="Al")
    data = _criteria(("open-name-use", "fail", "AGENT Lenny: Also, please try again later.", -1))
    apply_guards(convo, data)

    assert _verdicts(data) == {"open-name-use": "fail"}


def test_an_email_byline_is_not_treated_as_a_name():
    # _author_name falls back to the address when a customer has no name; matching on that
    # would let any mention of the address pass the criterion.
    convo = _convo(agent_text="Write to keely@x.com for help.", customer="keely@x.com")
    data = _criteria(("open-name-use", "fail", "AGENT Lenny: Write to keely@x.com for help.", -1))
    apply_guards(convo, data)

    assert _verdicts(data) == {"open-name-use": "fail"}


def test_the_grounding_rule_stays_scoped_to_greeting_and_name():
    # Mis-attribution and closure apply everywhere; *grounding* does not. Applied to every
    # criterion it would move 37% of failing verdicts and shift scores analysts work from. None
    # of these three cites another speaker, so nothing else in the guard touches them either.
    data = _criteria(
        ("req-understanding", "fail", "No greeting at conversation start", -8),
        ("res-effort", "fail", "n/a", -10),
        ("cf-friendly", "fail", "", -5),
    )
    flags = apply_guards(_convo(), data)

    assert _verdicts(data) == {
        "req-understanding": "fail", "res-effort": "fail", "cf-friendly": "fail"
    }
    assert flags == []


def test_pass_and_na_verdicts_are_never_changed():
    data = _criteria(
        ("open-greet", "pass", "n/a", -2),
        ("open-name-use", "n/a", "n/a", -1),
    )
    assert apply_guards(_convo(), data) == []
    assert _verdicts(data) == {"open-greet": "pass", "open-name-use": "n/a"}


def test_malformed_grader_output_is_tolerated():
    for data in ({}, {"criteria": None}, {"criteria": ["not a dict"]}, {"criteria": [{}]}):
        assert apply_guards(_convo(), data) == []


# ── someone else's words are never the agent's failure ───────────────────────────
def _thread(closed_by: str | None = None) -> Conversation:
    msgs = [
        Message(0, "user", "James Napier", _T0, "Oh piss off oswald"),
        Message(1, "bot", "Billy Jr.", _T0 + timedelta(seconds=10),
                "Which of the King's treasures catches your eye today?"),
        Message(2, "admin", "Lenny", _T0 + timedelta(minutes=5), _GREETING),
        Message(3, "bot", "Billy Jr.", _T0 + timedelta(minutes=15),
                "Looks like the chat has become inactive, therefore I will wrap things up."),
    ]
    if closed_by:
        msgs.append(Message(4, closed_by, "x", _T0 + timedelta(minutes=16), "", part_type="close"))
    return Conversation(
        id="215561064650839", created_at=_T0, updated_at=_T0 + timedelta(minutes=20),
        state="closed", assignee=Admin(id="1", name="Lenny"),
        contact=Contact(name="James Napier"), messages=msgs,
    )


def test_a_fail_quoting_the_bot_is_dropped_on_any_criterion():
    # The reported bug: the agent lost 14 points for the bot's marketing script.
    data = _criteria(
        ("info-relevance", "fail", "Which of the King's treasures catches your eye today?", -7),
        ("resp-no-template-abuse", "fail",
         "BOT Billy Jr.: Which of the King's treasures catches your eye today?", -7),
    )
    flags = apply_guards(_thread(), data)

    assert _verdicts(data) == {"info-relevance": "n/a", "resp-no-template-abuse": "n/a"}
    assert len(flags) == 2 and all("bot line" in f for f in flags)


def test_a_fail_quoting_the_player_is_dropped():
    # Player profanity scored as the agent's rudeness — which the rulebook expressly forbids.
    data = _criteria(("cf-friendly", "fail", "CUSTOMER James Napier: Oh piss off oswald", -5))
    flags = apply_guards(_thread(), data)

    assert _verdicts(data) == {"cf-friendly": "n/a"}
    assert "customer line" in flags[0]


def test_criteria_that_exist_to_react_to_the_player_keep_their_player_quote():
    # Churn detection is *defined* by what the player said, so a customer quote is the correct
    # evidence. Overturning these would break the criteria outright.
    data = _criteria(
        ("churn-detect-ack", "fail", "Oh piss off oswald", -10),
        ("churn-retention-handling", "fail", "Oh piss off oswald", -8),
        ("pay-withdrawal-sensitivity", "fail", "Oh piss off oswald", -10),
    )
    assert apply_guards(_thread(), data) == []
    assert set(_verdicts(data).values()) == {"fail"}


def test_a_fail_quoting_the_agent_is_left_alone():
    data = _criteria(("info-actionable", "fail", f"AGENT Lenny: {_GREETING}", -8))
    assert apply_guards(_thread(), data) == []
    assert _verdicts(data) == {"info-actionable": "fail"}


# ── the agent did not close the chat ─────────────────────────────────────────────
def test_closing_criteria_are_dropped_when_automation_closed_the_chat():
    data = _criteria(
        ("res-no-fake-close", "fail", "AGENT Lenny: anything else?", -15),
        ("close-confirm", "fail", "AGENT Lenny: anything else?", -3),
        ("close-courtesy", "fail", "AGENT Lenny: anything else?", -2),
    )
    convo = _thread(closed_by="bot")
    assert convo.closed_by == "bot"
    flags = apply_guards(convo, data)

    assert set(_verdicts(data).values()) == {"n/a"}
    assert len(flags) == 3 and all("closed by bot" in f for f in flags)


def test_closing_criteria_stand_when_the_agent_closed_the_chat():
    data = _criteria(("res-no-fake-close", "fail", f"AGENT Lenny: {_GREETING}", -15))
    convo = _thread(closed_by="admin")
    assert apply_guards(convo, data) == []
    assert _verdicts(data) == {"res-no-fake-close": "fail"}


def test_res_next_step_is_not_excused_by_a_bot_close():
    # Explaining what happens next is the agent's job however the chat ended.
    data = _criteria(("res-next-step", "fail", f"AGENT Lenny: {_GREETING}", -8))
    assert apply_guards(_thread(closed_by="bot"), data) == []
    assert _verdicts(data) == {"res-next-step": "fail"}
