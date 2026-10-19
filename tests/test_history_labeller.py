"""Labelling chats for the history index: request shape, parsing, live and batch paths, archives."""
import gzip
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from intercom_summary import service
from intercom_summary.intercom.archive import find_archives, iter_archive, load_admins
from intercom_summary.intercom.models import Admin, Contact, Conversation, Message
from intercom_summary.qa import history_labeller as hl
from intercom_summary.settings import settings
from intercom_summary.storage.conversations_store import ConversationsStore
from intercom_summary.storage.history_store import HistoryStore
from test_batch import FakeBatchClaude
from test_grader import reply
from test_history_store import _label


def _convo(cid="42", *, tags=("KYC",), contact="c1"):
    return Conversation(
        id=cid, created_at=datetime(2026, 9, 10, 10, tzinfo=timezone.utc), updated_at=None,
        state="closed", subject="Verification", brand="Betncare", tags=list(tags),
        assignee=Admin(id="1", name="Ada", email="ada@co.com"),
        contact=Contact(id=contact, name="Kim", email="kim@x.com"),
        messages=[Message(0, "user", "Kim", None, "My documents were rejected again, why?"),
                  Message(1, "admin", "Ada", None, "Let me check that for you.")],
    )


# ── request ──────────────────────────────────────────────────────────────────────────────
def test_label_request_is_a_cached_structured_haiku_call():
    p = hl.label_request(_convo())
    assert p["model"] == "claude-haiku-5-5"
    assert p["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert hl.label_request(_convo(), batch=True)["system"][0]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    assert p["output_config"]["format"]["schema"] is hl.LABEL_SCHEMA
    assert p["output_config"]["effort"] == "low"
    body = p["messages"][0]["content"]
    assert body.startswith("Chat date: 2026-09-10 · Brand: King Billy · Tags: KYC")
    assert "CUSTOMER Kim" in body and "AGENT Ada" in body
    assert len(hl.LABEL_VERSION) == 12


# ── parsing ──────────────────────────────────────────────────────────────────────────────
def test_parse_label_normalises_vocabulary_and_clips_lists():
    raw = _label(case_type="Cashout", outcome="Done", player_state="livid", language="xx",
                 player_requests=["a", "b", "c", "d"], keywords=[f"k{i}" for i in range(12)],
                 subtype="  a   very  long subtype with far too many words in it  ")
    out = hl.parse_label(reply(raw))
    assert out["case_type"] == "Other" and out["outcome"] == "Unresolved"
    assert out["player_state"] == "calm" and out["language"] == "other"
    assert out["player_requests"] == ["a", "b", "c"] and len(out["keywords"]) == 8
    assert out["subtype"] == "a very long subtype with far too many"
    assert set(out) == set(hl.LABEL_SCHEMA["properties"])


@pytest.mark.parametrize("resp", [
    reply(stop_reason="refusal", text=""),
    reply(stop_reason="max_tokens", text="{"),
    reply(text="not json"),
    reply(text="[1, 2]"),
    reply(_label(summary_short="   ")),
])
def test_parse_label_rejects_unusable_responses(resp):
    assert hl.parse_label(resp) is None


def test_label_row_and_fallback_query():
    row = hl.label_row(_convo(), _label(), source="cache", model="claude-haiku-5-5", cost_usd=0.00021)
    assert (row["conversation_id"], row["brand"], row["contact_id"], row["tags"]) == ("42", "Betncare", "c1", "KYC")
    assert row["created_at"] == "2026-09-10T10:00:00+00:00" and row["label_version"] == hl.LABEL_VERSION
    assert row["cost_usd"] == 0.00021
    assert hl.fallback_query(_convo()) == "KYC Verification My documents were rejected again, why?"


# ── live ─────────────────────────────────────────────────────────────────────────────────
class FakeAsyncClaude:
    def __init__(self, data, fail_ids=()):
        self.data, self.fail_ids, self.calls = data, set(fail_ids), []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    async def _create(self, **kwargs):
        self.calls.append(kwargs)
        body = kwargs["messages"][0]["content"]
        if any(f"Tags: {fid}" in body for fid in self.fail_ids):
            raise RuntimeError("boom")
        return reply(self.data, model="claude-haiku-5-5")


async def test_label_live_saves_each_label_and_counts_failures(tmp_path):
    hs = HistoryStore(tmp_path / "h.db")
    client = FakeAsyncClaude(_label(), fail_ids={"BOOM"})
    convos = [_convo("1"), _convo("2", tags=("BOOM",)), _convo("3")]
    progress = []
    st = await hl.label_live(client, convos, hs, source="cache", on_progress=lambda d, t: progress.append((d, t)))
    assert (st["labelled"], st["failed"]) == (2, 1) and st["cost_usd"] > 0
    assert hs.labelled_ids(hl.LABEL_VERSION) == {"1", "3"}
    assert hs.get_label("1")["model"] == "claude-haiku-5-5" and hs.get_label("1")["cost_usd"] > 0
    assert progress[-1] == (3, 3)
    hs.close()


def test_label_one_survives_a_failing_client():
    class Boom:
        beta = SimpleNamespace(messages=SimpleNamespace(create=lambda **kw: (_ for _ in ()).throw(RuntimeError("x"))))
    assert hl.label_one(Boom(), _convo()) is None


# ── batch ────────────────────────────────────────────────────────────────────────────────
def test_label_batch_submits_the_label_request_and_relabels_unusable_items_live(tmp_path, monkeypatch):
    from intercom_summary.qa import batch as batch_mod
    monkeypatch.setattr(batch_mod, "POLL_SECONDS", 0)
    hs = HistoryStore(tmp_path / "h.db")
    client = FakeBatchClaude(outcomes={"2": "junk", "3": "errored"}, data=_label())
    st = hl.label_batch(client, [_convo("1"), _convo("2"), _convo("3")], hs, source="archive", poll=0)
    assert (st["labelled"], st["failed"], st["fallbacks"]) == (3, 0, 2)
    req = client.submitted[0][0]
    assert req["custom_id"] == "1" and req["params"]["system"][0]["cache_control"]["ttl"] == "1h"
    assert req["params"]["output_config"]["format"]["schema"] is hl.LABEL_SCHEMA
    assert len(client.live_calls) == 2
    assert hs.get_label("3")["source"] == "archive"
    # A batch label is priced at half; the live fallback at full.
    assert hs.get_label("1")["cost_usd"] < hs.get_label("2")["cost_usd"]
    hs.close()


# ── archives ─────────────────────────────────────────────────────────────────────────────
def _raw(cid, *, source_type="conversation", ticket=None, created=1757500000):
    return {"id": cid, "created_at": created, "updated_at": created, "state": "closed",
            "ticket": ticket, "admin_assignee_id": 7,
            "custom_attributes": {"Brand": "Betncare"},
            "source": {"type": source_type, "body": "<p>Hello, my bonus is gone</p>",
                       "author": {"type": "user", "name": "Kim", "email": "kim@x.com"}},
            "contacts": {"contacts": [{"id": "c-1"}]},
            "conversation_parts": {"conversation_parts": [
                {"part_type": "comment", "body": "<p>Let me check</p>", "created_at": created + 60,
                 "author": {"type": "admin", "name": "Ada"}}]},
            "tags": {"tags": [{"name": "Bonus"}]}}


def _archive(root, rows, admins=True):
    raw = root / "raw"
    raw.mkdir(parents=True)
    with gzip.open(raw / "conversations.jsonl.gz", "wt", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    if admins:
        (raw / "admins.json").write_text(json.dumps([{"id": 7, "name": "Ada", "email": "ada@co.com"}]))
    return raw


def test_iter_archive_yields_chats_once_without_tickets_emails_or_skipped_ids(tmp_path):
    a = _archive(tmp_path / "client-archive-a", [_raw("1"), _raw("2", source_type="email"),
                                                 _raw("3", ticket={"id": 9}), _raw("4"), _raw("5")])
    b = _archive(tmp_path / "client-archive-b", [_raw("4"), _raw("6")], admins=False)
    assert find_archives(tmp_path) == [a, b]
    assert load_admins(a)["7"].name == "Ada" and load_admins(b) == {}
    got = list(iter_archive([a, b], skip_ids={"5"}))
    assert [c.id for c in got] == ["1", "4", "6"]
    assert got[0].assignee.name == "Ada" and got[0].contact.id == "c-1" and got[0].brand == "Betncare"
    assert got[0].tags == ["Bonus"] and got[0].messages[0].text == "Hello, my bonus is gone"
    assert find_archives(tmp_path / "nowhere") == []


# ── service ──────────────────────────────────────────────────────────────────────────────
@pytest.fixture
def history_env(tmp_path, monkeypatch):
    object.__setattr__(settings, "db_path", tmp_path / "svc.db")
    object.__setattr__(settings, "export_dir", tmp_path / "exports")
    object.__setattr__(settings, "anthropic_api_key", "sk-test")
    cs = ConversationsStore(settings.db_path)
    cs.save(_convo("1"))
    cs.save(_convo("2", tags=("Spam",)))
    cs.close()
    _archive(tmp_path / "exports" / "client-archive-x", [_raw("1"), _raw("9")])
    fake = FakeAsyncClaude(_label())
    import anthropic
    monkeypatch.setattr(anthropic, "AsyncAnthropic", lambda **kw: fake)
    return fake


def test_label_history_labels_cache_and_archive_without_importing(history_env):
    r = service.label_history(source="all", batch=False)
    assert (r["candidates"], r["labelled"], r["failed"]) == (2, 2, 0)
    assert r["by_source"]["cache"]["labelled"] == 1 and r["by_source"]["archive"]["labelled"] == 1
    hs = HistoryStore(settings.db_path)
    assert hs.get_label("1")["source"] == "cache" and hs.get_label("9")["source"] == "archive"
    assert hs.cached_ids() == {"1", "2"}          # the archive chat was labelled, not imported
    assert service.label_history(source="all", batch=False)["candidates"] == 0   # nothing left
    assert service.label_history(source="cache", batch=False, relabel=True)["candidates"] == 1
    hs.close()


def test_refresh_history_rebuilds_verdicts_index_and_meta(history_env):
    r = service.refresh_history(batch=False)
    assert r["labelled"] == 1 and r["indexed"] == 1 and r["verdicts"] == {}
    s = service.history_stats()
    assert s["labels"] == 1 and s["unlabelled_cache"] == 0 and s["label_version"] == hl.LABEL_VERSION
    assert s["last_refresh_at"] and s["index_version"] == r["index_version"]
    assert s["archives"] == ["client-archive-x"] and s["mode"] == "off"


def test_collect_label_batch_saves_an_interrupted_batch(tmp_path, monkeypatch):
    from intercom_summary.qa import batch as batch_mod
    monkeypatch.setattr(batch_mod, "POLL_SECONDS", 0)
    hs = HistoryStore(tmp_path / "h.db")
    client = FakeBatchClaude(outcomes={"2": "junk"}, data=_label())
    convos = {c.id: c for c in (_convo("1"), _convo("2"), _convo("3"))}
    client.beta.messages.batches.create(requests=[
        {"custom_id": cid, "params": hl.label_request(c, batch=True)} for cid, c in convos.items()])
    st = hl.collect_label_batch(client, ["msgbatch_1"], hs, {k: v for k, v in convos.items() if k != "3"},
                                source="cache", poll=0)
    assert (st["labelled"], st["failed"], st["unknown"]) == (1, 1, 1)
    assert hs.labelled_ids(hl.LABEL_VERSION) == {"1"} and not client.live_calls
    hs.close()


def test_service_collect_label_batch_reads_cache_chats(history_env, monkeypatch):
    from intercom_summary.qa import batch as batch_mod
    monkeypatch.setattr(batch_mod, "POLL_SECONDS", 0)
    import anthropic
    client = FakeBatchClaude(data=_label())
    monkeypatch.setattr(anthropic, "Anthropic", lambda **kw: client)
    client.beta.messages.batches.create(requests=[
        {"custom_id": "1", "params": hl.label_request(_convo("1"), batch=True)}])
    r = service.collect_label_batch(["msgbatch_1"], source="cache")
    assert r["labelled"] == 1
    assert HistoryStore(settings.db_path).get_label("1")["source"] == "cache"

