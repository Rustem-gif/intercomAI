"""The VIP chats tab: collect the Intercom "VIP managers" team's conversations and keep only
the real dialogues.

Where they come from. VIP players are looked after by a dedicated team of VIP managers (Alisa,
Jasmine, Eric, Taylor, Victoria, Walter, Sam), none of whom is a tracked support agent. Their
work lives in two Intercom teams, found with `GET /teams` on 2026-10-16:
`VIP managers` (50006396) and `pre-VIP` (50006577). The teams "VIP Players Chats" (50098297)
and "(DE)" (50101650) hold no conversations — they are routing labels on the support agents.

Every one of these threads is an **email**, so they are stored in `vip_conversations`, never in
`conversations` (chats-only), and none of them is graded.

Filtering is two-stage, because a large share of the inbox is not a conversation at all:
outbound offers nobody answered, a request answered only with an internal note, "thanks"-only
replies, and system mail (Plane / Jira work-item notifications) sent to the shared address.

1. **Rules** (deterministic, free): the player wrote something, it adds up to a few real words
   beyond pleasantries, and a manager answered publicly.
2. **Meaning** (Claude Haiku 5.5, structured JSON): of what is left, is it a genuine
   player ↔ VIP-manager exchange with substance? This also labels the topic.

Every fetched thread is stored, the dropped ones with the stage and reason, so the tab can show
what was filtered out and why.
"""
from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timedelta, timezone

from intercom_summary.intercom.client import IntercomClient
from intercom_summary.intercom.fetch import is_ticket, normalise_conversation
from intercom_summary.intercom.models import Admin, Conversation
from intercom_summary.logging_setup import get_logger
from intercom_summary.qa.pricing import UsageMeter
from intercom_summary.settings import settings

log = get_logger(__name__)

VIP_TEAM_IDS: tuple[int, ...] = (50006396, 50006577)   # "VIP managers", "pre-VIP"

CLASSIFIER_MODEL = "claude-haiku-5-5"
_CONCURRENCY = 8

# A player's text has to carry at least this many words beyond pleasantries to count as saying
# something. "Thanks!", "ok", "Yes please 🙏" do not; "Do you have any VIP spins for me?" does.
MIN_MEANINGFUL_WORDS = 3
_ACK_WORDS = frozenset("""
ok okay okey k kk yes yeah yep yup no nope sure fine great good cool nice perfect awesome
thanks thank thx ty tysm cheers you u much so very lot a the and hi hello hey dear regards
kind best wishes please pls plz mate love bye goodbye
""".split())
_WORD = re.compile(r"[^\W\d_]{2,}", re.UNICODE)
# Where a quoted earlier message starts in an email reply — the rest is not the player's text.
_QUOTE_START = re.compile(
    r"^\s*(>|On .{4,120} wrote:|Am .{4,120} schrieb|-{2,}\s*Original Message|From:\s)",
    re.MULTILINE | re.IGNORECASE,
)

_CUSTOMER_TYPES = frozenset({"user", "lead", "contact"})
# One player email often lands as several Intercom conversations. The managers answer in one of
# them and leave a note on each copy holding the id of the thread they answered in.
_CONVERSATION_ID = re.compile(r"\b\d{14,16}\b")

REASON_DUPLICATE = "duplicate thread"
REASON_NO_PLAYER = "no player message"
REASON_ONE_WORD = "one-word reply"
REASON_NOTES_ONLY = "only internal notes"
REASON_NO_REPLY = "no manager reply"

TOPICS = [
    "Bonus / free spins request", "VIP offer follow-up", "Withdrawal", "Deposit",
    "Verification (KYC)", "Responsible gaming", "Account", "Complaint", "Other",
]

_SYSTEM = """You review email threads from an online casino's VIP-manager inbox. Each thread is
between a VIP player and their personal VIP manager. Decide whether the thread is a GENUINE
CONVERSATION worth reviewing.

Keep it (keep=true) when a real player and a VIP manager actually exchange messages with
substance: the player asks, requests, complains, explains or responds to an offer in their own
words, and the manager answers them.

Drop it (keep=false) when any of these is true:
- the "player" is not a person: automated or system mail (work-item / ticket notifications such
  as Plane or Jira, delivery failures, no-reply senders, newsletters, auto-replies, out-of-office);
- spam or marketing that a person did not write;
- the only human input is a token reaction (thanks, ok, an emoji, a signature) with nothing to
  answer;
- the manager never actually replies to the player (only internal notes or automated messages).

Lines tagged "(note)" are internal notes the player never saw. BOT lines are automation.

Return JSON: keep, reason (one short sentence in English, saying why), and topic (the main
subject of the thread)."""

