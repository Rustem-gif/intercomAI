"""What every grading backend shares: the transcript it sends, and what counts as a usable grade.

Both graders (Claude API and Ollama) must send the same text and reject the same outputs —
otherwise the same conversation would be graded on different evidence depending on the backend.
"""
from __future__ import annotations


class GradeParseError(Exception):
    """The model did not return a usable grade (empty/unparseable/refused) after all retries."""


# Verdicts that count as the model having actually looked at a criterion. "cannot_determine"
# belongs here: it is a real, considered answer under the v4.1 evidence rules — "the data
# cannot show me this" — not a refusal to evaluate.
# "partial" is the VIP scorecard's half-defect on its two-level criteria (qa/vip_scoring.py).
_EVALUATED = ("pass", "partial", "fail", "n/a", "cannot_determine")


def is_valid_grade(data: dict) -> bool:
    """A real grade has a non-empty criteria list with at least one evaluated item.
    An empty list or one where the model declined to evaluate everything is rejected
    so it can be retried rather than saved as a meaningless 0/100."""
    criteria = data.get("criteria")
    if not isinstance(criteria, list) or not criteria:
        return False
    return any(c.get("v") in _EVALUATED for c in criteria)


# Transcripts longer than this are split into head + tail so that opening
# greetings (name use, initial tone) are never lost to truncation.
# Sized originally for qwen2.5:14b's context; kept for the Claude backend too so that a
# conversation is graded on the same evidence whichever backend runs it.
_MAX_TRANSCRIPT_CHARS = 15_000
_HEAD_CHARS = 2_000   # always keep the opening — greeting, name use, first impression


def trim_transcript(text: str, limit: int = _MAX_TRANSCRIPT_CHARS) -> str:
    """Keep head + tail so the greeting is never truncated away. A VIP case spans several
    email threads, so it passes a larger `limit` (qa/vip_transcript.py)."""
    if len(text) <= limit:
        return text
    head = text[:_HEAD_CHARS]
    # Avoid cutting mid-line at the head boundary.
    last_nl = head.rfind("\n")
    if last_nl > 0:
        head = head[:last_nl]
    tail = text[-(limit - _HEAD_CHARS):]
    # Avoid cutting mid-line at the tail boundary.
    first_nl = tail.find("\n")
    if first_nl > 0:
        tail = tail[first_nl + 1:]
    return f"{head}\n[... transcript truncated for length ...]\n{tail}"
