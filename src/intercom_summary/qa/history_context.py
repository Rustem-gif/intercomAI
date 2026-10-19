"""Historical context for one grade: the player's earlier chats and QA-confirmed precedents.

The grader sees one transcript. What it cannot see from it is whether the player was told
something different last week, whether the promise the agent is repeating was already broken,
whether "I escalated this" was true, or how QA ruled the last time an agent handled this exact
situation. This module looks those up in the labelled history (storage/history_store.py) and
renders them as a short block that `Grader.messages_for` puts in front of the transcript —
in the user turn, after the cached system blocks, so the cache is untouched.

    provider = HistoryProvider()                     # mode from HISTORY_CONTEXT
    provider.prepare(conversations)                  # label the ones the index lacks
    ctx = provider.context_for(conversation)         # HistoryContext | None
    ctx.text                                         # the block, "" when nothing was found

Two rules the retrieval enforces, because the evaluation depends on them: a chat never sees
itself, and never sees anything created at or after its own date (`as_of`). So a re-grade
and a calibration run read exactly the history that existed when the chat happened.

Precedents are human-confirmed only (case_verdicts): AI-graded chats are not shown as
precedents, or the grader would be fed its own earlier verdicts. The player's own chats are
shown through their labels, with the AI outcome marked as such.

`mode="shadow"` retrieves and records what would have been sent, and sends nothing: that is
how the context is measured against a run without it, on the same grades.
"""
from __future__ import annotations

import threading
from dataclasses import asdict, dataclass, field

from intercom_summary.intercom.models import Conversation
from intercom_summary.logging_setup import get_logger
from intercom_summary.qa import history_labeller as labeller
from intercom_summary.settings import settings

log = get_logger(__name__)

# Roughly 900 tokens. Precedents are dropped first, then the oldest of the player's chats.
_MAX_CHARS = 3_600
_NOTE_CHARS = 220
_SUMMARY_CHARS = 420

HEADER = "=== HISTORICAL CONTEXT (reference only — not evidence) ==="
RULES = (
    "Rules: verdicts must still be proven by AGENT lines in THIS transcript. Use the player's "
    "earlier chats to judge consistency with what they were told before and promises made. "
    "Use QA precedents to calibrate severity the way QA does; never copy their verdicts."
)
FOOTER = "=== END HISTORICAL CONTEXT ==="


@dataclass(frozen=True)
class HistoryContext:
    mode: str                                  # "on" | "shadow"
    index_version: str
    text: str                                  # the rendered block, "" when nothing was found
    contact_prior: list[str] = field(default_factory=list)
    precedents: list[str] = field(default_factory=list)
    label_source: str = "none"                 # cached | haiku | fallback | none
    tokens_est: int = 0

    def as_dict(self) -> dict:
        return asdict(self)


def _one_line(text: str, limit: int) -> str:
    s = " ".join((text or "").split())
    return s if len(s) <= limit else s[: limit - 1].rstrip() + "…"


def _best_verdict(verdicts: list[dict]) -> dict | None:
    """The verdict to show for a chat: a scored one over a comment, the newest of those."""
    if not verdicts:
        return None
    scored = [v for v in verdicts if v.get("human_score") is not None]
    pool = scored or verdicts
    return sorted(pool, key=lambda v: v.get("reviewed_at") or "")[-1]


def _qa_phrase(verdicts: list[dict]) -> str:
    v = _best_verdict(verdicts)
    if v is None:
        return ""
    parts = []
    if v.get("human_score") is not None:
        ai = f" (AI {v['ai_score']})" if v.get("ai_score") is not None and v["ai_score"] != v["human_score"] else ""
        parts.append(f"QA {v['human_score']}{ai}")
    else:
        parts.append("QA note")
    if v.get("note"):
        parts.append(f'"{_one_line(v["note"], _NOTE_CHARS)}"')
    changed = v.get("criteria") or {}
    if changed:
        items = [f"{k}={val}" for k, val in list(changed.items())[:4]]
        parts.append("criteria: " + ", ".join(items))
    return ": ".join(parts[:1]) + (": " + " · ".join(parts[1:]) if len(parts) > 1 else "")


