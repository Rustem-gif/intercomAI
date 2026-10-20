"""Persistence for VIP cases (vip_cases.py) and their Scorecard v1.6 grades.

`vip_grades` is deliberately not `grades`: that table feeds the support dashboards, agent stats
and the chat grading queue, and a VIP manager's case score is on a different scale for a
different job. An analyst override keeps the AI original beside it (scorecard §11) and requires
a written reason.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from intercom_summary.settings import settings


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class VipCasesStore:
    def __init__(self, db_path: str | Path | None = None) -> None:
        from intercom_summary.storage.db import connect
        self._conn = connect(db_path or settings.db_path)

    def close(self) -> None:
        self._conn.close()

    # ── Cases ──────────────────────────────────────────────────────────────────────────────
    def replace_cases(self, cases: list) -> None:
        """Swap the whole case set for a fresh grouping. Grades are keyed by case_id and kept:
        a case whose first thread is unchanged keeps its id, and `case_version` tells whether
        its grade still describes it."""
        now = _now()
        with self._conn:
            self._conn.execute("DELETE FROM vip_cases")
            self._conn.execute("DELETE FROM vip_case_threads")
            for c in cases:
                self._conn.execute(
                    """INSERT INTO vip_cases
                         (case_id, contact_id, contact_name, contact_email, brand, topic,
                          first_at, last_at, thread_count, threads_json, managers_json,
                          primary_manager, case_version, built_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (c.case_id, c.contact_id, c.contact_name, c.contact_email, c.brand, c.topic,
                     c.first_at, c.last_at, len(c.threads), json.dumps(c.thread_summaries()),
                     json.dumps(c.managers), c.primary_manager, c.case_version, now),
                )
                self._conn.executemany(
                    "INSERT OR REPLACE INTO vip_case_threads (thread_id, case_id) VALUES (?, ?)",
                    [(tid, c.case_id) for tid in c.thread_ids],
                )

    @staticmethod
    def _case(row) -> dict:
        d = dict(row)
        d["threads"] = json.loads(d.pop("threads_json"))
        d["managers"] = json.loads(d.pop("managers_json"))
        d["thread_ids"] = [t["id"] for t in d["threads"]]
        return d

    def count(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM vip_cases").fetchone()[0]

    def get_case(self, case_id: str) -> dict | None:
        row = self._conn.execute("SELECT * FROM vip_cases WHERE case_id = ?", (case_id,)).fetchone()
        return self._case(row) if row else None

    def case_for_thread(self, thread_id: str) -> str | None:
        row = self._conn.execute(
            "SELECT case_id FROM vip_case_threads WHERE thread_id = ?", (thread_id,)).fetchone()
        return row[0] if row else None

    def cases(self, *, manager: str | None = None, brand: str | None = None,
              search: str | None = None, since: str | None = None, until: str | None = None,
              result: str | None = None, flag: str | None = None) -> list[dict]:
        """Cases newest first, each with its grade summary (or None when ungraded).

        `result` = PASS | FAIL | ungraded | stale; `flag` = critical | service_failure | manual.
        """
        where, params = [], []
        if manager:
            where.append("c.primary_manager = ?")
            params.append(manager)
        if brand:
            where.append("c.brand = ?")
            params.append(brand)
        if since:
            where.append("substr(c.last_at, 1, 10) >= ?")
            params.append(since)
        if until:
            where.append("substr(c.first_at, 1, 10) <= ?")
            params.append(until)
        if search:
            like = f"%{search.lower()}%"
            where.append("(lower(c.contact_name) LIKE ? OR lower(c.contact_email) LIKE ? "
                         "OR lower(c.topic) LIKE ? OR c.case_id LIKE ? OR c.threads_json LIKE ?)")
            params += [like, like, like, like, like]
        if result in ("PASS", "FAIL"):
            where.append("COALESCE(CASE WHEN g.human_score IS NOT NULL THEN "
                         "(CASE WHEN g.human_score >= 85 AND g.critical_flag = 0 THEN 'PASS' "
                         "ELSE 'FAIL' END) END, g.overall_result) = ?")
            params.append(result)
        elif result == "ungraded":
            where.append("g.case_id IS NULL")
        elif result == "stale":
            where.append("g.case_id IS NOT NULL AND g.case_version != c.case_version")
        if flag == "critical":
            where.append("g.critical_flag = 1")
        elif flag == "service_failure":
            where.append("g.service_failure_flag = 1")
        elif flag == "manual":
            where.append("g.manual_review_needed = 1")
        sql = ("SELECT c.*, g.overall_score, g.overall_result, g.critical_flag, "
               "g.service_failure_flag, g.manual_review_needed, g.outcome_status, g.case_type, "
               "g.human_score, g.case_version AS graded_case_version, g.graded_at, "
               "g.rules_version FROM vip_cases c LEFT JOIN vip_grades g ON g.case_id = c.case_id")
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY c.last_at DESC"
        out = []
        for r in self._conn.execute(sql, params).fetchall():
            d = self._case(r)
            graded = d.pop("graded_case_version")
            grade_cols = ("overall_score", "overall_result", "critical_flag",
                          "service_failure_flag", "manual_review_needed", "outcome_status",
                          "case_type", "human_score", "graded_at", "rules_version")
            g = {k: d.pop(k) for k in grade_cols}
            d["grade"] = None if graded is None else {
                **g, "critical_flag": bool(g["critical_flag"]),
                "service_failure_flag": bool(g["service_failure_flag"]),
                "manual_review_needed": bool(g["manual_review_needed"]),
                "stale": graded != d["case_version"],
            }
            out.append(d)
        return out

    def summary(self) -> dict:
        c = self._conn
        cases = c.execute("SELECT COUNT(*), SUM(thread_count > 1), SUM(thread_count) "
                          "FROM vip_cases").fetchone()
        g = c.execute(
            """SELECT COUNT(*) AS graded,
                      SUM(COALESCE(g.human_score, g.overall_score) >= 85 AND g.critical_flag = 0)
                        AS passed,
                      SUM(g.critical_flag) AS critical, SUM(g.service_failure_flag) AS service,
                      SUM(g.manual_review_needed) AS manual,
                      AVG(COALESCE(g.human_score, g.overall_score)) AS avg_score,
                      SUM(g.case_version != c.case_version) AS stale
                 FROM vip_grades g JOIN vip_cases c ON c.case_id = g.case_id""").fetchone()
        return {
            "cases": cases[0] or 0, "multi_thread": cases[1] or 0, "threads": cases[2] or 0,
            "graded": g["graded"] or 0, "passed": g["passed"] or 0,
            "critical": g["critical"] or 0, "service_failure": g["service"] or 0,
            "manual_review": g["manual"] or 0, "stale": g["stale"] or 0,
            "avg_score": round(g["avg_score"], 1) if g["avg_score"] is not None else None,
        }

    # ── Grades ─────────────────────────────────────────────────────────────────────────────
    def save_grade(self, grade, case_version: str, primary_manager: str = "") -> None:
        """Upsert the AI grade. An analyst override on the row survives a re-grade, as in
        `grades` — the AI original changes, the human verdict does not."""
        self._conn.execute(
            """INSERT INTO vip_grades
                 (case_id, primary_manager, overall_score, overall_result, ruleset_id,
                  rules_version, case_version, model, graded_at, critical_flag,
                  service_failure_flag, manual_review_needed, outcome_status, case_type,
                  payload_json)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(case_id) DO UPDATE SET
                 primary_manager=excluded.primary_manager,
                 overall_score=excluded.overall_score, overall_result=excluded.overall_result,
                 ruleset_id=excluded.ruleset_id, rules_version=excluded.rules_version,
                 case_version=excluded.case_version, model=excluded.model,
                 graded_at=excluded.graded_at, critical_flag=excluded.critical_flag,
                 service_failure_flag=excluded.service_failure_flag,
                 manual_review_needed=excluded.manual_review_needed,
                 outcome_status=excluded.outcome_status, case_type=excluded.case_type,
                 payload_json=excluded.payload_json""",
            (grade.conversation_id, primary_manager or grade.agent_name, grade.overall_score,
             grade.overall_result, grade.ruleset_id, grade.rules_version, case_version,
             grade.model, grade.graded_at or _now(), int(bool(grade.critical_fail)),
             int(bool(grade.service_failure_flag)), int(bool(grade.manual_review_needed)),
             grade.outcome_status or None, grade.case_type or None,
             json.dumps(grade.to_dict())),
        )
        self._conn.commit()

    def get_grade(self, case_id: str) -> dict | None:
        from intercom_summary.storage.grades_store import _normalize_criteria

        row = self._conn.execute("SELECT * FROM vip_grades WHERE case_id = ?", (case_id,)).fetchone()
        if not row:
            return None
        d = json.loads(row["payload_json"])
        d["ruleset_id"] = row["ruleset_id"]
        d["case_version"] = row["case_version"]
        for k in ("human_score", "override_reason", "overridden_by", "overridden_at"):
            d[k] = row[k]
        d["human_criteria"] = json.loads(row["human_criteria"]) if row["human_criteria"] else None
        d["rule_results"] = _normalize_criteria(d.get("rule_results"), d["ruleset_id"])
        return d

    def is_current(self, case_id: str, rules_version: str, case_version: str) -> bool:
        row = self._conn.execute(
            "SELECT rules_version, case_version FROM vip_grades WHERE case_id = ?",
            (case_id,)).fetchone()
        return bool(row) and row[0] == rules_version and row[1] == case_version

    def save_override(self, case_id: str, human_score: int, reason: str, overridden_by: str,
                      human_criteria: dict[str, str] | None = None) -> bool:
        if not (reason or "").strip():
            raise ValueError("An override needs a written reason (scorecard §11)")
        cur = self._conn.execute(
            """UPDATE vip_grades SET human_score=?, human_criteria=?, override_reason=?,
                      overridden_by=?, overridden_at=? WHERE case_id=?""",
            (int(human_score), json.dumps(human_criteria) if human_criteria else None,
             reason.strip(), overridden_by, _now(), case_id),
        )
        self._conn.commit()
        return cur.rowcount > 0
