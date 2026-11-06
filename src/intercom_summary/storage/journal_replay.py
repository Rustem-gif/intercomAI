"""Re-apply the QA action journal (storage/journal.py) onto a restored database.

Used after restoring a backup: everything QA did after the snapshot is in the journal, and this
puts it back — with the original times and authors. Idempotent: an action the database already
reflects (same or newer timestamp) is skipped, so running it twice, or over a range that
overlaps the snapshot, changes nothing.

Actions that cannot be applied — a re-score of a chat that has no grade in the restored DB, a
coaching item whose session is gone — are reported, not dropped silently.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from intercom_summary.settings import settings
from intercom_summary.storage import journal


def replay(since: str | None = None, until: str | None = None, *, dry_run: bool = False,
           db_path: Path | None = None) -> dict[str, Any]:
    from intercom_summary.storage.acknowledgments_store import AcknowledgmentsStore
    from intercom_summary.storage.agent_groups_store import AgentGroupsStore
    from intercom_summary.storage.calibration_store import CalibrationStore
    from intercom_summary.storage.coaching_store import CoachingStore
    from intercom_summary.storage.conversation_comments_store import ConversationCommentsStore
    from intercom_summary.storage.conversations_store import ConversationsStore
    from intercom_summary.storage.grade_disputes_store import GradeDisputesStore
    from intercom_summary.storage.grades_store import GradesStore
    from intercom_summary.storage.vip_cases_store import VipCasesStore

    db = db_path or settings.db_path
    grades, vip = GradesStore(db), VipCasesStore(db)
    comments, disputes = ConversationCommentsStore(db), GradeDisputesStore(db)
    convos, cal = ConversationsStore(db), CalibrationStore(db)
    coaching, acks, groups = CoachingStore(db), AcknowledgmentsStore(db), AgentGroupsStore(db)
    conn = grades._conn
    stats: Counter = Counter()
    unapplied: list[dict] = []

    def row(sql: str, *args):
        return conn.execute(sql, args).fetchone()

    def newer_or_same(stored: str | None, ts: str) -> bool:
        return bool(stored) and stored >= ts

    try:
        for e in journal.entries(since, until):
            a, ts = e["action"], e["ts"]
            cid = e.get("conversation_id")
            reason = None          # why it could not be applied; None = applied or skipped
            skip = False

            if a == "grade.override":
                r = row("SELECT overridden_at FROM grades WHERE conversation_id=?", cid)
                if not r:
                    reason = "no grade in the restored database"
                elif newer_or_same(r["overridden_at"], ts):
                    skip = True
                elif not dry_run:
                    grades.save_override(cid, e["human_score"], e["reason"], e["overridden_by"],
                                         human_criteria=e.get("human_criteria"),
                                         human_deductions=e.get("human_deductions"),
                                         at=ts, journal=False)
            elif a == "grade.confirm":
                r = row("SELECT human_confirmed_at, human_score FROM grades WHERE conversation_id=?", cid)
                if not r:
                    reason = "no grade in the restored database"
                elif e.get("confirmed_by") and (newer_or_same(r["human_confirmed_at"], ts)
                                                or r["human_score"] is not None):
                    skip = True
                elif not e.get("confirmed_by") and not r["human_confirmed_at"]:
                    skip = True
                elif not dry_run:
                    grades.confirm(cid, e.get("confirmed_by"), at=ts, journal=False)
            elif a == "vip.override":
                r = row("SELECT overridden_at FROM vip_grades WHERE case_id=?", e["case_id"])
                if not r:
                    reason = "no VIP grade in the restored database"
                elif newer_or_same(r["overridden_at"], ts):
                    skip = True
                elif not dry_run:
                    vip.save_override(e["case_id"], e["human_score"], e["reason"], e["overridden_by"],
                                      human_criteria=e.get("human_criteria"), at=ts, journal=False)
            elif a == "vip.confirm":
                r = row("SELECT human_confirmed_at, human_score FROM vip_grades WHERE case_id=?",
                        e["case_id"])
                if not r:
                    reason = "no VIP grade in the restored database"
                elif e.get("confirmed_by") and (newer_or_same(r["human_confirmed_at"], ts)
                                                or r["human_score"] is not None):
                    skip = True
                elif not e.get("confirmed_by") and not r["human_confirmed_at"]:
                    skip = True
                elif not dry_run:
                    vip.confirm(e["case_id"], e.get("confirmed_by"), at=ts, journal=False)
            elif a == "comment.add":
                if row("SELECT 1 FROM conversation_comments WHERE id=?", e["comment_id"]):
                    skip = True
                elif not dry_run:
                    comments.add(cid, e["author"], e["text"], comment_id=e["comment_id"],
                                 at=ts, journal=False)
            elif a == "comment.delete":
                if not row("SELECT 1 FROM conversation_comments WHERE id=?", e["comment_id"]):
                    skip = True
                elif not dry_run:
                    comments.delete(e["comment_id"], journal=False)
            elif a == "dispute.create":
                r = row("SELECT created_at FROM grade_disputes WHERE conversation_id=?", cid)
                if r and newer_or_same(r["created_at"], ts):
                    skip = True
                elif not dry_run:
                    conn.execute(
                        """INSERT OR REPLACE INTO grade_disputes
                           (conversation_id, agent_name, reason, created_via, created_by,
                            created_at, status, resolution_note, resolved_by, resolved_at)
                           VALUES (?,?,?,?,?,?,'open',NULL,NULL,NULL)""",
                        (cid, e["agent_name"], e["reason"], e["created_via"], e["created_by"], ts))
                    conn.commit()
            elif a == "dispute.resolve":
                r = row("SELECT resolved_at FROM grade_disputes WHERE conversation_id=?", cid)
                if not r:
                    reason = "dispute not in the restored database"
                elif newer_or_same(r["resolved_at"], ts):
                    skip = True
                elif not dry_run:
                    disputes.resolve(cid, e["status"], e["note"], e["resolved_by"],
                                     at=ts, journal=False)
            elif a == "tags.set":
                r = row("SELECT custom_tags FROM conversations WHERE id=?", cid)
                if not r:
                    reason = "conversation not in the restored database"
                elif (r["custom_tags"] or "") == ",".join(e["tags"]):
                    skip = True
                elif not dry_run:
                    convos.update_custom_tags(cid, e["tags"], at=ts, journal=False)
            elif a == "calibration.review":
                r = row("SELECT reviewed_at FROM calibration_results WHERE run_id=? AND conversation_id=?",
                        e["run_id"], cid)
                if not r:
                    reason = "calibration result not in the restored database"
                elif newer_or_same(r["reviewed_at"], ts):
                    skip = True
                elif not dry_run:
                    cal.save_review(e["run_id"], cid, e["human_score"], e["note"], e["reviewed_by"],
                                    human_criteria=e.get("human_criteria"),
                                    human_deductions=e.get("human_deductions"), at=ts, journal=False)
            elif a == "coaching.add_item":
                if not row("SELECT 1 FROM coaching_sessions WHERE id=?", e["session_id"]):
                    reason = "coaching session not in the restored database"
                elif row("SELECT 1 FROM coaching_items WHERE session_id=? AND conversation_id=?",
                         e["session_id"], cid):
                    skip = True
                elif not dry_run:
                    coaching.add_item(e["session_id"], cid, e.get("note", ""), journal=False)
            elif a == "ack.set":
                present = bool(row("SELECT 1 FROM review_acknowledgments WHERE token=? AND conversation_id=?",
                                   e["token"], cid))
                if present == bool(e["acknowledged"]):
                    skip = True
                elif not dry_run:
                    if e["acknowledged"]:
                        conn.execute("INSERT INTO review_acknowledgments (token, conversation_id, "
                                     "acknowledged_at) VALUES (?,?,?)", (e["token"], cid, ts))
                    else:
                        conn.execute("DELETE FROM review_acknowledgments WHERE token=? AND "
                                     "conversation_id=?", (e["token"], cid))
                    conn.commit()
            elif a == "group.set":
                r = row("SELECT group_id FROM agent_groups WHERE agent_name=?", e["agent_name"])
                if r and r["group_id"] == e["group_id"]:
                    skip = True
                elif not dry_run:
                    groups.set_group(e["agent_name"], e["group_id"], e.get("agent_email", ""),
                                     e.get("intercom_admin_id", ""), e.get("updated_by", ""),
                                     journal=False)
            else:
                reason = f"unknown action {a!r}"

            if reason:
                stats["unapplied"] += 1
                unapplied.append({"ts": ts, "action": a, "id": cid or e.get("case_id"),
                                  "reason": reason})
            elif skip:
                stats["already_present"] += 1
            else:
                stats[f"applied:{a}"] += 1
    finally:
        for s in (grades, vip, comments, disputes, convos, cal, coaching, acks, groups):
            s.close()
    return {"dry_run": dry_run, "since": since, "until": until, "counts": dict(stats),
            "unapplied": unapplied}
