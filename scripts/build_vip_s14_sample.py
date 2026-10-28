"""Build the VIP calibration sample the way VIP Scorecard v1.6 §14 prescribes, and freeze it.

§14: period 1–15 Oct 2026; dialogues are split into cases (section 1) and a case started in
the period is graded whole. The sample is every complex case of the period — complaints,
Payments, high-value, RG / closure, lost email, lost follow-ups, justified refusals met with
negativity, "a happy player despite an error" — plus as many randomly chosen routine cases of
the same period, so complex cases are at least half. 150–200 cases in all: under 150, the
period is extended; over 100 complex, a random 100 of them. Frozen and versioned.

What "complex" means here, before any grade exists (all free, no LLM):

- topic (the Haiku pre-label): Complaint → complaint; Withdrawal / Deposit → payments;
  Responsible gaming → rg-closure;
- the case's own text: a closure / self-exclusion / cool-off request → rg-closure; a lost or
  changed email → lost-email; an amount of 10 000+ → high-value; vip_cs, the Payments or
  Finance team → payments; a manager's refusal followed by the player's anger →
  refusal-negative;
- the Exception Report: a player's message no manager answered → lost-follow-up.

"A happy player despite an error" needs a grade to see the error, so it is not detected here.

Each case is stored as ONE member, its first thread (the calibration run maps a thread to
its case, service._calibrate_vip_cases); `category` is "complex:<reasons>" or "routine".

    .venv/bin/python scripts/build_vip_s14_sample.py --dry-run
    .venv/bin/python scripts/build_vip_s14_sample.py --db /path/to/copy.db
"""
from __future__ import annotations

import argparse
import random
import re
from datetime import date, timedelta

from intercom_summary import vip_cases
from intercom_summary.settings import settings
from intercom_summary.storage.calibration_store import CalibrationStore
from intercom_summary.storage.vip_store import VipStore

TOPIC_REASON = {"Complaint": "complaint", "Withdrawal": "payments", "Deposit": "payments",
                "Responsible gaming": "rg-closure"}
COMPLEX_CAP = 100
TOTAL_MIN, TOTAL_MAX = 150, 200

_CLOSURE = re.compile(r"\b(close|closing|closed|delete|deactivate|block)\b.{0,25}\baccount\b|"
                      r"self[- ]?exclu|cool[- ]?(off|down) period|take a break from", re.I)
_LOST_EMAIL = re.compile(r"\b(lost|no longer (have|use)|can'?t access|cannot access|"
                         r"change|changed|update)\b.{0,30}\be-?mail\b", re.I)
_PAYMENTS = re.compile(r"\bvip_cs\b|\b(payments?|finance) (team|department)\b", re.I)
# Thousands are separated by "," or "." only: a space would read "07 Oct, 2026 200 EUR" as 2 026 200.
_AMOUNT = re.compile(r"(?:€|eur|\$|usd|cad|aud|nzd|£)\s?(\d{1,3}(?:[,.]\d{3})+|\d{5,})"
                     r"|(\d{1,3}(?:[,.]\d{3})+|\d{5,})\s?(?:€|eur|usd|cad|aud|nzd)\b", re.I)
_REFUSAL = re.compile(r"\b(unfortunately|not (possible|eligible)|unable to|we cannot|"
                      r"we can'?t|i can'?t offer)\b", re.I)
_NEGATIVE = re.compile(r"\b(disappointed|unfair|ridiculous|angry|scam|worst|terrible|"
                       r"disgusting|joke|unacceptable|never again)\b", re.I)


def _amount_over(text: str, limit: int = 10_000) -> bool:
    for m in _AMOUNT.finditer(text):
        digits = re.sub(r"\D", "", m.group(1) or m.group(2) or "")
        # "10,000.00" → drop the cents before comparing.
        if re.search(r"[.,]\d{2}$", m.group(0)):
            digits = digits[:-2]
        if digits and int(digits) >= limit:
            return True
    return False


def reasons(case: vip_cases.Case, unanswered: set[str]) -> list[str]:
    """Why a case is complex; [] for a routine case."""
    out: list[str] = []
    if case.topic in TOPIC_REASON:
        out.append(TOPIC_REASON[case.topic])
    convo = vip_cases.case_conversation(case)
    player = " ".join(m.text or "" for m in convo.messages if m.author_type in vip_cases._PLAYER)
    staff = " ".join(m.text or "" for m in convo.messages if m.author_type == "admin")
    text = player + " " + staff
    if _CLOSURE.search(player):
        out.append("rg-closure")
    if _LOST_EMAIL.search(player):
        out.append("lost-email")
    if _amount_over(text):
        out.append("high-value")
    if _PAYMENTS.search(staff):
        out.append("payments")
    if _REFUSAL.search(staff) and _NEGATIVE.search(player):
        out.append("refusal-negative")
    if any(t["id"] in unanswered for t in case.threads):
        out.append("lost-follow-up")
    return sorted(set(out))


