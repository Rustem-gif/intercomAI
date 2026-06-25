"""Persist and query ConversationGrade rows (idempotent so we don't re-grade)."""
from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from intercom_summary.settings import settings
from intercom_summary.qa.schema import ConversationGrade
from intercom_summary.storage.db import connect


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _annotate_criteria(rule_results: list | None) -> None:
    """Tag each recognised rule_result with its canonical `deduction` and `critical` flag so
    the UI can render ScoreBuddy-style toggles and preview the recomputed score. Unknown
    criteria (e.g. legacy Claude-backend grades) are left without a deduction, which the UI
    uses to fall back to the manual slider."""
    from intercom_summary.qa.casino_prompt import CRITERION_DEDUCTIONS, CRITICAL_CRITERIA

    for r in rule_results or []:
        cid = r.get("rule_id", "")
        if cid in CRITERION_DEDUCTIONS or cid in CRITICAL_CRITERIA:
            r["deduction"] = CRITERION_DEDUCTIONS.get(cid, 0)
            r["critical"] = cid in CRITICAL_CRITERIA


class GradesStore:
    def __init__(self, db_path: str | Path | None = None) -> None:
        self._conn: sqlite3.Connection = connect(db_path or settings.db_path)

    def close(self) -> None:
        self._conn.close()

    def is_graded(self, conversation_id: str, rules_version: str | None = None) -> bool:
        if rules_version:
            row = self._conn.execute(
                "SELECT 1 FROM grades WHERE conversation_id=? AND rules_version=?",
                (conversation_id, rules_version),
            ).fetchone()
        else:
            row = self._conn.execute(
                "SELECT 1 FROM grades WHERE conversation_id=?", (conversation_id,)
            ).fetchone()
        return row is not None

    def count_graded(self, rules_version: str | None = None) -> int:
        """Count distinct conversations that have a grade (optionally for a specific ruleset)."""
        if rules_version:
            return self._conn.execute(
                "SELECT COUNT(DISTINCT conversation_id) AS n FROM grades WHERE rules_version=?",
                (rules_version,),
            ).fetchone()["n"]
        return self._conn.execute(
            "SELECT COUNT(DISTINCT conversation_id) AS n FROM grades"
        ).fetchone()["n"]

    def save(self, grade: ConversationGrade) -> None:
        # Use upsert (INSERT … ON CONFLICT DO UPDATE) so that human overrides
        # (human_score, override_reason, overridden_by, overridden_at) are never
        # clobbered when the AI re-grades a conversation. INSERT OR REPLACE would
        # delete the row and re-insert it with NULL override columns.
        self._conn.execute(
            """INSERT INTO grades
               (conversation_id, agent_name, agent_email, overall_score, summary,
                rules_version, model, graded_at, payload_json)
               VALUES (?,?,?,?,?,?,?,?,?)
               ON CONFLICT(conversation_id) DO UPDATE SET
                   agent_name    = excluded.agent_name,
                   agent_email   = excluded.agent_email,
                   overall_score = excluded.overall_score,
                   summary       = excluded.summary,
                   rules_version = excluded.rules_version,
                   model         = excluded.model,
                   graded_at     = excluded.graded_at,
                   payload_json  = excluded.payload_json""",
            (
                grade.conversation_id,
                grade.agent_name,
                grade.agent_email,
                grade.overall_score,
                grade.summary,
                grade.rules_version,
                grade.model,
                grade.graded_at,
                json.dumps(grade.to_dict()),
            ),
        )
        self._conn.commit()

    def get(self, conversation_id: str) -> dict | None:
        row = self._conn.execute(
            """SELECT payload_json, human_score, override_reason, overridden_by,
                      overridden_at, human_criteria
               FROM grades WHERE conversation_id=?""",
            (conversation_id,),
        ).fetchone()
        if not row:
            return None
        d = json.loads(row["payload_json"])
        d["human_score"] = row["human_score"]
        d["override_reason"] = row["override_reason"]
        d["overridden_by"] = row["overridden_by"]
        d["overridden_at"] = row["overridden_at"]
        d["human_criteria"] = json.loads(row["human_criteria"]) if row["human_criteria"] else None
        _annotate_criteria(d.get("rule_results"))
        return d

    def agent_scores(self, since: str | None = None) -> list[dict]:
        """Average effective QA score (human override if present, else AI) per agent.

        Joins grades with conversations so the period filter reflects when the chat
        *happened* (`conversations.created_at`), not when it was graded. `since` is an
        ISO timestamp; None means all-time. Returns rows sorted by avg_score desc.
        """
        sql = (
            "SELECT g.agent_name AS agent, "
            "       ROUND(AVG(COALESCE(g.human_score, g.overall_score)), 1) AS avg_score, "
            "       COUNT(*) AS count "
            "FROM grades g JOIN conversations c ON c.id = g.conversation_id "
        )
        args: list[object] = []
        if since:
            sql += "WHERE c.created_at >= ? "
            args.append(since)
        sql += "GROUP BY g.agent_name ORDER BY avg_score DESC"
        rows = self._conn.execute(sql, args).fetchall()
        return [dict(r) for r in rows]

    def for_agent(self, agent_name: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT payload_json FROM grades WHERE agent_name=? ORDER BY graded_at DESC",
            (agent_name,),
        ).fetchall()
        return [json.loads(r["payload_json"]) for r in rows]

    def all(self) -> list[dict]:
        rows = self._conn.execute(
            """SELECT payload_json, human_score, override_reason, overridden_by, overridden_at
               FROM grades ORDER BY graded_at DESC"""
        ).fetchall()
        result = []
        for r in rows:
            d = json.loads(r["payload_json"])
            d["human_score"] = r["human_score"]
            d["override_reason"] = r["override_reason"]
            d["overridden_by"] = r["overridden_by"]
            d["overridden_at"] = r["overridden_at"]
            result.append(d)
        return result

    def save_override(
        self,
        conversation_id: str,
        human_score: int,
        reason: str,
        overridden_by: str,
        human_criteria: dict[str, str] | None = None,
    ) -> bool:
        """Persist a human override. `human_criteria` (a {criterion_id: verdict} diff vs the
        AI's verdicts) records ScoreBuddy-style per-criterion changes; pass None for a plain
        score override, which clears any prior criterion changes."""
        cur = self._conn.execute(
            """UPDATE grades
               SET human_score=?, override_reason=?, overridden_by=?, overridden_at=?,
                   human_criteria=?
               WHERE conversation_id=?""",
            (
                human_score, reason.strip(), overridden_by, _now(),
                json.dumps(human_criteria) if human_criteria else None,
                conversation_id,
            ),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def accuracy_stats(self) -> dict:
        """AI-vs-human accuracy metrics for the traceability dashboard."""
        rows = self._conn.execute(
            """SELECT conversation_id, overall_score, human_score,
                      agent_name, override_reason, overridden_by, overridden_at, graded_at
               FROM grades"""
        ).fetchall()

        total = len(rows)
        overridden = [r for r in rows if r["human_score"] is not None]
        n_over = len(overridden)

        if overridden:
            deltas = [r["human_score"] - r["overall_score"] for r in overridden]
            avg_delta = sum(deltas) / len(deltas)
            avg_abs_delta = sum(abs(d) for d in deltas) / len(deltas)
            agreement = sum(1 for d in deltas if abs(d) <= 5) / len(deltas)
            ai_too_low  = sum(1 for d in deltas if d > 5)
            ai_too_high = sum(1 for d in deltas if d < -5)
            agreed      = sum(1 for d in deltas if abs(d) <= 5)
        else:
            deltas = []
            avg_delta = avg_abs_delta = 0.0
            agreement = 1.0
            ai_too_low = ai_too_high = agreed = 0

        # Per-agent deviation
        by_agent: dict[str, list[int]] = defaultdict(list)
        for r in overridden:
            by_agent[r["agent_name"] or "(unknown)"].append(
                r["human_score"] - r["overall_score"]
            )
        agent_breakdown = sorted(
            [
                {
                    "agent": a,
                    "overrides": len(ds),
                    "avg_delta": round(sum(ds) / len(ds), 1),
                    "avg_abs_delta": round(sum(abs(d) for d in ds) / len(ds), 1),
                }
                for a, ds in by_agent.items()
            ],
            key=lambda x: abs(x["avg_delta"]),
            reverse=True,
        )

        # Deviation histogram (rounded to nearest 5)
        bucket_counts: dict[int, int] = defaultdict(int)
        for d in deltas:
            bucket = round(d / 5) * 5
            bucket_counts[bucket] += 1
        deviation_distribution = [
            {"delta": k, "count": v} for k, v in sorted(bucket_counts.items())
        ]

        # Recent overrides
        recent_rows = self._conn.execute(
            """SELECT conversation_id, overall_score, human_score, agent_name,
                      override_reason, overridden_by, overridden_at
               FROM grades WHERE human_score IS NOT NULL
               ORDER BY overridden_at DESC LIMIT 25"""
        ).fetchall()
        recent_overrides = [dict(r) for r in recent_rows]

        # Weekly trend (overridden_at grouped by week)
        weekly: dict[str, list[int]] = defaultdict(list)
        for r in overridden:
            week = (r["overridden_at"] or "")[:10]   # use date as weekly proxy
            if week:
                weekly[week].append(abs(r["human_score"] - r["overall_score"]))
        trend = [
            {"date": d, "avg_abs_delta": round(sum(ds) / len(ds), 1), "count": len(ds)}
            for d, ds in sorted(weekly.items())
        ]

        # Actionable insights
        insights: list[str] = []
        if n_over >= 3:
            if avg_delta > 5:
                insights.append(
                    f"AI scores {avg_delta:.0f} pts too low on average — the model is more strict than your managers. "
                    "Consider tightening the rubric or adjusting the prompt."
                )
            elif avg_delta < -5:
                insights.append(
                    f"AI scores {abs(avg_delta):.0f} pts too high on average — the model is more lenient than your managers. "
                    "Review the scoring anchors in the system prompt."
                )
            else:
                insights.append("AI scores are broadly calibrated with manager judgement (avg delta < ±5 pts).")

            if agent_breakdown and abs(agent_breakdown[0]["avg_delta"]) > 10:
                a = agent_breakdown[0]
                direction = "too low" if a["avg_delta"] > 0 else "too high"
                insights.append(
                    f"Largest disagreement: {a['agent']} — AI scores {abs(a['avg_delta']):.0f} pts {direction} "
                    f"on average across {a['overrides']} override(s)."
                )

            if agreement < 0.4:
                insights.append(
                    f"Only {agreement * 100:.0f}% of overrides are within ±5 pts — "
                    "AI reliability is low; consider expanding the training examples in the prompt."
                )
            elif agreement >= 0.75:
                insights.append(
                    f"{agreement * 100:.0f}% of overrides are within ±5 pts — "
                    "most disagreements are edge cases rather than systematic errors."
                )

        return {
            "summary": {
                "total_graded": total,
                "total_overridden": n_over,
                "override_rate": round(n_over / total, 3) if total else 0,
                "avg_delta": round(avg_delta, 1),
                "avg_abs_delta": round(avg_abs_delta, 1),
                "agreement_rate": round(agreement, 3),
                "ai_too_low": ai_too_low,
                "ai_too_high": ai_too_high,
                "agreed_on_override": agreed,
            },
            "agent_breakdown": agent_breakdown[:10],
            "deviation_distribution": deviation_distribution,
            "recent_overrides": recent_overrides,
            "trend": trend,
            "insights": insights,
        }

    def delete(self, conversation_id: str) -> None:
        self._conn.execute("DELETE FROM grades WHERE conversation_id=?", (conversation_id,))
        self._conn.commit()

    def delete_many(self, conversation_ids: list[str]) -> None:
        if not conversation_ids:
            return
        placeholders = ",".join("?" * len(conversation_ids))
        self._conn.execute(
            f"DELETE FROM grades WHERE conversation_id IN ({placeholders})", conversation_ids
        )
        self._conn.commit()
