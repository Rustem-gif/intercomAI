"""QA grading agent.

For each conversation we ask Claude Opus 4.8 to grade the agent's handling against the
editable ruleset. Two cost/latency optimisations:

  • Prompt caching — the system prompt + the (constant) ruleset are sent as a cached
    block, so grading N conversations only pays to process the rules once.
  • Structured output — the model must call the `submit_grade` tool, giving us a
    validated ConversationGrade instead of free-form text to parse.
"""
from __future__ import annotations

from datetime import datetime, timezone

from anthropic import Anthropic

from intercom_summary.settings import settings
from intercom_summary.intercom.models import Conversation
from intercom_summary.logging_setup import get_logger
from intercom_summary.qa.prompt import SYSTEM_INSTRUCTIONS
from intercom_summary.qa.rules import Ruleset, load_ruleset
from intercom_summary.qa.schema import GRADE_TOOL_SCHEMA, ConversationGrade

log = get_logger(__name__)

# Reuse the shared instructions so every backend grades identically; the API backend
# adds a tool-call nudge on top.
_SYSTEM_INSTRUCTIONS = (
    SYSTEM_INSTRUCTIONS + "\nThen submit your evaluation by calling the `submit_grade` tool."
)


class Grader:
    def __init__(
        self,
        ruleset: Ruleset | None = None,
        model: str | None = None,
        client: Anthropic | None = None,
    ) -> None:
        self._ruleset = ruleset or load_ruleset()
        self._model = model or settings.qa_model
        self._client = client or Anthropic(api_key=settings.anthropic_api_key)

    @property
    def rules_version(self) -> str:
        return self._ruleset.version

    def _system_blocks(self) -> list[dict]:
        # The ruleset block is marked cacheable: constant across every conversation.
        return [
            {"type": "text", "text": _SYSTEM_INSTRUCTIONS},
            {
                "type": "text",
                "text": f"# SUPPORT RULESET (version {self._ruleset.version})\n\n{self._ruleset.text}",
                "cache_control": {"type": "ephemeral"},
            },
        ]

    def grade(self, conversation: Conversation) -> ConversationGrade:
        user_content = (
            f"Conversation ID: {conversation.id}\n"
            f"Assigned agent: {conversation.assignee_name or 'unknown'}\n"
            f"Subject: {conversation.subject}\n"
            f"State: {conversation.state}\n\n"
            f"=== TRANSCRIPT ===\n{conversation.transcript_text()}\n=== END ==="
        )

        resp = self._client.messages.create(
            model=self._model,
            max_tokens=2000,
            system=self._system_blocks(),
            tools=[GRADE_TOOL_SCHEMA],
            tool_choice={"type": "tool", "name": "submit_grade"},
            messages=[{"role": "user", "content": user_content}],
        )

        tool_input = None
        for block in resp.content:
            if getattr(block, "type", None) == "tool_use" and block.name == "submit_grade":
                tool_input = block.input
                break
        if tool_input is None:
            raise RuntimeError(f"Model did not return a grade for conversation {conversation.id}")

        grade = ConversationGrade.from_tool_input(
            conversation.id, conversation.assignee_name, tool_input
        )
        grade.agent_email = conversation.assignee.email if conversation.assignee else ""
        grade.rules_version = self._ruleset.version
        grade.model = self._model
        grade.graded_at = datetime.now(timezone.utc).isoformat()
        log.info("Graded %s: %d/100", conversation.id, grade.overall_score)
        return grade
