"""What `Conversation.transcript_text()` shows the grader.

This is the only view the QA model gets of a conversation, so what it drops and how it labels
each turn decides what the model can possibly conclude. A client reported being penalised for
missing greetings that were plainly present; part of the cause was here.
"""
from datetime import datetime, timedelta, timezone

from intercom_summary.intercom.models import Admin, Contact, Conversation, Message

_T0 = datetime(2026, 8, 1, 2, 10, 0, tzinfo=timezone.utc)


def _at(seconds: int) -> datetime:
    return _T0 + timedelta(seconds=seconds)


def _convo(messages: list[Message]) -> Conversation:
    return Conversation(
        id="42", created_at=_T0, updated_at=_at(600), state="closed",
        assignee=Admin(id="1", name="Lenny"), contact=Contact(name="Keely Smith"),
        messages=messages,
    )


def test_empty_bookkeeping_turns_are_dropped_so_the_agent_opens_with_the_greeting():
    # An empty `admin (assignment)` part used to survive the filter, so the first AGENT line
    # in the transcript was blank and the model read it as the agent opening with nothing.
    convo = _convo([
        Message(0, "user", "Keely Smith", _at(0), "Game error"),
        Message(1, "admin", "Lenny", _at(300), "", part_type="assignment"),
        Message(2, "admin", "Lenny", _at(360), "Hello, Keely! How can I help?"),
        Message(3, "admin", "Lenny", _at(900), "", part_type="close"),
    ])
    lines = convo.transcript_text().splitlines()

    assert len(lines) == 2, lines
    first_agent = next(ln for ln in lines if " AGENT " in ln)
    assert "Hello, Keely!" in first_agent
    assert "(assignment)" not in convo.transcript_text()
    assert "(close)" not in convo.transcript_text()


def test_the_first_reply_is_timed_from_when_the_chat_reached_the_agent():
    # The player wrote at 0 and the chat was routed at 5m, so the agent's own latency is 1m —
    # not the 6m the player waited. Billing the agent for the whole 6m marked 22.3% of chats
    # as SLA breaches that were inside target from the moment the agent could see them.
    convo = _convo([
        Message(0, "user", "Keely Smith", _at(0), "Game error"),
        Message(1, "admin", "Lenny", _at(300), "", part_type="assignment"),
        Message(2, "admin", "Lenny", _at(360), "Hello, Keely!"),
    ])
    reply = next(ln for ln in convo.transcript_text().splitlines() if "Hello, Keely!" in ln)

    assert convo.agent_first_reply_seconds == 60
    assert "+1m 00s after the chat reached the agent" in reply
    assert "6m 00s" not in reply


def test_an_empty_human_comment_is_still_shown():
    # Only bookkeeping is noise. A comment a person actually sent empty is worth seeing.
    convo = _convo([
        Message(0, "user", "Keely Smith", _at(0), "", part_type="comment"),
        Message(1, "admin", "Lenny", _at(60), "Hello?"),
    ])
    assert len(convo.transcript_text().splitlines()) == 2


def test_a_lead_reads_as_a_customer():
    # Intercom calls an unidentified visitor a "lead". They are the customer, and with no
    # assignment event the agent's clock falls back to their first message.
    convo = _convo([
        Message(0, "lead", "Sanna Rokka", _at(0), "Please block my account"),
        Message(1, "admin", "Oswald", _at(120), "Hello, Sanna!"),
    ])
    text = convo.transcript_text()

    assert "CUSTOMER Sanna Rokka" in text
    assert "LEAD" not in text
    assert convo.agent_first_reply_seconds == 120


def test_bot_turns_with_content_are_kept_and_labelled_bot():
    # The model must be able to tell the bot's opening from the agent's, which is exactly the
    # distinction the greeting criterion turns on.
    convo = _convo([
        Message(0, "user", "Keely Smith", _at(0), "Game error"),
        Message(1, "bot", "Billy Jr.", _at(1), "I am sorry to hear that."),
        Message(2, "bot", "Billy Jr.", _at(2), "", part_type="quick_reply"),
        Message(3, "admin", "Lenny", _at(360), "Hello, Keely!"),
    ])
    lines = convo.transcript_text().splitlines()

    assert len(lines) == 3                       # the empty quick_reply is gone
    assert "BOT Billy Jr.: I am sorry" in lines[1]
    assert lines[2].split(" AGENT ")[0] != lines[2]


# ── who closed the chat ──────────────────────────────────────────────────────────
def test_closed_by_names_the_actor():
    # Automation closes 52.8% of chats here. Three criteria judge the agent's closing
    # behaviour, two of them with an N/A clause reading "agent didn't close the chat" — a rule
    # the model could never apply, because nothing told it who closed.
    bot_closed = _convo([
        Message(0, "user", "Keely Smith", _at(0), "Game error"),
        Message(1, "admin", "Lenny", _at(60), "Hello, Keely!"),
        Message(2, "bot", "Billy Jr.", _at(600), "", part_type="close"),
    ])
    agent_closed = _convo([
        Message(0, "user", "Keely Smith", _at(0), "Game error"),
        Message(1, "admin", "Lenny", _at(60), "All sorted, thanks!"),
        Message(2, "admin", "Lenny", _at(120), "", part_type="close"),
    ])
    still_open = _convo([Message(0, "user", "Keely Smith", _at(0), "Game error")])

    assert bot_closed.closed_by == "bot"
    assert agent_closed.closed_by == "admin"
    assert still_open.closed_by == ""


def test_closed_by_takes_the_last_close_when_a_chat_was_reopened():
    convo = _convo([
        Message(0, "user", "Keely Smith", _at(0), "Game error"),
        Message(1, "admin", "Lenny", _at(60), "", part_type="close"),
        Message(2, "user", "Keely Smith", _at(120), "Still broken"),
        Message(3, "bot", "Billy Jr.", _at(900), "", part_type="close"),
    ])
    assert convo.closed_by == "bot"


