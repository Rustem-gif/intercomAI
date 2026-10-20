"""The history index store: schema, labels, retrieval bounds, verdict gathering."""
import json
import sqlite3
from datetime import datetime, timedelta, timezone

from intercom_summary.intercom.models import Admin, Contact, Conversation, Message
from intercom_summary.storage.conversations_store import ConversationsStore
from intercom_summary.storage.db import connect
from intercom_summary.storage.grades_store import GradesStore
from intercom_summary.storage.history_store import HistoryStore, fts_query, searchable_text


def _convo(cid, *, contact="c1", created="2026-09-10T10:00:00+00:00", tags=(), brand="Betncare",
           text="Where is my withdrawal?"):
    return Conversation(
        id=cid, created_at=datetime.fromisoformat(created), updated_at=None, state="closed",
        subject="Help", brand=brand, assignee=Admin(id="1", name="Ada", email="ada@co.com"),
        contact=Contact(id=contact, name="Kim", email="kim@x.com"), tags=list(tags),
        messages=[Message(0, "user", "Kim", None, text), Message(1, "admin", "Ada", None, "Hi")],
    )


def _label(**over):
    d = {
        "language": "en", "case_type": "Withdrawal", "subtype": "withdrawal pending",
        "player_requests": ["when does the withdrawal arrive"], "agent_actions": ["escalated to Payments"],
        "promises": ["1-3 business days"], "escalated_to": "Payments", "outcome": "Pending-legitimate",
        "agent_errors": [], "correct_handling": "Check status, give the timeframe.",
        "player_state": "calm", "risk_flag": "Financial", "keywords": ["withdrawal", "bank transfer"],
        "summary_short": "Player asked when a pending withdrawal arrives; agent escalated to Payments.",
    }
    d.update(over)
    return d


def _row(cid, label=None, *, contact="c1", created="2026-09-10T10:00:00+00:00", brand="Betncare",
         source="cache"):
    return {"conversation_id": cid, "source": source, "brand": brand, "contact_id": contact,
            "agent_name": "Ada", "created_at": created, "tags": "",
            "label": label or _label(), "label_version": "v1", "model": "claude-haiku-5-5",
            "cost_usd": 0.0002}


# ── schema ───────────────────────────────────────────────────────────────────────────────
def test_schema_adds_history_tables_and_backfills_contact_id(tmp_path):
    db = tmp_path / "old.db"
    old = sqlite3.connect(db)
    old.execute("""CREATE TABLE conversations (
        id TEXT PRIMARY KEY, agent_name TEXT, agent_email TEXT, customer_name TEXT,
        customer_email TEXT, state TEXT, subject TEXT, created_at TEXT, updated_at TEXT,
        message_count INTEGER, csat_rating INTEGER, tags TEXT, payload_json TEXT, fetched_at TEXT)""")
    old.execute("INSERT INTO conversations (id, payload_json) VALUES ('1', ?)",
                (json.dumps({"id": "1", "contact": {"id": "c-9", "name": "", "email": ""}}),))
    old.execute("""CREATE TABLE calibration_runs (id TEXT PRIMARY KEY, sample_id TEXT NOT NULL,
        ruleset_id TEXT NOT NULL, rules_version TEXT, model TEXT, effort TEXT, started_at TEXT NOT NULL,
        finished_at TEXT, job_id TEXT, cost_usd REAL, created_by TEXT NOT NULL DEFAULT '')""")
    old.commit()
    old.close()

    conn = connect(db)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"chat_labels", "chat_labels_fts", "case_verdicts", "meta"} <= tables
    assert conn.execute("SELECT contact_id FROM conversations WHERE id='1'").fetchone()[0] == "c-9"
    indexes = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='conversations'")}
    assert "idx_convo_contact" in indexes
    run_cols = {r[1] for r in conn.execute("PRAGMA table_info(calibration_runs)")}
    assert {"history_context", "history_index_version"} <= run_cols
    conn.close()
    connect(db).close()   # idempotent


def test_conversations_store_writes_contact_id(tmp_path):
    db = tmp_path / "c.db"
    cs = ConversationsStore(db)
    cs.save(_convo("1", contact="c-77"))
    assert cs._conn.execute("SELECT contact_id FROM conversations WHERE id='1'").fetchone()[0] == "c-77"
    cs.close()