def in_period(case: vip_cases.Case, since: date, until: date) -> bool:
    started = (case.first_at or "")[:10]
    return since.isoformat() <= started <= until.isoformat()


def select(cases, unanswered, since: date, until: date, seed: int, data_until: date):
    """(complex picks, routine picks, until used, note) — extends `until` while under 150."""
    note = ""
    while True:
        period = [c for c in cases if in_period(c, since, until)]
        tagged = [(c, reasons(c, unanswered)) for c in period]
        complex_ = [(c, r) for c, r in tagged if r]
        routine = [(c, r) for c, r in tagged if not r]
        n_complex = min(len(complex_), COMPLEX_CAP)
        n_routine = min(len(routine), n_complex)
        if n_complex + n_routine >= TOTAL_MIN or until >= data_until:
            break
        until += timedelta(days=1)
        note = f"period extended to {until} to reach {TOTAL_MIN}"
    if n_complex + n_routine < TOTAL_MIN:
        note = (f"only {n_complex + n_routine} cases available up to {data_until}; "
                f"§14 asks for {TOTAL_MIN}–{TOTAL_MAX}")
    rng = random.Random(seed)
    key = lambda cr: cr[0].case_id  # noqa: E731 — a stable order before the seeded draw
    picks_c = rng.sample(sorted(complex_, key=key), n_complex)
    picks_r = rng.sample(sorted(routine, key=key), n_routine)
    return picks_c, picks_r, until, note, len(complex_), len(routine)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--since", default="2026-10-01")
    ap.add_argument("--until", default="2026-10-15")
    ap.add_argument("--seed", type=int, default=20261028)
    ap.add_argument("--sample-id", default="vip-v16-s14-v1")
    ap.add_argument("--db", help="database path (default: DB_PATH)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    db = args.db or settings.db_path
    vs = VipStore(db)
    try:
        rows = vs.kept_with_payload()
    finally:
        vs.close()
    cases = vip_cases.group_cases(rows)
    unanswered = {e["thread_id"] for e in vip_cases.exceptions(db).get("unanswered", [])}
    data_until = max(date.fromisoformat((c.first_at or "1970-01-01")[:10]) for c in cases)

    picks_c, picks_r, until, note, pool_c, pool_r = select(
        cases, unanswered, date.fromisoformat(args.since), date.fromisoformat(args.until),
        args.seed, data_until)

    from collections import Counter
    why = Counter(r for _c, rs in picks_c for r in rs)
    print(f"{db}: {len(cases)} VIP case(s) in all; period {args.since}..{until} "
          f"(data up to {data_until}).")
    print(f"Pool: {pool_c} complex, {pool_r} routine → picked {len(picks_c)} complex + "
          f"{len(picks_r)} routine = {len(picks_c) + len(picks_r)} (seed {args.seed}).")
    print(f"Complex reasons among the picks: {dict(why.most_common())}")
    if note:
        print(f"Note: {note}")
    if args.dry_run:
        return

    items = []
    ordered = sorted([*picks_c, *picks_r], key=lambda cr: cr[0].first_at or "")
    for i, (case, rs) in enumerate(ordered, start=1):
        items.append({
            "conversation_id": case.threads[0]["id"], "seq": i,
            "chat_date": (case.first_at or "")[:10], "agent_name": case.primary_manager,
            "category": ("complex:" + "+".join(rs)) if rs else "routine",
        })
    cal = CalibrationStore(db)
    try:
        cal.create(args.sample_id,
                   f"VIP Scorecard v1.6 §14 — {len(picks_c)} complex + {len(picks_r)} routine cases",
                   source=(f"scripts/build_vip_s14_sample.py seed={args.seed} "
                           f"period={args.since}..{until}" + (f" ({note})" if note else "")),
                   created_by="script", default_ruleset="vip-v16")
        n = cal.replace_items(args.sample_id, items)
    finally:
        cal.close()
    print(f"sample {args.sample_id}: {n} case(s) stored (frozen), one thread per case.")


if __name__ == "__main__":
    main()
