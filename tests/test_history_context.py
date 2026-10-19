"""Historical context in the grading prompt: placement, shadow mode, leakage bounds, batch,
run-level flags and the record on the grade."""
import json
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from intercom_summary import service
from intercom_summary.intercom.models import Admin, Contact, Conversation, Message
from intercom_summary.qa import batch as batch_mod
from intercom_summary.qa import history_context as hc
from intercom_summary.qa.grader import Grader
from intercom_summary.qa.history_labeller import LABEL_VERSION
from intercom_summary.qa.schema import ConversationGrade
from intercom_summary.settings import settings
from intercom_summary.storage.calibration_store import CalibrationStore
from intercom_summary.storage.history_store import HistoryStore
from test_batch import FakeBatchClaude
from test_grader import FakeClaude, _v41, reply
from test_history_store import _label, _row

TARGET_AT = "2026-10-06T12:00:00+00:00"


def _target(cid="42", contact="c1"):
    return Conversation(
        id=cid, created_at=datetime.fromisoformat(TARGET_AT), updated_at=None, state="closed",
        subject="Withdrawal", brand="Betncare",
        assignee=Admin(id="1", name="Ada", email="ada@co.com"),
        contact=Contact(id=contact, name="Kim", email="kim@x.com"),
        messages=[Message(0, "user", "Kim", None, "Where is my withdrawal?"),
                  Message(1, "admin", "Ada", None, "Hello, how can I help?")],
    )


def _seed(tmp_path):
    """The target's own label, two earlier chats of the same player (one with a QA verdict),
    a later chat of theirs, a QA-confirmed precedent, an AI-only lookalike, a future one."""
    hs = HistoryStore(tmp_path / "h.db")
    hs.save_label({**_row("42", _label(), created=TARGET_AT), "label_version": LABEL_VERSION})
    hs.save_label(_row("p-old", _label(summary_short="Withdrawal pending; agent promised 1-3 days.",
                                       promises=["1-3 business days"]), created="2026-09-20T00:00:00+00:00"))
    hs.save_label(_row("p-new", _label(summary_short="Asked again about the withdrawal; escalated."),
                       created="2026-10-01T00:00:00+00:00"))
    hs.save_label(_row("p-later", _label(), created="2026-10-07T00:00:00+00:00"))
    hs.save_label(_row("prec", _label(summary_short="Withdrawal delayed, bank transfer pending; agent gave timeframe.",
                                      agent_errors=["did not confirm escalation"]),
                       contact="c9", created="2026-08-15T00:00:00+00:00"))
    hs.save_label(_row("ai-only", _label(summary_short="Withdrawal delayed, bank transfer pending."),
                       contact="c8", created="2026-08-16T00:00:00+00:00"))
    hs.save_label(_row("prec-future", _label(summary_short="Withdrawal delayed, bank transfer pending."),
                       contact="c7", created="2026-11-01T00:00:00+00:00"))
    for cid, note in (("prec", "escalation not confirmed in a note"), ("prec-future", "x"),
                      ("p-new", "fine")):
        hs._conn.execute(
            """INSERT INTO case_verdicts (conversation_id, source, source_ref, ai_score, human_score,
               criteria_json, note, reviewed_by, reviewed_at)
               VALUES (?, 'override', '', 100, 75, '{"action-escalation-missed": "fail"}', ?, 'kate', '2026-10-02')""",
            (cid, note))
    hs._conn.commit()
    return hs


def _grader(client, history):
    return Grader(ruleset_id="kb-v41", client=client, jev=False, history=history)


# ── the block ────────────────────────────────────────────────────────────────────────────
def test_context_is_the_players_earlier_chats_and_human_precedents_only(tmp_path):
    hs = _seed(tmp_path)
    ctx = hc.HistoryProvider(mode="on", store=hs).context_for(_target())
    assert ctx.contact_prior == ["p-new", "p-old"]          # newest first; not self, not later
    assert ctx.precedents == ["prec"]                        # not the AI-only twin, not the future
    assert ctx.label_source == "cached" and ctx.tokens_est > 0 and len(ctx.index_version) == 12
    text = ctx.text
    assert text.startswith(hc.HEADER) and text.endswith(hc.FOOTER)
    assert "promised: 1-3 business days" in text and "outcome: Pending-legitimate (AI)" in text
    assert 'QA 75 (AI 100): "fine"' in text                  # the player's own chat, QA-scored
    assert "errors noted: did not confirm escalation" in text
    assert "action-escalation-missed=fail" in text
    assert not any(line.startswith(("AGENT", "CUSTOMER")) for line in text.splitlines())
    hs.close()


