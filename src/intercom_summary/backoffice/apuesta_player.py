"""A Tomb Riches player's back-office facts, read from Apuesta Cloud (backoffice/apuesta.py).

Produces the same PlayerFacts as backoffice/player.py does for King Billy, so the cache, the
sync and the BACK OFFICE block need no brand logic beyond picking the client. Field numbers
below come from the console's generated protobuf code (explored 2026-11-09).

What maps to what:
- status: Customer.Status (new / active / verified / blocked); a block's reason code, comment
  and dates become a lock row; a cooling-off end time becomes an RG-limit row.
- limits: the wallets' deposit / withdrawal limits (self-set or system), with amount and use.
- payments: deposits (ListCustomerPayments) and withdrawal orders (with their approval state —
  awaiting approval, rejected and why), newest first, 25 of each.
- comments: the staff notes on the customer.
- bonuses: ListCustomerGameBonuses (GAME_BONUS_VIEW) with the template's name, the free-spin
  count and "given by staff" / "automatic (campaign)"; without the permission the list is
  recorded as not read, never as empty. No separate free-spin list, no duplicate count.
- history: the customer timeline (CUSTOMER_TIMELINE_VIEW) — dated status changes (block,
  unblock, verification, by whom), limit / cooling-off changes and manual balance adjustments.
  It starts on 2026-11-01 (rows before 13:00 UTC that day are the replayed initial state and
  are skipped); the block says so, so an older chat is not read as "never blocked".

Only what a QA verdict needs is kept — no names, addresses, documents, IPs or contact details.
"""
from __future__ import annotations

from datetime import datetime, timezone

from intercom_summary.backoffice.apuesta import (
    ApuestaClient, pb_bytes, pb_int, pb_msg, pb_msgs, pb_str, pb_strs, pb_time, pb_varint)
from intercom_summary.backoffice.client import NotAuthorized
from intercom_summary.backoffice.player import PlayerFacts

S = "userservice.bo.v1."
CUSTOMER_STATUS = {1: "new", 2: "active", 3: "verified", 4: "blocked"}
BLOCK_REASON = {1: "general", 2: "fraud", 3: "multi-account", 4: "self-exclusion"}
PAYMENT_STATUS = {1: "", 2: "Yes", 3: "No"}            # "" = pending (context.py's convention)
WITHDRAWAL_STATUS = {1: "pending", 2: "awaiting approval", 3: "Yes", 4: "failed",
                     5: "failed attempt", 6: "REJECTED", 7: "canceled"}
REJECT_REASON = {1: "daily limit", 2: "wagering conditions", 3: "account verification required",
                 4: "additional verification required", 5: "additional information required",
                 6: "incorrect payment details", 7: "AML verification required",
                 8: "weekly limit", 9: "monthly limit", 10: "partially approved", 100: "other"}
LIMIT_TYPE = {1: "self-set", 2: "system"}
LIMIT_PERIOD = {1: "permanent", 2: "per day", 3: "per 7 days", 4: "per 30 days"}
BONUS_STATUS = {1: "offered, not accepted", 2: "active", 3: "done", 4: "offer timed out",
                5: "rejected", 6: "timed out", 7: "canceled"}
PAGE = 25

_level_names: dict[str, dict[str, str]] = {}            # region → level id → name, per process


def _money(cents: int, currency: str) -> str:
    return f"{cents / 100:,.2f} {currency}".strip()


def _sv(value: str) -> bytes:                            # google.protobuf.StringValue
    return pb_bytes(1, value)


def _list_req(filter_: bytes, size: int = PAGE) -> bytes:
    """Filter, sorting (create time DESC), first page — the shape of the console's list calls."""
    return pb_bytes(1, filter_) + pb_bytes(2, pb_varint(1, 2)) + pb_bytes(3, pb_varint(1, size))


