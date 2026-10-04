"""Grading through the Message Batches API (qa/batch.py and the service's batch path)."""
import threading
from types import SimpleNamespace

import pytest

from intercom_summary.qa import batch as batch_mod
from intercom_summary.qa.grader import Grader
from intercom_summary.settings import settings
from test_grader import _convo, _v41, reply


class FakeBatchClaude:
    """`beta.messages.create` for live calls plus `beta.messages.batches.*`. Each submitted
    request is answered from `outcomes[custom_id]` ("ok", "errored", "refusal", "junk"),
    default "ok". Batches end after `polls_to_end` retrieves."""

    def __init__(self, outcomes=None, polls_to_end=1, data=None):
        self.outcomes = outcomes or {}
        self.polls_to_end = polls_to_end
        self.data = data or _v41()
        self.live_calls: list[dict] = []
        self.submitted: list[list[dict]] = []
        self.cancelled: list[str] = []
        self._polls: dict[str, int] = {}
        self.beta = SimpleNamespace(messages=SimpleNamespace(
            create=self._create,
            batches=SimpleNamespace(create=self._b_create, retrieve=self._b_retrieve,
                                    results=self._b_results, cancel=self._b_cancel),
        ))

    def _create(self, **kwargs):
        self.live_calls.append(kwargs)
        return reply(self.data)

    def _b_create(self, *, requests):
        self.submitted.append(requests)
        bid = f"msgbatch_{len(self.submitted)}"
        self._polls[bid] = 0
        return SimpleNamespace(id=bid)

    def _b_retrieve(self, bid):
        self._polls[bid] += 1
        n = len(self.submitted[int(bid.split("_")[1]) - 1])
        done = self._polls[bid] >= self.polls_to_end or bid in self.cancelled
        return SimpleNamespace(
            id=bid, processing_status="ended" if done else "in_progress",
            request_counts=SimpleNamespace(processing=0 if done else n,
                                           succeeded=n if done else 0,
                                           errored=0, canceled=0, expired=0))

    def _b_cancel(self, bid):
        self.cancelled.append(bid)

    def _b_results(self, bid):
        for req in self.submitted[int(bid.split("_")[1]) - 1]:
            cid = req["custom_id"]
            kind = self.outcomes.get(cid, "ok")
            if bid in self.cancelled and kind == "ok" and cid in self.outcomes.get("_unfinished", ()):
                kind = "canceled"
            if kind in ("errored", "canceled", "expired"):
                result = SimpleNamespace(
                    type=kind, error=SimpleNamespace(error=SimpleNamespace(type="api_error")))
            elif kind == "refusal":
                result = SimpleNamespace(type="succeeded",
                                         message=reply(stop_reason="refusal", text=""))
            elif kind == "junk":
                result = SimpleNamespace(type="succeeded", message=reply(text="not json"))
            else:
                result = SimpleNamespace(type="succeeded", message=reply(self.data))
            yield SimpleNamespace(custom_id=cid, result=result)


def _grader(client):
    return Grader(ruleset_id="kb-v41", client=client, jev=False)


# ── qa/batch.py ────────────────────────────────────────────────────────────────────────
def test_a_batch_request_is_the_live_request_with_a_one_hour_cache():
    client = FakeBatchClaude()
    grader = _grader(client)
    ids = batch_mod.submit(grader, [_convo()])
    assert ids == ["msgbatch_1"]
    req = client.submitted[0][0]
    assert req["custom_id"] == "42"
    assert req["params"] == grader.request_params(grader.messages_for(_convo()), batch=True)
    assert "betas" not in req["params"]


def test_large_runs_are_split_into_several_batches(monkeypatch):
    from dataclasses import replace

    monkeypatch.setattr(batch_mod, "MAX_PER_BATCH", 2)
    client = FakeBatchClaude()
    convos = [replace(_convo(), id=str(i)) for i in range(5)]
    assert batch_mod.submit(_grader(client), convos) == ["msgbatch_1", "msgbatch_2", "msgbatch_3"]


def test_wait_reports_progress_until_the_batch_ends():
    client = FakeBatchClaude(polls_to_end=3)
    batch_mod.submit(_grader(client), [_convo()])
    seen = []
    cancelled = batch_mod.wait(client, ["msgbatch_1"], on_status=seen.append, poll=0)
    assert not cancelled
    assert [s["ended"] for s in seen] == [False, False, True]
    assert seen[-1]["counts"]["succeeded"] == 1


def test_wait_cancels_the_batch_when_the_run_is_stopped():
    client = FakeBatchClaude(polls_to_end=99)
    batch_mod.submit(_grader(client), [_convo()])
    stop = threading.Event()
    stop.set()
    assert batch_mod.wait(client, ["msgbatch_1"], cancel_event=stop, poll=0) is True
    assert client.cancelled == ["msgbatch_1"]


def test_a_succeeded_result_becomes_a_half_price_grade_without_a_live_call():
    client = FakeBatchClaude()
    grader = _grader(client)
    batch_mod.submit(grader, [_convo()])
    (cid, result), = batch_mod.results(client, ["msgbatch_1"])
    grade, fell_back = batch_mod.grade_result(grader, _convo(), result)
    assert not fell_back and client.live_calls == []
    assert grade.overall_score == 72 and grade.ruleset_id == "kb-v41"
    assert grade.usage["batch_calls"] == 1
    full = (900 * 2 + 3000 * 0.2 + 700 * 10) / 1e6
    assert grade.usage["cost_usd"] == pytest.approx(full / 2, abs=1e-6)


