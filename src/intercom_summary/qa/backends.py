"""Pick the QA grader backend from settings.

Both graders expose the same surface: `.rules_version` / `.ruleset_id` properties and a
`.grade(conversation) -> ConversationGrade` method, so callers don't care which is used.

`ruleset_id` selects which criteria + system prompt to grade against (qa/rulesets.py). It is
resolved per conversation from the assigned agent's group, so a review run builds one grader
per ruleset it encounters.
"""
from __future__ import annotations

from intercom_summary.logging_setup import get_logger
from intercom_summary.settings import settings

log = get_logger(__name__)


def get_grader(backend: str | None = None, ruleset_id: str | None = None):
    backend = (backend or settings.qa_backend).lower()
    if backend == "api":
        from intercom_summary.qa.grader import Grader

        return Grader(ruleset_id=ruleset_id)
    if backend == "ollama":
        from intercom_summary.qa.ollama_grader import OllamaGrader

        return OllamaGrader(ruleset_id=ruleset_id)
    raise RuntimeError(f"Unknown QA_BACKEND '{backend}' (use api or ollama).")
