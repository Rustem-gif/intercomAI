"""QA grader backed by the Claude API (Claude Sonnet 5.5 by default — `QA_MODEL`).

It grades exactly like the Ollama grader so the two backends are interchangeable:

  • the same per-ruleset system prompt (rulesets.get_ruleset(...).prompt_text),
  • the same transcript block (TIMING / closed-by / tags / CSAT headers + messages),
  • the same JSON shape (rulesets.output_schema_for), enforced here with structured outputs,
  • the same verdict guards, validity check and score computation (from_model_output),
  • the same `rules_version` stamp (the prompt-text hash), so switching backend does not
    mark existing grades stale.

The model never supplies the score: it reports verdicts and evidence, and the ruleset's
own scoring model (flat or v4.1 gated) turns them into a number in code.

The system prompt is marked cacheable — it is identical for every conversation graded
against a ruleset, so a batch pays to process it once.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from anthropic import Anthropic

from intercom_summary.intercom.models import Conversation
from intercom_summary.logging_setup import get_logger
from intercom_summary.qa.grading_common import GradeParseError, is_valid_grade, trim_transcript
from intercom_summary.qa.prompt import extract_grade_dict, transcript_block
from intercom_summary.qa.rulesets import get_ruleset, strict_schema, validate_ruleset
from intercom_summary.qa.schema import ConversationGrade
from intercom_summary.qa.verdict_guard import apply_guards
from intercom_summary.settings import settings

log = get_logger(__name__)

# Ask for a usable grade at most this many times before skipping the conversation.
_MAX_ATTEMPTS = 2
# Room for adaptive thinking plus the JSON grade (a v4.1 grade is ~1.5k tokens).
_MAX_TOKENS = 16_000
# On a policy decline, the API re-runs the request on a fallback model it picks by refusal
# category. A refused grade would otherwise just be skipped.
_FALLBACK_BETA = "server-side-fallback-2026-07-01"


class Grader:
    def __init__(
        self,
        ruleset_id: str | None = None,
        model: str | None = None,
        client: Anthropic | None = None,
    ) -> None:
        self._model = model or settings.qa_model
        self._client = client or Anthropic(
            api_key=settings.anthropic_api_key, timeout=180.0, max_retries=4
        )
        # Resolved once per batch, like OllamaGrader: admin edits take effect on the next run.
        self._ruleset = get_ruleset(ruleset_id)
        self._schema = strict_schema(self._ruleset)
        # Token usage of the most recent request — for cost reporting (scripts/dry_run_grades.py).
        self.last_usage = None
        for warning in validate_ruleset(self._ruleset):
            log.warning("Ruleset '%s' drift: %s", self._ruleset.id, warning)

    @property
    def ruleset_id(self) -> str:
        return self._ruleset.id

    @property
    def rules_version(self) -> str:
        # The prompt-text hash — the same stamp OllamaGrader uses, so a grade made by either
        # backend under the same prompt counts as current for the other.
        return self._ruleset.version

    def _request(self, transcript: str):
        kwargs: dict = {}
        if settings.qa_refusal_fallback:
            kwargs["betas"] = [_FALLBACK_BETA]
            kwargs["extra_body"] = {"fallbacks": "default"}
        return self._client.beta.messages.create(
            model=self._model,
            max_tokens=_MAX_TOKENS,
            system=[{
                "type": "text",
                "text": self._ruleset.prompt_text,
                "cache_control": {"type": "ephemeral"},
            }],
            messages=[{"role": "user", "content": transcript}],
            output_config={
                "effort": settings.qa_effort,
                "format": {"type": "json_schema", "schema": self._schema},
            },
            **kwargs,
        )

    @staticmethod
    def _parse(resp) -> dict | None:
        text = "".join(
            getattr(b, "text", "") for b in resp.content if getattr(b, "type", None) == "text"
        )
        if not text.strip():
            return None
        try:
            return json.loads(text)
        except ValueError:
            try:
                return extract_grade_dict(text)
            except ValueError:
                return None

    def grade(self, conversation: Conversation) -> ConversationGrade:
        transcript = trim_transcript(transcript_block(conversation))

        data: dict | None = None
        served_by = self._model
        for attempt in range(_MAX_ATTEMPTS):
            log.info(
                "Grading %s via %s [%s ruleset %s] (attempt %d/%d)",
                conversation.id, self._model, self._ruleset.id, self._ruleset.version,
                attempt + 1, _MAX_ATTEMPTS,
            )
            resp = self._request(transcript)
            usage = self.last_usage = getattr(resp, "usage", None)
            if usage is not None:
                log.debug(
                    "Usage %s: in=%s cache_read=%s cache_write=%s out=%s",
                    conversation.id,
                    getattr(usage, "input_tokens", None),
                    getattr(usage, "cache_read_input_tokens", None),
                    getattr(usage, "cache_creation_input_tokens", None),
                    getattr(usage, "output_tokens", None),
                )
            if resp.stop_reason == "refusal":
                # The fallback chain (if enabled) has already been tried inside this call.
                details = getattr(resp, "stop_details", None)
                category = getattr(details, "category", None) if details else None
                raise GradeParseError(
                    f"Model declined to grade conversation {conversation.id} "
                    f"(refusal category: {category or 'unknown'})"
                )
            candidate = None if resp.stop_reason == "max_tokens" else self._parse(resp)
            if candidate is not None and is_valid_grade(candidate):
                data = candidate
                served_by = getattr(resp, "model", None) or self._model
                break
            log.warning(
                "Grade for %s was empty/unusable on attempt %d/%d (stop_reason=%s) — %s",
                conversation.id, attempt + 1, _MAX_ATTEMPTS, resp.stop_reason,
                "retrying" if attempt + 1 < _MAX_ATTEMPTS else "giving up",
            )

        if data is None:
            # Caller (review_and_store) skips this conversation rather than saving a 0/100.
            raise GradeParseError(
                f"No usable grade for conversation {conversation.id} after {_MAX_ATTEMPTS} attempts"
            )

        # Overturn verdicts the transcript contradicts BEFORE the grade is built —
        # from_model_output computes the score from these same criteria entries.
        if guard_flags := apply_guards(conversation, data):
            data["flags"] = [*(data.get("flags") or []), *guard_flags]

        grade = ConversationGrade.from_model_output(
            conversation.id, conversation.assignee_name, data, ruleset_id=self._ruleset.id
        )
        grade.agent_email = conversation.assignee.email if conversation.assignee else ""
        grade.rules_version = self._ruleset.version
        grade.ruleset_id = self._ruleset.id
        # The model that actually answered — differs from QA_MODEL when a fallback served it.
        grade.model = served_by
        grade.graded_at = datetime.now(timezone.utc).isoformat()
        log.info(
            "Graded %s via %s: %d/100 (%s)",
            conversation.id, served_by, grade.overall_score, grade.overall_result or "no result",
        )
        return grade
