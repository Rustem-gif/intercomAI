"""A second, independent look at every FAIL before it is final.

QA overturns most of the AI's FAILs: of the 146 kb-v41 chats the AI failed and QA reviewed
(Oct 2026), 130 came back PASS. The errors are almost all one-directional — a criterion
failed on a judgement call ("the player was ghosted", "this statement is inaccurate") that a
QA manager does not share. A PASS is rarely overturned. So the check sits only on the FAIL
side, where it is cheap (~1 chat in 10) and where the errors are:

    grader → guards/derivations → [provisional FAIL?] → fail verifier → score

The verifier is the same model reading the same cached system prompt and knowledge base, with
the same transcript, but a different job: for each failed criterion, is the FAIL proven by the
transcript under the manual's definition, and does none of the known NOT-A-FAIL boundaries
apply? It answers `upheld` or `not_proven` per criterion.

Policy (code, not the model):
  • not_proven on a Gate 2/3 criterion → the verdict becomes pass, with a flag naming it.
  • not_proven on a Gate 1 (critical) criterion → unchanged, but the chat goes to manual
    review: a zero is never lifted automatically.
  • a verifier error, refusal or unusable answer → the grade is left exactly as it was.

Modes (QA_FAIL_VERIFIER): off · shadow (ask and record, change nothing) · on.
"""
from __future__ import annotations

import copy
import json
from typing import Any

from intercom_summary.logging_setup import get_logger

log = get_logger(__name__)

VERIFIER_MODES = ("off", "shadow", "on")
UPHELD, NOT_PROVEN = "upheld", "not_proven"

# The NOT-A-FAIL boundaries, per criterion — the patterns behind QA's overturns. Each one is a
# situation in which the manual's FAIL definition is not met although a first reading may
# suggest it. They are given only for the criteria being checked.
BOUNDARIES: dict[str, list[str]] = {
    "resp-no-ghost": [
        "The player's last messages are thanks, ok, goodbye, an emoji, or a statement that needs "
        "no answer — nothing was left to reply to. (An 'are you still there?', 'I have sent the "
        "email' or a new question DOES need a reply; see WHO STOPPED RESPONDING.)",
        "The agent answered the request (even briefly or with a refusal by the terms) and the "
        "player did not come back with a new question.",
        "The agent asked the player for something (a document, a detail, a check) or told them "
        "what happens next, and the player went silent — the case waits on the player.",
        "The request depends on another team or a process the agent explained or started "
        "(Finance processing, KYC review, a pending withdrawal within its term) — that is "
        "pending, not unresolved.",
        "The player's 'unanswered' message was answered by the agent's next message, or the "
        "agent's reply covered it together with another question.",
        "The agent's reply dealt with the request indirectly: the reason it cannot be done (a "
        "duplicate account was closed, verification is needed first, not eligible under the "
        "terms, a T&C reference), the step that must come first, or a handover to whoever will "
        "handle it (the VIP manager). QA counts that as answered.",
        "A secondary question skipped while the main request was handled, which the player "
        "did not repeat and which is not in their last messages, is info-completeness at most — "
        "even when it concerns money.",
        "The 'request' is a bot-menu button — a customer line marked '(bot-menu choice)' — "
        "clicked before the agent joined ('Withdrawal issue'), and the player's own words "
        "pursued a different topic, which the agent dealt with.",
        "The player's last message only repeats a demand the agent already answered, or refused "
        "with a reason and an alternative (e.g. 'we cannot open files — please type the "
        "details'); a reminder of the rule that bars the request also answers it.",
        "An internal note shows a problem on the agent's side (power outage, lost connection, "
        "system down) — not proven; a QC manager decides.",
        "NOT covered by these: the player corrected the agent and got the same answer again; or "
        "a vague 'it has been forwarded' repeated to a player asking for the status.",
    ],
    "res-no-fake-close": [
        "Automation closed the chat after the player stopped replying, or the agent closed with "
        "the standard inactivity message after the player stopped replying to an agent message "
        "that moved the case forward. (NOT when the player's last message was left unanswered, "
        "or the agent's last message only re-asked for something already given.)",
        "An internal note shows a problem on the agent's side (power outage, lost connection).",
        "The agent closed after answering, after asking the player for something, or after the "
        "player stopped replying to the agent's last message.",
        "The problem is pending with another team and the agent said so.",
    ],
    "accuracy-material": [
        "The only proof is a difference from the KNOWLEDGE BASE. QA treats the Help Center as "
        "out of date for offers, payment timeframes and internal policy; an accuracy FAIL needs "
        "the transcript itself to show the statement wrong.",
        "The agent's standard wording: 'Crypto E-wallets up to 24 hours', 'withdrawals are "
        "approved within 24 hours', 'Support does not add bonuses in live chat'.",
        "The statement is about THIS player's own account, transaction, verification or what "
        "the agent sees in the back office — not a general rule. The player denying it does "
        "not prove it wrong.",
        "The 'contradiction' is between statements that can both be true: a general rule and a "
        "refusal of this player's exception; two wordings of one standard term ('up to 5 days', "
        "'5–7 business days'); a later message adding a further reason or reflecting an action "
        "the player asked for; an internal note in which the agent is unsure.",
        "(Still a FAIL: denying service on eligibility — country, age, account type — when the "
        "KNOWLEDGE BASE says the player is accepted.)",
    ],
    "accuracy-minor": [
        "The only proof is a difference from the KNOWLEDGE BASE, or the statement is a harmless "
        "simplification.",
    ],
    "action-escalation-missed": [
        "None of the MANDATORY ESCALATION cases applies — answering or refusing by the terms "
        "was the correct handling.",
        "An internal note shows the handoff, or the agent resolved the matter themselves.",
        "The agent was still waiting for proof or a detail they asked the player for, or for "
        "the player to try a step they suggested, so no escalation was due yet.",
        "The reward was not yet due under the promotion's own timeline.",
        "The player only voiced dissatisfaction with their manager ('I think I need a new "
        "manager') — not a request to be put in touch with one.",
    ],
    "financial-case-abandoned": [
        "The agent gave the status they could see, the next step, or asked for what was needed, "
        "and the case now waits on the player or on Finance.",
        "A refusal strictly by the terms, explained to the player.",
    ],
    "severe-business-trust": [
        "The brand's standard processing terms ('withdrawals are reviewed within up to 24 "
        "hours', 'usually faster'), or a timeframe that matches the Help Center.",
        "A status the agent reports seeing in the back office — reporting it is not inventing it.",
        "A hedged estimate ('usually', 'should', 'up to') is not a promise.",
    ],
    "churn-rg-signal-ignored": [
        "The player complained or was dissatisfied but never said in plain words they are "
        "leaving, closing the account or playing elsewhere.",
        "The agent apologised, empathised, explained, offered a solution or escalated.",
        "A threat used as leverage ('a bonus or I play elsewhere') answered with a polite refusal "
        "by the terms and an offer of further help.",
    ],
    "info-completeness": [
        "The answer covers what the player asked; it could merely have been more detailed. "
        "(Not when a refusal or status gave the player no reason and no next step.)",
        "The missing part depends on another team, or on information the player has not given.",
    ],
    "ownership-effort": [
        "The agent did what was available to them in the chat; the limit was outside their "
        "authority or waited on another team.",
        "Asking the player once for a detail needed to act is not making them repeat it — "
        "unless they had already given it or it was visible (a screenshot). The standard opening "
        "question asking the player to describe the request is not making them repeat it.",
        "Declining for security reasons to open an external link or file and asking for the "
        "details as text.",
    ],
    "comm-tone": [
        "Brief, neutral or templated wording is not cold or irritated — unless the player is "
        "visibly upset and no reply acknowledges it, or the same template is pasted twice.",
        "The wording judged is in an internal (yellow) note, which the player never sees.",
        "A firm refusal by the terms, said politely, is the correct tone.",
    ],
    "resp-sla": [
        "The TIMING header does not show the reply gap over the SLA, or the agent acknowledged "
        "the delay, or a legitimate reason for the pause is visible.",
    ],
    "tag-chat": [
        "The Chat tags header lists a tag that fits the case.",
    ],
}

