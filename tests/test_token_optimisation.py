"""Cost metering, the cacheable prefix, the warm-up before fan-out and QA_CONCURRENCY."""
import json
import threading
import time
from dataclasses import replace
from types import SimpleNamespace

import pytest

from intercom_summary.qa.grader import Grader
from intercom_summary.qa.pricing import (
    UsageMeter,
    cache_hit_share,
    cost_usd,
    sum_usage,
    usage_dict,
)
from intercom_summary.qa.rulesets import get_ruleset, strict_schema
from intercom_summary.settings import settings
from test_grader import FakeClaude, _convo, _v41, reply


# ── pricing ────────────────────────────────────────────────────────────────────────────
def test_cost_uses_each_meter_at_its_own_rate():
    counts = {"input": 1_000_000, "cache_write_5m": 1_000_000, "cache_write_1h": 1_000_000,
              "cache_read": 1_000_000, "output": 1_000_000}
    assert cost_usd(counts, "claude-sonnet-5-5") == pytest.approx(2 + 2.5 + 4 + 0.2 + 10)
    assert cost_usd(counts, "claude-sonnet-5-5", batch=True) == pytest.approx(18.7 / 2)
    # A dated or unknown id prices as its family rather than crashing.
    assert cost_usd({"output": 1_000_000}, "claude-haiku-4-5-20251001") == pytest.approx(5)


def test_cache_writes_split_by_ttl_when_the_api_reports_it():
    u = SimpleNamespace(input_tokens=10, cache_read_input_tokens=0, output_tokens=5,
                        cache_creation_input_tokens=100,
                        cache_creation=SimpleNamespace(ephemeral_1h_input_tokens=60,
                                                       ephemeral_5m_input_tokens=40))
    assert usage_dict(u) == {"input": 10, "cache_write_5m": 40, "cache_write_1h": 60,
                             "cache_read": 0, "output": 5}
    assert usage_dict(None)["output"] == 0


def test_usage_is_summed_over_every_call_behind_a_grade():
    """A retry is a second paid call; the grade must carry both."""
    claude = FakeClaude(reply(text=""), reply(_v41()))
    grade = Grader(ruleset_id="kb-v41", client=claude, jev=False).grade(_convo())
    u = grade.usage
    assert u["calls"] == 2 and u["batch_calls"] == 0
    assert u["input"] == 1800 and u["cache_read"] == 6000 and u["output"] == 1400
    one = (900 * 2 + 3000 * 0.2 + 700 * 10) / 1e6
    assert u["cost_usd"] == pytest.approx(2 * one, abs=1e-6)


def test_run_totals_and_cache_hit_share():
    blocks = [{"input": 100, "cache_read": 900, "output": 10, "calls": 1, "cost_usd": 0.01}] * 3
    total = sum_usage(blocks)
    assert total["calls"] == 3 and total["cost_usd"] == pytest.approx(0.03)
    assert cache_hit_share(total) == pytest.approx(0.9)


def test_meter_prices_batch_calls_at_half():
    m = UsageMeter()
    u = SimpleNamespace(input_tokens=1_000_000, output_tokens=0, cache_read_input_tokens=0,
                        cache_creation_input_tokens=0)
    m.add(u, "claude-sonnet-5-5", batch=True)
    m.add(u, "claude-sonnet-5-5")
    assert m.as_dict()["cost_usd"] == pytest.approx(1 + 2)
    assert m.as_dict()["batch_calls"] == 1


# ── the cached prefix ──────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("ruleset_id", ["kb-v41", "default", "vip"])
def test_everything_before_the_transcript_is_identical_across_conversations(ruleset_id):
    """The system prompt, model and output format form the cached prefix. Any byte that
    differs per conversation there silently turns every cache read into a cache write."""
    claude = FakeClaude(reply(_v41()))
    grader = Grader(ruleset_id=ruleset_id, client=claude, jev=False)
    a = _convo()
    b = replace(_convo(), id="43", subject="Bonus")
    pa = grader.request_params(grader.messages_for(a))
    pb = grader.request_params(grader.messages_for(b))
    for key in ("model", "system", "output_config", "max_tokens"):
        assert json.dumps(pa[key]) == json.dumps(pb[key]), key
    assert pa["messages"] != pb["messages"]
    assert pa["system"][-1]["cache_control"] == {"type": "ephemeral"}


def test_strict_schema_serialises_identically_every_time():
    rs = get_ruleset("kb-v41")
    assert json.dumps(strict_schema(rs)) == json.dumps(strict_schema(rs))


