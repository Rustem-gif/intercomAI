"""Shared grading prompt + JSON extraction, used by every QA backend.

Keeping the system instructions and the expected output shape in one place means the
Anthropic-API grader, the Claude Code CLI grader, and the manual hand-off task file all
ask for exactly the same thing.
"""
from __future__ import annotations

import json
import re

from intercom_summary.intercom.models import Conversation, fmt_duration
from intercom_summary.settings import settings

SYSTEM_INSTRUCTIONS = """You are a meticulous, fair customer-support QA reviewer.
You are given a RULESET that support agents must follow, and a single conversation
transcript between a support AGENT and a CUSTOMER.

Evaluate ONLY the AGENT's behaviour against the ruleset. For every rule (or numbered
item) in the ruleset, decide pass / fail / n-a, cite brief evidence from the transcript,
and explain. Be objective: reward good handling, flag real violations, do not invent
rules that are not in the ruleset.
"""

# Human-readable description of the JSON we want back (for CLI + task-file backends).
OUTPUT_SCHEMA_TEXT = """Return ONLY a single JSON object (no markdown, no commentary) of the form:
{
  "overall_score": <integer 0-100>,
  "summary": "<2-4 sentence summary of how the agent handled this conversation>",
  "rule_results": [
    {"rule_id": "<rule id or item number>", "title": "<short rule title>",
     "verdict": "pass" | "fail" | "n/a",
     "evidence": "<short quote or reference>", "comment": "<why>"}
  ],
  "violations": ["<concrete violation, most important first>"],
  "suggestions": ["<actionable coaching suggestion>"]
}"""


def _timing_block(conversation: Conversation) -> str:
    """Authoritative SLA facts + targets, so the grader judges timeliness from stated
    numbers rather than doing timestamp arithmetic over the transcript."""
    first_target = settings.sla_first_response_sec
    followup_target = settings.sla_followup_sec
    sla = conversation.sla_summary(first_target, followup_target)
    agent_frt = sla["agent_first_reply"]
    if agent_frt is None:
        first_line = "Agent's first reply: the agent never replied in this chat"
    else:
        verdict = "BREACHED" if sla["first_response_breached"] else "OK"
        first_line = (
            f"Agent's first reply: {sla['agent_first_reply_human']} after the chat reached them "
            f"(target ≤ {fmt_duration(first_target)} → {verdict})"
        )
    return (
        "=== TIMING (authoritative, from Intercom) ===\n"
        f"{first_line}\n"
        f"Time to close: {sla['time_to_close_human']}\n"
        f"Follow-up SLA target: ≤ {fmt_duration(followup_target)} between agent replies\n"
        "The first-reply clock starts when the chat was routed to the agent, not when the player\n"
        "wrote — a bot answers first and holds the chat, and the agent cannot reply before it is\n"
        "handed over. Judge first-reply speed ONLY against the number above, never by doing your\n"
        "own arithmetic on the transcript, and record it ONLY under resp-first-reply.\n"
        "(In the transcript, '+Xm waited after customer' on a later AGENT line is the player's wait.)"
    )


def _csat_block(conversation: Conversation) -> str:
    """Customer's post-chat rating, when present — a ground-truth signal for the grader.

    Returned empty (no header) when no rating was left, to avoid cluttering the prompt.
    """
    if conversation.csat_rating is None:
        return ""
    remark = f' — "{conversation.csat_remark}"' if conversation.csat_remark else ""
    return (
        "=== CUSTOMER SATISFACTION (post-chat survey) ===\n"
        f"CSAT: {conversation.csat_rating}/5{remark}\n"
        "Use this as a corroborating signal for resolution_effectiveness and tone_and_empathy, "
        "but judge the agent's behaviour independently — a high CSAT does not excuse a "
        "compliance/security/RG failure, and a low CSAT may be unfair to a correct agent.\n\n"
    )


def _closed_by_line(conversation: Conversation) -> str:
    """Who ended the chat.

    Three criteria judge the agent's closing behaviour and two of them carry the N/A clause
    "agent didn't close the chat" — a correct rule the model could never apply, because nothing
    in this prompt named the actor. `State: closed` is binary and `Time to close` is a duration.
    The bot closes 52.8% of chats here, so the missing fact was worth up to −15 a time.
    """
    return {
        "bot":   "Chat closed by: automation — the bot closed this chat, NOT the agent. "
                 "The agent had no control over the closing.",
        "admin": "Chat closed by: the agent",
    }.get(conversation.closed_by, "Chat closed by: nobody — the chat is still open")


def transcript_block(conversation: Conversation) -> str:
    customer_name = conversation.contact.name or conversation.contact.email or "unknown"
    return (
        f"Conversation ID: {conversation.id}\n"
        f"Assigned agent: {conversation.assignee_name or 'unknown'}\n"
        f"Customer name: {customer_name}\n"
        f"Subject: {conversation.subject}\n"
        f"State: {conversation.state}\n"
        f"{_closed_by_line(conversation)}\n\n"
        f"{_timing_block(conversation)}\n\n"
        f"{_csat_block(conversation)}"
        "=== TRANSCRIPT (agent and player only — automation removed) ===\n"
        f"{conversation.transcript_text(include_bots=False)}\n=== END ==="
    )


def build_text_prompt(ruleset_text: str, ruleset_version: str, conversation: Conversation) -> str:
    """Full self-contained prompt for text backends (Claude Code CLI / manual)."""
    return (
        f"{SYSTEM_INSTRUCTIONS}\n\n"
        f"# SUPPORT RULESET (version {ruleset_version})\n\n{ruleset_text}\n\n"
        f"# CONVERSATION TO GRADE\n\n{transcript_block(conversation)}\n\n"
        f"# OUTPUT\n{OUTPUT_SCHEMA_TEXT}"
    )


def extract_grade_dict(text: str) -> dict:
    """Pull the grade JSON object out of arbitrary model text.

    Tolerates ```json fences and surrounding prose by scanning for the first balanced
    top-level object.
    """
    text = text.strip()
    # Strip code fences if present.
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)

    # Fast path: whole string is JSON.
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Scan for the first balanced { ... } block.
    start = text.find("{")
    while start != -1:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    candidate = text[start : i + 1]
                    try:
                        return json.loads(candidate)
                    except json.JSONDecodeError:
                        break
        start = text.find("{", start + 1)
    raise ValueError("No JSON grade object found in model output.")
