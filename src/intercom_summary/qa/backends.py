"""Pick the QA grader backend from settings.

Both graders expose the same surface: a `.rules_version` property and a
`.grade(conversation) -> ConversationGrade` method, so callers don't care which is used.
"""
from __future__ import annotations

from intercom_summary.settings import settings


def get_grader(backend: str | None = None):
    backend = (backend or settings.qa_backend).lower()
    if backend == "api":
        from intercom_summary.qa.grader import Grader

        return Grader()
    if backend == "ollama":
        from intercom_summary.qa.ollama_grader import OllamaGrader

        return OllamaGrader()
    raise RuntimeError(f"Unknown QA_BACKEND '{backend}' (use ollama or api).")