@pytest.mark.parametrize("outcome", ["errored", "expired", "refusal", "junk"])
def test_an_unusable_result_is_regraded_live(outcome):
    client = FakeBatchClaude(outcomes={"42": outcome})
    grader = _grader(client)
    batch_mod.submit(grader, [_convo()])
    (_, result), = batch_mod.results(client, ["msgbatch_1"])
    grade, fell_back = batch_mod.grade_result(grader, _convo(), result)
    assert fell_back and len(client.live_calls) == 1
    assert grade.overall_score == 72
    # The batch call (if it produced tokens) and the live call are both on the grade.
    assert grade.usage["calls"] == (1 if outcome in ("errored", "expired") else 2)


def test_live_fallback_can_be_switched_off():
    from intercom_summary.qa.grading_common import GradeParseError

    client = FakeBatchClaude(outcomes={"42": "errored"})
    grader = _grader(client)
    batch_mod.submit(grader, [_convo()])
    (_, result), = batch_mod.results(client, ["msgbatch_1"])
    with pytest.raises(GradeParseError):
        batch_mod.grade_result(grader, _convo(), result, live_fallback=False)
    assert client.live_calls == []


def test_only_valid_custom_ids_are_batched():
    assert batch_mod.batchable("215561177656052")
    assert not batch_mod.batchable("a b") and not batch_mod.batchable("x" * 65)


# ── the service's batch path ───────────────────────────────────────────────────────────
@pytest.fixture
def batch_service(tmp_path, monkeypatch):
    from test_service import _convo as svc_convo

    from intercom_summary.storage.conversations_store import ConversationsStore

    object.__setattr__(settings, "db_path", tmp_path / "svc.db")
    monkeypatch.setattr(batch_mod, "POLL_SECONDS", 0)
    cs = ConversationsStore(settings.db_path)
    for cid in ("1", "2", "3"):
        cs.save(svc_convo(cid))
    cs.close()

    def make(client):
        import intercom_summary.qa.backends as backends_mod

        grader = Grader(ruleset_id="kb-v41", client=client, jev=False)
        monkeypatch.setattr(backends_mod, "get_grader",
                            lambda backend=None, ruleset_id=None: grader)
        return grader
    return make


def test_a_batch_run_saves_every_grade_and_reports_the_batch(batch_service):
    from intercom_summary import service
    from intercom_summary.storage.grades_store import GradesStore

    client = FakeBatchClaude(outcomes={"3": "errored"}, polls_to_end=2)
    batch_service(client)
    statuses = []
    result = service.review_and_store(conversation_ids=["1", "2", "3"], backend="api",
                                      ruleset_id="kb-v41", batch=True, on_batch=statuses.append)
    assert result["graded"] == 3 and result["failed"] == 0
    assert result["batch_ids"] == ["msgbatch_1"] and result["batch_fallbacks"] == 1
    assert len(client.live_calls) == 1                     # only the errored item
    assert result["usage"]["batch_calls"] == 2
    # The batch id reached the caller before the batch ended, so a dead run is recoverable.
    assert statuses[0]["batch_ids"] == ["msgbatch_1"] and statuses[0].get("submitted") == 3
    assert statuses[-1]["ended"] is True
    store = GradesStore(settings.db_path)
    try:
        assert store.get("1")["usage"]["batch_calls"] == 1
    finally:
        store.close()


def test_a_cancelled_batch_run_saves_what_finished_and_grades_nothing_live(batch_service):
    from intercom_summary import service

    client = FakeBatchClaude(outcomes={"_unfinished": ("2", "3")}, polls_to_end=99)
    batch_service(client)
    stop = threading.Event()

    def on_batch(status):
        stop.set()                    # the user presses Stop while the batch is processing

    result = service.review_and_store(conversation_ids=["1", "2", "3"], backend="api",
                                      ruleset_id="kb-v41", batch=True, on_batch=on_batch,
                                      cancel_event=stop)
    assert client.cancelled == ["msgbatch_1"]
    assert result["cancelled"] is True
    assert result["graded"] == 1                 # chat 1 finished before the cancel — kept
    assert client.live_calls == []               # cancelled items are not re-graded live


def test_batch_needs_the_claude_backend(batch_service):
    from intercom_summary import service

    batch_service(FakeBatchClaude())
    with pytest.raises(ValueError):
        service.review_and_store(conversation_ids=["1"], backend="ollama", batch=True)


def test_collect_batch_saves_an_orphaned_batch(batch_service, monkeypatch):
    from intercom_summary import cli
    from intercom_summary.storage.grades_store import GradesStore

    client = FakeBatchClaude()
    grader = batch_service(client)
    batch_mod.submit(grader, [_convo_for(cid) for cid in ("1", "2")])
    monkeypatch.setattr("anthropic.Anthropic", lambda **_: client)
    monkeypatch.setattr(settings.__class__, "require_qa", lambda self, backend=None: None)
    cli.main(["collect-batch", "msgbatch_1", "--ruleset", "kb-v41"])
    store = GradesStore(settings.db_path)
    try:
        assert store.get("1")["ruleset_id"] == "kb-v41"
        assert store.get("2")["usage"]["batch_calls"] == 1
    finally:
        store.close()


def _convo_for(cid):
    from test_service import _convo as svc_convo

    return svc_convo(cid)