def test_block_goes_in_front_of_the_transcript_and_leaves_the_cached_prefix_alone(tmp_path):
    hs = _seed(tmp_path)
    on = _grader(FakeClaude(reply(_v41())), hc.HistoryProvider(mode="on", store=hs))
    off = _grader(FakeClaude(reply(_v41())), False)
    pa, pb = on.request_params(_target()), off.request_params(_target())
    for key in ("model", "system", "output_config", "max_tokens"):
        assert json.dumps(pa[key]) == json.dumps(pb[key]), key
    body = pa["messages"][0]["content"]
    assert body.startswith(hc.HEADER)
    assert body.index(hc.FOOTER) < body.index("Conversation ID:")
    assert body.endswith(pb["messages"][0]["content"])
    # The same block in the batch shape, so a batch item is the live request.
    assert on.request_params(_target(), batch=True)["messages"] == pa["messages"]
    hs.close()


def test_shadow_mode_sends_nothing_but_records_what_it_would_have_sent(tmp_path):
    hs = _seed(tmp_path)
    client = FakeClaude(reply(_v41()))
    grader = _grader(client, hc.HistoryProvider(mode="shadow", store=hs))
    grade = grader.grade(_target())
    assert client.calls[0]["messages"] == _grader(FakeClaude(reply(_v41())), False).request_params(_target())["messages"]
    h = grade.history_context
    assert h["mode"] == "shadow" and h["contact_prior"] == ["p-new", "p-old"] and h["text"].startswith(hc.HEADER)
    assert ConversationGrade.from_dict(grade.to_dict()).history_context == h
    hs.close()


def test_the_grade_records_the_context_and_a_batch_finish_reads_the_same_one(tmp_path):
    hs = _seed(tmp_path)
    grader = _grader(FakeClaude(reply(_v41())), hc.HistoryProvider(mode="on", store=hs))
    live = grader.grade(_target())
    finished = grader.finish(_target(), reply(_v41()))
    assert live.history_context["mode"] == "on" and live.history_context == finished.history_context
    assert _grader(FakeClaude(reply(_v41())), False).grade(_target()).history_context == {}
    hs.close()


def test_batch_requests_carry_the_block(tmp_path, monkeypatch):
    monkeypatch.setattr(batch_mod, "POLL_SECONDS", 0)
    hs = _seed(tmp_path)
    client = FakeBatchClaude()
    grader = _grader(client, hc.HistoryProvider(mode="on", store=hs))
    batch_mod.submit(grader, [_target()])
    assert client.submitted[0][0]["params"]["messages"][0]["content"].startswith(hc.HEADER)
    hs.close()


# ── robustness ───────────────────────────────────────────────────────────────────────────
class _BrokenStore:
    def index_version(self):
        return "deadbeef0000"

    def get_label(self, cid):
        raise RuntimeError("index locked")


def test_a_failing_index_never_stops_a_grade():
    grader = _grader(FakeClaude(reply(_v41())), hc.HistoryProvider(mode="on", store=_BrokenStore()))
    grade = grader.grade(_target())
    assert grade.overall_score >= 0 and grade.history_context == {}
    assert grader.history_for(_target()) is None


def test_without_a_label_the_lookup_falls_back_to_keywords_and_still_finds_the_player(tmp_path):
    hs = _seed(tmp_path)
    hs._conn.execute("DELETE FROM chat_labels WHERE conversation_id = '42'")
    hs._conn.commit()
    failing = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(
        create=lambda **kw: (_ for _ in ()).throw(RuntimeError("no labeller")))))
    ctx = hc.HistoryProvider(mode="on", store=hs, client=failing).context_for(_target())
    assert ctx.label_source == "fallback" and ctx.contact_prior == ["p-new", "p-old"]
    hs.close()


def test_a_missing_label_is_made_on_the_spot_and_saved(tmp_path):
    hs = _seed(tmp_path)
    hs._conn.execute("DELETE FROM chat_labels WHERE conversation_id = '42'")
    hs._conn.commit()
    labeller = FakeClaude(reply(_label(), model="claude-haiku-5-5"))
    ctx = hc.HistoryProvider(mode="on", store=hs, client=labeller).context_for(_target())
    assert ctx.label_source == "haiku" and hs.get_label("42")["label_version"] == LABEL_VERSION
    assert labeller.calls[0]["model"] == "claude-haiku-5-5"
    hs.close()


