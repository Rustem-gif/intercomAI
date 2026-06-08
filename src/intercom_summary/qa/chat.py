"""Streaming chat with a local Qwen/Ollama model, grounded in a conversation transcript."""
from __future__ import annotations

import json
from typing import AsyncIterator

import httpx

from intercom_summary.intercom.models import Conversation
from intercom_summary.logging_setup import get_logger
from intercom_summary.qa.casino_prompt import CASINO_QA_SYSTEM_PROMPT
from intercom_summary.settings import settings

log = get_logger(__name__)

# Appended after the full QA system prompt so the model has both the framework
# and the actual transcript in scope when answering analyst questions.
_TRANSCRIPT_SUFFIX = """\

---

You are now in **interactive QA assistant mode**. The transcript below is the conversation
you are helping the analyst review. Answer questions concisely, cite specific turns when
relevant, and apply the evaluation framework above in your reasoning.

CONVERSATION TRANSCRIPT:
{transcript}"""


async def stream_chat(
    conversation: Conversation,
    message: str,
    history: list[dict],
) -> AsyncIterator[str]:
    """Yield SSE data lines from a streaming Ollama chat response.

    Yields ``data: {"token": "..."}`` lines while the model is generating,
    then ``data: [DONE]``. On error yields ``data: {"error": "..."}`` and stops.
    """
    system_prompt = CASINO_QA_SYSTEM_PROMPT + _TRANSCRIPT_SUFFIX.format(
        transcript=conversation.transcript_text()
    )
    messages = [*history, {"role": "user", "content": message}]

    log.info("AI chat for conversation %s via ollama/%s", conversation.id, settings.ollama_model)
    try:
        async with httpx.AsyncClient(timeout=300.0) as client:
            async with client.stream(
                "POST",
                f"{settings.ollama_base_url}/api/chat",
                json={
                    "model": settings.ollama_model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        *messages,
                    ],
                    "stream": True,
                    "options": {"temperature": 0.7},
                },
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    token = (data.get("message") or {}).get("content", "")
                    if token:
                        yield f"data: {json.dumps({'token': token})}\n\n"
                    if data.get("done"):
                        break
    except httpx.ConnectError:
        yield f"data: {json.dumps({'error': f'Ollama not reachable at {settings.ollama_base_url}. Run: brew services start ollama'})}\n\n"
    except Exception as exc:
        yield f"data: {json.dumps({'error': str(exc)})}\n\n"

    yield "data: [DONE]\n\n"
