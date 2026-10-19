"""Label a chat for the history index: a structured, English digest of what happened.

One Claude Haiku call per chat (about $0.0002 in a batch), structured output against
LABEL_SCHEMA. The label is what the grader's historical context is built from
(qa/history_context.py): the player's earlier chats are shown through their labels, and
similar chats are found by searching the labels' text. It is also the cheap screener the
effort router reads (qa/effort_router.py).

The label records what is observable — requests, actions, promises, how it ended — and keeps
the agent's mistakes conservative. It is an AI opinion and is stored as one: human verdicts
live in `case_verdicts`, and only those are shown to the grader as precedents.

    label = label_one(client, conversation)              # live, one chat
    stats = await label_live(client, conversations, store, source="cache")
    stats = label_batch(client, conversations, store, source="archive")

`LABEL_VERSION` hashes the prompt and the schema: a change re-labels everything on the next
refresh, so every row in the index was made the same way.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import threading
from collections.abc import Callable

from intercom_summary.intercom.brands import brand_label
from intercom_summary.intercom.models import Conversation
from intercom_summary.logging_setup import get_logger
from intercom_summary.qa import batch as batch_mod
from intercom_summary.qa.grading_common import trim_transcript
from intercom_summary.qa.pricing import UsageMeter
from intercom_summary.settings import settings

log = get_logger(__name__)

_MAX_TOKENS = 4000
_CONCURRENCY = 8

CASE_TYPES = ["Deposit", "Withdrawal", "Bonus", "KYC", "Account",
              "Game", "Technical", "Complaint", "Other"]
RISK_FLAGS = ["None", "Financial", "RG", "Security", "Churn"]
OUTCOMES = ["Resolved", "Pending-legitimate", "Escalated correctly",
            "Partially resolved", "Unresolved", "Abandoned-fake-closed"]
PLAYER_STATES = ["calm", "frustrated", "angry", "churn_risk", "rg_concern"]
LANGUAGES = ["en", "de", "fr", "other"]

# Property order is generation order: facts first, the digest last.
LABEL_SCHEMA = {
    "type": "object",
    "properties": {
        "language": {"type": "string", "enum": LANGUAGES},
        "case_type": {"type": "string", "enum": CASE_TYPES},
        "subtype": {"type": "string"},
        "player_requests": {"type": "array", "items": {"type": "string"}},
        "agent_actions": {"type": "array", "items": {"type": "string"}},
        "promises": {"type": "array", "items": {"type": "string"}},
        "escalated_to": {"type": "string"},
        "outcome": {"type": "string", "enum": OUTCOMES},
        "agent_errors": {"type": "array", "items": {"type": "string"}},
        "correct_handling": {"type": "string"},
        "player_state": {"type": "string", "enum": PLAYER_STATES},
        "risk_flag": {"type": "string", "enum": RISK_FLAGS},
        "keywords": {"type": "array", "items": {"type": "string"}},
        "summary_short": {"type": "string"},
    },
    "required": ["language", "case_type", "subtype", "player_requests", "agent_actions",
                 "promises", "escalated_to", "outcome", "agent_errors", "correct_handling",
                 "player_state", "risk_flag", "keywords", "summary_short"],
    "additionalProperties": False,
}

LABEL_SYSTEM = """You index support chats of an online casino for a quality-assurance team. For each chat you
write a short, factual, structured digest in ENGLISH, whatever language the chat is in. The
digest is read later next to other chats of the same player and by a grader looking for
similar cases, so it must be specific about the facts and neutral in tone.

Lines marked AGENT are the support agent, CUSTOMER is the player, "(note)" is an internal
note the player never saw. Automation has been removed.

Fields:
- language: the player's language.
- case_type: Deposit (money in, failed/pending deposits), Withdrawal (cashout, payout timing,
  cancelled withdrawals), Bonus (bonus terms, wagering, free spins, promo codes), KYC
  (verification, documents), Account (login, closure, limits, self-exclusion, personal data),
  Game (game errors, bets, results), Technical (site/app faults), Complaint (the player's
  main purpose is to complain or dispute), Other.