def _customer(bo: ApuestaClient, cid: str, f: PlayerFacts) -> str:
    """Status, block, cooling off, tags. Returns the operating region (for level names)."""
    c = bo.call(S + "CustomerService/GetCustomer", pb_bytes(1, cid))
    status = pb_int(c, 4)
    f.status = CUSTOMER_STATUS.get(status, str(status))
    detail = []
    if pb_str(c, 16):
        detail.append(pb_str(c, 16)[:120])
    if status != 4 and pb_int(c, 19) == 4:
        detail.append("previously blocked")
    f.status_detail = "; ".join(detail)
    if status == 4 or pb_int(c, 17):
        f.locks.append({"kind": "account block",
                        "reason": BLOCK_REASON.get(pb_int(c, 17), "unspecified"),
                        "comment": pb_str(c, 16)[:200],
                        "created_at": pb_time(c, 20),
                        "until": pb_time(c, 18)})
    if pb_time(c, 27):
        until = pb_time(c, 27)
        active = until > datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        f.limits.append({"type": "cooling off", "status": "active" if active else "ended",
                         "period": "", "duration": "", "created_at": "", "modified_at": "",
                         "disable_at": until})
    f.tags = pb_strs(c, 12)
    return pb_str(c, 23)


def _limits(bo: ApuestaClient, cid: str, f: PlayerFacts) -> None:
    wallets = bo.call("billing.bo.v1.WalletService/ListCustomerWallets",
                      pb_bytes(1, pb_bytes(2, cid)) + pb_bytes(2, pb_varint(1, 100)))
    for w in pb_msgs(wallets, 1):
        cur = pb_str(w, 3)
        lim = bo.call("paymentgateway.bo.v1.PaymentLimitService/GetCustomerLimits",
                      pb_bytes(1, cid) + pb_bytes(2, pb_str(w, 1)))
        for kind, num in (("deposit", 3), ("withdrawal", 6)):
            for x in pb_msgs(lim, num):
                f.limits.append({
                    "type": f"{kind} limit ({LIMIT_TYPE.get(pb_int(x, 1), 'unspecified')})",
                    "period": LIMIT_PERIOD.get(pb_int(x, 2), ""),
                    "status": f"{_money(pb_int(x, 3), cur)}, used {_money(pb_int(x, 4), cur)}",
                    "duration": "", "created_at": pb_time(x, 6) or pb_time(lim, 4),
                    "modified_at": pb_time(x, 6), "disable_at": ""})


def _directions(bo: ApuestaClient, ids: set[str]) -> dict[str, str]:
    ids = {i for i in ids if i}
    if not ids:
        return {}
    msg = b"".join(pb_bytes(1, i) for i in sorted(ids))
    r = bo.call("paymentgateway.bo.v1.PaymentRoutingService/ListDirectionsBrief", msg)
    return {pb_str(d, 1): pb_str(d, 2) for d in pb_msgs(r, 1)}


def _payments(bo: ApuestaClient, cid: str, f: PlayerFacts) -> None:
    flt = pb_bytes(2, _sv(cid))
    pays = pb_msgs(bo.call("paymentgateway.bo.v1.PaymentService/ListCustomerPayments",
                           _list_req(flt)), 1)
    orders = pb_msgs(bo.call("paymentgateway.bo.v1.PaymentService/ListCustomerWithdrawalOrders",
                             _list_req(flt)), 1)
    deposits = [p for p in pays if pb_int(p, 3) == 1]
    # A withdrawal order is the record people work with (approval, rejection); the payment
    # rows of type withdrawal only execute an order, so they are used only when there is none.
    payouts = [p for p in pays if pb_int(p, 3) == 2] if not orders else []
    names = _directions(bo, {pb_str(p, 22) for p in pays} | {pb_str(o, 13) for o in orders})
    rows = []
    for p in deposits + payouts:
        done = pb_int(p, 11) in (2, 3)
        rows.append({"action": "deposit" if pb_int(p, 3) == 1 else "withdrawal",
                     "amount": _money(pb_int(p, 7), pb_str(p, 8)),
                     "source": names.get(pb_str(p, 22), ""),
                     "success": PAYMENT_STATUS.get(pb_int(p, 11), ""),
                     "approval": "", "manual": "", "return_type": "",
                     "comments": pb_str(p, 16) if pb_int(p, 11) == 3 else "",
                     "created_at": pb_time(p, 12),
                     "finished_at": pb_time(p, 13) if done else ""})
    for o in orders:
        status = pb_int(o, 6)
        state = WITHDRAWAL_STATUS.get(status, str(status))
        why = REJECT_REASON.get(pb_int(o, 19), "") or pb_str(o, 10)
        if why and status in (4, 5, 6, 7):
            state = f"{state} ({why})"
        rows.append({"action": "withdrawal", "amount": _money(pb_int(o, 4), pb_str(o, 5)),
                     "source": names.get(pb_str(o, 13), ""), "success": state,
                     "approval": "reviewed by staff" if pb_str(o, 20) else "",
                     "manual": "", "return_type": "", "comments": pb_str(o, 10)[:120],
                     "created_at": pb_time(o, 7),
                     "finished_at": pb_time(o, 8) if status in (3, 4, 6, 7) else ""})
    f.payments = sorted(rows, key=lambda r: r["created_at"], reverse=True)


