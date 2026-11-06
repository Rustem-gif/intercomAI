import json
import shutil

import pytest

from intercom_summary import backup
from intercom_summary.settings import settings
from intercom_summary.storage import journal
from intercom_summary.storage.conversation_comments_store import ConversationCommentsStore
from intercom_summary.storage.conversations_store import ConversationsStore
from intercom_summary.storage.db import connect
from intercom_summary.storage.grade_disputes_store import GradeDisputesStore
from intercom_summary.storage.grades_store import GradesStore
from intercom_summary.storage.journal_replay import replay


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "grades.db"
    conn = connect(path)
    for cid in ("c1", "c2"):
        conn.execute("INSERT INTO conversations (id, agent_name, payload_json) VALUES (?, 'Ada', '{}')",
                     (cid,))
        conn.execute("INSERT INTO grades (conversation_id, agent_name, overall_score, payload_json) "
                     "VALUES (?, 'Ada', 80, '{}')", (cid,))
    conn.commit()
    conn.close()
    return path


def _lines():
    return [json.loads(l) for f in sorted(settings.journal_dir.glob("*.jsonl"))
            for l in f.read_text().splitlines() if l.strip()]


def test_human_actions_are_journaled(db):
    g = GradesStore(db)
    g.save_override("c1", 60, " too slow ", "kate")
    g.confirm("c2", "daria")
    g.close()
    c = ConversationCommentsStore(db)
    c.add("c1", "kate", "see the deposit part")
    c.close()
    d = GradeDisputesStore(db)
    d.create("c1", "Ada", "I did answer", created_via="portal", created_by="Ada")
    d.close()
    s = ConversationsStore(db)
    s.update_custom_tags("c1", ["rescore-lost", " "])
    s.close()

    acts = [e["action"] for e in _lines()]
    assert acts == ["grade.override", "grade.confirm", "comment.add", "dispute.create", "tags.set"]
    first = _lines()[0]
    assert first["conversation_id"] == "c1" and first["human_score"] == 60
    assert first["reason"] == "too slow" and first["overridden_by"] == "kate" and first["ts"]
    assert _lines()[-1]["tags"] == ["rescore-lost"]


def test_replay_restores_lost_work_and_is_idempotent(db, tmp_path):
    snapshot = tmp_path / "snapshot.db"
    shutil.copy(db, snapshot)            # the "backup", taken before QA's work

    g = GradesStore(db)
    g.save_override("c1", 55, "missed KYC step", "kate", human_criteria={"kyc": "fail"})
    g.confirm("c2", "daria")
    g.close()
    c = ConversationCommentsStore(db)
    comment = c.add("c1", "kate", "coach this")
    c.close()

    # The live DB is lost; restore the snapshot and replay the journal onto it.
    r = replay(db_path=snapshot)
    assert r["counts"] == {"applied:grade.override": 1, "applied:grade.confirm": 1,
                           "applied:comment.add": 1}
    gs = GradesStore(snapshot)
    c1, c2 = gs.get("c1"), gs.get("c2")
    gs.close()
    live = GradesStore(db)
    original = live.get("c1")
    live.close()
    assert c1["human_score"] == 55 and c1["override_reason"] == "missed KYC step"
    assert c1["overridden_by"] == "kate" and c1["human_criteria"] == {"kyc": "fail"}
    assert c1["overridden_at"] == original["overridden_at"]     # the original time, not now
    assert c2["human_confirmed_by"] == "daria"
    cs = ConversationCommentsStore(snapshot)
    assert [x["id"] for x in cs.list("c1")] == [comment["id"]]
    cs.close()

    before = len(_lines())
    again = replay(db_path=snapshot)
    assert again["counts"] == {"already_present": 3}
    assert len(_lines()) == before                               # replay journals nothing


def test_replay_reports_what_it_cannot_apply(db, tmp_path):
    g = GradesStore(db)
    g.save_override("c1", 40, "rude", "kate")
    g.close()
    empty = tmp_path / "empty.db"
    connect(empty).close()
    r = replay(db_path=empty)
    assert r["counts"] == {"unapplied": 1}
    assert r["unapplied"][0]["reason"] == "no grade in the restored database"


def test_dry_run_changes_nothing(db, tmp_path):
    snapshot = tmp_path / "s.db"
    shutil.copy(db, snapshot)
    g = GradesStore(db)
    g.save_override("c1", 70, "ok-ish", "kate")
    g.close()
    assert replay(db_path=snapshot, dry_run=True)["counts"] == {"applied:grade.override": 1}
    gs = GradesStore(snapshot)
    assert gs.get("c1")["human_score"] is None
    gs.close()


def test_a_broken_journal_never_blocks_the_rescore(db, tmp_path, monkeypatch):
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x")
    object.__setattr__(settings, "journal_dir", blocker / "journal")
    g = GradesStore(db)
    assert g.save_override("c1", 50, "late reply", "kate")
    assert g.get("c1")["human_score"] == 50
    g.close()
    assert journal.status()["last_error"]
    journal._state.update(last_error=None, last_error_at=None)


def test_snapshot_records_journal_through_and_health(db, tmp_path):
    object.__setattr__(settings, "rclone_remote", "")
    g = GradesStore(db)
    g.save_override("c1", 65, "tone", "kate")
    g.close()
    result = backup.run_backup(db_path=db, backup_dir=tmp_path / "bk", root=tmp_path)
    assert result["journal_through"] == _lines()[-1]["ts"]
    assert (tmp_path / "bk" / result["stamp"] / "journal").is_dir()
    h = backup.health(db)
    assert h["ok"], h["problems"]
