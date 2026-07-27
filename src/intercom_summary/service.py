"""Shared orchestration used by BOTH the web API and the Slack bot.

Keeping fetch / review / overview here means the two interfaces call identical code
instead of each re-implementing it.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import threading
from typing import Any, Callable

from intercom_summary.intercom.fetch import fetch_conversations_for_agents, normalise_conversation
from intercom_summary.logging_setup import get_logger
from intercom_summary.settings import settings
from intercom_summary.storage.conversations_store import ConversationsStore, tags_are_ignored
from intercom_summary.storage.grades_store import GradesStore

log = get_logger("service")


# ── Agents (live teammate roster from Intercom) ───────────────────────────────────
async def list_agents() -> list[dict[str, str]]:
    """All current Intercom teammates, so the UI can offer a pick-list."""
    from intercom_summary.intercom.client import IntercomClient

    client = IntercomClient()
    try:
        raw = await client.list_admins()
    finally:
        await client.aclose()
    agents = [
        {"id": str(a.get("id", "")), "name": a.get("name", ""), "email": a.get("email", "")}
        for a in raw
        if a.get("name") or a.get("email")
    ]
    agents.sort(key=lambda a: a["name"].lower())
    return agents


# ── Fetch ─────────────────────────────────────────────────────────────────────
async def fetch_and_store(
    agents: list[str],
    since: str | None = None,
    until: str | None = None,
    state: str | None = None,
    limit: int | None = None,
    on_progress: Callable[[int, int], None] | None = None,
) -> dict[str, Any]:
    """Fetch conversations from Intercom and cache them locally.

    `on_progress(fetched, total)` is called after each conversation is saved,
    enabling incremental DB writes and progress reporting.
    """
    store = ConversationsStore()
    batch: list = []
    BATCH_SIZE = 5
    saved = 0

    def _flush() -> None:
        nonlocal saved
        saved += store.save_many(list(batch))
        batch.clear()

    def _on_conversation(conv, fetched: int, total: int) -> None:
        batch.append(conv)
        if len(batch) >= BATCH_SIZE or fetched == total:
            _flush()
        if on_progress:
            on_progress(fetched, total)

    try:
        convos = await fetch_conversations_for_agents(
            agents=agents, since=since, until=until, state=state, limit=limit,
            on_conversation=_on_conversation,
        )
        # Flush any stragglers (e.g. when total < BATCH_SIZE).
        if batch:
            _flush()
    finally:
        store.close()

    # Blacklisted conversations are dropped by the store. Without this count a fetch that
    # imported nothing still reports a healthy "fetched" total and looks like it succeeded.
    skipped = len(convos) - saved
    if skipped:
        log.warning(
            "%d of %d fetched conversation(s) were skipped — they are blacklisted in the "
            "Trash and cannot be re-imported until restored or purged.", skipped, len(convos),
        )

    return {
        "fetched": len(convos),
        "saved": saved,
        "skipped_deleted": skipped,
        "agents": agents,
        "conversation_ids": [c.id for c in convos],
    }


# ── Repair: fix agent names on existing conversations ────────────────────────────
async def repair_agent_names(concurrency: int = 10) -> dict[str, Any]:
    """Re-fetch every conversation with a missing agent_name from Intercom and update the DB."""
    import asyncio
    from intercom_summary.intercom.client import IntercomClient
    from intercom_summary.intercom.models import Admin

    store = ConversationsStore()
    try:
        ids = store.get_empty_agent_ids()
        if not ids:
            return {"total": 0, "fixed": 0}

        client = IntercomClient()
        try:
            raw_admins = await client.list_admins()
            known_admins: dict[str, Admin] = {
                str(a["id"]): Admin(id=str(a["id"]), name=a.get("name", ""), email=a.get("email", ""))
                for a in raw_admins if a.get("id")
            }

            fixed = 0
            sem = asyncio.Semaphore(concurrency)

            async def repair_one(cid: str) -> bool:
                async with sem:
                    try:
                        full = await client.get_conversation(cid)
                    except Exception:
                        return False
                conv = normalise_conversation(full, known_admins=known_admins)
                if conv.assignee_name:
                    store.update_agent(cid, conv.assignee_name,
                                       conv.assignee.email if conv.assignee else "")
                    return True
                return False

            results = await asyncio.gather(*[repair_one(cid) for cid in ids])
            fixed = sum(results)
        finally:
            await client.aclose()
    finally:
        store.close()

    log.info("repair_agent_names: fixed %d / %d conversations", fixed, len(ids))

    # Cascade: update any grades whose agent_name is still empty by joining with conversations.
    grades_store = GradesStore()
    try:
        import json as _json
        rows = grades_store._conn.execute(
            """SELECT g.conversation_id, c.agent_name, c.agent_email, g.payload_json
               FROM grades g JOIN conversations c ON c.id = g.conversation_id
               WHERE (g.agent_name IS NULL OR g.agent_name = '') AND c.agent_name != ''"""
        ).fetchall()
        grades_fixed = 0
        for r in rows:
            payload = _json.loads(r["payload_json"])
            payload["agent_name"] = r["agent_name"]
            grades_store._conn.execute(
                "UPDATE grades SET agent_name=?, agent_email=?, payload_json=? WHERE conversation_id=?",
                (r["agent_name"], r["agent_email"], _json.dumps(payload), r["conversation_id"]),
            )
            grades_fixed += 1
        grades_store._conn.commit()
        log.info("repair_agent_names: cascaded to %d grade rows", grades_fixed)
    finally:
        grades_store.close()

    return {"total": len(ids), "fixed": fixed}


# ── Review (QA grading) ─────────────────────────────────────────────────────────
# Concurrency caps per backend.  Ollama runs one model on a single local GPU: with a
# large model on a memory-constrained box, two concurrent calls thrash (and bust the
# warm system-prompt KV cache), so 1 is faster end-to-end than 2 here.  The Anthropic
# API can handle many more before hitting rate limits.
_REVIEW_CONCURRENCY: dict[str, int] = {
    "ollama": 1,
    "api": 5,
}


def review_and_store(
    conversation_ids: list[str] | None = None,
    agents: list[str] | None = None,
    since: str | None = None,
    until: str | None = None,
    state: str | None = None,
    regrade: bool = False,
    backend: str | None = None,
    on_progress: Callable[[int, int, int], None] | None = None,
    cancel_event: "threading.Event | None" = None,
) -> dict[str, Any]:
    """Grade cached conversations concurrently and persist the grades.

    `on_progress(graded, skipped, total)` is called after each conversation is
    processed so the UI can display a live progress bar.

    Concurrency is capped per backend so we don't overwhelm a local GPU or hit
    API rate limits.
    """
    import asyncio
    from concurrent.futures import ThreadPoolExecutor

    import httpx

    from intercom_summary.qa.backends import get_grader
    from intercom_summary.qa.rulesets import agent_ruleset_resolver

    convos_store = ConversationsStore()
    grades_store = GradesStore()
    try:
        if conversation_ids:
            convos = [c for cid in conversation_ids if (c := convos_store.get(cid))]
        else:
            rows, _ = convos_store.query(
                agents=agents, since=since, until=until, state=state, limit=10_000
            )
            convos = [c for r in rows if (c := convos_store.get(r["id"]))]

        # Triage/noise chats (tagged spam, empty, test, Jira, Follow-Up, no request) are
        # never graded — drop them here so they count as neither graded nor pending.
        ignored = sum(1 for c in convos if tags_are_ignored(c.tags))
        convos = [c for c in convos if not tags_are_ignored(c.tags)]

        total = len(convos)
        concurrency = _REVIEW_CONCURRENCY.get(settings.qa_backend, 2)

        # Each conversation is graded against its assigned agent's ruleset — a VIP agent's
        # chats and emails get the VIP ruleset. Build one grader per ruleset we actually see
        # (lazily, so a run with no VIP agents never loads the VIP prompt).
        graders: dict[str, Any] = {}

        def grader_for(ruleset_id: str):
            if ruleset_id not in graders:
                graders[ruleset_id] = get_grader(backend, ruleset_id=ruleset_id)
            return graders[ruleset_id]

        # Bucket by ruleset, skipping conversations whose grade is already current. Staleness
        # is judged against the grader that would run now: a grade produced by a *different*
        # ruleset is left alone rather than re-graded, so moving an agent into the VIP group
        # does not silently invalidate their standard-ruleset history (see is_current).
        # Group membership is read once for the whole run, not once per conversation — the
        # latter opens a DB connection each time and exhausts the process's file descriptors.
        ruleset_for = agent_ruleset_resolver()

        buckets: dict[str, list] = defaultdict(list)
        for c in convos:
            rid = ruleset_for(c.assignee_name)
            grader = grader_for(rid)
            if regrade or not grades_store.is_current(c.id, rid, grader.rules_version):
                buckets[rid].append(c)

        pending = [c for bucket in buckets.values() for c in bucket]
        skipped = total - len(pending)
        if len(buckets) > 1:
            log.info(
                "Review split across rulesets: %s",
                ", ".join(f"{rid}={len(b)}" for rid, b in buckets.items()),
            )

        if on_progress:
            on_progress(0, skipped, total)

        if not pending:
            return {"graded": 0, "skipped": skipped, "failed": 0, "total": total, "ignored": ignored}

        graded_count = 0
        failed_count = 0
        cancelled = False
        backend_unreachable = False

        async def _run() -> None:
            nonlocal graded_count, failed_count, cancelled, backend_unreachable
            sem = asyncio.Semaphore(concurrency)
            loop = asyncio.get_running_loop()
            executor = ThreadPoolExecutor(max_workers=concurrency)

            async def grade_one(convo, grader) -> None:
                nonlocal graded_count, failed_count, backend_unreachable
                if cancel_event and cancel_event.is_set():
                    return
                # The backend (e.g. local Ollama) died — don't burn through the rest of
                # the batch racking up thousands of identical "connection refused" failures
                # and then report a misleading "done". Stop scheduling new work.
                if backend_unreachable:
                    return
                async with sem:
                    if cancel_event and cancel_event.is_set() or backend_unreachable:
                        return
                    try:
                        grade = await loop.run_in_executor(executor, grader.grade, convo)
                    except (httpx.ConnectError, httpx.ConnectTimeout,
                            httpx.RemoteProtocolError) as exc:
                        # The grader already retried with backoff and the backend is still
                        # down — treat as fatal for the whole run rather than a
                        # per-conversation skip (so we don't churn through the rest racking
                        # up identical failures and then report a misleading "done").
                        backend_unreachable = True
                        log.error("Backend unreachable, aborting review: %s", exc)
                        return
                    except Exception as exc:
                        # Skip this conversation rather than aborting the whole batch or
                        # persisting a bogus grade (e.g. an unparseable model response).
                        failed_count += 1
                        log.warning("Skipping %s: grading failed: %s", convo.id, exc)
                        if on_progress:
                            on_progress(graded_count, skipped + failed_count, total)
                        return
                # DB writes happen back on the event-loop thread — no thread-safety issue.
                grades_store.save(grade)
                graded_count += 1
                if on_progress:
                    on_progress(graded_count, skipped + failed_count, total)

            # One ruleset at a time: with ollama concurrency of 1 the throughput win comes from
            # a warm system-prompt KV cache, and alternating two system prompts would thrash it.
            for ruleset_id, bucket in buckets.items():
                if backend_unreachable or (cancel_event and cancel_event.is_set()):
                    break
                grader = grader_for(ruleset_id)
                await asyncio.gather(*[grade_one(c, grader) for c in bucket])

            executor.shutdown(wait=False)
            if cancel_event and cancel_event.is_set():
                cancelled = True

        asyncio.run(_run())

        if failed_count:
            log.info("Review finished with %d conversation(s) skipped after grading errors", failed_count)

        return {
            "graded": graded_count,
            # `skipped` includes both already-graded and grading failures so the UI's
            # progress (graded + skipped) still reaches total; `failed` breaks it out.
            "skipped": skipped + failed_count,
            "failed": failed_count,
            "total": total,
            "ignored": ignored,
            "cancelled": cancelled,
            # True when the backend (Ollama) became unreachable mid-run; the caller marks
            # the job as errored instead of "done" so the partial run isn't mistaken for
            # a complete one.
            "backend_unreachable": backend_unreachable,
        }
    finally:
        convos_store.close()
        grades_store.close()


def build_conversation_snapshot(conversation_id: str) -> dict[str, Any] | None:
    """Freeze a conversation + its grade into a self-contained exemplar for the knowledge
    base, so the case stays viewable after the source conversation/grade is deleted.

    Returns the same shape as the conversation-detail API (conversation/transcript/grade/sla)
    plus a denormalised `summary` for list views. None if the conversation isn't cached.
    """
    cstore = ConversationsStore()
    gstore = GradesStore()
    try:
        convo = cstore.get(conversation_id)
        if not convo:
            return None
        row = cstore._conn.execute(
            "SELECT custom_tags FROM conversations WHERE id=?", (conversation_id,)
        ).fetchone()
        convo_dict = convo.to_dict()
        convo_dict["custom_tags"] = row["custom_tags"] if row else ""
        grade = gstore.get(conversation_id)
        score = None
        if grade:
            score = grade.get("human_score")
            if score is None:
                score = grade.get("overall_score")
        return {
            "conversation": convo_dict,
            "transcript": convo.transcript_text(),
            "grade": grade,
            "sla": convo.sla_summary(
                settings.sla_first_response_sec, settings.sla_followup_sec
            ),
            "summary": {
                "id": convo.id,
                "agent_name": convo.assignee_name,
                "customer_name": convo_dict.get("customer_name")
                    or (convo.contact.name if convo.contact else ""),
                "subject": convo_dict.get("subject", ""),
                "state": convo_dict.get("state", ""),
                "created_at": convo_dict.get("created_at"),
                "score": score,
            },
        }
    finally:
        cstore.close()
        gstore.close()


# ── Overview (bento dashboard payload) ───────────────────────────────────────────
def build_overview(agents_scope: list[str] | None = None) -> dict[str, Any]:
    """Dashboard aggregates. `agents_scope` limits everything to a group's agents (the UI's
    Standard/VIP switcher) — None means all agents. Scores from different rulesets are not
    comparable, which is exactly why the dashboard can be scoped to one group at a time."""
    convos_store = ConversationsStore()
    grades_store = GradesStore()
    try:
        grades = grades_store.all(agents=agents_scope)
        total_convos = convos_store.count(agents=agents_scope)
        agents = convos_store.agents()
        if agents_scope is not None:
            in_scope = {a.lower() for a in agents_scope}
            agents = [a for a in agents if a.lower() in in_scope]
    finally:
        convos_store.close()
        grades_store.close()

    scores = [g["overall_score"] for g in grades]
    avg = round(sum(scores) / len(scores), 1) if scores else 0.0
    violations = Counter(v for g in grades for v in g.get("violations", []))

    # Per-agent leaderboard
    by_agent: dict[str, list[int]] = defaultdict(list)
    for g in grades:
        by_agent[g.get("agent_name") or "(unknown)"].append(g["overall_score"])
    leaderboard = sorted(
        (
            {"agent": a, "avg_score": round(sum(s) / len(s), 1), "count": len(s)}
            for a, s in by_agent.items()
        ),
        key=lambda x: x["avg_score"],
        reverse=True,
    )

    # Score trend grouped by calendar day
    by_day: dict[str, list[int]] = defaultdict(list)
    for g in grades:
        day = (g.get("graded_at") or "")[:10]
        if day:
            by_day[day].append(g["overall_score"])
    trend = [
        {"date": d, "avg_score": round(sum(s) / len(s), 1), "count": len(s)}
        for d, s in sorted(by_day.items())
    ]

    worst = sorted(grades, key=lambda g: g["overall_score"])[:8]
    worst_list = [
        {
            "id": g["conversation_id"],
            "agent": g.get("agent_name", ""),
            "score": g["overall_score"],
            "summary": g.get("summary", ""),
        }
        for g in worst
    ]

    return {
        "kpis": {
            "conversations": total_convos,
            "graded": len(grades),
            "avg_score": avg,
            "violations": sum(violations.values()),
            "agents": len(agents),
        },
        "score_trend": trend,
        "top_violations": [{"text": t, "count": n} for t, n in violations.most_common(8)],
        "agent_leaderboard": leaderboard,
        "worst_conversations": worst_list,
    }
