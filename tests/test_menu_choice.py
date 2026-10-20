"""Bot-menu buttons are marked in the transcript so the grader does not read them as the
player's own words (QA feedback, Oct 2026: "My deposit did not come" was a button, and the AI
failed the agent for never answering it)."""
from datetime import datetime, timedelta, timezone

from intercom_summary.intercom.models import Admin, Contact, Conversation, Message

T0 = datetime(2026, 10, 7, 16, 17, 54, tzinfo=timezone.utc)


def _msg(seq, author_type, name, secs, text, part_type="comment"):
    return Message(seq, author_type, name, T0 + timedelta(seconds=secs), text, part_type)


def _convo(messages):
    return Conversation(id="c1", created_at=T0, updated_at=T0, state="closed", subject="s",
                        assignee=Admin(id="1", name="Hannah", email="h@x"),
                        contact=Contact(name="Pierre", email="p@x"), messages=messages)


def test_menu_clicks_are_marked_and_typed_lines_are_not():
    c = _convo([
        _msg(0, "user", "Pierre", 0, "Transaction info"),                 # the opening menu
        _msg(1, "bot", "Billy Jr.", 0, "", "default_assignment"),
        _msg(2, "bot", "Billy Jr.", 1, "Please choose an option"),
        _msg(3, "bot", "Billy Jr.", 1, "", "quick_reply"),                 # the menu itself
        _msg(4, "user", "Pierre", 10, "My deposit did not come"),          # a button
        _msg(5, "bot", "Billy Jr.", 10, "Could you send a screenshot?"),
        _msg(6, "user", "Pierre", 22, "What game are my 10 fs with?"),    # typed
        _msg(7, "admin", "Hannah", 65, "Hello, Pierre!"),
        _msg(8, "user", "Pierre", 80, "Other bonuses"),                    # typed to the agent
    ])
    assert c.menu_choices() == {0, 4}
    text = c.transcript_text()
    assert "CUSTOMER Pierre (bot-menu choice): Transaction info" in text
    assert "CUSTOMER Pierre (bot-menu choice): My deposit did not come" in text
    assert "CUSTOMER Pierre: What game are my 10 fs with?" in text
    assert "CUSTOMER Pierre: Other bonuses" in text
    # The grader's view (bots omitted) keeps the markers.
    assert "(bot-menu choice): My deposit did not come" in c.transcript_text(include_bots=False)


def test_a_typed_opening_line_is_not_a_menu_choice():
    c = _convo([
        _msg(0, "user", "Pierre", 0, "Hello, I just confirmed my email but still cannot log in"),
        _msg(1, "bot", "Billy Jr.", 1, "Welcome! How may I serve you today?"),
        _msg(2, "admin", "Hannah", 60, "Hello!"),
    ])
    assert c.menu_choices() == set()
    assert "(bot-menu choice)" not in c.transcript_text()