# ── the grader's bot-free view ───────────────────────────────────────────────────
def _mixed() -> Conversation:
    return _convo([
        Message(0, "user", "Keely Smith", _at(0), "Game error"),
        Message(1, "bot", "Billy Jr.", _at(10), "Welcome to King Billy! How may I serve you?"),
        Message(2, "bot", "Billy Jr.", _at(20), "Which of the King's treasures catches your eye?"),
        Message(3, "admin", "Lenny", _at(300), "Hello, Keely! Let me check that."),
        Message(4, "bot", "Billy Jr.", _at(900),
                "Looks like the chat has become inactive, therefore I will wrap things up."),
    ])


def test_bot_turns_are_stripped_from_the_graders_view_but_kept_for_the_ui():
    convo = _mixed()

    graded = convo.transcript_text(include_bots=False)
    assert "] BOT " not in graded
    assert "King's treasures" not in graded          # bot marketing, scored as the agent's
    assert "wrap things up" not in graded            # the bot's auto-close, worth −15 a time
    assert "Hello, Keely!" in graded

    # Analysts still need the whole thread, and that is the default.
    full = convo.transcript_text()
    assert "] BOT Billy Jr.: Welcome to King Billy!" in full
    assert "wrap things up" in full


def test_each_removed_run_of_bot_turns_leaves_one_counted_marker():
    graded = _mixed().transcript_text(include_bots=False)

    assert "— 2 automated messages omitted —" in graded    # the two-turn opening run
    assert "— 1 automated message omitted —" in graded     # the closing run, singular
    assert graded.count("omitted") == 2


def test_timing_is_measured_between_the_surviving_turns():
    # The customer waited 5 minutes. Two bot turns sat in between and this chat carries no
    # assignment event, so the agent's clock falls back to the player's message — removing the
    # bot turns must not make the wait look shorter than it was.
    convo = _mixed()
    reply = next(ln for ln in convo.transcript_text(include_bots=False).splitlines()
                 if "Hello, Keely!" in ln)

    assert convo.agent_first_reply_seconds == 300
    assert "5m 00s" in reply


# ── the agent's own SLA clock ────────────────────────────────────────────────────
def test_the_agent_is_not_billed_for_time_the_bot_held_the_chat():
    # The reported case: player wrote at 02:11:10, bot answered a second later and held the
    # chat, routing happened at 02:16:13 and the agent replied at 02:16:26. Intercom reports
    # 5m 16s; the agent's own latency is 13s.
    convo = _convo([
        Message(0, "user", "Sheraldyn Cassells", _at(0), "Bonus request"),
        Message(1, "bot", "Billy Jr.", _at(1), "Which of the King's Treasures interests you?"),
        Message(2, "bot", "Billy Jr.", _at(302), "", part_type="user_became_idle"),
        Message(3, "bot", "Billy Jr.", _at(303), "", part_type="message_strategy_assignment"),
        Message(4, "admin", "Lenny", _at(316), "Hello, Sheraldyn!"),
    ])
    assert convo.agent_first_reply_seconds == 13

    sla = convo.sla_summary(120, 300)
    assert sla["first_response_breached"] is False    # 13s is well inside the 2m target
    assert sla["first_response_time"] is None         # Intercom's figure is not set here
    assert sla["agent_first_reply_human"] == "13s"


def test_an_agent_who_had_the_chat_all_along_is_still_measured_from_assignment():
    # The clock must not become an excuse: assigned at the start, replied 20 minutes later.
    convo = _convo([
        Message(0, "user", "Keely Smith", _at(0), "Game error"),
        Message(1, "bot", "Billy Jr.", _at(0), "", part_type="default_assignment"),
        Message(2, "admin", "Lenny", _at(1200), "Sorry for the wait!"),
    ])
    assert convo.agent_first_reply_seconds == 1200
    assert convo.sla_summary(120, 300)["first_response_breached"] is True


def test_a_reassignment_starts_the_clock_again_for_whoever_replies():
    convo = _convo([
        Message(0, "user", "Keely Smith", _at(0), "Game error"),
        Message(1, "bot", "Billy Jr.", _at(0), "", part_type="default_assignment"),
        Message(2, "bot", "Billy Jr.", _at(600), "", part_type="assignment"),
        Message(3, "admin", "Lenny", _at(630), "Hello, Keely!"),
    ])
    assert convo.agent_first_reply_seconds == 30


def test_no_agent_reply_means_no_latency_and_no_breach():
    convo = _convo([
        Message(0, "user", "Keely Smith", _at(0), "Game error"),
        Message(1, "bot", "Billy Jr.", _at(1), "Hi!"),
    ])
    assert convo.agent_first_reply_seconds is None
    assert convo.sla_summary(120, 300)["first_response_breached"] is False


def test_only_the_first_agent_turn_uses_the_assignment_clock():
    convo = _convo([
        Message(0, "user", "Keely Smith", _at(0), "Game error"),
        Message(1, "bot", "Billy Jr.", _at(60), "", part_type="assignment"),
        Message(2, "admin", "Lenny", _at(90), "Hello, Keely!"),
        Message(3, "user", "Keely Smith", _at(120), "Any news?"),
        Message(4, "admin", "Lenny", _at(300), "Still checking."),
    ])
    lines = convo.transcript_text().splitlines()
    first = next(ln for ln in lines if "Hello, Keely!" in ln)
    later = next(ln for ln in lines if "Still checking." in ln)

    assert "+30s after the chat reached the agent" in first
    # A later gap really is the agent keeping the player waiting, so it keeps the plain marker.
    assert "waited after customer" in later and "3m 00s" in later
