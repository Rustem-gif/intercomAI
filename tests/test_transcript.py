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


def test_the_sla_wait_marker_lands_on_the_real_reply_not_the_assignment_row():
    convo = _convo([
        Message(0, "user", "Keely Smith", _at(0), "Game error"),
        Message(1, "admin", "Lenny", _at(300), "", part_type="assignment"),
        Message(2, "admin", "Lenny", _at(360), "Hello, Keely!"),
    ])
    reply = next(ln for ln in convo.transcript_text().splitlines() if "Hello, Keely!" in ln)
    assert "waited after customer" in reply
    assert "6m 00s" in reply     # measured from the customer's turn, not the assignment


def test_an_empty_human_comment_is_still_shown():
    # Only bookkeeping is noise. A comment a person actually sent empty is worth seeing.
    convo = _convo([
        Message(0, "user", "Keely Smith", _at(0), "", part_type="comment"),
        Message(1, "admin", "Lenny", _at(60), "Hello?"),
    ])
    assert len(convo.transcript_text().splitlines()) == 2


def test_a_lead_reads_as_a_customer():
    # Intercom calls an unidentified visitor a "lead". They are the customer; labelling them
    # LEAD hid that from the model and suppressed the "waited" marker on the agent's reply.
    convo = _convo([
        Message(0, "lead", "Sanna Rokka", _at(0), "Please block my account"),
        Message(1, "admin", "Oswald", _at(120), "Hello, Sanna!"),
    ])
    text = convo.transcript_text()

    assert "CUSTOMER Sanna Rokka" in text
    assert "LEAD" not in text
    assert "waited after customer" in text


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
    # The customer waited 5 minutes for the agent. Two bot turns sat in between; removing them
    # must not make the wait look shorter than it was.
    reply = next(ln for ln in _mixed().transcript_text(include_bots=False).splitlines()
                 if "Hello, Keely!" in ln)
    assert "waited after customer" in reply
    assert "5m 00s" in reply