# ── labels ───────────────────────────────────────────────────────────────────────────────
def test_save_label_upserts_and_keeps_the_index_in_step(tmp_path):
    hs = HistoryStore(tmp_path / "h.db")
    hs.save_label(_row("1"))
    hs.save_label(_row("1", _label(case_type="Bonus", subtype="bonus gone", summary_short="Second version about a bonus.",
                                   keywords=["bonus"], player_requests=["where is my bonus"])))
    assert hs.get_label("1")["label"]["keywords"] == ["bonus"]
    assert hs._conn.execute("SELECT COUNT(*) FROM chat_labels").fetchone()[0] == 1
    assert hs._conn.execute("SELECT COUNT(*) FROM chat_labels_fts").fetchone()[0] == 1
    assert hs.search("bonus") and not hs.search("withdrawal")
    assert hs.labelled_ids("v1") == {"1"} and hs.labelled_ids("v2") == set()
    hs.close()


def test_fts_query_drops_stopwords_and_quotes_terms():
    assert fts_query("Where is my withdrawal to the bank?") == '"where" OR "withdrawal" OR "bank"'
    assert fts_query("the a of") == ""
    assert "bonus" in searchable_text(_label(keywords=["bonus"]))


def test_for_contact_returns_only_earlier_chats_of_that_player_within_the_window(tmp_path):
    hs = HistoryStore(tmp_path / "h.db")
    hs.save_label(_row("old", created="2026-01-01T00:00:00+00:00"))          # too old
    hs.save_label(_row("a", created="2026-08-01T00:00:00+00:00"))
    hs.save_label(_row("b", created="2026-09-01T00:00:00+00:00"))
    hs.save_label(_row("same", created="2026-09-10T10:00:00+00:00"))         # the target itself
    hs.save_label(_row("later", created="2026-09-20T00:00:00+00:00"))        # the future
    hs.save_label(_row("other", contact="c2", created="2026-09-05T00:00:00+00:00"))
    got = hs.for_contact("c1", before="2026-09-10T10:00:00+00:00", exclude={"same"}, days=180)
    assert [r["conversation_id"] for r in got] == ["b", "a"]
    assert [r["conversation_id"] for r in hs.for_contact("c1", before="2026-09-10T10:00:00+00:00",
                                                         exclude={"same"}, days=180, limit=1)] == ["b"]
    assert hs.for_contact("", before="2026-09-10T10:00:00+00:00") == []
    hs.close()


def test_search_filters_and_ranks_by_overlap(tmp_path):
    hs = HistoryStore(tmp_path / "h.db")
    hs.save_label(_row("w1", _label(summary_short="Withdrawal pending, bank transfer delayed.",
                                    keywords=["withdrawal", "bank transfer"])))
    hs.save_label(_row("w2", _label(summary_short="Withdrawal question answered quickly.",
                                    keywords=["withdrawal"])))
    hs.save_label(_row("b1", _label(case_type="Bonus", subtype="wagering not met", summary_short="Bonus wagering not met.",
                                    keywords=["bonus", "wagering"], player_requests=["release my bonus winnings"])))
    hs.save_label(_row("tr", _label(summary_short="Withdrawal bank transfer delayed."), brand="Tomb Riches"))
    hs.save_label(_row("fut", _label(summary_short="Withdrawal bank transfer delayed."),
                       created="2026-12-01T00:00:00+00:00"))
    q = "withdrawal bank transfer delayed"
    ids = [r["conversation_id"] for r in hs.search(q, brand="Betncare", before="2026-10-01T00:00:00+00:00")]
    assert ids[0] == "w1" and "tr" not in ids and "fut" not in ids and "b1" not in ids
    assert [r["conversation_id"] for r in hs.search(q, brand="Betncare", case_type="Bonus")] == []
    assert "w1" not in [r["conversation_id"] for r in hs.search(q, brand="Betncare", exclude={"w1"})]
    assert hs.search(q, human_only=True) == []
    hs._conn.execute("INSERT INTO case_verdicts (conversation_id, source) VALUES ('w2', 'override')")
    hs._conn.commit()
    assert [r["conversation_id"] for r in hs.search(q, human_only=True)] == ["w2"]
    assert hs.search("") == []
    hs.close()


