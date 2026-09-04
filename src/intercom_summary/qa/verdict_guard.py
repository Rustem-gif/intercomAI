"""Reject grader verdicts the conversation itself contradicts.

Three client complaints, one shape: the model deducts points from the agent for something the
agent did not do, and its own cited evidence proves it. Re-wording the rubric makes the model
more likely to be right; it cannot make it reliable. This is the backstop. Every rule here is
deterministic, and every correction returns a flag that the caller puts in `flags`, so it
reaches `ConversationGrade.signal_flags` and stays auditable — nothing is corrected silently.

Measured across the 5,348 graded conversations in the cache when this was written:

**Mis-attribution** — 646 failing verdicts (4.7%) cite a BOT line and 371 (2.7%) a CUSTOMER
line. Automation writes ~41% of the text while the agent writes a median of four turns, and
`BOT Billy Jr.:` looks exactly like `AGENT Lenny:`. Real examples: the bot's German marketing
script scored as `info-relevance`; `CUSTOMER James Napier: Oh piss off oswald` scored as the
*agent's* `cf-friendly` failure. Applies to every criterion, because someone else's words are
never the agent's failure — except the handful of criteria that exist to react to what the
player said, where a player quote is the correct evidence (`PLAYER_EVIDENCED_CRITERIA`).

**Closure attribution** — automation closes 52.8% of chats. `close-confirm` and `close-courtesy`
already carry the N/A clause "agent didn't close the chat", but the prompt never named the
closer, so the escape was taken on 6.8% of verdicts and 1,834 closing failures were issued on
chats the bot closed. `res-no-fake-close` costs −15 each time.

**Greeting and name** — 433 of 434 `open-greet` fails cited the rubric's own FAIL text instead
of the chat; 89 of 112 `open-name-use` fails cited a line where the agent greets the player *by
name*. The grounding rule stays scoped to these two: applied everywhere it would flip 37% of all
failing verdicts and move scores by +6.28 points across 29% of chats, far beyond the reported
bugs. Mis-attribution and closure are not scoped that way — they are unambiguous.
"""
from __future__ import annotations

import re
from typing import Any

from intercom_summary.intercom.models import Conversation
from intercom_summary.logging_setup import get_logger

log = get_logger(__name__)

# The criteria this guard is allowed to touch. Both are mechanically checkable against the
# transcript, which is precisely why a model getting them wrong is worth catching.
GREETING_CRITERIA: frozenset[str] = frozenset({"open-greet", "vip-greet-personal"})
NAME_CRITERIA: frozenset[str] = frozenset({"open-name-use", "vip-greet-personal"})
GUARDED: frozenset[str] = GREETING_CRITERIA | NAME_CRITERIA

# Criteria that judge the agent's *closing behaviour*. They are meaningless when the agent did
# not close the chat — and in this workspace automation closes 52.8% of them. `close-confirm`
# and `close-courtesy` already say so in their own N/A column; this enforces it rather than
# hoping the model reads the header. `res-next-step` is deliberately absent: explaining what
# happens next is the agent's job however the chat ended.
CLOSING_CRITERIA: frozenset[str] = frozenset(
    {"res-no-fake-close", "close-confirm", "close-courtesy", "vip-close-confirm"}
)

# Criteria whose trigger IS a player statement, so quoting the player is correct evidence and
# overturning it would break them. Churn detection exists precisely to react to what the player
# said; the same is true of the RG signal and of a withdrawal complaint.
PLAYER_EVIDENCED_CRITERIA: frozenset[str] = frozenset({
    "churn-detect-ack", "churn-retention-handling", "churn_signal",
    "churn_retention_handling", "pay-withdrawal-sensitivity",
    "pay_withdrawal_sensitivity", "crit-rg-care",
})

# How much of the cited evidence has to appear in the transcript. The model paraphrases tails
# and re-wraps whitespace, so requiring the whole string would reject good citations; a prefix
# this long is far more than any criterion description shares with a real quote.
_PREFIX = 60

# Names shorter than this are not matched — "Al" or "Jo" appear inside ordinary words often
# enough that a word-boundary match on them is not evidence of anything.
_MIN_NAME = 3


def _norm(text: str) -> str:
    """Whitespace-collapsed, lowercased text for substring comparison."""
    return re.sub(r"\s+", " ", text or "").strip().lower()


def _is_grounded(evidence: str, transcript: str) -> bool:
    """True if the cited evidence really comes from the conversation."""
    ev = _norm(evidence)
    if not ev or ev == "n/a":
        return False
    return ev[:_PREFIX] in transcript


def _player_names(conversation: Conversation) -> list[str]:
    """Every name the agent could reasonably have used, longest first.

    The full name and each of its parts count: agents address players by first name
    ("Hello, Keely!") while the record holds "Keely Smith", and treating that as unused was one
    half of the reported bug.
    """
    raw = [conversation.contact.name or ""]
    raw += [
        m.author_name for m in conversation.messages
        if m.author_type in ("user", "contact", "lead") and m.author_name
    ]
    names: set[str] = set()
    for value in raw:
        value = value.strip()
        if not value or "@" in value:  # a byline that fell back to an email is not a name
            continue
        names.add(value)
        names.update(part for part in value.split() if len(part) >= _MIN_NAME)
    return sorted((n for n in names if len(n) >= _MIN_NAME), key=len, reverse=True)


