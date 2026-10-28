"""backoffice/player.py, context.py and sync.py on synthetic pages shaped like the real BO's
(the real samples hold personal data and stay in git-ignored data/bo/)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx

from intercom_summary.backoffice import sync as bo_sync
from intercom_summary.backoffice.context import block, render
from intercom_summary.backoffice.player import PlayerFacts, fetch, parse_lazy, parse_player_page
from intercom_summary.intercom.models import Contact, Conversation
from intercom_summary.intercom.segments import bo_id_from
from intercom_summary.storage.bo_players_store import BoPlayersStore

PLAYER = """<html><body>
<div class="panel"><h3>Status</h3><div class="panel_contents"><table class="player-statuses">
<tr class="row"><th>Suspend Activate Disable</th><td><select class="user_status">
<option value="suspend">Suspend</option><option value="enable">Activate</option>
<option value="disable" selected="selected">Disable</option></select></td></tr></table></div></div>
<div class="panel"><h3>Player Locks</h3><div class="panel_contents"><table>
<thead><tr><th class="col col-cs_reason">Cs reason</th><th class="col col-comment">Comment</th>
<th class="col col-created_at">Created at</th><th class="col col-sb_reason">Sb reason</th></tr></thead>
<tbody><tr><td class="col col-cs_reason">manual</td>
<td class="col col-comment">DISABLE: closed per player's request</td>
<td class="col col-created_at">2026-10-12 05:18:15</td></tr></tbody></table></div></div>
<div class="panel"><h3>Groups</h3><div class="panel_contents">
<span class="player-tag">Group: preVIP</span><span class="player-tag">Group: AU</span>
<span class="player-tag">Group: Closed accounts</span></div></div>
<div class="panel"><h3>Comments (1)</h3><div class="active_admin_comment">
<div class="active_admin_comment_meta"><h4 class="active_admin_comment_author">staff@kb</h4>
<span>2026-10-09 01:08:16</span></div><div class="active_admin_comment_body"><p>$50 vip cash added</p></div>
</div><form><li class="hidden input"><input type="hidden"/></li></form></div>
</body></html>"""

PAYMENTS = """<table><thead><tr><th>Action</th></tr></thead><tbody>
<tr><td class="col col-action">Cashout</td><td class="col col-source">Bank</td>
<td class="col col-success"></td><td class="col col-created_at">2026-10-08 07:20:00</td>
<td class="col col-finished_at"></td><td class="col col-amount">2,000 AUD</td></tr>
<tr><td class="col col-action">Deposit</td><td class="col col-source">Card</td>
<td class="col col-success">Yes</td><td class="col col-created_at">2026-09-01 10:00:00</td>
<td class="col col-finished_at">2026-09-01 10:01:00</td><td class="col col-amount">100 AUD</td></tr>
</tbody></table><table><tbody><tr><td class="col col-source">x</td></tr></tbody></table>"""

BONUSES = """<table><tbody><tr><td class="col col-id">Personal bonus from Jasmine</td>
<td class="col col-issued_at">2026-10-09 01:08:00</td><td class="col col-amount">50 AUD</td>
<td class="col col-strategy">Personal</td><td class="col col-stage">Lost</td></tr></tbody></table>"""

LIMITS = """<table><tbody><tr><td class="col col-type_name">Cooling Off</td>
<td class="col col-status">Active</td><td class="col col-period_name">6 Months</td>
<td class="col col-duration">-</td><td class="col col-created_at">2026-10-08 07:30:00</td>
<td class="col col-disable_at">-</td></tr></tbody></table>"""

TAGS = """<select name="tag[]"><option selected="selected">Taylor_vip</option><option>Other</option></select>"""
DUPS = """<ul><li><a href="/backend/players/2">x@y.z</a> with 2 duplicate(s)</li></ul>"""

START = datetime(2026, 10, 8, 7, 15, tzinfo=timezone.utc)
END = datetime(2026, 10, 8, 7, 34, tzinfo=timezone.utc)


def _facts() -> PlayerFacts:
    f = PlayerFacts("1745500", fetched_at="2026-10-28T04:08:28+00:00")
    parse_player_page(PLAYER, f)
    for name, html in (("payments", PAYMENTS), ("bonuses", BONUSES), ("limits", LIMITS),
                       ("tags", TAGS), ("duplicates", DUPS), ("freespins", "<table></table>")):
        parse_lazy(name, html, f)
    return f


def test_the_page_and_its_lazy_panels_parse_by_cell_class():
    f = _facts()
    assert f.status == "disable"
    assert f.locks == [{"kind": "casino", "reason": "manual",
                        "comment": "DISABLE: closed per player's request",
                        "created_at": "2026-10-12 05:18:15"}]
    assert f.groups == ["preVIP", "AU", "Closed accounts"]
    assert f.comments == [{"created_at": "2026-10-09 01:08:16", "text": "$50 vip cash added"}]
    assert [p["action"] for p in f.payments] == ["Cashout", "Deposit"]   # the 2nd table ignored
    assert f.bonuses[0]["title"] == "Personal bonus from Jasmine"
    assert f.limits[0]["type"] == "Cooling Off" and f.tags == ["Taylor_vip"] and f.duplicates == 2
    assert PlayerFacts.from_dict(f.to_dict()) == f


def test_the_block_places_dated_rows_around_the_chat():
    text = render(_facts(), START, END)
    assert "Account status now: DISABLED" in text
    assert "2026-10-12 05:18 UTC (after the chat)" in text           # closed four days later
    assert "Cooling Off 6 Months — Active, set 2026-10-08 07:30 UTC (during the chat)" in text
    assert "Cashout 2,000 AUD via Bank — pending" in text
    assert "Deposit 100 AUD" not in text                              # outside the 14-day window
    assert "Personal bonus from Jasmine — Lost" in text and "(after the chat)" in text
    assert "Groups (relevant): preVIP, Closed accounts" in text and "AU" not in text.split("Groups")[1].split("\n")[0]
    assert "Staff comment 2026-10-09 01:08 UTC (after the chat): $50 vip cash added" in text


def test_the_block_falls_back_when_the_bo_has_nothing(tmp_path):
    store = BoPlayersStore(tmp_path / "b.db")
    assert "not available" in block(Contact(id="c"), START, END, store)
    assert "not available" in block(Contact(id="c", bo_id="1", bo_brand="tombriches"), START, END, store)
    assert "not read" in block(Contact(id="c", bo_id="1", bo_brand="kingbilly"), START, END, store)
    store.put("kingbilly", _facts())
    assert "BACK OFFICE (the casino's own records; player 1745500" in block(
        Contact(id="c", bo_id="1745500", bo_brand="kingbilly"), START, END, store)
    store.close()


def test_the_bo_id_comes_from_cleanid_or_external_id():
    assert bo_id_from({"custom_attributes": {"cleanid": "1745500", "brand": "kingbilly"}}) == \
        ("1745500", "kingbilly")
    assert bo_id_from({"external_id": "c1321289", "custom_attributes": {"brandsmall": "KingBilly"}}) == \
        ("1321289", "kingbilly")
    assert bo_id_from({"external_id": "lead-x", "custom_attributes": {}}) == ("", "")


class FakeBO:
    def __init__(self):
        self.calls = []

    def player_page(self, pid):
        self.calls.append(("page", pid))
        return PLAYER

    def _get(self, path, headers, params=None):
        self.calls.append(("get", path))
        body = {"payments": PAYMENTS, "bonus_issues": BONUSES, "limits": LIMITS, "tags": TAGS,
                "duplicates_v2": DUPS}.get(path.split("/")[-1].split("?")[0], "<table></table>")
        return httpx.Response(200, text=body, request=httpx.Request("GET", "https://bo.test" + path))

    def close(self):
        pass


def test_fetch_reads_the_page_and_every_lazy_panel_with_xhr_headers():
    bo = FakeBO()
    f = fetch(bo, "1745500")
    assert f.status == "disable" and f.duplicates == 2 and not f.errors
    assert {p for kind, p in bo.calls if kind == "get"} == {
        "/backend/players/1745500/" + s for s in (
            "payments?order=created_at_desc", "bonus_issues", "freespin_issues", "limits",
            "tags", "duplicates_v2")}


def test_sync_skips_fresh_players_and_supported_brands_only(tmp_path):
    db = tmp_path / "s.db"
    convos = [Conversation(id=str(i), created_at=START, updated_at=None, state="closed",
                           contact=Contact(id=f"c{i}", bo_id=bid, bo_brand=brand))
              for i, (bid, brand) in enumerate([("1745500", "kingbilly"), ("1745500", "kingbilly"),
                                                ("9", "tombriches"), ("", "kingbilly")])]
    assert bo_sync.players_of(convos) == [("kingbilly", "1745500")]
    bo = FakeBO()
    s1 = bo_sync.sync(bo_sync.players_of(convos), client=bo, db_path=db)
    assert (s1["fetched"], s1["fresh"]) == (1, 0)
    s2 = bo_sync.sync(bo_sync.players_of(convos), client=bo, db_path=db)
    assert (s2["fetched"], s2["fresh"]) == (0, 1)
    s3 = bo_sync.sync(bo_sync.players_of(convos), client=bo, db_path=db, max_age_hours=0)
    assert s3["fetched"] == 1


def test_the_grader_puts_the_block_in_front_of_the_transcript(tmp_path, monkeypatch):
    from intercom_summary.qa import grader as grader_mod
    from intercom_summary.settings import settings

    db = tmp_path / "g.db"
    old_db = settings.db_path
    object.__setattr__(settings, "db_path", db)
    try:
        BoPlayersStore(db).put("kingbilly", _facts())
        convo = Conversation(id="7", created_at=START, updated_at=END, state="closed",
                             contact=Contact(id="c", bo_id="1745500", bo_brand="kingbilly"))
        text = grader_mod.back_office_block(convo)
        assert text.startswith("=== BACK OFFICE (the casino's own records")
        assert "(after the chat)" in text
    finally:
        object.__setattr__(settings, "db_path", old_db)