_SCHEMA = {
    "type": "object",
    "properties": {
        "keep": {"type": "boolean"},
        "reason": {"type": "string"},
        "topic": {"type": "string", "enum": TOPICS},
    },
    "required": ["keep", "reason", "topic"],
    "additionalProperties": False,
}


# ── Stage 1: rules ───────────────────────────────────────────────────────────────────────────
def _own_text(text: str) -> str:
    """The player's own words in an email body, without the quoted earlier messages."""
    m = _QUOTE_START.search(text or "")
    return (text[: m.start()] if m else text or "").strip()


def meaningful_words(text: str) -> int:
    return sum(1 for w in _WORD.findall(_own_text(text).lower()) if w not in _ACK_WORDS)


def counts(convo: Conversation) -> dict[str, int]:
    customer = [m for m in convo.messages
                if m.author_type in _CUSTOMER_TYPES and (m.text or "").strip()]
    admin = [m for m in convo.messages if m.author_type == "admin" and (m.text or "").strip()]
    public = [m for m in admin if m.part_type != "note"]
    return {
        "customer_msgs": len(customer),
        "agent_replies": len(public),
        "agent_notes": len(admin) - len(public),
        "points_elsewhere": int(any(m.part_type == "note" and _CONVERSATION_ID.search(m.text)
                                    for m in admin)),
        "customer_words": sum(meaningful_words(m.text) for m in customer),
    }


def rule_verdict(convo: Conversation) -> str | None:
    """The stage-1 reason to drop a thread, or None when it passes."""
    c = counts(convo)
    if not c["agent_replies"] and c["points_elsewhere"]:
        return REASON_DUPLICATE
    if not c["customer_msgs"]:
        return REASON_NO_PLAYER
    if c["customer_words"] < MIN_MEANINGFUL_WORDS:
        return REASON_ONE_WORD
    if not c["agent_replies"]:
        return REASON_NOTES_ONLY if c["agent_notes"] else REASON_NO_REPLY
    return None


# ── Stage 2: meaning ─────────────────────────────────────────────────────────────────────────
async def _classify(client, convo: Conversation, meter: UsageMeter) -> dict:
    """{keep, reason, topic} from Claude, or keep=True with an explanatory reason when the
    check could not run — a thread that passed the rules is not dropped on a technical error."""
    header = f"Subject: {convo.subject or '—'}\n"
    try:
        resp = await client.beta.messages.create(
            model=CLASSIFIER_MODEL,
            max_tokens=4000,
            system=_SYSTEM,
            messages=[{"role": "user", "content": header + convo.transcript_text()}],
            output_config={"effort": "low",
                           "format": {"type": "json_schema", "schema": _SCHEMA}},
        )
    except Exception as exc:  # noqa: BLE001 - one failed call must not stop the run
        log.warning("VIP classify %s failed: %s", convo.id, exc)
        return {"keep": True, "reason": "AI check failed — kept on the rules", "topic": "Other"}
    meter.add(resp.usage, CLASSIFIER_MODEL)
    if resp.stop_reason == "refusal":
        return {"keep": True, "reason": "AI check declined — kept on the rules", "topic": "Other"}
    text = "".join(getattr(b, "text", "") for b in resp.content if getattr(b, "type", "") == "text")
    try:
        out = json.loads(text)
        return {"keep": bool(out["keep"]), "reason": str(out["reason"]).strip(),
                "topic": out["topic"] if out.get("topic") in TOPICS else "Other"}
    except (ValueError, KeyError, TypeError):
        return {"keep": True, "reason": "AI answer unreadable — kept on the rules", "topic": "Other"}


# ── Collect ──────────────────────────────────────────────────────────────────────────────────
def _window(since: str, until: str) -> tuple[int, int]:
    """Unix bounds for an inclusive UTC date range: [since 00:00, until+1 00:00)."""
    start = datetime.fromisoformat(since).replace(tzinfo=timezone.utc)
    end = datetime.fromisoformat(until).replace(tzinfo=timezone.utc) + timedelta(days=1)
    return int(start.timestamp()), int(end.timestamp())


def _row(convo: Conversation, delivered_as: str, c: dict, verdict: dict) -> dict:
    return {
        "id": convo.id,
        "agent_name": convo.assignee.name if convo.assignee else "",
        "agent_email": convo.assignee.email if convo.assignee else "",
        "customer_name": convo.contact.name,
        "customer_email": convo.contact.email,
        "brand": convo.brand,
        "subject": convo.subject,
        "created_at": convo.created_at.isoformat() if convo.created_at else None,
        "delivered_as": delivered_as,
        "message_count": len(convo.messages),
        "customer_msgs": c["customer_msgs"],
        "agent_replies": c["agent_replies"],
        **verdict,
        "payload": convo.to_dict(),
    }