def render_prior(row: dict, verdicts: list[dict]) -> str:
    lab = row["label"]
    bits = [(row.get("created_at") or "")[:10] or "undated",
            " / ".join(b for b in (lab.get("case_type"), lab.get("subtype")) if b),
            _one_line(lab.get("summary_short", ""), _SUMMARY_CHARS)]
    if lab.get("promises"):
        bits.append("promised: " + "; ".join(lab["promises"][:2]))
    if lab.get("escalated_to"):
        bits.append(f"escalated to {lab['escalated_to']}")
    bits.append(f"outcome: {lab.get('outcome', '?')} (AI)")
    if qa := _qa_phrase(verdicts):
        bits.append(qa)
    return "- " + " · ".join(b for b in bits if b)


def render_precedent(row: dict, verdicts: list[dict]) -> str:
    lab = row["label"]
    bits = [" / ".join(b for b in (lab.get("case_type"), lab.get("subtype")) if b),
            _one_line(lab.get("summary_short", ""), _SUMMARY_CHARS)]
    if lab.get("agent_errors"):
        bits.append("errors noted: " + "; ".join(lab["agent_errors"][:2]))
    bits.append(_qa_phrase(verdicts) or "QA-confirmed")
    return "- " + " · ".join(b for b in bits if b)


def render(prior_lines: list[str], precedent_lines: list[str]) -> str:
    if not prior_lines and not precedent_lines:
        return ""
    out = [HEADER, RULES]
    if prior_lines:
        out.append("This player's earlier chats (newest first):")
        out.extend(prior_lines)
    if precedent_lines:
        out.append("QA precedents (similar cases, QA-confirmed):")
        out.extend(precedent_lines)
    out.append(FOOTER)
    return "\n".join(out)


def budget(prior_lines: list[str], precedent_lines: list[str],
           max_chars: int = _MAX_CHARS) -> tuple[list[str], list[str]]:
    """Trim until the block fits: precedents go first (last one first), then the player's
    oldest chats. The newest of the player's chats is never dropped."""
    prior, prec = list(prior_lines), list(precedent_lines)
    while len(render(prior, prec)) > max_chars:
        if prec:
            prec.pop()
        elif len(prior) > 1:
            prior.pop()
        else:
            break
    return prior, prec