def test_nothing_known_means_no_block(tmp_path):
    hs = HistoryStore(tmp_path / "h.db")
    ctx = hc.HistoryProvider(mode="on", store=hs, client=FakeClaude(reply(_label()))).context_for(_target(contact="nobody"))
    assert ctx is not None and ctx.text == "" and ctx.contact_prior == [] and ctx.precedents == []
    grader = _grader(FakeClaude(reply(_v41())), hc.HistoryProvider(mode="on", store=hs, client=FakeClaude(reply(_label()))))
    assert not grader.request_params(_target(contact="nobody"))["messages"][0]["content"].startswith(hc.HEADER)
    hs.close()


def test_budget_drops_precedents_first_and_never_the_newest_prior_chat():
    prior = [f"- prior {i} " + "x" * 600 for i in range(5)]
    prec = [f"- prec {i} " + "y" * 600 for i in range(3)]
    p, q = hc.budget(prior, prec, max_chars=2000)
    assert q == [] and p == prior[:2]
    p, q = hc.budget(["- one " + "z" * 5000], ["- p"], max_chars=100)
    assert p == ["- one " + "z" * 5000] and q == []
    assert hc.render([], []) == ""


def test_provider_memoises_per_conversation(tmp_path):
    hs = _seed(tmp_path)
    p = hc.HistoryProvider(mode="on", store=hs)
    assert p.context_for(_target()) is p.context_for(_target())
    assert hc.HistoryProvider(mode="off", store=hs).context_for(_target()) is None
    hs.close()


# ── runs ─────────────────────────────────────────────────────────────────────────────────
class _FakeProvider:
    mode = "on"

    def __init__(self):
        self.prepared: list[list[str]] = []

    def prepare(self, convos):
        self.prepared.append([c.id for c in convos])

    def context_for(self, c, **kw):
        return hc.HistoryContext(mode="on", index_version="v", text="")


def test_review_run_prelabels_each_bucket_once_and_passes_only_given_overrides(tmp_path, monkeypatch):
    object.__setattr__(settings, "db_path", tmp_path / "r.db")
    import intercom_summary.qa.backends as backends_mod
    from test_calibration_run import FakeGrader

    seen: list[dict] = []
    fake = FakeGrader()
    fake.history = _FakeProvider()

    def get_grader(backend=None, ruleset_id=None, **kw):
        seen.append(kw)
        return fake

    monkeypatch.setattr(backends_mod, "get_grader", get_grader)
    saved = []
    service.review_and_store(conversations=[_target("1"), _target("2")], save_grade=saved.append,
                             backend="api", ruleset_id="default")
    assert fake.history.prepared == [["1", "2"]] and len(saved) == 2
    assert seen == [{}]
    service.review_and_store(conversations=[_target("3")], save_grade=saved.append,
                             backend="api", ruleset_id="default", history_context="off", effort="low")
    assert seen[-1] == {"history": False, "effort": "low"}


def test_a_real_grader_gets_the_run_overrides(tmp_path, monkeypatch):
    object.__setattr__(settings, "db_path", tmp_path / "r.db")
    import intercom_summary.qa.grader as grader_mod

    made: list[Grader] = []
    real = grader_mod.Grader

    def spy(**kw):
        g = real(client=FakeClaude(reply(_v41())), **kw)
        made.append(g)
        return g

    monkeypatch.setattr(grader_mod, "Grader", spy)
    service.review_and_store(conversations=[_target("1")], save_grade=lambda g: None,
                             backend="api", ruleset_id="kb-v41", history_context="shadow", effort="medium")
    g = made[0]
    assert g.effort == "medium" and g.history.mode == "shadow"
    assert g.request_params(_target())["output_config"]["effort"] == "medium"


def test_calibration_run_records_its_context_mode_and_index_snapshot(tmp_path, monkeypatch):
    object.__setattr__(settings, "db_path", tmp_path / "cal.db")
    from test_calibration_run import FakeGrader, _seed as seed_cal
    import intercom_summary.qa.backends as backends_mod

    seed_cal(settings.db_path)
    fake = FakeGrader()
    monkeypatch.setattr(backends_mod, "get_grader", lambda backend=None, ruleset_id=None, **kw: fake)
    fetch = lambda ids: {i: "not_found" for i in ids}
    r = service.run_calibration("s1", ruleset_id="default", fetch=fetch, history_context="shadow", effort="low")
    cal = CalibrationStore(settings.db_path)
    run = cal.get_run(r["run_id"])
    cal.close()
    assert r["history_context"] == "shadow"
    assert run["history_context"] == "shadow" and len(run["history_index_version"]) == 12
    assert run["effort"] == "low"
    r2 = service.run_calibration("s1", ruleset_id="default", fetch=fetch)
    cal = CalibrationStore(settings.db_path)
    assert cal.get_run(r2["run_id"])["history_context"] == "off"
    assert cal.get_run(r2["run_id"])["history_index_version"] == ""
    cal.close()
