"""The player's VIP segment from the Intercom contact (intercom/segments.py) and the QC rules
that turn on it (QC answers to the contradictions, 2026-10-21)."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from intercom_summary.intercom.client import IntercomError
from intercom_summary.intercom.models import Admin, Contact, Conversation, Message
from intercom_summary.intercom.segments import (
    ContactSegmenter,
    header_line,
    segment_from_attrs,
)
from intercom_summary.qa.gated_rules import check_verdicts, escalation_note_mandatory
from intercom_summary.qa.prompt import transcript_block
from intercom_summary.qa.rulesets import get_ruleset
from intercom_summary.qa.schema import ConversationGrade
from intercom_summary.settings import settings
from intercom_summary.storage.contact_segments_store import ContactSegmentsStore
from intercom_summary.storage.conversations_store import ConversationsStore


# The attributes as Intercom returned them for chats QC classified (trimmed).
PREVIP = {"Status": "new_baron", "Profitable": "-",
          "Groups": "all_except_vip_and_ba,new_baron,previps_and_vips,>1deposit,previp,4_dep"}
VIP = {"Status": "new_count", "Profitable": "regular_vip",
       "Groups": "except_bonus_abuser,vip_except_ba,regular_vip,new_count,previps_and_vips"}
REGULAR = {"Status": "new_knight", "Profitable": "-",
           "Groups": "except_bonus_abuser,all_except_vip_and_ba,1-10_deps,new_knight"}


@pytest.mark.parametrize("attrs, expected", [
    (VIP, ("VIP", "count")),
    (PREVIP, ("preVIP", "baron")),
    (REGULAR, ("regular", "knight")),
    ({"Profitable": "regular_vip"}, ("VIP", "")),
    ({"Groups": "previp,regular_vip"}, ("VIP", "")),   # promoted: VIP wins
    ({"Status": "None", "Groups": "zero_deps,crypto"}, ("regular", "")),
    ({}, ("", "")),
    (None, ("", "")),
    ({"Profitable": "-"}, ("", "")),
])
def test_segment_from_the_contact_attributes(attrs, expected):
    assert segment_from_attrs(attrs) == expected


def test_header_line_says_unknown_when_not_looked_up():
    assert header_line(Contact(id="c1")) == "Player segment: unknown"
    line = header_line(Contact(id="c1", segment="VIP", loyalty_level="marquess"))
    assert line.startswith("Player segment: VIP (loyalty level: marquess)")
    assert "current" in line


class FakeClient:
    def __init__(self, contacts: dict[str, dict], fail: set[str] = frozenset()):
        self.contacts, self.fail, self.calls = contacts, fail, []

    async def get_contact(self, contact_id: str) -> dict:
        self.calls.append(contact_id)
        await asyncio.sleep(0)
        if contact_id in self.fail:
            raise IntercomError("404 not found")
        return {"custom_attributes": self.contacts.get(contact_id, {})}


def _convo(cid: str, contact_id: str, created: datetime | None = None, *msgs: Message):
    return Conversation(
        id=cid, created_at=created or datetime(2026, 10, 6, tzinfo=timezone.utc),
        updated_at=None, state="closed", channel="conversation",
        assignee=Admin(id="1", name="Ada", email="ada@co.com"),
        contact=Contact(id=contact_id, name="Kim"),
        messages=list(msgs) or [Message(0, "user", "Kim", None, "Close my account please"),
                                Message(1, "admin", "Ada", None, "Done.")],
    )


def test_one_lookup_per_player_and_failures_leave_the_segment_empty():
    client = FakeClient({"vip": VIP, "reg": REGULAR}, fail={"gone"})
    seg = ContactSegmenter(client)
    convos = [_convo("1", "vip"), _convo("2", "vip"), _convo("3", "reg"), _convo("4", "gone")]

    async def run():
        await asyncio.gather(*[seg.annotate(c) for c in convos])

    asyncio.run(run())
    assert [c.contact.segment for c in convos] == ["VIP", "VIP", "regular", ""]
    assert sorted(client.calls) == ["gone", "reg", "vip"]
    assert (seg.lookups, seg.failures) == (3, 1)


def test_the_cache_table_saves_a_lookup_and_expires(tmp_path):
    db = tmp_path / "s.db"
    store = ContactSegmentsStore(db)
    client = FakeClient({"vip": VIP})
    asyncio.run(ContactSegmenter(client, cache=store).annotate(_convo("1", "vip")))
    assert store.get("vip") == ("VIP", "count")

    client2 = FakeClient({"vip": VIP})
    c = asyncio.run(ContactSegmenter(client2, cache=store).annotate(_convo("2", "vip")))
    assert c.contact.segment == "VIP" and client2.calls == []   # served from the table

    stale = ContactSegmentsStore(db, max_age=timedelta(seconds=-1))
    assert stale.get("vip") is None and stale.known("vip") == ("VIP", "count")
    store.close()
    stale.close()


def test_the_segment_survives_the_payload_round_trip_and_a_failed_refetch(tmp_path):
    db = tmp_path / "c.db"
    convo = _convo("9", "vip")
    convo.contact.segment, convo.contact.loyalty_level = "VIP", "count"
    assert Conversation.from_dict(convo.to_dict()).contact.segment == "VIP"
    # Cached before the lookup existed: no segment keys at all.
    old = convo.to_dict()
    del old["contact"]["segment"], old["contact"]["loyalty_level"]
    assert Conversation.from_dict(old).contact.segment == ""

    segments = ContactSegmentsStore(db)
    segments.put("vip", "VIP", "count", VIP["Groups"])
    store = ConversationsStore(db)
    store.save(_convo("9", "vip"))        # this fetch's lookup failed: segment ""
    assert store.get("9").contact.segment == "VIP"
    store.close()
    segments.close()


def test_the_transcript_header_carries_the_segment():
    convo = _convo("1", "vip")
    assert "Player segment: unknown" in transcript_block(convo)
    convo.contact.segment, convo.contact.loyalty_level = "preVIP", "baron"
    head = transcript_block(convo).split("=== TRANSCRIPT")[0]
    assert "Player segment: preVIP (loyalty level: baron)" in head


def test_the_prompt_states_the_qc_rules():
    text = get_ruleset("kb-v41").prompt_text
    assert "The header's Player segment line" in text
    assert "support@kingbillycasino.com" in text
    assert "GDPR request" in text
    assert '"I am NEVER coming back to this site"' in text
    assert "possible scam chat — QC decides" in text
    assert "telling a VIP / preVIP player to write to their VIP manager" in text


# ── QC 2: the mandatory internal note, from a configurable date ─────────────────────────
@pytest.fixture
def note_from():
    before = settings.qa_escalation_note_from

    def set_(value: str):
        object.__setattr__(settings, "qa_escalation_note_from", value)
    yield set_
    object.__setattr__(settings, "qa_escalation_note_from", before)


def _claimed_escalation():
    return {
        "case_type": "Withdrawal", "risk_flag": "Financial",
        "expected_handling": "Internal escalation", "data_sufficiency": "Sufficient",
        "requests": [{"text": "Where is my withdrawal?", "status": "escalated", "material": True}],
        "problems": [],
        "criteria": [{"id": "action-escalation-missed", "v": "pass",
                      "ev": "I have sent your request to the payment department"}],
        "outcome_status": "Escalated correctly", "summary": "", "manual_review_needed": False,
    }


def test_the_note_rule_is_off_until_a_date_is_set(note_from):
    convo = _convo("1", "x", datetime(2026, 11, 5, tzinfo=timezone.utc))
    note_from("")
    assert escalation_note_mandatory(convo) is False
    data = _claimed_escalation()
    check_verdicts(convo, data, get_ruleset("kb-v41"))
    assert data["criteria"][0]["v"] == "cannot_determine"


def test_after_the_date_an_escalation_without_a_note_fails_and_goes_to_review(note_from):
    note_from("2026-11-01")
    before = _convo("1", "x", datetime(2026, 10, 31, 23, 0, tzinfo=timezone.utc))
    after = _convo("2", "x", datetime(2026, 11, 1, 8, 0, tzinfo=timezone.utc))
    assert (escalation_note_mandatory(before), escalation_note_mandatory(after)) == (False, True)

    rs = get_ruleset("kb-v41")
    data = _claimed_escalation()
    check_verdicts(after, data, rs)
    verdict = {c["id"]: c["v"] for c in data["criteria"]}
    assert verdict["action-escalation-missed"] == "fail"
    g = ConversationGrade.from_model_output("2", "Ada", data, ruleset_id="kb-v41")
    assert g.overall_result == "FAIL" and g.manual_review_needed is True
    assert "Escalation missed" in g.manual_review_reason


def test_an_escalation_with_a_note_still_passes_after_the_date(note_from):
    note_from("2026-11-01")
    convo = _convo("3", "x", datetime(2026, 11, 2, tzinfo=timezone.utc),
                   Message(0, "user", "Kim", None, "Where is my withdrawal?"),
                   Message(1, "admin", "Ada", None, "escalated to Payments", part_type="note"),
                   Message(2, "admin", "Ada", None, "I have sent it to the payment department."))
    data = _claimed_escalation()
    check_verdicts(convo, data, get_ruleset("kb-v41"))
    assert data["criteria"][0]["v"] == "pass"


@pytest.mark.parametrize("cid, reason", [
    ("action-escalation-missed", "Escalation missed"),
    ("financial-case-abandoned", "Money case not handled"),
])
def test_escalation_and_money_fails_always_go_to_a_qc_manager(cid, reason):
    rs = get_ruleset("kb-v41")
    criteria = [{"id": c["id"], "v": "fail" if c["id"] == cid else "pass", "ev": "x"}
                for c in rs.criteria]
    data = {"criteria": criteria, "problems": [], "requests": [], "outcome_status": "Unresolved",
            "data_sufficiency": "Sufficient", "manual_review_needed": False,
            "manual_review_reason": ""}
    g = ConversationGrade.from_model_output("7", "Ada", data, ruleset_id="kb-v41")
    assert g.manual_review_needed is True and reason in g.manual_review_reason