# Lines the prompt itself supplies. A fail "evidenced" by one of these quotes the instructions
# rather than the conversation — 46 stored verdicts do exactly that, most of them citing the SLA
# header to justify a deduction the header was never meant to carry.
_PROMPT_HEADER_MARKERS: tuple[str, ...] = (
    "first response time:", "agent's first reply:", "time to close:",
    "follow-up sla target", "=== timing", "target ≤",
)


# The one criterion whose correct evidence IS the header line: first-reply speed is judged from
# the stated SLA figure precisely so the model stops doing its own transcript arithmetic.
HEADER_EVIDENCED_CRITERIA: frozenset[str] = frozenset({"resp-first-reply"})


def _quotes_the_prompt(evidence: str) -> bool:
    """True if the citation is a line of the prompt header, not something anyone said."""
    ev = _norm(evidence)
    return bool(ev) and any(marker in ev for marker in _PROMPT_HEADER_MARKERS)


def _role_of_evidence(evidence: str, conversation: Conversation) -> str | None:
    """The role of the transcript line a citation came from, or None if it matches nothing.

    Matched against the FULL thread, bots included — the grader's own transcript has automation
    stripped out, so a bot citation would otherwise vanish into "quotes nothing I can check"
    instead of being recognised for what it is: the agent blamed for the bot's words.
    """
    ev = _norm(evidence)
    if not ev or ev == "n/a":
        return None
    probe = ev[:_PREFIX]
    for m in conversation.messages:
        if not (m.text and m.text.strip()):
            continue
        if probe in _norm(m.text) or probe in _norm(f"{conversation._role_of(m.author_type)} "
                                                    f"{m.author_name}: {m.text}"):
            return conversation._role_of(m.author_type)
    return None


def _agent_text(conversation: Conversation) -> str:
    return " ".join(m.text or "" for m in conversation.messages if m.author_type == "admin")


def _name_was_used(conversation: Conversation) -> str | None:
    """The player name found in the agent's own words, or None."""
    agent = _agent_text(conversation)
    if not agent:
        return None
    for name in _player_names(conversation):
        if re.search(rf"\b{re.escape(name)}\b", agent, re.IGNORECASE):
            return name
    return None


def apply_guards(conversation: Conversation, data: dict[str, Any]) -> list[str]:
    """Correct contradicted verdicts in a raw grader response, in place.

    `data` is the JSON the model returned; its `criteria` entries are edited directly, so this
    must run BEFORE `ConversationGrade.from_ollama_output`, which computes the score from them.

    Returns a flag per correction, for the caller to append to `data["flags"]`.
    """
    criteria = data.get("criteria")
    if not isinstance(criteria, list):
        return []

    # The full thread, not the grader's bot-stripped view: a bot citation is caught by the
    # mis-attribution rule below, so anything reaching the grounding check should be judged
    # against everything that was really said rather than dropped for quoting a removed line.
    transcript = _norm(conversation.transcript_text())
    used_name = None
    checked_name = False
    flags: list[str] = []

    for c in criteria:
        if not isinstance(c, dict):
            continue
        cid = c.get("id", "")
        if c.get("v") != "fail":
            continue

        # The agent did not close this chat, so nothing about how it was closed is theirs.
        if cid in CLOSING_CRITERIA and conversation.closed_by != "admin":
            c["v"] = "n/a"
            closer = conversation.closed_by or "nobody — the chat is still open"
            flags.append(f"guard:{cid} fail dropped — the chat was closed by {closer}, "
                         f"not the agent")
            log.info("Guard dropped %s on %s: closed by %r", cid, conversation.id, closer)
            continue

        # The "quote" is a line of this prompt's own timing header. Nobody said it, so it
        # cannot evidence anything the agent did.
        if cid not in HEADER_EVIDENCED_CRITERIA and _quotes_the_prompt(c.get("ev", "")):
            c["v"] = "n/a"
            flags.append(f"guard:{cid} fail dropped — the cited quote is a line of the prompt "
                         f"header, not the conversation")
            log.info("Guard dropped prompt-quoting %s on %s", cid, conversation.id)
            continue

        # The quote belongs to someone else. Applies to every criterion: automation's words and
        # the player's words are never the agent's failure.
        role = _role_of_evidence(c.get("ev", ""), conversation)
        if role == "BOT" or (role == "CUSTOMER" and cid not in PLAYER_EVIDENCED_CRITERIA):
            c["v"] = "n/a"
            flags.append(f"guard:{cid} fail dropped — the cited quote is a "
                         f"{role.lower()} line, not the agent's")
            log.info("Guard dropped mis-attributed %s on %s (%s line)",
                     cid, conversation.id, role)
            continue

        if cid not in GUARDED:
            continue

        # The agent demonstrably used the player's name, so a name-based fail is contradicted
        # by the conversation. Checked first: it is the stronger signal, and unlike grounding
        # it survives the model citing a real line.
        if cid in NAME_CRITERIA:
            if not checked_name:
                used_name, checked_name = _name_was_used(conversation), True
            if used_name:
                c["v"] = "pass"
                flags.append(f"guard:{cid} fail overturned — agent used the player's name "
                             f"({used_name!r})")
                log.info("Guard overturned %s on %s: agent used %r",
                         cid, conversation.id, used_name)
                continue

        # Nothing in the conversation backs the deduction. Downgrade to n/a rather than pass:
        # the verdict is unsupported, which is not the same as the agent being in the clear,
        # and n/a costs no points either way.
        if not _is_grounded(c.get("ev", ""), transcript):
            c["v"] = "n/a"
            flags.append(f"guard:{cid} fail dropped — cited evidence is not in the transcript")
            log.info("Guard dropped ungrounded %s fail on %s", cid, conversation.id)

    return flags