def vip_member_ids(admins: list[dict]) -> list[int]:
    """Intercom admin ids of everyone in the VIP teams (the managers)."""
    teams = {str(t) for t in VIP_TEAM_IDS}
    return sorted(int(a["id"]) for a in admins
                  if a.get("id") and teams & {str(t) for t in a.get("team_ids") or []})


def collect_query(lo: int, hi: int, member_ids: list[int]) -> dict:
    """The Intercom search: created in the window, and assigned to a VIP team or to a VIP
    manager personally (an email answered from the manager's own inbox carries no team)."""
    assigned = [{"field": "team_assignee_id", "operator": "=", "value": t} for t in VIP_TEAM_IDS]
    assigned += [{"field": "admin_assignee_id", "operator": "=", "value": i} for i in member_ids]
    return {"operator": "AND", "value": [
        {"field": "created_at", "operator": ">", "value": lo - 1},
        {"field": "created_at", "operator": "<", "value": hi},
        {"operator": "OR", "value": assigned},
    ]}


async def collect(since: str, until: str, reclassify: bool = False) -> dict:
    """Fetch the threads created in [since, until] (inclusive, UTC) that are assigned to a VIP
    team or to a VIP manager, filter, store."""
    from anthropic import AsyncAnthropic

    from intercom_summary.storage.vip_store import VipStore

    lo, hi = _window(since, until)

    store = VipStore()
    known = {} if reclassify else store.classified()
    meter = UsageMeter()
    stats = {"matched": 0, "tickets": 0, "fetched": 0, "kept": 0, "excluded_rule": {},
             "excluded_ai": 0, "ai_checked": 0, "reused": 0}
    try:
        async with IntercomClient() as ic:
            raw_admins = await ic.list_admins()
            admins = {str(a["id"]): Admin(id=str(a["id"]), name=a.get("name", ""),
                                          email=a.get("email", ""))
                      for a in raw_admins}
            # A thread assigned to the VIP team, OR straight to a VIP manager with no team:
            # in Oct 2026, 617 of the 1,508 emails assigned to a manager had no team at all
            # (outbound offers and follow-ups mostly), so a team-only search missed them.
            members = vip_member_ids(raw_admins)
            query = collect_query(lo, hi, members)
            stubs = []
            async for s in ic.search_conversations(query):
                stats["matched"] += 1
                if is_ticket(s):
                    stats["tickets"] += 1
                    continue
                stubs.append(s)
            log.info("VIP: %d thread(s) in %s..%s, fetching full threads…", len(stubs), since, until)

            sem = asyncio.Semaphore(10)

            async def fetch(stub: dict):
                async with sem:
                    raw = await ic.get_conversation(str(stub["id"]))
                delivered = (raw.get("source") or {}).get("delivered_as") or ""
                return normalise_conversation(raw, known_admins=admins), delivered

            threads = await asyncio.gather(*[fetch(s) for s in stubs])
        stats["fetched"] = len(threads)

        ai = AsyncAnthropic(api_key=settings.anthropic_api_key, timeout=120.0, max_retries=4)
        ai_sem = asyncio.Semaphore(_CONCURRENCY)

        async def decide(convo: Conversation, delivered: str) -> None:
            c = counts(convo)
            if (reason := rule_verdict(convo)) is not None:
                verdict = {"keep": 0, "filter_stage": "rule", "reason": reason, "topic": ""}
                stats["excluded_rule"][reason] = stats["excluded_rule"].get(reason, 0) + 1
            elif (prev := known.get(convo.id)) and prev["filter_stage"] == "ai":
                verdict = {k: prev[k] for k in ("keep", "filter_stage", "reason", "topic")}
                stats["reused"] += 1
            else:
                async with ai_sem:
                    out = await _classify(ai, convo, meter)
                stats["ai_checked"] += 1
                verdict = {"keep": int(out["keep"]), "filter_stage": "ai",
                           "reason": out["reason"], "topic": out["topic"]}
            if verdict["keep"]:
                stats["kept"] += 1
            elif verdict["filter_stage"] == "ai":
                stats["excluded_ai"] += 1
            store.save(_row(convo, delivered, c, verdict))

        await asyncio.gather(*[decide(cv, d) for cv, d in threads])
    finally:
        store.close()
    stats["cost_usd"] = round(meter.cost, 4)
    return stats