_INSTRUCTIONS = """=== SECOND REVIEW — VERIFY THE FAILS ===
A first-pass grader read this chat under the rules above and marked the criteria below as FAIL. You are an independent senior QA manager checking that work. The first pass is known to be too strict: QA managers overturn most of its FAILs, almost always because a judgement call was read as a failure.

For each criterion below answer:
- upheld — the transcript proves the FAIL as the manual defines it: a direct AGENT line, or for an omission the transcript plainly showing the agent left something that was theirs to do; and none of the NOT A FAIL situations applies.
- not_proven — anything else: the evidence is not an AGENT line or does not show what the definition requires; the case waits on the player or on another team; the knowledge base does not contradict the statement; or a reasonable QA manager could see it either way.
Judge each criterion on its own; do not look for new problems and do not re-grade anything else. Quote, in at most 25 words, the line that decides it.

Criteria to verify:
{items}

Return ONLY this JSON object:
{{"checks": [{{"id": "<criterion id>", "verdict": "<upheld|not_proven>", "quote": "<the deciding line, or empty>", "reason": "<one sentence>"}}]}}"""

VERIFIER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["checks"],
    "properties": {
        "checks": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["id", "verdict", "quote", "reason"],
                "properties": {
                    "id": {"type": "string"},
                    "verdict": {"type": "string", "enum": [UPHELD, NOT_PROVEN]},
                    "quote": {"type": "string"},
                    "reason": {"type": "string"},
                },
            },
        },
    },
}


def failed_criteria(data: dict[str, Any]) -> list[dict]:
    """The fails a verifier re-reads. A fail settled by Intercom's clock (`src: timing`, the
    BREACHED SLA rule in qa/gated_rules.py) is not a reading of the chat and is left out."""
    return [c for c in data.get("criteria") or []
            if isinstance(c, dict) and c.get("v") == "fail" and c.get("id")
            and c.get("src") != "timing"]