- subtype: the specific situation in up to six words, e.g. "withdrawal pending 3 days",
  "wagering requirement not met", "documents rejected twice".
- player_requests: what the player asked for, each in up to 15 words, at most 3.
- agent_actions: what the agent actually did (answered X, refused per terms, asked for
  documents, escalated to Payments, cancelled bonus, ...), up to 4.
- promises: commitments made to the player, with the timeframe stated ("withdrawal within
  1-3 business days", "Payments will email you", "callback today"). Empty if none.
- escalated_to: the team or person the case was passed to, or "" if not escalated.
- outcome: Resolved (request fully handled in the chat), Pending-legitimate (the agent did
  their part; waits on the player or another team), Escalated correctly, Partially resolved,
  Unresolved (left open with no next step), Abandoned-fake-closed (closed on the player
  without handling them).
- agent_errors: only clear, observable mistakes: ignored a question, gave wrong or
  contradictory information, no answer before closing, rude or dismissive wording, failed to
  escalate when the player asked for a manager or raised a safety concern, broke a promise
  made earlier in the chat. Up to 4, each up to 15 words. Empty when nothing is clearly
  wrong — do not invent faults.
- correct_handling: in up to 40 words, what a correct handling of this case looks like.
- player_state: calm, frustrated, angry, churn_risk (says they will leave / close the
  account / play elsewhere), rg_concern (gambling-harm or self-exclusion signals).
- risk_flag: Financial (money at stake and disputed), RG (responsible gaming), Security
  (account access, fraud), Churn, or None.
- keywords: up to 8 short English search terms for this case (e.g. "withdrawal", "bank
  transfer", "bonus wagering", "self-exclusion", "KYC documents").
- summary_short: up to 60 words: who asked what, what the agent did, how it ended. No
  names, no emails, no account numbers; amounts only when they matter.

Everything must come from the chat itself. If there is no agent reply, say so in the summary
and leave agent_actions empty."""

LABEL_VERSION = hashlib.sha256(
    (LABEL_SYSTEM + json.dumps(LABEL_SCHEMA, sort_keys=True)).encode()
).hexdigest()[:12]


# ── request ──────────────────────────────────────────────────────────────────────────────
def label_text(conversation: Conversation) -> str:
    """What the labeller reads: a short factual header and the human-only transcript."""
    date = conversation.created_at.strftime("%Y-%m-%d") if conversation.created_at else "unknown"
    tags = ", ".join(conversation.tags) or "none"
    header = (
        f"Chat date: {date} · Brand: {brand_label(conversation.brand)} · Tags: {tags}\n"
        f"Human agent assigned: {'yes' if conversation.assignee_name else 'no'} · "
        f"Closed by: {conversation.closed_by}\n"
        "=== TRANSCRIPT (agent and player only — automation removed) ===\n"
    )
    return header + trim_transcript(conversation.transcript_text(include_bots=False))


def label_request(conversation: Conversation, model: str | None = None,
                  batch: bool = False) -> dict:
    """The Messages API body for one label call. The system block is cache-marked: it is the
    same for every chat, and a batch keeps it warm for an hour."""
    cache = {"type": "ephemeral", "ttl": "1h"} if batch else {"type": "ephemeral"}
    return {
        "model": model or settings.history_label_model,
        "max_tokens": _MAX_TOKENS,
        "system": [{"type": "text", "text": LABEL_SYSTEM, "cache_control": cache}],
        "messages": [{"role": "user", "content": label_text(conversation)}],
        "output_config": {"effort": "low",
                          "format": {"type": "json_schema", "schema": LABEL_SCHEMA}},
    }


# ── response ─────────────────────────────────────────────────────────────────────────────
def _enum(value, allowed: list[str], default: str) -> str:
    return value if value in allowed else default


def _strs(value, limit: int, max_words: int = 25) -> list[str]:
    out = []
    for v in value if isinstance(value, list) else []:
        s = " ".join(str(v).split())
        if s:
            out.append(" ".join(s.split()[:max_words]))
        if len(out) >= limit:
            break
    return out


def _str(value, max_words: int) -> str:
    return " ".join(" ".join(str(value or "").split()).split()[:max_words])


def parse_label(resp) -> dict | None:
    """The label dict from a response, normalised to the schema's vocabulary; None when the
    response is a refusal, was cut off, or is not the JSON asked for."""
    if getattr(resp, "stop_reason", None) in ("refusal", "max_tokens"):
        return None
    text = "".join(getattr(b, "text", "") for b in getattr(resp, "content", []) or []
                   if getattr(b, "type", "") == "text")
    try:
        raw = json.loads(text)
    except (ValueError, TypeError):
        return None
    if not isinstance(raw, dict) or not str(raw.get("summary_short", "")).strip():
        return None
    return {
        "language": _enum(raw.get("language"), LANGUAGES, "other"),
        "case_type": _enum(raw.get("case_type"), CASE_TYPES, "Other"),
        "subtype": _str(raw.get("subtype"), 8),
        "player_requests": _strs(raw.get("player_requests"), 3, 20),
        "agent_actions": _strs(raw.get("agent_actions"), 4, 20),
        "promises": _strs(raw.get("promises"), 3, 20),
        "escalated_to": _str(raw.get("escalated_to"), 8),
        "outcome": _enum(raw.get("outcome"), OUTCOMES, "Unresolved"),
        "agent_errors": _strs(raw.get("agent_errors"), 4, 20),
        "correct_handling": _str(raw.get("correct_handling"), 50),
        "player_state": _enum(raw.get("player_state"), PLAYER_STATES, "calm"),
        "risk_flag": _enum(raw.get("risk_flag"), RISK_FLAGS, "None"),
        "keywords": _strs(raw.get("keywords"), 8, 4),
        "summary_short": _str(raw.get("summary_short"), 70),
    }


def label_row(conversation: Conversation, label: dict, *, source: str, model: str,
              cost_usd: float) -> dict:
    """A `chat_labels` row (HistoryStore.save_label) for one labelled conversation."""
    return {
        "conversation_id": conversation.id,
        "source": source,
        "brand": conversation.brand or "",
        "contact_id": conversation.contact.id or "",
        "agent_name": conversation.assignee_name,
        "created_at": conversation.created_at.isoformat() if conversation.created_at else None,
        "tags": ",".join(conversation.tags),
        "label": label,
        "label_version": LABEL_VERSION,
        "model": model,
        "cost_usd": round(cost_usd, 6),
    }


def fallback_query(conversation: Conversation) -> str:
    """Search text for a chat that has no label (the labeller failed or was skipped): its
    tags, subject and the player's opening words."""
    first = next((m.text for m in conversation.messages
                  if m.author_type != "admin" and (m.text or "").strip()), "")
    return " ".join([*conversation.tags, conversation.subject or "", first[:300]])


# ── live ─────────────────────────────────────────────────────────────────────────────────
def label_one(client, conversation: Conversation, model: str | None = None,
              meter: UsageMeter | None = None) -> dict | None:
    """Label one chat with a synchronous client. None when the call failed or was unusable —
    labelling must never stop a grading run."""
    model = model or settings.history_label_model
    try:
        resp = client.beta.messages.create(**label_request(conversation, model))
    except Exception as exc:  # noqa: BLE001 — one failed label is not a run failure
        log.warning("Label %s failed: %s", conversation.id, exc)
        return None
    if meter is not None:
        meter.add(resp.usage, model)
    return parse_label(resp)


async def label_live(client, conversations: list[Conversation], store, *, source: str,
                     model: str | None = None, concurrency: int = _CONCURRENCY,
                     on_progress: Callable[[int, int], None] | None = None,
                     cancel_event: threading.Event | None = None) -> dict:
    """Label chats concurrently with an async client and save each as it lands."""
    model = model or settings.history_label_model
    sem = asyncio.Semaphore(concurrency)
    done = failed = 0
    total_cost = 0.0

    async def one(convo: Conversation) -> None:
        nonlocal done, failed, total_cost
        if cancel_event is not None and cancel_event.is_set():
            return
        async with sem:
            meter = UsageMeter()
            try:
                resp = await client.beta.messages.create(**label_request(convo, model))
                meter.add(resp.usage, model)
                label = parse_label(resp)
            except Exception as exc:  # noqa: BLE001
                log.warning("Label %s failed: %s", convo.id, exc)
                label = None
        if label is None:
            failed += 1
        else:
            store.save_label(label_row(convo, label, source=source, model=model,
                                       cost_usd=meter.cost))
            done += 1
            total_cost += meter.cost
        if on_progress:
            on_progress(done + failed, len(conversations))

    await asyncio.gather(*(one(c) for c in conversations))
    return {"labelled": done, "failed": failed, "cost_usd": round(total_cost, 4)}


# ── batch ────────────────────────────────────────────────────────────────────────────────
def label_batch(client, conversations: list[Conversation], store, *, source: str,
                model: str | None = None, on_status: Callable[[dict], None] | None = None,
                cancel_event: threading.Event | None = None, poll: float | None = None,
                live_fallback: bool = True) -> dict:
    """Label chats through the Message Batches API (half price). Anything that does not come
    back usable is labelled live, so a batch run labels every chat it was given."""
    model = model or settings.history_label_model
    by_id = {c.id: c for c in conversations if batch_mod.batchable(c.id)}
    requests = [{"custom_id": cid, "params": label_request(c, model, batch=True)}
                for cid, c in by_id.items()]
    if not requests:
        return {"labelled": 0, "failed": 0, "cost_usd": 0.0, "batch_ids": [], "fallbacks": 0}
    batch_ids = batch_mod.submit_requests(client, requests, f"history labels {LABEL_VERSION}")
    cancelled = batch_mod.wait(client, batch_ids, cancel_event, on_status, poll=poll)

    done = failed = 0
    total_cost = 0.0
    leftovers: list[Conversation] = []
    returned: set[str] = set()
    for custom_id, result in batch_mod.results(client, batch_ids):
        convo = by_id.get(custom_id)
        if convo is None:
            continue
        returned.add(custom_id)
        label = None
        meter = UsageMeter()
        if getattr(result, "type", None) == "succeeded":
            meter.add(result.message.usage, model, batch=True)
            label = parse_label(result.message)
        if label is None:
            leftovers.append(convo)
            continue
        store.save_label(label_row(convo, label, source=source, model=model, cost_usd=meter.cost))
        done += 1
        total_cost += meter.cost
    leftovers.extend(c for cid, c in by_id.items() if cid not in returned)

    fallbacks = 0
    if leftovers and live_fallback and not cancelled:
        for convo in leftovers:
            meter = UsageMeter()
            label = label_one(client, convo, model, meter)
            if label is None:
                failed += 1
                continue
            store.save_label(label_row(convo, label, source=source, model=model,
                                       cost_usd=meter.cost))
            done += 1
            fallbacks += 1
            total_cost += meter.cost
    else:
        failed += len(leftovers)
    log.info("Labelled %d chat(s) in batch %s (%d live fallbacks, %d failed), $%.4f",
             done, ", ".join(batch_ids), fallbacks, failed, total_cost)
    return {"labelled": done, "failed": failed, "cost_usd": round(total_cost, 4),
            "batch_ids": batch_ids, "fallbacks": fallbacks, "cancelled": cancelled}