def test_unlabelled_cache_ids_skips_ignored_tags_and_known_labels(tmp_path):
    db = tmp_path / "h.db"
    cs = ConversationsStore(db)
    cs.save(_convo("1", created="2026-09-01T00:00:00+00:00"))
    cs.save(_convo("2", created="2026-09-02T00:00:00+00:00", tags=["Spam"]))
    cs.save(_convo("3", created="2026-09-03T00:00:00+00:00"))
    cs.close()
    hs = HistoryStore(db)
    hs.save_label(_row("3"))
    assert hs.unlabelled_cache_ids("v1") == ["1"]
    assert hs.unlabelled_cache_ids("v2") == ["1", "3"]
    assert hs.unlabelled_cache_ids("v2", since="2026-09-03") == ["3"]
    assert hs.unlabelled_cache_ids("v2", limit=1) == ["1"]
    assert {c.id for c in hs.load_conversations(["1", "3"])} == {"1", "3"}
    assert hs.cached_ids() == {"1", "2", "3"}
    hs.close()


# ── verdicts ─────────────────────────────────────────────────────────────────────────────
def test_rebuild_verdicts_gathers_every_human_source(tmp_path):
    db = tmp_path / "h.db"
    conn = connect(db)
    conn.execute("""INSERT INTO grades (conversation_id, overall_score, ruleset_id, human_score,
                    override_reason, human_criteria, overridden_by, overridden_at, payload_json)
                    VALUES ('1', 60, 'kb-v41', 90, 'escalation was fine', '{"a":"pass"}', 'kate',
                            '2026-09-01', '{}')""")
    conn.execute("INSERT INTO grades (conversation_id, overall_score, payload_json) VALUES ('2', 100, '{}')")
    conn.execute("""INSERT INTO calibration_runs (id, sample_id, ruleset_id, started_at)
                    VALUES ('r1', 's', 'kb-v41', '2026-10-01')""")
    conn.execute("""INSERT INTO calibration_results (run_id, conversation_id, status, overall_score,
                    human_score, human_note, reviewed_by) VALUES ('r1', '3', 'graded', 80, 70, 'ghosted', 'ann')""")
    conn.execute("""INSERT INTO calibration_results (run_id, conversation_id, status, overall_score)
                    VALUES ('r1', '4', 'graded', 80)""")
    conn.execute("""INSERT INTO conversation_comments (id, conversation_id, author, text, created_at)
                    VALUES ('m1', '2', 'christina', 'No escalation to VIP was made.', '2026-09-02')""")
    conn.execute("""INSERT INTO grade_disputes (conversation_id, agent_name, reason, created_via,
                    created_by, created_at, status, resolution_note, resolved_by, resolved_at)
                    VALUES ('2', 'Ada', 'unfair', 'portal', 'Ada', '2026-09-03', 'accepted', 'ok', 'kate', '2026-09-04'),
                           ('5', 'Ada', 'unfair', 'portal', 'Ada', '2026-09-03', 'open', NULL, NULL, NULL)""")
    conn.commit()
    conn.close()

    hs = HistoryStore(db)
    assert hs.rebuild_verdicts() == {"override": 1, "calibration": 1, "comment": 1, "dispute": 1}
    v = hs.verdicts_for(["1", "2", "3", "5"])
    assert v["1"][0]["human_score"] == 90 and v["1"][0]["criteria"] == {"a": "pass"}
    assert v["3"][0]["source"] == "calibration" and v["3"][0]["note"] == "ghosted"
    assert {x["source"] for x in v["2"]} == {"comment", "dispute"}
    assert v["2"][0]["ai_score"] == 100
    assert "5" not in v
    assert hs.rebuild_verdicts()["override"] == 1   # idempotent
    hs.close()


def test_index_version_and_meta_track_changes(tmp_path):
    hs = HistoryStore(tmp_path / "h.db")
    v0 = hs.index_version()
    hs.save_label(_row("1"))
    v1 = hs.index_version()
    assert v0 != v1 and len(v1) == 12
    assert hs.get_meta("history.last_refresh_at") is None
    hs.set_meta("history.last_refresh_at", "2026-10-19")
    hs.set_meta("history.last_refresh_at", "2026-10-20")
    assert hs.get_meta("history.last_refresh_at") == "2026-10-20"
    s = hs.stats()
    assert s["labels"] == 1 and s["by_case_type"] == [{"key": "Withdrawal", "n": 1}]
    assert s["index_version"] == v1 and s["last_refresh_at"] == "2026-10-20"
    assert hs.rebuild_fts() == 1
    hs.close()
