"""Agentic Qwen chat with MCP-style Intercom tools.

The agent can search conversations, read transcripts, and look up agent stats
from the local database. Tool calls are handled in a loop before the final
answer is streamed back to the client.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, AsyncIterator

import httpx

from intercom_summary.logging_setup import get_logger
from intercom_summary.qa.casino_prompt import load_qa_prompt
from intercom_summary.settings import settings

log = get_logger(__name__)

MAX_TOOL_ROUNDS = 6

# ── Tool schemas (Ollama / OpenAI function-calling format) ─────────────────────
TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "search_conversations",
            "description": (
                "Search the local QA database for conversations. "
                "Returns a list of matching conversations with their scores."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "agent":  {"type": "string",  "description": "Filter by agent name (partial match)"},
                    "search": {"type": "string",  "description": "Keyword to search in subject or customer name"},
                    "since":  {"type": "string",  "description": "ISO date lower bound e.g. 2024-01-01"},
                    "until":  {"type": "string",  "description": "ISO date upper bound"},
                    "state":  {"type": "string",  "description": "open | closed | snoozed"},
                    "limit":  {"type": "integer", "description": "Max results (default 10, max 20)"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_conversation",
            "description": (
                "Get the full transcript and QA grade for a specific conversation by its ID. "
                "Use this to read what was actually said or to examine a grade in detail."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "conversation_id": {"type": "string"},
                },
                "required": ["conversation_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_agent_stats",
            "description": "Get QA performance statistics for a specific support agent.",
            "parameters": {
                "type": "object",
                "properties": {
                    "agent_name": {"type": "string"},
                },
                "required": ["agent_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_agents",
            "description": "List all agents in the QA database with their average scores.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]

_AGENT_SUFFIX = """

---

## ASSISTANT MODE

You are now acting as an **interactive QA assistant** with access to the full database of fetched conversations and grades.

Use your tools proactively:
- When asked about an agent → call `get_agent_stats` first.
- When asked about a specific conversation → call `get_conversation`.
- When asked to find examples or patterns → call `search_conversations`.
- Chain tools if needed (e.g., search then get details on a result).