def test_batch_requests_use_the_one_hour_cache_and_otherwise_match_live():
    grader = Grader(ruleset_id="kb-v41", client=FakeClaude(reply(_v41())), jev=False)
    msgs = grader.messages_for(_convo())
    live, batch = grader.request_params(msgs), grader.request_params(msgs, batch=True)
    assert batch["system"][0]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    assert {k: v for k, v in live.items() if k != "system"} == \
           {k: v for k, v in batch.items() if k != "system"}


def test_live_requests_go_out_with_the_cached_system_prompt():
    claude = FakeClaude(reply(_v41()))
    Grader(ruleset_id="kb-v41", client=claude, jev=False).grade(_convo())
    sent = claude.calls[0]
    assert sent["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert sent["system"][0]["text"] == get_ruleset("kb-v41").prompt_text


def test_v41_prompt_asks_for_no_evidence_on_passes():
    text = get_ruleset("kb-v41").prompt_text
    assert 'pass and n/a — an empty string ""' in text


def test_an_empty_evidence_on_a_pass_is_a_valid_grade():
    data = _v41(criteria=[
        {"id": "resp-no-ghost", "v": "fail", "ev": "Hello, how can I help?"},
        {"id": "crit-data-care", "v": "pass", "ev": ""},
        {"id": "tag-chat", "v": "n/a", "ev": ""},
    ])
    grade = Grader(ruleset_id="kb-v41", client=FakeClaude(reply(data)), jev=False).grade(_convo())
    assert grade.overall_score == 75          # one Major → cap
    assert any(r.rule_id == "crit-data-care" and r.verdict == "pass" for r in grade.rule_results)


# ── warm-up and concurrency ────────────────────────────────────────────────────────────
class _SlowGrader:
    """Records when each grade starts and ends, to check the first finishes before the rest."""

    rules_version = "v"
    ruleset_id = "default"

    def __init__(self):
        self.events: list[tuple[str, str]] = []
        self.active = 0
        self.peak = 0
        self.lock = threading.Lock()

    def grade(self, convo):
        from intercom_summary.qa.schema import ConversationGrade

        with self.lock:
            self.events.append(("start", convo.id))
            self.active += 1
            self.peak = max(self.peak, self.active)
        time.sleep(0.05)
        with self.lock:
            self.active -= 1
            self.events.append(("end", convo.id))
        g = ConversationGrade(conversation_id=convo.id, agent_name="Ada", overall_score=90,
                              summary="", rule_results=[], violations=[], suggestions=[])
        g.rules_version, g.ruleset_id = "v", "default"
        return g


@pytest.fixture
def six_chats(tmp_path, monkeypatch):
    from test_service import _convo as svc_convo

    from intercom_summary.storage.conversations_store import ConversationsStore

    object.__setattr__(settings, "db_path", tmp_path / "svc.db")
    cs = ConversationsStore(settings.db_path)
    for i in range(1, 7):
        cs.save(svc_convo(str(i)))
    cs.close()
    fake = _SlowGrader()
    import intercom_summary.qa.backends as backends_mod

    monkeypatch.setattr(backends_mod, "get_grader", lambda backend=None, ruleset_id=None: fake)
    return fake


def test_one_chat_is_graded_alone_before_the_run_fans_out(six_chats):
    from intercom_summary import service

    service.review_and_store(conversation_ids=[str(i) for i in range(1, 7)], backend="api")
    ev = six_chats.events
    # The first grade has ended before any other has started: its cache write is readable.
    assert ev[0][0] == "start" and ev[1] == ("end", ev[0][1])
    assert six_chats.peak > 1           # …and the rest then ran in parallel


def test_qa_concurrency_caps_parallel_grades(six_chats):
    from intercom_summary import service

    old = settings.qa_concurrency
    try:
        object.__setattr__(settings, "qa_concurrency", 2)
        service.review_and_store(conversation_ids=[str(i) for i in range(1, 7)], backend="api")
    finally:
        object.__setattr__(settings, "qa_concurrency", old)
    assert six_chats.peak == 2


def test_qa_concurrency_defaults_to_ten(monkeypatch):
    from intercom_summary.settings import Settings

    monkeypatch.delenv("QA_CONCURRENCY", raising=False)
    assert Settings().qa_concurrency == 10
    monkeypatch.setenv("QA_CONCURRENCY", "0")
    assert Settings().qa_concurrency == 1