def would_fail(conversation, data: dict[str, Any], ruleset_id: str) -> bool:
    """The result this raw grade would get, computed on a copy — the verifier only looks at
    chats heading for FAIL."""
    from intercom_summary.qa.schema import ConversationGrade

    g = ConversationGrade.from_model_output(conversation.id, conversation.assignee_name,
                                            copy.deepcopy(data), ruleset_id=ruleset_id)
    return g.overall_result == "FAIL"


def verification_text(fails: list[dict], extra: dict[str, list[str]] | None = None) -> str:
    """The verification task. `extra` adds boundaries per criterion — the approved QA lessons
    (qa/lessons.py), on top of the hand-written BOUNDARIES."""
    items = []
    for c in fails:
        cid = c["id"]
        lines = [f'- {cid} — the first pass wrote: "{str(c.get("ev") or "").strip()[:300]}"']
        for b in [*BOUNDARIES.get(cid, []), *(extra or {}).get(cid, [])]:
            lines.append(f"    NOT A FAIL: {b}")
        items.append("\n".join(lines))
    return _INSTRUCTIONS.format(items="\n".join(items))


class FailVerifier:
    def __init__(self, mode: str | None = None) -> None:
        from intercom_summary.settings import settings

        self.mode = mode or settings.qa_fail_verifier
        if self.mode not in VERIFIER_MODES:
            raise ValueError(f"fail verifier mode must be one of {VERIFIER_MODES} (got {self.mode!r})")
        self._learned: dict[str, dict[str, list[str]]] = {}

    def learned(self, ruleset_id: str) -> dict[str, list[str]]:
        """Approved NOT-A-FAIL QA lessons for this ruleset, read once per verifier (one grading
        run). An unreadable table means none — the verifier still has its own boundaries."""
        if ruleset_id not in self._learned:
            try:
                from intercom_summary.storage.lessons_store import LessonsStore

                store = LessonsStore()
                try:
                    self._learned[ruleset_id] = store.approved_boundaries(ruleset_id)
                finally:
                    store.close()
            except Exception as exc:  # noqa: BLE001
                log.warning("Fail verifier: QA lessons unavailable: %s", exc)
                self._learned[ruleset_id] = {}
        return self._learned[ruleset_id]

    def verify(self, grader, conversation, messages: list[dict], data: dict[str, Any],
               meter) -> dict:
        """Check the FAILs of a raw grade heading for FAIL. Changes `data` in place in `on`
        mode; always returns the record stored on the grade ({} when nothing was checked)."""
        if self.mode == "off":
            return {}
        fails = failed_criteria(data)
        if not fails or not would_fail(conversation, data, grader.ruleset_id):
            return {}
        record: dict[str, Any] = {"mode": self.mode, "checked": [c["id"] for c in fails]}
        try:
            answers = self._ask(grader, conversation, messages, fails, meter)
        except Exception as exc:  # noqa: BLE001 — a verifier failure never fails the grade
            log.warning("Fail verifier skipped for %s: %s", conversation.id, exc)
            record["error"] = str(exc)
            return record
        record["answers"] = answers
        critical = set(grader.critical_ids)
        overturned, disputed_critical = [], []
        for c in fails:
            ans = answers.get(c["id"])
            if not ans or ans.get("verdict") != NOT_PROVEN:
                continue
            if c["id"] in critical:
                disputed_critical.append(c["id"])
                continue
            overturned.append(c["id"])
            if self.mode == "on":
                c["v"] = "pass"
                c["ev"] = ""
                data["flags"] = [*(data.get("flags") or []),
                                 f"verifier:{c['id']} fail → pass — {ans.get('reason', '')[:160]}"]
        record["overturned"] = overturned
        if disputed_critical:
            record["disputed_critical"] = disputed_critical
        return record

    def _ask(self, grader, conversation, messages: list[dict], fails: list[dict], meter) -> dict:
        # Same system blocks (cached), same transcript, then the verification task.
        transcript = messages[-1]["content"] if messages else ""
        task = verification_text(fails, self.learned(grader.ruleset_id))
        params = grader.request_params(conversation, messages=[{
            "role": "user", "content": f"{transcript}\n\n{task}"}])
        params["output_config"]["format"] = {"type": "json_schema", "schema": VERIFIER_SCHEMA}
        params["max_tokens"] = 8_000
        resp = grader.client.beta.messages.create(**params)
        meter.add(getattr(resp, "usage", None), getattr(resp, "model", None) or params["model"])
        if resp.stop_reason in ("refusal", "max_tokens"):
            raise RuntimeError(f"verifier stop_reason={resp.stop_reason}")
        text = "".join(getattr(b, "text", "") for b in resp.content
                       if getattr(b, "type", None) == "text")
        out = json.loads(text)
        wanted = {c["id"] for c in fails}
        return {a["id"]: {k: a.get(k, "") for k in ("verdict", "quote", "reason")}
                for a in out.get("checks") or [] if a.get("id") in wanted}
