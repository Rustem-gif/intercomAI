"""scripts/build_vip_s14_sample.py — the §14 VIP calibration sample, and the segment line in
the VIP case header."""
from __future__ import annotations

import importlib.util
from datetime import date
from pathlib import Path

from intercom_summary import vip_cases
from intercom_summary.qa.vip_transcript import case_block

from tests.vip_case_fixtures import msg, pair, thread, vip_env  # noqa: F401 — vip_env is a fixture

_spec = importlib.util.spec_from_file_location(
    "build_vip_s14_sample",
    Path(__file__).resolve().parents[1] / "scripts" / "build_vip_s14_sample.py")
s14 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(s14)


def _cases(rows):
    return vip_cases.group_cases(rows)


def test_complex_reasons_come_from_topic_text_and_the_exception_report():
    rows = [
        pair("1", 0, contact="a", topic="Complaint"),
        thread("2", 30, [msg(0, "user", "Pat Player", 30, "Please close my account, I'm done."),
                         msg(1, "admin", "Vera VIP", 31, "Of course, Pat.")],
               contact="b", topic="Account"),
        thread("3", 60, [msg(0, "user", "Pat Player", 60, "I won $240,000 last night!"),
                         msg(1, "admin", "Vera VIP", 61, "Congratulations!")],
               contact="c", topic="Other"),
        thread("4", 90, [msg(0, "user", "Pat Player", 90, "Proof of the deposit (07 Oct, 2026 200 EUR)"),
                         msg(1, "admin", "Vera VIP", 91, "Thanks, received.")],
               contact="d", topic="Other"),
        thread("5", 120, [msg(0, "user", "Pat Player", 120, "Any free spins for me?"),
                          msg(1, "admin", "Vera VIP", 121, "Unfortunately not this week."),
                          msg(2, "user", "Pat Player", 122, "That is ridiculous and unfair.")],
               contact="e", topic="Bonus / free spins request"),
    ]
    by_id = {c.case_id: c for c in _cases(rows)}
    assert s14.reasons(by_id["vc-1"], set()) == ["complaint"]
    assert s14.reasons(by_id["vc-2"], set()) == ["rg-closure"]
    assert s14.reasons(by_id["vc-3"], set()) == ["high-value"]
    assert s14.reasons(by_id["vc-4"], set()) == []            # a date is not an amount
    assert s14.reasons(by_id["vc-5"], set()) == ["refusal-negative"]
    assert s14.reasons(by_id["vc-4"], {"4"}) == ["lost-follow-up"]


def test_selection_is_half_complex_capped_and_reproducible():
    rows = [pair(str(i), i, contact=f"p{i}", topic="Complaint" if i < 130 else "Other")
            for i in range(400)]
    cases = _cases(rows)
    picks_c, picks_r, until, note, pool_c, pool_r = s14.select(
        cases, set(), date(2026, 10, 1), date(2026, 10, 31), 7, date(2026, 10, 31))
    assert (pool_c, pool_r) == (130, 270)
    assert (len(picks_c), len(picks_r)) == (100, 100)        # complex capped at 100, equal routine
    again = s14.select(cases, set(), date(2026, 10, 1), date(2026, 10, 31), 7, date(2026, 10, 31))
    assert [c.case_id for c, _ in again[0]] == [c.case_id for c, _ in picks_c]
    assert note == ""


def test_a_short_period_is_extended_and_a_shortfall_reported():
    # 1 complex + 1 routine case a day from 1 Oct: 150 cases need 75 days, data has 20.
    rows = []
    for day in range(20):
        rows.append(pair(f"c{day}", day * 24, contact=f"c{day}", topic="Complaint"))
        rows.append(pair(f"r{day}", day * 24 + 1, contact=f"r{day}", topic="Other"))
    cases = _cases(rows)
    picks_c, picks_r, until, note, *_ = s14.select(
        cases, set(), date(2026, 10, 1), date(2026, 10, 5), 1, date(2026, 10, 20))
    assert until == date(2026, 10, 20) and len(picks_c) == len(picks_r) == 20
    assert "§14 asks for 150" in note


def test_the_vip_case_header_carries_the_player_segment(vip_env):
    row = pair("1", 0)
    row["payload"]["contact"].update(segment="VIP", loyalty_level="marquess")
    case = _cases([row])[0]
    head = case_block(vip_cases.case_conversation(case, {"vera vip"}))
    assert "Player segment: VIP (loyalty level: marquess)" in head
    assert "Player segment: unknown" in case_block(
        vip_cases.case_conversation(_cases([pair("2", 0)])[0], {"vera vip"}))