class HistoryProvider:
    """Builds the context for a conversation. Safe to share across the grading threads: each
    thread gets its own store connection, and results are memoised per conversation so the
    live call, its retry and a batch `finish` all see the same block."""

    def __init__(self, mode: str | None = None, store=None, client=None,
                 days: int | None = None, max_prior: int | None = None,
                 max_precedents: int | None = None, label_model: str | None = None) -> None:
        self.mode = mode or settings.history_context
        self._store = store                     # an injected store is used from every thread
        self._client = client                   # None → an Anthropic client, made lazily
        self._client_lock = threading.Lock()
        self._local = threading.local()
        self.days = days or settings.history_days
        self.max_prior = max_prior or settings.history_max_prior
        self.max_precedents = max_precedents or settings.history_max_precedents
        self.label_model = label_model or settings.history_label_model
        self._memo: dict[str, HistoryContext | None] = {}
        self._memo_lock = threading.Lock()
        self._version: str | None = None

    # ── plumbing ─────────────────────────────────────────────────────────────────────────
    def store(self):
        if self._store is not None:
            return self._store
        s = getattr(self._local, "store", None)
        if s is None:
            from intercom_summary.storage.history_store import HistoryStore
            s = self._local.store = HistoryStore()
        return s

    def client(self):
        with self._client_lock:
            if self._client is None:
                from anthropic import Anthropic
                self._client = Anthropic(api_key=settings.anthropic_api_key, timeout=60.0,
                                         max_retries=2)
            return self._client

    @property
    def version(self) -> str:
        if self._version is None:
            try:
                self._version = self.store().index_version()
            except Exception as exc:  # noqa: BLE001
                log.warning("History index unavailable: %s", exc)
                self._version = ""
        return self._version

    # ── before a run ─────────────────────────────────────────────────────────────────────
    def prepare(self, conversations: list[Conversation]) -> dict:
        """Label the chats the index has no current label for, so grading does not stop to
        label them one by one. Returns the labeller's counts; a failure here is logged and
        grading goes on (the missing labels fall back to a keyword query)."""
        try:
            store = self.store()
            have = store.labelled_ids(labeller.LABEL_VERSION)
            todo = [c for c in conversations if c.id not in have]
            if not todo:
                return {"labelled": 0, "failed": 0, "cost_usd": 0.0}
            import asyncio

            from anthropic import AsyncAnthropic

            log.info("History: labelling %d chat(s) the index lacks", len(todo))
            return asyncio.run(labeller.label_live(
                AsyncAnthropic(api_key=settings.anthropic_api_key), todo, store,
                source="cache", model=self.label_model))
        except Exception as exc:  # noqa: BLE001
            log.warning("History: pre-labelling failed, grading goes on without it: %s", exc)
            return {"labelled": 0, "failed": len(conversations), "cost_usd": 0.0}

    # ── per conversation ─────────────────────────────────────────────────────────────────
    def context_for(self, conversation: Conversation, *, as_of: str | None = None,
                    exclude_ids=()) -> HistoryContext | None:
        """The context for one chat, or None when the history is off or unavailable."""
        if self.mode == "off":
            return None
        with self._memo_lock:
            if conversation.id in self._memo:
                return self._memo[conversation.id]
        try:
            ctx = self._build(conversation, as_of, set(exclude_ids))
        except Exception as exc:  # noqa: BLE001 — grading must not depend on the index
            log.warning("History context for %s failed: %s", conversation.id, exc)
            ctx = None
        with self._memo_lock:
            self._memo[conversation.id] = ctx
        return ctx

    def _label(self, conversation: Conversation) -> tuple[dict | None, str]:
        store = self.store()
        if (row := store.get_label(conversation.id)) and row["label_version"] == labeller.LABEL_VERSION:
            return row["label"], "cached"
        label = labeller.label_one(self.client(), conversation, self.label_model)
        if label is not None:
            store.save_label(labeller.label_row(conversation, label, source="cache",
                                                model=self.label_model, cost_usd=0.0))
            return label, "haiku"
        return None, "fallback"

    def _build(self, conversation: Conversation, as_of: str | None, exclude: set[str]) -> HistoryContext:
        store = self.store()
        as_of = as_of or (conversation.created_at.isoformat() if conversation.created_at else "")
        exclude = exclude | {conversation.id}
        label, label_source = self._label(conversation)

        prior = store.for_contact(conversation.contact.id or "", before=as_of, exclude=exclude,
                                  days=self.days, limit=self.max_prior) if as_of else []
        prior_ids = [r["conversation_id"] for r in prior]

        if label:
            query = " ".join([*label.get("keywords", []), label.get("subtype", ""),
                              *label.get("player_requests", []), label.get("summary_short", "")])
            case_type = label.get("case_type")
        else:
            query, case_type = labeller.fallback_query(conversation), None
        kw = dict(brand=conversation.brand or None, before=as_of or None,
                  exclude=exclude | set(prior_ids), human_only=True, limit=self.max_precedents)
        precedents = store.search(query, case_type=case_type, **kw) if query else []
        if not precedents and case_type and query:
            precedents = store.search(query, **kw)          # same brand, any case type
        prec_ids = [r["conversation_id"] for r in precedents]

        verdicts = store.verdicts_for(prior_ids + prec_ids)
        prior_lines = [render_prior(r, verdicts.get(r["conversation_id"], [])) for r in prior]
        prec_lines = [render_precedent(r, verdicts.get(r["conversation_id"], [])) for r in precedents]
        prior_lines, prec_lines = budget(prior_lines, prec_lines)
        text = render(prior_lines, prec_lines)
        return HistoryContext(
            mode=self.mode, index_version=self.version, text=text,
            contact_prior=prior_ids[:len(prior_lines)], precedents=prec_ids[:len(prec_lines)],
            label_source=label_source, tokens_est=len(text) // 4,
        )
