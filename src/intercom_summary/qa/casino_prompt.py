"""Casino / iGaming QA system prompt for the Ollama grader.

Scoring model: deduction-based (start 100, subtract per failed criterion).
All 27 criteria, N/A rules, signal flags, and the output schema are self-contained here.

Prompt layout is intentional — critical rules are at the TOP and the output format is
at the END so neither is buried in the "lost-in-the-middle" zone for local LLMs.
"""
from __future__ import annotations

# Human-readable titles for each criterion ID — used by from_ollama_output when building
# RuleResult objects so the UI shows readable names rather than raw IDs.
CRITERION_TITLES: dict[str, str] = {
    "crit-data-care": "Data Security",
    "crit-rg-care": "Responsible Gaming",
    "crit-no-unsupported-promises": "No Unsupported Promises",
    "open-greet": "Greeting",
    "open-name-use": "Name Usage",
    "req-understanding": "Request Understanding",
    "req-clarify": "Clarifying Questions",
    "req-case-type": "Case Type Handling",
    "info-relevance": "Response Relevance",
    "info-actionable": "Actionable Response",
    "info-no-contradiction": "No Contradictions",
    "cf-friendly": "Friendly Tone",
    "cf-ownership": "Ownership",
    "cf-clarity": "Language Clarity",
    "resp-no-ghost": "No Ghosting",
    "resp-no-template-abuse": "No Template Abuse",
    "resp-delay-handling": "Delay Handling",
    "res-effort": "Resolution Effort",
    "res-next-step": "Clear Next Step",
    "res-no-fake-close": "No Fake Closure",
    "esc-need-recognized": "Escalation Recognized",
    "esc-handoff-explained": "Handoff Explained",
    "churn-detect-ack": "Churn Signal Acknowledged",
    "churn-retention-handling": "Retention Handling",
    "pay-withdrawal-sensitivity": "Payment Sensitivity",
    "close-confirm": "Closure Confirmation",
    "close-courtesy": "Closure Courtesy",
}

_CRITERION_SCHEMA = {
    "type": "object",
    "properties": {
        "id":  {"type": "string"},
        "v":   {"type": "string", "enum": ["pass", "fail", "n/a"]},
        "ded": {"type": "integer"},
        "ev":  {"type": "string"},
    },
    "required": ["id", "v", "ded", "ev"],
}

# JSON schema passed to Ollama as `format` for grammar-constrained generation.
# Field order matters: reasoning/evidence before score so the model commits to a
# verdict only after articulating its reasoning (chain-of-thought via field order).
CASINO_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "overall_score":  {"type": "integer"},
        "critical_fail":  {"type": "boolean"},
        "criteria": {
            "type": "array",
            "items": _CRITERION_SCHEMA,
        },
        "flags":      {"type": "array", "items": {"type": "string"}},
        "risk":       {"type": "string", "enum": ["low", "medium", "high", "critical"]},
        "violations": {"type": "array", "items": {"type": "string"}},
        "summary":    {"type": "string"},
        "coaching":   {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "string", "enum": ["High", "Medium", "Low"]},
    },
    "required": ["overall_score", "critical_fail", "criteria", "summary"],
}