def _notes(bo: ApuestaClient, cid: str, f: PlayerFacts) -> None:
    r = bo.call(S + "CustomerNoteService/ListNotes", _list_req(pb_bytes(3, _sv(cid)), 20))
    f.comments = [{"created_at": pb_time(n, 5), "text": " ".join(pb_str(n, 4).split())[:400]}
                  for n in pb_msgs(r, 1) if pb_str(n, 4).strip()]


def _level(bo: ApuestaClient, cid: str, region: str, f: PlayerFacts) -> None:
    lvl = bo.call("gamification.bo.v1.LoyaltyProgramService/GetCustomerLoyaltyLevel",
                  pb_bytes(1, cid))
    level_id = pb_str(lvl, 3)
    if region and region not in _level_names:
        prog = bo.call("gamification.bo.v1.LoyaltyProgramService/GetLoyaltyProgram",
                       pb_bytes(1, region))
        _level_names[region] = {pb_str(c, 1): pb_str(c, 2) for c in pb_msgs(prog, 4)}
    f.level = _level_names.get(region, {}).get(level_id, level_id)


def _bonuses(bo: ApuestaClient, cid: str, f: PlayerFacts) -> None:
    """Issued bonuses with the template's name, free-spin count, and who gave them: a bonus
    with a creator was given by a staff member, one without came from a campaign rule."""
    r = bo.call("bonusservice.bo.v1.GameBonusService/ListCustomerGameBonuses",
                _list_req(pb_bytes(2, _sv(cid))))
    rows = pb_msgs(r, 1)
    names: dict[str, str] = {}
    ids = sorted({pb_str(b, 3) for b in rows if pb_str(b, 3)})
    if ids:
        try:
            t = bo.call("bonusservice.bo.v1.GameBonusTemplateService/ListGameBonusTemplatesBrief",
                        b"".join(pb_bytes(1, i) for i in ids))
            names = {pb_str(x, 1): pb_str(x, 2) for x in pb_msgs(t, 1)}
        except Exception:  # noqa: BLE001 — a name is a nicety; the rows stand without it
            pass
    f.bonuses = []
    for b in rows:
        spins = sum(pb_int(c, 2) for c in pb_msgs(pb_msg(b, 14), 1))
        title = names.get(pb_str(b, 3)) or "bonus"
        stage = BONUS_STATUS.get(pb_int(b, 5), str(pb_int(b, 5)))
        stage += ", given by staff" if pb_str(b, 15) else ", automatic (campaign)"
        f.bonuses.append({
            "title": (title + (f" + {spins} FS" if spins else ""))[:40],
            "issued_at": pb_time(b, 6), "stage": stage,
            "amount": _money(pb_int(b, 9), pb_str(b, 10)) if pb_int(b, 9) else "",
            "strategy": "", "wager": "", "expiry": pb_time(b, 8)})


# Timeline (audit trail) events worth a dated row, and how many pages of 200 to read at most.
# Apuesta started keeping the timeline on 2026-11-01 around 12:00 UTC (no record anywhere is
# older), and the first hour holds the existing state replayed as events — several status
# changes of one player in the same second, for checks done days earlier. Rows before
# TIMELINE_FROM are therefore not used.
TIMELINE_FROM = "2026-11-01 13:00:00"
TIMELINE_PAGES = 3
_ACTOR = {0: "system", 1: "the player", 2: "staff", 3: "an administrator", 5: "an API key"}


