"""Who stopped responding — `Conversation.ending` and the header line built from it.

QA (2026-10-09): the grader confused "the agent did not reply" with "the player did not reply".
The order of the last few lines decides which it was, so it is computed and stated, never left
to the model's own timestamp reading.
"""
from datetime import datetime, timedelta, timezone

from intercom_summary.intercom.models import Admin, Conversation, Message, is_agent_macro
from intercom_summary.qa.prompt import _ending_line, transcript_block

_T0 = datetime(2026, 8, 24, 19, 0, 0, tzinfo=timezone.utc)


def _at(minutes: int) -> datetime:
    return _T0 + timedelta(minutes=minutes)


def _convo(*messages: Message) -> Conversation:
    return Conversation(id="1", created_at=_T0, updated_at=None, state="closed",
                        assignee=Admin(id="1", name="Lenny"), messages=list(messages))


def _close(seq: int, who: str, minute: int) -> Message:
    return Message(seq, who, "x", _at(minute), "", part_type="close")


def test_the_player_wrote_last_and_was_never_answered():
    """№103: two player messages after the agent's last reply; the bot closed the chat."""
    c = _convo(
        Message(0, "user", "Nicola", _at(0), "My card expired"),
        Message(1, "admin", "Lenny", _at(2), "Forwarded to finance.", part_type="comment"),
        Message(2, "bot", "Billy Jr.", _at(3), "Just checking back in"),
        Message(3, "user", "Nicola", _at(5), "Are you still connected to my chat Lenny"),
        Message(4, "user", "Nicola", _at(7), "I have sent an email to your team"),
        _close(5, "bot", 8),
        Message(6, "admin", "Lenny", _at(9), "forwarded the request", part_type="note"),
    )
    e = c.ending
    assert e["last_role"] == "CUSTOMER" and not c.agent_spoke_last
    assert [m.seq for m in e["unanswered_player"]] == [3, 4]
    line = _ending_line(c)
    assert line.startswith("Ending: the player's last 2 message(s) came after the agent's last "
                           "reply at 19:02:00 and got no agent reply")
    assert '19:05:00 "Are you still connected to my chat Lenny"' in line
    assert line.endswith("automation closed the chat at 19:08:00.")


def test_the_agent_asked_waited_and_closed_after_the_player_went_quiet():
    """№116: only inactivity prompts after the player's "Don't go anywhere"."""
    c = _convo(
        Message(0, "user", "Jessica", _at(0), "My deposit did not come"),
        Message(1, "admin", "Markus", _at(1), "Can you please provide the bank statement?"),
        Message(2, "user", "Jessica", _at(2), "Don't go anywhere"),
        Message(3, "admin", "Markus", _at(3), "I am here, I am waiting for your response."),
        Message(4, "admin", "Markus", _at(6), "Are we still connected?"),
        Message(5, "admin", "Markus", _at(9), "I see no reply from your side and I will close"),
        _close(6, "admin", 9),
    )
    e = c.ending
    assert c.agent_spoke_last and e["agent_tail_is_template"]
    assert e["last_substantive_agent"].seq == 1
    line = _ending_line(c)
    assert "after the player's last message at 19:02:00 (\"Don't go anywhere\")" in line
    assert "3 message(s), all inactivity/closing prompts" in line
    assert "last substantive reply was at 19:01:00, before it" in line
    assert line.endswith("the agent closed the chat at 19:09:00.")


def test_the_agent_answered_last_with_substance():
    c = _convo(
        Message(0, "user", "Amber", _at(0), "Any free spins?"),
        Message(1, "admin", "Markus", _at(1), "Unfortunately, we don't have any free bonuses."),
        Message(2, "admin", "Markus", _at(2), "Is there anything else I can help you with?"),
        _close(3, "admin", 5),
    )
    assert not c.ending["agent_tail_is_template"]
    assert _ending_line(c) == (
        'Ending: the agent replied after the player\'s last message at 19:00:00 ("Any free '
        'spins?") — 2 message(s) from 19:01:00; the player did not write again; the agent '
        'closed the chat at 19:05:00.')


def test_no_agent_reply_and_an_open_chat():
    c = _convo(Message(0, "user", "Lead", None, "123456"))
    assert _ending_line(c) == "Ending: the agent never wrote in this chat; the chat is still open."
    assert _ending_line(_convo()) == "Ending: no agent or player messages; the chat is still open."


def test_notes_bots_and_bookkeeping_parts_are_not_comments():
    c = _convo(
        Message(0, "user", "Kim", _at(0), "Hi"),
        Message(1, "admin", "Ada", _at(1), "Hello!"),
        Message(2, "admin", "Ada", _at(2), "internal remark", part_type="note"),
        Message(3, "bot", "Billy Jr.", _at(3), "Rate us"),
        Message(4, "admin", "Ada", _at(4), "", part_type="assignment"),
    )
    assert [m.seq for m in c.human_comments()] == [0, 1]
    assert c.agent_spoke_last


def test_the_ending_line_is_in_the_graders_header():
    c = _convo(Message(0, "user", "Kim", _at(0), "Hi"), Message(1, "admin", "Ada", _at(1), "Hello"))
    assert "\nEnding: the agent replied after" in transcript_block(c)


def test_macros_are_recognised_in_english_and_german():
    for text in ("I see no reply from your side and I will close the chat for now.",
                 "Looks like the chat has become inactive, therefore…",
                 "Is there anything else I can help you with? 🙂",
                 "Ich sehe keine Antwort von Ihrer Seite und werde den Chat schließen",
                 "My pleasure."):
        assert is_agent_macro(text), text
    for text in ("Can you please provide the bank statement?",
                 "Unfortunately, we don't have any free bonuses for you right now."):
        assert not is_agent_macro(text), text
