"""Grade through the Message Batches API: every token at half price, results in minutes to hours.

A batch request is exactly the live request (`Grader.request_params`), so a batch grade is
indistinguishable from a live one apart from `usage.batch_calls` and its price. Whatever does
not come back as a usable grade — an errored or expired item, a refusal, unusable JSON — is
re-graded live by the caller, so a batch run never loses a chat.

    ids = submit(grader, conversations)          # one batch per 10k chats of one ruleset
    wait(grader.client, ids, cancel_event, on_status)
    for custom_id, result in results(grader.client, ids): ...
    grade_result(grader, conversation, result)   # → (grade, fell_back_to_live)

The batch ids are reported as soon as they exist: a batch is paid for once submitted, so a
run interrupted mid-wait can still be collected (`intercom-summary collect-batch <id>`).
"""
from __future__ import annotations

import re
import threading
import time
from collections.abc import Callable, Iterable, Iterator

from intercom_summary.logging_setup import get_logger
from intercom_summary.qa.grading_common import GradeParseError
from intercom_summary.qa.pricing import UsageMeter

log = get_logger(__name__)

POLL_SECONDS = 30
# The API caps a batch at 100k requests / 256 MB; a v4.1 request is ~25 KB with its schema,
# so 10k stays far below the size cap and keeps each batch quick to submit.
MAX_PER_BATCH = 10_000
_CUSTOM_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_COUNT_KEYS = ("processing", "succeeded", "errored", "canceled", "expired")


def batchable(conversation_id: str) -> bool:
    """The conversation id doubles as the batch custom_id, which the API restricts."""
    return bool(_CUSTOM_ID.match(conversation_id or ""))


def warm_up_set(grader, conversations: list) -> list:
    """One conversation per distinct cached prefix (ruleset prompt + the brand's KB block).

    Batch requests run concurrently, so without a warm cache most of them each pay the 1-hour
    write for the whole prefix — with the ~15k-token Help Center in it, a miss costs about 20x
    a hit. Grading these few live first, in the batch's exact request shape, writes the entry
    the rest of the batch then reads."""
    seen: set = set()
    picked = []
    for c in conversations:
        kb = grader.knowledge_for(c)
        key = kb.version if kb else ""
        if key not in seen:
            seen.add(key)
            picked.append(c)
    return picked


def submit(grader, conversations: Iterable) -> list[str]:
    requests = [
        {"custom_id": c.id,
         "params": grader.request_params(c, batch=True)}
        for c in conversations
    ]
    ids: list[str] = []
    for start in range(0, len(requests), MAX_PER_BATCH):
        chunk = requests[start:start + MAX_PER_BATCH]
        batch = grader.client.beta.messages.batches.create(requests=chunk)
        ids.append(batch.id)
        log.info("Submitted batch %s: %d chats [%s ruleset %s]",
                 batch.id, len(chunk), grader.ruleset_id, grader.rules_version)
    return ids


def wait(client, batch_ids: list[str], cancel_event: threading.Event | None = None,
         on_status: Callable[[dict], None] | None = None, poll: float | None = None) -> bool:
    """Block until every batch has ended. On cancel, ask the API to cancel and keep waiting
    for it to wind down — requests that already finished are paid for and still collected.
    Returns True when the run was cancelled."""
    poll = POLL_SECONDS if poll is None else poll
    cancelled = False
    while True:
        counts = dict.fromkeys(_COUNT_KEYS, 0)
        ended = 0
        for bid in batch_ids:
            b = client.beta.messages.batches.retrieve(bid)
            rc = getattr(b, "request_counts", None)
            for k in _COUNT_KEYS:
                counts[k] += getattr(rc, k, 0) or 0
            ended += getattr(b, "processing_status", "") == "ended"
        if on_status:
            on_status({"batch_ids": batch_ids, "counts": counts,
                       "ended": ended == len(batch_ids), "cancelled": cancelled})
        if ended == len(batch_ids):
            return cancelled
        if cancel_event is not None and cancel_event.is_set() and not cancelled:
            for bid in batch_ids:
                try:
                    client.beta.messages.batches.cancel(bid)
                except Exception as exc:  # noqa: BLE001 — already ending is fine
                    log.warning("Could not cancel batch %s: %s", bid, exc)
            cancelled = True
            log.info("Cancelling batch(es) %s", ", ".join(batch_ids))
        if cancel_event is not None:
            cancel_event.wait(poll if not cancelled else min(poll, 5))
        else:
            time.sleep(poll)


def results(client, batch_ids: list[str]) -> Iterator[tuple[str, object]]:
    for bid in batch_ids:
        for item in client.beta.messages.batches.results(bid):
            yield item.custom_id, item.result


def grade_result(grader, conversation, result, live_fallback: bool = True):
    """(grade, fell_back) for one batch result. A succeeded, usable result is finished like a
    live response; anything else is re-graded live (unless `live_fallback` is off, when it
    raises GradeParseError). The returned grade's usage covers both calls."""
    meter = UsageMeter()
    kind = getattr(result, "type", None)
    if kind == "succeeded":
        try:
            return grader.finish(conversation, result.message, meter), False
        except GradeParseError as exc:
            reason = str(exc)
    else:
        error = getattr(getattr(getattr(result, "error", None), "error", None), "type", None)
        reason = f"batch item {kind}" + (f" ({error})" if error else "")
    if not live_fallback:
        raise GradeParseError(f"{conversation.id}: {reason}")
    log.info("Batch result for %s unusable (%s) — grading live", conversation.id, reason)
    return grader.grade(conversation, meter), True