def _timeline(bo: ApuestaClient, cid: str, f: PlayerFacts) -> None:
    """Status changes (block / unblock / verification) and limit, cooling-off and manual
    balance changes, each dated — what the customer record alone cannot show."""
    records = []
    for page in range(TIMELINE_PAGES):                  # pages are 0-based
        r = bo.call("audittrail.bo.v1.ActivityService/ListCustomerTimeline",
                    pb_bytes(1, cid) + pb_bytes(3, pb_varint(1, 200) + pb_varint(2, page)))
        batch = pb_msgs(r, 1)
        records += batch
        if len(batch) < 200:
            break
    f.history_note = (f"Status history (dated rows below) is kept only since "
                      f"{TIMELINE_FROM[:16]} UTC: no status row before that means unknown, "
                      f"not none.")
    status_rows, other = [], []
    for x in records:
        kind = pb_str(x, 3).removeprefix("customer.")
        kv = {pb_str(p, 1): pb_str(p, 2) for p in pb_msgs(x, 9)}
        when, by = pb_time(x, 12), _ACTOR.get(pb_int(x, 7), "")
        if when < TIMELINE_FROM:
            continue
        if kind == "status_changed":
            move = f"{kv.get('from', '?').lower()} -> {kv.get('to', '?').lower()}"
            block = BLOCK_REASON.get({"GENERAL": 1, "FRAUD": 2, "MULTIACCOUNT": 3,
                                      "SELF_EXCLUSION": 4}.get(kv.get("block_reason", ""), 0), "")
            status_rows.append({"kind": "status change", "reason": move + (f" ({block})" if block else ""),
                                "comment": kv.get("reason", "")[:200], "created_at": when, "by": by})
        elif kind == "payment_limit_changed":
            other.append({"type": "limit change", "period": "",
                          "status": ", ".join(f"{k} {v}" for k, v in kv.items() if v)[:160],
                          "duration": "", "created_at": when, "modified_at": "", "disable_at": ""})
        elif kind == "updated":
            for ch in pb_msgs(x, 10):
                path = pb_str(ch, 1)
                if "cooling" in path:                   # block dates: status_changed
                    other.append({"type": f"{path.replace('_', ' ')} changed", "period": "",
                                  "status": f"{pb_str(ch, 2) or '-'} -> {pb_str(ch, 3) or '-'}",
                                  "duration": "", "created_at": when, "modified_at": "",
                                  "disable_at": ""})
        elif kind == "balance_adjusted":
            f.payments.append({"action": "manual balance adjustment", "source": f"by {by}",
                               "amount": f"{kv.get('amount', '')} {kv.get('currency', '')}".strip(),
                               "success": "Yes", "approval": "", "manual": "yes",
                               "return_type": "", "comments": kv.get("reason", "")[:120],
                               "created_at": when, "finished_at": ""})
    if any("-> blocked" in r["reason"] for r in status_rows):
        f.locks = [l for l in f.locks if l.get("kind") != "account block"]  # the dated row says it
    f.locks = status_rows + f.locks
    f.limits += other
    f.payments.sort(key=lambda r: r["created_at"], reverse=True)


def fetch(bo: ApuestaClient, customer_id: str) -> PlayerFacts:
    """Every list for one customer. A list that fails is recorded in `errors`, not raised —
    except the customer itself: without it there is no record to keep."""
    cid = str(customer_id).strip()
    f = PlayerFacts(player_id=cid, unavailable=["freespins"],
                    fetched_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    region = _customer(bo, cid, f)
    for name, step in (("limits", lambda: _limits(bo, cid, f)),
                       ("payments", lambda: _payments(bo, cid, f)),
                       ("comments", lambda: _notes(bo, cid, f)),
                       ("level", lambda: _level(bo, cid, region, f)),
                       ("bonuses", lambda: _bonuses(bo, cid, f)),
                       ("history", lambda: _timeline(bo, cid, f))):
        try:
            step()
        except NotAuthorized:
            f.errors.append(f"{name}: the BO account has no permission to view them")
        except Exception as exc:  # noqa: BLE001 — one list must not cost the others
            f.errors.append(f"{name}: {type(exc).__name__}: {str(exc)[:120]}")
    return f
