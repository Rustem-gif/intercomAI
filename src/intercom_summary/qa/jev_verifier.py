"""Jev in the middle of scoring — a cheap, calibrated second opinion on the verdicts that
move a v4.1 score the most. Design and measurement plan: docs/jev-integration.md.

    Sonnet (judge)  →  Jev (check)  →  code policy (decide)  →  score_gated()

Jev never grades and never does arithmetic. It answers narrow yes/no and pick-one questions
about the transcript, and code compares those answers with Sonnet's verdicts:

  blacklist.<crit-id>   always   Noul    Did the agent do one of the manual §5.1 blacklisted
                                         things? Independent of Sonnet — Gate 1 is the only
                                         thing that zeroes a chat.
  support.<n>           per fail Noul    Does the transcript really show this Gate 1 / Gate 2
                                         Major failure? (Code first checks that whatever the
                                         grader quoted was really said — evidence_holds.)
  same_episode.<n>      per pair Noul    Are a Major failure and a deduction the same mistake
                                         counted twice (manual §10)?
  rg_signal             always   Noul    Did the player show a gambling-harm signal?
  case_type, risk_flag  always   Choice  Step 0 cross-check.

All questions go in ONE request per chat (Jev evaluates them in parallel over one state).
Cost ≈ 5k input tokens × $0.042/Mtok ≈ $0.0002 per chat.

Modes (JEV_MODE): off · shadow (record only — the default) · flag (disagreements set
manual_review_needed) · reconcile (flag, and first ask Sonnet once to re-examine the disputed
criteria). Jev never changes a score by itself, and a Jev failure never fails a grade.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from intercom_summary.intercom.models import Conversation
from intercom_summary.logging_setup import get_logger
from intercom_summary.qa.grading_common import trim_transcript
from intercom_summary.qa.verdict_guard import evidence_in_conversation
from intercom_summary.settings import settings

log = get_logger(__name__)

JEV_MODES = ("off", "shadow", "flag", "reconcile")

# Thresholds on Jev's probabilities. Starting points, to be tuned on our own data (see the
# measurement plan in docs/jev-integration.md) — not universal constants.
GATE1_MISSED_P = 0.80      # blacklist says yes this strongly while Sonnet passed it
UNSUPPORTED_P = 0.30       # Jev's support for a Sonnet fail is below this
SAME_EPISODE_P = 0.70      # two penalties are one mistake
RG_SIGNAL_P = 0.80         # player clearly signalled gambling harm
STEP0_CONFIDENCE = 0.70    # Jev is sure enough of a different Step 0 answer to mention it

# Findings that send a chat to a QC manager (in flag/reconcile mode). The rest are notes.
FLAG_RULES = frozenset({
    "gate1_missed", "gate1_unsupported", "major_unsupported", "double_count", "rg_signal_missed",
})
# Findings Sonnet is asked to re-examine in reconcile mode.
RECONCILE_RULES = frozenset({
    "gate1_missed", "gate1_unsupported", "major_unsupported", "double_count",
})

_ROLES = ("In `transcript`, lines from AGENT are the support agent, lines from CUSTOMER are the "
          "player. Lines marked (note) are internal notes visible only to the support team.")

# What a FAIL means, per criterion — the manual's own "when FAIL" column (v4.1 §5, §6, §7),
# in English because Jev is strongest in English. Gate 1 entries double as the §5.1 blacklist.
FAIL_DEFINITIONS: dict[str, str] = {
    # Gate 1 — the closed blacklist (§5.1)
    "crit-data-care": (
        "The agent asked the player to send a password or passphrase, a card CVV/CVC code, a "
        "one-time code (from SMS, email or an authenticator app / 2FA), a PIN, or the full "
        "card number. Asking for only the last 4 digits of a card is allowed."),
    "crit-rg-care": (
        "The player signalled gambling harm, loss of control, being unable to stop, or "
        "self-exclusion, and the agent ignored it, offered a bonus or promotion, encouraged "
        "them to keep playing or to deposit, or removed or bypassed a limit set for "
        "responsible-gaming reasons without the confirmation procedure."),
    "crit-pii-leak": (
        "The agent disclosed personal data of a DIFFERENT player (name, email, phone, address, "
        "balance, transaction history) or quoted or retold another player's chat or ticket."),
    "crit-nda-security": (
        "In a message to the player, the agent revealed internal system names, admin-panel "
        "links or access, internal procedures, SOPs or scripts, an employee's personal data, "
        "or details of an unannounced promotion or feature."),
    "crit-policy-violation": (
        "The agent guaranteed a win or hinted that a game result is fixed, manually credited "
        "balance or a bonus outside the allowed tools, knowingly bypassed a mandatory KYC "
        "check, or promised a refund without approved permission."),
    # Gate 2 — Major Outcome Failure (§6.1)
    "resp-no-ghost": (
        "A direct, material question or request from the player was left with no answer, no "
        "action and no escalation by the agent."),
    "res-no-fake-close": (
        "The agent closed the chat, or pushed the player to end it, while the player's problem "
        "was clearly not resolved."),
    "action-escalation-missed": (
        "The agent did not perform an action or escalation they were required to perform. An "
        "internal note saying what was escalated and where counts as performed."),
    "financial-case-abandoned": (
        "A deposit, withdrawal or bonus problem was left without proper handling: no status, no "
        "action taken, no next step and no escalation."),
    "accuracy-material": (
        "The agent gave wrong information that can affect the player's money, a withdrawal or "
        "deposit, wagering, eligibility, KYC or account status, bonus rights, limits, "
        "responsible gaming, or what the player does next."),
    "severe-business-trust": (
        "The agent stated an invented operation status, gave a financial deadline that was not "
        "confirmed, or promised a refund or bonus beyond their authority without confirmation "
        "from the system or policy."),
    "churn-rg-signal-ignored": (
        "The player clearly said they would leave or stop playing, or showed a gambling-harm "
        "signal, and the agent ignored it completely: no acknowledgement and no response."),
    # Deductions that can double-count a Major (§7 note, §10)
    "ownership-effort": (
        "The agent did not do everything available to them, made the player repeat information "
        "already given, or sent the player to find another department themselves when the "
        "process required an internal escalation."),
    "info-completeness": (
        "The answer was incomplete, the next step was not specific enough, or a secondary part "
        "of the player's request was skipped."),
    "accuracy-minor": (
        "The agent made a small factual mistake that does not affect money, the player's "
        "actions, responsible gaming or the outcome."),
}

GATE1 = ("crit-data-care", "crit-rg-care", "crit-pii-leak", "crit-nda-security",
         "crit-policy-violation")
MAJOR = ("resp-no-ghost", "res-no-fake-close", "action-escalation-missed",
         "financial-case-abandoned", "accuracy-material", "severe-business-trust",
         "churn-rg-signal-ignored")

# Failures of OMISSION — something the agent did not do. Their evidence is a description of
# the gap ("the withdrawal question was never answered"), because an absence cannot be quoted,
# so only quotes the grader chose to include are checked against the conversation.
OMISSION = frozenset({
    "resp-no-ghost", "res-no-fake-close", "action-escalation-missed",
    "financial-case-abandoned", "churn-rg-signal-ignored",
})

# Text the grader put in quotes: "…", “…”, «…», „…“, or '…' that opens after a space (so the
# apostrophe in "player's" does not start a quote). Ellipses inside a quote mark cuts.
_QUOTED = re.compile(
    r'["“”«»„]([^"“”«»„]{8,}?)["“”«»„]'
    r"|(?:(?<=\s)|^)['‘]([^'‘’]{8,}?)['’](?=[\s.,;:!?)]|$)"
)
_MIN_FRAGMENT = 12


def quoted_fragments(evidence: str) -> list[str]:
    out: list[str] = []
    for m in _QUOTED.finditer(evidence or ""):
        for frag in re.split(r"\.\.\.|…|\[\.\.\.\]", m.group(1) or m.group(2) or ""):
            frag = frag.strip(" .,;:")
            if len(frag) >= _MIN_FRAGMENT:
                out.append(frag)
    return out


def evidence_holds(criterion: str, evidence: str, conversation: Conversation) -> bool:
    """Code's own check, before Jev: does the cited evidence come from this conversation?

    Every quoted fragment must really have been said. Without quotes, a commission failure
    (something the agent said or did) must cite real text; an omission failure may describe
    the gap, and is left to Jev's judgement.
    """
    fragments = quoted_fragments(evidence)
    if fragments:
        return all(evidence_in_conversation(f, conversation) for f in fragments)
    if criterion in OMISSION:
        return True
    return evidence_in_conversation(evidence, conversation)


# Only pairs where counting twice changes the score: a Major caps the score flatly, so two
# Majors on one mistake cost nothing extra — but a Major plus a deduction does (§7 note: "if
# the passive avoidance is the same episode that already failed Gate 2, no extra penalty").
DOUBLE_COUNT_PAIRS: tuple[tuple[str, str], ...] = (
    *((m, "ownership-effort") for m in ("resp-no-ghost", "action-escalation-missed",
                                         "financial-case-abandoned", "res-no-fake-close")),
    ("accuracy-minor", "info-completeness"),
)

CASE_TYPES = {
    "Deposit": "Putting money into the account: a deposit that failed, is pending, missing or "
               "was declined.",
    "Withdrawal": "Taking money out: a withdrawal request, its status, delay, rejection or limits.",
    "Bonus": "Bonuses, free spins, promotions, wagering requirements or bonus terms.",
    "KYC": "Identity or document verification: documents, verification status, source of funds.",
    "Account": "Account access or settings: login, password reset, profile details, limits, "
               "closing or reopening the account.",
    "Game": "A specific game: its rules, a round result, a malfunction or a disputed spin.",
    "Technical": "The site or app not working: errors, loading or device problems not tied to "
                 "one game.",
    "Complaint": "The player's main purpose is to complain about the casino or the service.",
    "Other": "Anything else.",
}
RISK_FLAGS = {
    "None": "No particular risk.",
    "Financial": "The player's money is at stake: a deposit, withdrawal or bonus balance.",
    "RG": "Signs of gambling harm, loss of control, or self-exclusion.",
    "Security": "Account security, personal data, or possible fraud.",
    "Churn": "The player threatens to leave because of the service, trust or terms.",
}


# ── questions ──────────────────────────────────────────────────────────────────────────
def build_request(transcript: str, criteria: list[dict]) -> tuple[dict, dict, dict]:
    """(state, questions, plan). `plan` maps question ids back to what code does with them."""
    verdicts = {c.get("id"): c for c in criteria if isinstance(c, dict)}
    graded: list[dict] = []
    questions: dict[str, dict] = {}
    plan: dict[str, Any] = {"support": {}, "same_episode": {}}

    for cid in GATE1:
        questions[f"blacklist.{cid}"] = {
            "type": "noul",
            "instructions": f"{_ROLES} Did the agent do the following? {FAIL_DEFINITIONS[cid]}",
            "criteria": {
                "true": "Yes — an AGENT message clearly does this.",
                "false": "No — no AGENT message does this (a player volunteering such data "
                         "unprompted, or an internal note, does not count).",
            },
        }

    def graded_index(cid: str) -> int:
        for i, g in enumerate(graded):
            if g["criterion"] == cid:
                return i
        graded.append({"criterion": cid, "definition": FAIL_DEFINITIONS[cid],
                       "evidence": str(verdicts[cid].get("ev", ""))})
        return len(graded) - 1

    for cid in (*GATE1, *MAJOR):
        if verdicts.get(cid, {}).get("v") != "fail":
            continue
        i = graded_index(cid)
        questions[f"support.{i}"] = {
            "type": "noul",
            "instructions": (
                f"{_ROLES} Does `transcript` show the failure described in "
                f"`graded[{i}].definition`? `graded[{i}].evidence` is the line a grader cited "
                "as proof — judge from the transcript itself, not from the grader's choice."),
            "criteria": {
                "true": "Yes — the transcript shows the agent failing in exactly this way.",
                "false": "No — the transcript does not show this failure, the cited line does "
                         "not prove it, or the agent actually handled it.",
            },
        }
        plan["support"][f"support.{i}"] = cid

    for n, (a, b) in enumerate(DOUBLE_COUNT_PAIRS):
        if verdicts.get(a, {}).get("v") == "fail" and verdicts.get(b, {}).get("v") == "fail":
            ia, ib = graded_index(a), graded_index(b)
            questions[f"same_episode.{n}"] = {
                "type": "noul",
                "instructions": (
                    f"{_ROLES} A grader penalised the agent twice: for `graded[{ia}]` and for "
                    f"`graded[{ib}]`. Do both penalties describe one and the same mistake by "
                    "the agent (a single episode in the transcript)?"),
                "criteria": {
                    "true": "Yes — it is one mistake, counted under two names.",
                    "false": "No — they are two separate defects at different points or of a "
                             "different kind.",
                },
            }
            plan["same_episode"][f"same_episode.{n}"] = [a, b]

    questions["rg_signal"] = {
        "type": "noul",
        "instructions": (
            f"{_ROLES} Does the CUSTOMER show a sign of gambling harm: loss of control, being "
            "unable to stop, chasing losses, distress about their gambling, or asking for "
            "self-exclusion, a cooling-off period or limits for that reason?"),
        "criteria": {
            "true": "Yes — the player says something like this.",
            "false": "No — nothing the player says points to gambling harm (being upset about "
                     "a delay, a bonus or a lost bet alone is not a harm signal).",
        },
    }
    questions["case_type"] = {
        "type": "choice",
        "instructions": f"{_ROLES} What is the player's main request about?",
        "criteria": CASE_TYPES,
    }
    questions["risk_flag"] = {
        "type": "choice",
        "instructions": f"{_ROLES} Which risk is most important in this conversation? "
                        "Gambling harm outranks everything else.",
        "criteria": RISK_FLAGS,
    }

    state = {"transcript": transcript, "graded": graded}
    return state, questions, plan


# ── policy ─────────────────────────────────────────────────────────────────────────────
def _noul(answers: dict, qid: str) -> float | None:
    a = answers.get(qid)
    return None if a is None else float(a.get("noul"))


def evaluate(answers: dict, plan: dict, criteria: list[dict], data: dict,
             conversation: Conversation) -> list[dict]:
    """Compare Jev's answers with Sonnet's verdicts. Pure code — every threshold is here."""
    verdicts = {c.get("id"): c for c in criteria if isinstance(c, dict)}
    findings: list[dict] = []

    def add(rule: str, criterion: str | None, p: float | None, detail: str) -> None:
        findings.append({"rule": rule, "criterion": criterion,
                         "p": None if p is None else round(p, 3), "detail": detail})

    for cid in GATE1:
        p = _noul(answers, f"blacklist.{cid}")
        if p is None:
            continue
        sonnet_failed = verdicts.get(cid, {}).get("v") == "fail"
        if p >= GATE1_MISSED_P and not sonnet_failed:
            add("gate1_missed", cid, p, "Jev sees a blacklisted action the grader passed")

    for qid, cid in plan["support"].items():
        p = _noul(answers, qid)
        ev = str(verdicts.get(cid, {}).get("ev", ""))
        rule = "gate1_unsupported" if cid in GATE1 else "major_unsupported"
        if not evidence_holds(cid, ev, conversation):
            add(rule, cid, p, "the quoted evidence is not in the conversation")
        elif p is not None and p < UNSUPPORTED_P:
            add(rule, cid, p, "Jev does not find this failure in the transcript")
        elif cid in GATE1:
            # A zeroed chat deserves both signals: the independent blacklist question too.
            bp = _noul(answers, f"blacklist.{cid}")
            if bp is not None and bp < UNSUPPORTED_P:
                add(rule, cid, bp, "the independent blacklist check does not see it")

    for qid, (a, b) in plan["same_episode"].items():
        p = _noul(answers, qid)
        if p is not None and p >= SAME_EPISODE_P:
            add("double_count", f"{a}+{b}", p, "one mistake penalised twice (manual §10)")

    p = _noul(answers, "rg_signal")
    if p is not None and p >= RG_SIGNAL_P and data.get("risk_flag") != "RG":
        add("rg_signal_missed", None, p,
            f"player shows a gambling-harm signal but risk_flag is {data.get('risk_flag')!r}")

    for qid, field in (("case_type", "case_type"), ("risk_flag", "risk_flag")):
        a = answers.get(qid)
        if a and data.get(field) and a.get("choice") != data.get(field) \
                and float(a.get("confidence") or 0) >= STEP0_CONFIDENCE:
            add(f"{field}_mismatch", None, float(a["confidence"]),
                f"Jev: {a['choice']!r}, grader: {data.get(field)!r}")
    return findings


# ── client ─────────────────────────────────────────────────────────────────────────────
def _answers_to_dict(resp) -> dict:
    out: dict[str, dict] = {}
    for qid, a in (resp.answers or {}).items():
        if getattr(a, "type", None) == "noul":
            out[qid] = {"type": "noul", "noul": float(a.noul)}
        elif getattr(a, "type", None) == "choice":
            out[qid] = {"type": "choice", "choice": a.choice,
                        "confidence": float(a.confidence)}
    return out


class JevVerifier:
    def __init__(self, client=None, model: str | None = None, mode: str | None = None) -> None:
        self.mode = mode or settings.jev_mode
        self.model = model or settings.jev_model
        if client is None:
            from typesafe_sdk import TypeSafeClient

            client = TypeSafeClient(api_key=settings.jev_api_key, timeout=30.0)
        self._client = client

    def verify(self, conversation: Conversation, data: dict) -> dict:
        """The `jev` block stored on the grade. Never raises."""
        block: dict[str, Any] = {
            "model": self.model, "mode": self.mode,
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "answers": {}, "findings": [], "usage": {}, "error": "",
        }
        try:
            criteria = data.get("criteria") or []
            transcript = trim_transcript(conversation.transcript_text(include_bots=False))
            state, questions, plan = build_request(transcript, criteria)
            resp = self._client.system_one(state=state, questions=questions, model=self.model)
            answers = _answers_to_dict(resp)
            block["model"] = getattr(resp, "model", None) or self.model
            block["answers"] = answers
            usage = getattr(resp, "usage", None)
            if usage is not None:
                block["usage"] = {"input_tokens": getattr(usage, "input_tokens", 0),
                                  "output_tokens": getattr(usage, "output_tokens", 0)}
            block["findings"] = evaluate(answers, plan, criteria, data, conversation)
        except Exception as exc:  # noqa: BLE001 — Jev is advisory; grading must go on
            log.warning("Jev check failed for %s: %s", conversation.id, exc)
            block["error"] = f"{type(exc).__name__}: {exc}"
        return block


def flaggable(block: dict) -> list[dict]:
    return [f for f in block.get("findings") or [] if f["rule"] in FLAG_RULES]


def reconcile_targets(block: dict) -> list[dict]:
    return [f for f in block.get("findings") or [] if f["rule"] in RECONCILE_RULES]


def flag_reason(findings: list[dict]) -> str:
    return "; ".join(
        f"jev:{f['rule']}" + (f"({f['criterion']})" if f.get("criterion") else "")
        for f in findings
    )


def dispute_text(findings: list[dict]) -> str:
    """The message Sonnet gets in reconcile mode."""
    lines = []
    for f in findings:
        c = f.get("criterion") or ""
        if f["rule"] == "gate1_missed":
            lines.append(f"- {c}: you marked it pass, but a check found an AGENT message that "
                         f"may do this: {FAIL_DEFINITIONS.get(c, '')}")
        elif f["rule"] in ("gate1_unsupported", "major_unsupported"):
            lines.append(f"- {c}: you marked it fail, but {f['detail']}.")
        elif f["rule"] == "double_count":
            lines.append(f"- {c}: these may be the same single mistake penalised twice; per "
                         "manual §10 a single episode is penalised under one criterion only.")
    return (
        "An independent check disputes some of your verdicts:\n" + "\n".join(lines) + "\n\n"
        "Re-examine ONLY these criteria against the transcript. Keep a verdict if you can "
        "support it with a verbatim quote from the conversation; otherwise change it. Leave "
        "every other criterion as it was. Return the complete grade JSON again."
    )