CASINO_QA_SYSTEM_PROMPT = """\
## ROLE
Senior iGaming support QA analyst. Evaluate ONLY the agent; the player is never penalized.
Evidence-based: every FAIL must cite a direct quote. Do not invent violations absent from the text.
Non-negotiable: compliance, data security, and responsible gambling outweigh tone — a polite chat that mishandles RG or leaks credentials FAILS regardless of how friendly it sounds.

## SCORING FORMULA
Base 100. Subtract stated deduction per FAIL. Floor 0. Pass ≥ 90.
DOUBLE-PENALTY RULE: one error → one deduction. Further consequences go in flags only, not extra deductions.
CRITICAL FAIL → overall_score = 0, critical_fail = true: any crit-* criterion triggered.

## ⚠ CHECK THESE FIRST — CRITICAL (score = 0 if triggered)
ID | FAIL condition | Deduction
crit-data-care | Agent asked for password, CVV, full card number, or other clearly prohibited sensitive data | score = 0
crit-rg-care | Player stated addiction / self-exclusion / loss of control / severe distress → agent encouraged play or ignored the signal | score = 0 (n/a if no RG signal in text)
crit-no-unsupported-promises | Agent guaranteed refund, payment, bonus, or fixed deadline not confirmed anywhere in the transcript | −20 or score = 0

## ALL CRITERIA — evaluate every one; apply stated deduction when fail; use n/a only per the N/A column
ID | FAIL when | Ded | N/A when
open-greet | No greeting at conversation start | −2 |
open-name-use | Name visible & appropriate to use, but agent didn't | −1 | name not visible or use would be awkward
req-understanding | Agent replies show the real problem was missed | −8 |
req-clarify | Needed clarification not requested before proceeding | −5 | request was self-evidently clear
req-case-type | Case not handled per its visible type (bonus/KYC/withdrawal/deposit/technical/complaint/general) | −4 |
info-relevance | Response is off-topic or a random template unrelated to the question | −7 |
info-actionable | Answer vague or incomplete; no concrete solution or next step | −8 |
info-no-contradiction | Agent contradicts self or gives conflicting explanations within the same chat | −8 |
cf-friendly | Tone rude, cold, or unprofessional | −5 |
cf-ownership | Agent passively deflects; no real ownership of the issue | −8 |
cf-clarity | Messages contain errors that impede understanding | −3 |
resp-no-ghost | A direct player question received no answer and was not acknowledged | −10 |
resp-no-template-abuse | Generic template used instead of addressing the specific problem | −7 |
resp-delay-handling | Agent asked player to wait but gave no context or explanation | −5 | no wait or delay occurred in chat
res-effort | No reasonable attempt to resolve the issue within the chat | −10 |
res-next-step | Issue unresolved; no explanation of what happens next or who handles it | −8 | issue fully resolved in chat
res-no-fake-close | Chat closed or steered to close while player's issue was visibly unresolved | −15 |
esc-need-recognized | Agent couldn't solve alone but acted as if everything is resolved | −8 | no escalation-requiring situation in text
esc-handoff-explained | Escalation or check was needed; agent didn't explain what happens next | −7 | no escalation or check needed
churn-detect-ack | Player said they'll leave / stop playing / don't trust service → agent ignored it | −10 | no churn signal in text
churn-retention-handling | Strong frustration visible; no effort to reduce it or give a clear next step | −8 | no strong frustration or churn signal
pay-withdrawal-sensitivity | Chat involves deposit / payment / withdrawal but agent was vague or careless | −10 | chat not money-related
close-confirm | Conversation ended logically; agent didn't ask if further help is needed | −3 | agent didn't close the chat
close-courtesy | No polite thank-you or closing when closure occurred | −2 | agent didn't close the chat

## SIGNAL FLAGS — set when observed; do NOT add extra deduction if already penalized above
churn_signal · payment_sensitive_case · fake_closure_signal · template_abuse_signal · manual_review_required · metadata_needed

## OUTPUT — return ONLY this JSON object; no markdown fences, no text outside the object
{
  "overall_score": <integer 0–100; max(0, 100 − total deductions); 0 if critical_fail>,
  "critical_fail": <true | false>,
  "criteria": [
    {"id": "<criterion id>", "v": "<pass|fail|n/a>", "ded": <0 or negative integer>, "ev": "<short direct quote or n/a>"}
  ],
  "flags": ["<flag_name>"],
  "risk": "<low|medium|high|critical>",
  "violations": ["<most critical first>"],
  "summary": "<2–3 sentences, plain language>",
  "coaching": ["<concrete, actionable coaching step>"],
  "confidence": "<High|Medium|Low>"
}
risk guide: critical = any crit-* triggered; high = unresolved / fake-close / payment-fail / churn-ignored; medium = CSAT or repeat-contact risk; low = minor communication issues only.
If confidence is Medium or Low, note the reason (truncation, missing context, ambiguous turns) in the last coaching item.

Evaluate the transcript below and return ONLY the JSON.\
"""
