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
against a ruleset, so a run pays to process it once. `request_params` is the single source of
the request shape, shared by live grading and the Batch API (qa/batch.py); `finish` turns a
batch result into a grade exactly as `grade` does for a live response.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from anthropic import Anthropic

from intercom_summary.intercom.models import Conversation
from intercom_summary.logging_setup import get_logger
from intercom_summary.qa import knowledge_base
from intercom_summary.qa.grading_common import GradeParseError, is_valid_grade, trim_transcript
from intercom_summary.qa.jev_verifier import (
    JevVerifier,
    dispute_text,
    flag_reason,
    flaggable,
    reconcile_targets,
)
from intercom_summary.qa.pricing import UsageMeter
from intercom_summary.qa.prompt import extract_grade_dict, transcript_block
from intercom_summary.qa.rulesets import SCORING_GATED, get_ruleset, strict_schema, validate_ruleset
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
        jev=None,
        knowledge: dict | None = None,
    ) -> None:
        self._model = model or settings.qa_model
        self._client = client or Anthropic(
            api_key=settings.anthropic_api_key, timeout=180.0, max_retries=4
        )
        # Resolved once per batch, like OllamaGrader: admin edits take effect on the next run.
        self._ruleset = get_ruleset(ruleset_id)
        self._schema = strict_schema(self._ruleset)
        # Raw `usage` of the most recent response. Per-grade totals live on grade.usage.
        self.last_usage = None
        for warning in validate_ruleset(self._ruleset):
            log.warning("Ruleset '%s' drift: %s", self._ruleset.id, warning)
        # Jev checks the v4.1 verdicts it has definitions for, so only gated rulesets get it.
        # Pass jev=False to switch it off, or a JevVerifier to inject one (tests).
        if jev is None:
            jev = (
                self._ruleset.scoring_model == SCORING_GATED
                and settings.jev_mode != "off" and bool(settings.jev_api_key)
            )
            try:
                jev = JevVerifier() if jev else None
            except ImportError:  # typesafe-sdk missing from this interpreter — grade without
                log.warning("JEV_MODE=%s but typesafe-sdk is not installed — Jev check skipped",
                            settings.jev_mode)
                jev = None
        self._jev = jev or None
        # The brand's Help Center — the approved KB/T&C v4.1 judges accuracy against. Loaded
        # once per batch (refreshed if stale); a brand without one gets no KB block. Pass
        # knowledge={} to grade without it, or a {brand: KnowledgeBase} map to inject one.
        if knowledge is None:
            use = self._ruleset.scoring_model == SCORING_GATED and settings.kb_enabled
            knowledge = knowledge_base.load_all() if use else {}
        self._kb: dict = knowledge

    @property
    def client(self) -> Anthropic:
        return self._client

    @property
    def ruleset_id(self) -> str:
        return self._ruleset.id

    @property
    def rules_version(self) -> str:
        # The prompt-text hash — the same stamp OllamaGrader uses, so a grade made by either
        # backend under the same prompt counts as current for the other.
        return self._ruleset.version

    def messages_for(self, conversation: Conversation) -> list[dict]:
        return [{"role": "user", "content": trim_transcript(transcript_block(conversation))}]

    def knowledge_for(self, conversation: Conversation):
        return self._kb.get(conversation.brand or "")

    def request_params(self, conversation: Conversation, messages: list[dict] | None = None,
                       effort: str | None = None, batch: bool = False) -> dict:
        """The Messages API body for one grading call. Everything above `messages` is
        identical for every conversation of a ruleset and brand — that is the cached prefix,
        and tests/test_token_optimisation.py fails if anything per-conversation leaks into it.

        Two cached blocks: the ruleset prompt (shared by every brand) and then the brand's
        knowledge base, so a run mixing brands still reads the prompt from one cache entry."""
        # A batch can take longer than 5 minutes to work through, so its entries get the
        # 1-hour TTL; a live run's requests are seconds apart and keep 5 minutes warm.
        cache = {"type": "ephemeral", "ttl": "1h"} if batch else {"type": "ephemeral"}
        system = [{"type": "text", "text": self._ruleset.prompt_text, "cache_control": cache}]
        if kb := self.knowledge_for(conversation):
            system.append({"type": "text", "text": kb.text, "cache_control": dict(cache)})
        return {
            "model": self._model,
            "max_tokens": _MAX_TOKENS,
            "system": system,
            "messages": messages if messages is not None else self.messages_for(conversation),
            "output_config": {
                "effort": effort or settings.qa_effort,
                "format": {"type": "json_schema", "schema": self._schema},
            },
        }

    def _request(self, conversation: Conversation, messages: list[dict],
                 effort: str | None = None, batch_shape: bool = False):
        """One live call. `batch_shape` sends exactly what a batch request would (1-hour cache
        TTL, no fallback beta), so the cache entry it writes is the one a batch then reads."""
        kwargs: dict = {}
        if settings.qa_refusal_fallback and not batch_shape:
            kwargs["betas"] = [_FALLBACK_BETA]
            kwargs["extra_body"] = {"fallbacks": "default"}
        return self._client.beta.messages.create(
            **self.request_params(conversation, messages, effort, batch=batch_shape), **kwargs)

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

    def _usable(self, conversation: Conversation, resp) -> dict | None:
        """The grade JSON in a response, None when it is unusable, GradeParseError on a refusal."""
        if resp.stop_reason == "refusal":
            # The fallback chain (if enabled) has already been tried inside this call.
            details = getattr(resp, "stop_details", None)
            category = getattr(details, "category", None) if details else None
            raise GradeParseError(
                f"Model declined to grade conversation {conversation.id} "
                f"(refusal category: {category or 'unknown'})"
            )
        candidate = None if resp.stop_reason == "max_tokens" else self._parse(resp)
        return candidate if candidate is not None and is_valid_grade(candidate) else None

    def _meter(self, meter: UsageMeter, resp, conversation: Conversation, batch: bool = False) -> None:
        usage = self.last_usage = getattr(resp, "usage", None)
        meter.add(usage, getattr(resp, "model", None) or self._model, batch=batch)
        if usage is not None:
            log.debug(
                "Usage %s: in=%s cache_read=%s cache_write=%s out=%s%s",
                conversation.id,
                getattr(usage, "input_tokens", None),
                getattr(usage, "cache_read_input_tokens", None),
                getattr(usage, "cache_creation_input_tokens", None),
                getattr(usage, "output_tokens", None),
                " (batch)" if batch else "",
            )

    def _ask(self, conversation: Conversation, messages: list[dict],
             effort: str | None = None, meter: UsageMeter | None = None,
             batch_shape: bool = False) -> tuple[dict, str]:
        """A usable grade JSON and the model that produced it, or GradeParseError."""
        meter = meter if meter is not None else UsageMeter()
        for attempt in range(_MAX_ATTEMPTS):
            log.info(
                "Grading %s via %s [%s ruleset %s] (attempt %d/%d)",
                conversation.id, self._model, self._ruleset.id, self._ruleset.version,
                attempt + 1, _MAX_ATTEMPTS,
            )
            resp = self._request(conversation, messages, effort, batch_shape)
            self._meter(meter, resp, conversation)
            candidate = self._usable(conversation, resp)
            if candidate is not None:
                return candidate, getattr(resp, "model", None) or self._model
            log.warning(
                "Grade for %s was empty/unusable on attempt %d/%d (stop_reason=%s) — %s",
                conversation.id, attempt + 1, _MAX_ATTEMPTS, resp.stop_reason,
                "retrying" if attempt + 1 < _MAX_ATTEMPTS else "giving up",
            )
        # Caller (review_and_store) skips this conversation rather than saving a 0/100.
        raise GradeParseError(
            f"No usable grade for conversation {conversation.id} after {_MAX_ATTEMPTS} attempts"
        )

    @staticmethod
    def _guard(conversation: Conversation, data: dict) -> None:
        # Overturn verdicts the transcript contradicts BEFORE the grade is built —
        # from_model_output computes the score from these same criteria entries.
        if guard_flags := apply_guards(conversation, data):
            data["flags"] = [*(data.get("flags") or []), *guard_flags]

    def _reconcile(self, conversation: Conversation, messages: list[dict], data: dict,
                   served_by: str, jev: dict, meter: UsageMeter) -> tuple[dict, str, dict]:
        """Ask the grader once to re-examine the criteria Jev disputes, then check again."""
        targets = reconcile_targets(jev)
        follow_up = [
            *messages,
            {"role": "assistant", "content": json.dumps(data, ensure_ascii=False)},
            {"role": "user", "content": dispute_text(targets)},
        ]
        effort = "high" if settings.qa_effort in ("low", "medium") else settings.qa_effort
        try:
            revised, revised_by = self._ask(conversation, follow_up, effort, meter)
        except GradeParseError as exc:
            jev["reconcile"] = {"error": str(exc)}
            return data, served_by, jev
        self._guard(conversation, revised)
        before = {c.get("id"): c.get("v") for c in data.get("criteria") or []}
        after = {c.get("id"): c.get("v") for c in revised.get("criteria") or []}
        checked = self._jev.verify(conversation, revised, self._kb_text(conversation))
        checked["reconcile"] = {
            "disputed": targets,
            "changed": {cid: [before.get(cid), v] for cid, v in after.items()
                        if before.get(cid) != v},
        }
        return revised, revised_by, checked

    def grade(self, conversation: Conversation, meter: UsageMeter | None = None,
              batch_shape: bool = False) -> ConversationGrade:
        """`batch_shape=True` grades live but in the batch's request shape — used to warm the
        cache a batch is about to read (qa/batch.py `warm_up_set`)."""
        meter = meter if meter is not None else UsageMeter()
        messages = self.messages_for(conversation)
        data, served_by = self._ask(conversation, messages, meter=meter, batch_shape=batch_shape)
        return self._complete(conversation, messages, data, served_by, meter)

    def finish(self, conversation: Conversation, resp, meter: UsageMeter | None = None) -> ConversationGrade:
        """A grade from a Batch API result — the same path a live response takes. Raises
        GradeParseError when the result is unusable; the caller re-grades it live."""
        meter = meter if meter is not None else UsageMeter()
        self._meter(meter, resp, conversation, batch=True)
        data = self._usable(conversation, resp)
        if data is None:
            raise GradeParseError(
                f"Unusable batch result for conversation {conversation.id} "
                f"(stop_reason={resp.stop_reason})"
            )
        served_by = getattr(resp, "model", None) or self._model
        return self._complete(conversation, self.messages_for(conversation), data, served_by, meter)

    def _kb_text(self, conversation: Conversation) -> str:
        kb = self.knowledge_for(conversation)
        return kb.text if kb else ""

    def _complete(self, conversation: Conversation, messages: list[dict], data: dict,
                  served_by: str, meter: UsageMeter) -> ConversationGrade:
        self._guard(conversation, data)

        jev: dict | None = None
        if self._jev is not None:
            jev = self._jev.verify(conversation, data, self._kb_text(conversation))
            if self._jev.mode == "reconcile" and reconcile_targets(jev):
                data, served_by, jev = self._reconcile(conversation, messages, data,
                                                       served_by, jev, meter)

        grade = ConversationGrade.from_model_output(
            conversation.id, conversation.assignee_name, data, ruleset_id=self._ruleset.id
        )
        grade.agent_email = conversation.assignee.email if conversation.assignee else ""
        grade.rules_version = self._ruleset.version
        grade.ruleset_id = self._ruleset.id
        # The model that actually answered — differs from QA_MODEL when a fallback served it.
        grade.model = served_by
        grade.graded_at = datetime.now(timezone.utc).isoformat()
        grade.usage = meter.as_dict()
        kb = self.knowledge_for(conversation)
        grade.kb_version = f"{kb.brand}:{kb.version}" if kb else ""
        if jev is not None:
            grade.jev = jev
            # In shadow mode Jev's findings are recorded and change nothing.
            if self._jev.mode in ("flag", "reconcile") and (found := flaggable(jev)):
                grade.manual_review_needed = True
                grade.manual_review_reason = "; ".join(
                    r for r in (grade.manual_review_reason, flag_reason(found)) if r
                )
        log.info(
            "Graded %s via %s: %d/100 (%s) $%.4f%s",
            conversation.id, served_by, grade.overall_score, grade.overall_result or "no result",
            grade.usage["cost_usd"],
            f" — Jev: {len(jev['findings'])} finding(s)" if jev and jev.get("findings") else "",
        )
        return grade