Keep answers concise and evidence-based. Reference specific conversation IDs and scores when available.
"""


def _build_system() -> str:
    return load_qa_prompt().text + _AGENT_SUFFIX


# ── Tool execution (local DB only — no Intercom API calls) ─────────────────────
def _exec_tool(name: str, args: Any, db_path: Path | None = None) -> dict:
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            args = {}

    try:
        if name == "search_conversations":
            return _search_conversations(args, db_path)
        if name == "get_conversation":
            return _get_conversation(args, db_path)
        if name == "get_agent_stats":
            return _get_agent_stats(args, db_path)
        if name == "list_agents":
            return _list_agents(db_path)
        return {"error": f"Unknown tool: {name}"}
    except Exception as exc:
        log.warning("Tool %s failed: %s", name, exc)
        return {"error": str(exc)}


def _search_conversations(args: dict, db_path) -> dict:
    from intercom_summary.storage.conversations_store import ConversationsStore

    store = ConversationsStore(db_path or settings.db_path)
    try:
        agent = args.get("agent")
        rows, total = store.query(
            agents=[agent] if agent else None,
            search=args.get("search"),
            since=args.get("since"),
            until=args.get("until"),
            state=args.get("state"),
            limit=min(int(args.get("limit", 10)), 20),
        )
        return {
            "total_found": total,
            "returned": len(rows),
            "conversations": [
                {
                    "id": r["id"],
                    "agent": r["agent_name"] or "(unknown)",
                    "subject": r["subject"],
                    "state": r["state"],
                    "created_at": (r["created_at"] or "")[:10],
                    "score": r["score"],
                    "grade_summary": (r["grade_summary"] or "")[:120],
                }
                for r in rows
            ],
        }
    finally:
        store.close()


def _get_conversation(args: dict, db_path) -> dict:
    from intercom_summary.storage.conversations_store import ConversationsStore
    from intercom_summary.storage.grades_store import GradesStore

    cid = args.get("conversation_id", "")
    store = ConversationsStore(db_path or settings.db_path)
    gstore = GradesStore(db_path or settings.db_path)
    try:
        convo = store.get(cid)
        if not convo:
            return {"error": f"Conversation {cid} not found"}
        grade = gstore.get(cid)
        return {
            "id": convo.id,
            "agent": convo.assignee_name or "(unknown)",
            "subject": convo.subject,
            "state": convo.state,
            "transcript": convo.transcript_text()[:4000],
            "grade": {
                "score": grade["overall_score"],
                "human_score": grade.get("human_score"),
                "summary": grade.get("summary", ""),
                "violations": grade.get("violations", []),
                "suggestions": grade.get("suggestions", []),
            } if grade else None,
        }
    finally:
        store.close()
        gstore.close()


def _get_agent_stats(args: dict, db_path) -> dict:
    from intercom_summary.storage.grades_store import GradesStore

    name = args.get("agent_name", "")
    gstore = GradesStore(db_path or settings.db_path)
    try:
        grades = gstore.for_agent(name)
        if not grades:
            return {"error": f"No grades found for agent '{name}'"}
        scores = [g["overall_score"] for g in grades]
        overrides = [g for g in grades if g.get("human_score") is not None]
        return {
            "agent": name,
            "total_graded": len(grades),
            "avg_score": round(sum(scores) / len(scores), 1),
            "min_score": min(scores),
            "max_score": max(scores),
            "override_count": len(overrides),
            "recent": [
                {
                    "id": g["conversation_id"],
                    "score": g["overall_score"],
                    "human_score": g.get("human_score"),
                    "summary": (g.get("summary") or "")[:120],
                }
                for g in grades[:5]
            ],
        }
    finally:
        gstore.close()


def _list_agents(db_path) -> dict:
    from intercom_summary.storage.conversations_store import ConversationsStore
    from intercom_summary.storage.grades_store import GradesStore
    from collections import defaultdict

    gstore = GradesStore(db_path or settings.db_path)
    try:
        all_grades = gstore.all()
        by_agent: dict[str, list[int]] = defaultdict(list)
        for g in all_grades:
            by_agent[g.get("agent_name") or "(unknown)"].append(
                g.get("human_score") or g["overall_score"]
            )
        return {
            "agents": sorted(
                [
                    {"agent": a, "graded": len(s), "avg_score": round(sum(s) / len(s), 1)}
                    for a, s in by_agent.items()
                ],
                key=lambda x: x["avg_score"],
                reverse=True,
            )
        }
    finally:
        gstore.close()


# ── Agentic streaming loop ─────────────────────────────────────────────────────
async def run_agent(
    message: str,
    history: list[dict],
    db_path: Path | None = None,
) -> AsyncIterator[str]:
    """Yield SSE data lines: tool_call, tool_result, token, or [DONE]."""

    messages: list[dict] = [*history, {"role": "user", "content": message}]
    base = settings.ollama_base_url.rstrip("/")

    async with httpx.AsyncClient(timeout=300.0) as client:
        for _round in range(MAX_TOOL_ROUNDS):
            # Non-streaming call so we can inspect tool_calls before streaming.
            resp = await client.post(
                f"{base}/api/chat",
                json={
                    "model": settings.ollama_model,
                    "messages": [{"role": "system", "content": _build_system()}, *messages],
                    "tools": TOOLS,
                    "stream": False,
                    "options": {"temperature": 0.1},
                },
            )
            resp.raise_for_status()
            assistant_msg = resp.json()["message"]
            tool_calls: list[dict] = assistant_msg.get("tool_calls") or []

            if not tool_calls:
                # Final answer — stream it token-by-token for a typing effect.
                final_text: str = assistant_msg.get("content", "")
                chunk_size = 6
                for i in range(0, len(final_text), chunk_size):
                    yield f"data: {json.dumps({'token': final_text[i:i + chunk_size]})}\n\n"
                break

            # Append assistant message with tool calls to history.
            messages.append(assistant_msg)

            # Execute each tool and stream call/result events to the frontend.
            for tc in tool_calls:
                fn = tc.get("function", {})
                fn_name = fn.get("name", "")
                fn_args = fn.get("arguments", {})

                yield f"data: {json.dumps({'tool_call': fn_name, 'args': fn_args})}\n\n"

                result = _exec_tool(fn_name, fn_args, db_path)
                preview = json.dumps(result)[:300]
                yield f"data: {json.dumps({'tool_result': fn_name, 'preview': preview})}\n\n"

                messages.append({
                    "role": "tool",
                    "content": json.dumps(result),
                })

    yield "data: [DONE]\n\n"
