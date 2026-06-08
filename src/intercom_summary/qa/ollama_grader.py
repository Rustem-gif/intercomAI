"""QA grader backed by a local Ollama server.

Uses the casino/iGaming QA system prompt (casino_prompt.py) which is self-contained —
no external ruleset file is needed. The model is expected to return the rich JSON
defined in that prompt; from_ollama_output() maps it into ConversationGrade.

Cold-start latency (~10–30 s for Qwen 2.5 14B to load) is expected and acceptable.
With OLLAMA_KEEP_ALIVE=0 the model unloads immediately after each response, keeping
idle RAM at ~50 MB (server daemon only).
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

import httpx

from intercom_summary.intercom.models import Conversation
from intercom_summary.logging_setup import get_logger
from intercom_summary.qa.casino_prompt import CASINO_OUTPUT_SCHEMA, load_qa_prompt
from intercom_summary.qa.prompt import extract_grade_dict, transcript_block
from intercom_summary.qa.rules import Ruleset, load_ruleset
from intercom_summary.qa.schema import ConversationGrade
from intercom_summary.settings import settings

log = get_logger(__name__)

# Number of times to ask the model for a usable grade before giving up on a conversation.
_MAX_ATTEMPTS = 2
# Temperatures per attempt. The first is deterministic; a retry at temperature 0 would
# reproduce the same unparseable output, so perturb generation on the retry.
_ATTEMPT_TEMPERATURES = [0.0, 0.5]


class GradeParseError(Exception):
    """The model did not return a usable grade (empty/unparseable) after all retries."""


# Connection-level errors that mean "the server isn't there right now" rather than "this
# conversation can't be graded". When Ollama crashes (e.g. a USB-mounted model store
# hiccups) launchd restarts it within a couple of seconds, so we ride out the gap here
# instead of failing the conversation. If it stays down past the whole backoff window we
# re-raise, and service.py aborts the run rather than churning through the rest.
_RETRYABLE_CONN_ERRORS = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.RemoteProtocolError,  # "Server disconnected without sending a response" — died mid-request
)
# Seconds to wait between connection attempts (~67s total). Generous enough to cover a
# launchd restart plus the start of a cold model reload from the USB store.
_CONNECT_RETRY_BACKOFF = [2, 5, 10, 20, 30]


def _is_valid_grade(data: dict) -> bool:
    """A real grade has a non-empty criteria list with at least one evaluated item.
    An empty list or one where the model declined to evaluate everything is rejected
    so it can be retried rather than saved as a meaningless 0/100."""
    criteria = data.get("criteria")
    if not isinstance(criteria, list) or not criteria:
        return False
    return any(c.get("v") in ("pass", "fail", "n/a") for c in criteria)


# Transcripts longer than this are truncated to the most-recent turns.
# A 14B-parameter model on a typical GPU processes ~500-800 tokens/s;
# shorter transcripts = dramatically faster inference with minimal quality loss.
_MAX_TRANSCRIPT_CHARS = 6_000


def _trim_transcript(text: str) -> str:
    """Keep as many recent turns as fit in _MAX_TRANSCRIPT_CHARS."""
    if len(text) <= _MAX_TRANSCRIPT_CHARS:
        return text
    truncated = text[-_MAX_TRANSCRIPT_CHARS:]
    # Avoid cutting mid-line.
    first_newline = truncated.find("\n")
    if first_newline > 0:
        truncated = truncated[first_newline + 1:]
    return f"[... transcript truncated for length ...]\n{truncated}"


class OllamaGrader:
    def __init__(
        self,
        ruleset: Ruleset | None = None,
        model: str | None = None,
        base_url: str | None = None,
    ) -> None:
        self._ruleset = ruleset or load_ruleset()
        self._model = model or settings.ollama_model
        self._base_url = (base_url or settings.ollama_base_url).rstrip("/")
        # Load the QA prompt from disk so admin edits via the web UI take effect
        # at grader construction time (once per batch, not once per conversation).
        qa_prompt = load_qa_prompt()
        self._system_prompt = qa_prompt.text
        self._prompt_version = qa_prompt.version

    @property
    def rules_version(self) -> str:
        # Use the QA prompt version — this is what Ollama actually grades against.
        # Changing the prompt via the web UI bumps the hash, causing ungraded status
        # on all existing conversations so they get re-evaluated on the next run.
        return self._prompt_version

    def _call(self, transcript: str, temperature: float) -> str:
        options: dict = {"temperature": temperature}
        # Only override Ollama's defaults when explicitly configured — forcing num_ctx /
        # num_predict can slow prefill or change how much the model generates.
        if settings.ollama_num_ctx > 0:
            options["num_ctx"] = settings.ollama_num_ctx
        if settings.ollama_num_predict > 0:
            options["num_predict"] = settings.ollama_num_predict

        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": self._system_prompt},
                {"role": "user", "content": transcript},
            ],
            "stream": False,
            # Structured outputs: constrain generation to the exact grade schema. This
            # guarantees a populated scorecard and a parseable object, and bounds output
            # (grammar must close the JSON), preventing the runaway timeouts seen with
            # a bare {"format": "json"}.
            "format": CASINO_OUTPUT_SCHEMA,
            # Keep the model resident across the batch (the server default may be
            # OLLAMA_KEEP_ALIVE=0, which reloads the model for every conversation).
            "keep_alive": settings.ollama_keep_alive,
            "options": options,
        }

        # Retry only on connection-level failures (server restarting), not on HTTP errors
        # or slow inference — those are handled by raise_for_status / the request timeout.
        for i in range(len(_CONNECT_RETRY_BACKOFF) + 1):
            try:
                resp = httpx.post(
                    f"{self._base_url}/api/chat",
                    json=payload,
                    timeout=600.0,  # 10 min: accounts for cold model load + long inference
                )
                resp.raise_for_status()
                return resp.json()["message"]["content"]
            except _RETRYABLE_CONN_ERRORS as exc:
                if i >= len(_CONNECT_RETRY_BACKOFF):
                    log.error(
                        "Ollama still unreachable after %d retries (~%ds) — giving up on this call",
                        len(_CONNECT_RETRY_BACKOFF), sum(_CONNECT_RETRY_BACKOFF),
                    )
                    raise
                wait = _CONNECT_RETRY_BACKOFF[i]
                log.warning(
                    "Ollama unreachable (%s); waiting %ds before retry %d/%d",
                    exc, wait, i + 1, len(_CONNECT_RETRY_BACKOFF),
                )
                time.sleep(wait)
        raise AssertionError("unreachable")  # loop either returns or raises

    def grade(self, conversation: Conversation) -> ConversationGrade:
        transcript = _trim_transcript(transcript_block(conversation))

        data: dict | None = None
        for attempt in range(_MAX_ATTEMPTS):
            temp = _ATTEMPT_TEMPERATURES[min(attempt, len(_ATTEMPT_TEMPERATURES) - 1)]
            log.info(
                "Grading %s via Ollama/%s (attempt %d/%d, temp=%s; cold-start may take ~30s)",
                conversation.id, self._model, attempt + 1, _MAX_ATTEMPTS, temp,
            )
            content = self._call(transcript, temp)
            try:
                candidate = extract_grade_dict(content)
            except ValueError:
                candidate = None
            if candidate is not None and _is_valid_grade(candidate):
                data = candidate
                break
            log.warning(
                "Grade for %s was empty/unparseable on attempt %d/%d — %s",
                conversation.id, attempt + 1, _MAX_ATTEMPTS,
                "retrying" if attempt + 1 < _MAX_ATTEMPTS else "giving up",
            )

        if data is None:
            # Caller (review_and_store) skips this conversation rather than saving a 0/100.
            raise GradeParseError(
                f"No usable grade for conversation {conversation.id} after {_MAX_ATTEMPTS} attempts"
            )

        grade = ConversationGrade.from_ollama_output(
            conversation.id, conversation.assignee_name, data
        )
        grade.agent_email = conversation.assignee.email if conversation.assignee else ""
        grade.rules_version = self._ruleset.version
        grade.model = f"ollama/{self._model}"
        grade.graded_at = datetime.now(timezone.utc).isoformat()
        log.info(
            "Graded %s via Ollama: %d/100 (%s)",
            conversation.id,
            grade.overall_score,
            grade.overall_result or "no result",
        )
        return grade
