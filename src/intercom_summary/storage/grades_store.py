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


def _normalize_criteria(rule_results: list | None, ruleset_id: str | None = None) -> list:
    """Return the grade's rule_results as the ruleset's full, ordered criteria list.

    Two jobs:

    1. Tag each recognised rule_result with its canonical `deduction` and `critical` flag so
       the UI can render ScoreBuddy-style toggles and preview the recomputed score.
    2. Fill in the criteria the model never reported, as `n/a`.

    (2) matters because the model returns only the criteria it chose to emit — typically 24 of
    the standard ruleset's 27 (it reads the prompt's "ALL CRITERIA" table as the enumeration
    and skips the crit-* table above it), and on some conversations far fewer. Without the
    backfill the Grade panel shows a partial checklist, and worse, the override endpoint 422s
    on any criterion absent from this list — so an analyst could not fail a rule the model
    skipped, including the crit-* compliance rules.

    Backfilling as `n/a` is score-neutral: only `fail` deducts, so a grade's score is identical
    before and after. It is also honest — the AI genuinely did not assess those criteria.

    `ruleset_id` is the ruleset that produced the grade, so an old standard grade keeps its
    standard points (and its 27 criteria) even if the agent has since moved to the VIP group."""
    from intercom_summary.qa.rulesets import get_ruleset

    rs = get_ruleset(ruleset_id)
    deductions, critical, titles = rs.deductions, rs.critical, rs.titles

    reported: dict[str, dict] = {}
    for r in rule_results or []:
        cid = r.get("rule_id", "")
        if cid in deductions or cid in critical:
            r["deduction"] = deductions.get(cid, 0)
            r["critical"] = cid in critical
        reported.setdefault(cid, r)

    # Legacy Claude-backend grades use an entirely different criteria vocabulary
    # ("tone-greeting" etc.). Grafting this ruleset's catalogue onto them would be nonsense,
    # so leave them exactly as they are — the UI falls back to the manual slider for those.
    if not any(cid in deductions or cid in critical for cid in reported):
        return list(rule_results or [])

    out: list[dict] = []
    for c in rs.criteria:
        cid = c["id"]
        entry = reported.pop(cid, None)
        if entry is None:
            entry = {
                "rule_id": cid,
                "title": titles.get(cid, cid),
                "verdict": "n/a",
                "evidence": "",
                "comment": "",
                "deduction": deductions.get(cid, 0),
                "critical": cid in critical,
            }
        out.append(entry)
    # Anything the model emitted that the catalogue doesn't know about is kept rather than
    # silently dropped — it is still a record of what the model said.
    out.extend(reported.values())
    return out


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

    def is_current(self, conversation_id: str, ruleset_id: str, rules_version: str) -> bool:
        """True if the stored grade needs no re-grading.

        `ruleset_id` / `rules_version` are the ruleset the conversation *would* be graded with
        now, taken from the live grader (not from the ruleset file directly — the Anthropic
        backend grades against support_rules.md and stamps that hash instead).

        Two cases count as current:
        - the grade was produced by the same ruleset, at its current version → nothing changed;
        - the grade was produced by a *different* ruleset → it was graded correctly at the
          time, so leave it alone. This is what stops a VIP agent's entire back catalogue from
          being invalidated and re-graded the moment they are added to the VIP group. Those
          grades are surfaced separately as `wrong_ruleset` on the Evaluation page, where an
          analyst can choose to re-grade them.

        Editing a ruleset's prompt still marks that ruleset's own grades stale, as before.
        """
        row = self._conn.execute(
            "SELECT rules_version, ruleset_id FROM grades WHERE conversation_id=?",
            (conversation_id,),
        ).fetchone()
        if row is None:
            return False
        if (row["ruleset_id"] or "default") != ruleset_id:
            return True
        return row["rules_version"] == rules_version

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
                rules_version, ruleset_id, model, graded_at, payload_json)
               VALUES (?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(conversation_id) DO UPDATE SET
                   agent_name    = excluded.agent_name,
                   agent_email   = excluded.agent_email,
                   overall_score = excluded.overall_score,
                   summary       = excluded.summary,
                   rules_version = excluded.rules_version,
                   ruleset_id    = excluded.ruleset_id,
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
                grade.ruleset_id or "default",
                grade.model,
                grade.graded_at,
                json.dumps(grade.to_dict()),
            ),
        )
        self._conn.commit()

    def get(self, conversation_id: str) -> dict | None:
        row = self._conn.execute(
            """SELECT payload_json, human_score, override_reason, overridden_by,
                      overridden_at, human_criteria, human_deductions, ruleset_id
               FROM grades WHERE conversation_id=?""",
            (conversation_id,),
        ).fetchone()
        if not row:
            return None
        d = json.loads(row["payload_json"])
        # Grades written before the VIP ruleset existed have no ruleset_id in their payload;
        # the column backfills them to 'default', which is what graded them.
        d["ruleset_id"] = row["ruleset_id"] or d.get("ruleset_id") or "default"
        d["human_score"] = row["human_score"]
        d["override_reason"] = row["override_reason"]
        d["overridden_by"] = row["overridden_by"]
        d["overridden_at"] = row["overridden_at"]
        d["human_criteria"] = json.loads(row["human_criteria"]) if row["human_criteria"] else None
        d["human_deductions"] = json.loads(row["human_deductions"]) if row["human_deductions"] else None
        d["rule_results"] = _normalize_criteria(d.get("rule_results"), d["ruleset_id"])
        return d

    def agent_scores(self, since: str | None = None, until: str | None = None) -> list[dict]:
        """Average effective QA score (human override if present, else AI) per agent.

        Joins grades with conversations so the period filter reflects when the chat
        *happened* (`conversations.created_at`), not when it was graded. `since` and
        `until` are ISO timestamps bounding the conversation date (`until` exclusive);
        either None means unbounded on that side. Returns rows sorted by avg_score desc.
        """
        sql = (
            "SELECT g.agent_name AS agent, "
            "       ROUND(AVG(COALESCE(g.human_score, g.overall_score)), 1) AS avg_score, "
            "       COUNT(*) AS count "
            "FROM grades g JOIN conversations c ON c.id = g.conversation_id "
        )
        args: list[object] = []
        clauses: list[str] = []
        if since:
            clauses.append("c.created_at >= ?")
            args.append(since)
        if until:
            clauses.append("c.created_at < ?")
            args.append(until)
        if clauses:
            sql += "WHERE " + " AND ".join(clauses) + " "
        sql += "GROUP BY g.agent_name ORDER BY avg_score DESC"
        rows = self._conn.execute(sql, args).fetchall()
        return [dict(r) for r in rows]

    def for_agent(self, agent_name: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT payload_json FROM grades WHERE agent_name=? ORDER BY graded_at DESC",
            (agent_name,),
        ).fetchall()
        return [json.loads(r["payload_json"]) for r in rows]

    def all(self, agents: list[str] | None = None) -> list[dict]:
        sql = ("SELECT payload_json, human_score, override_reason, overridden_by, overridden_at "
               "FROM grades")
        args: list[object] = []
        if agents is not None:
            if not agents:  # an empty group matches nothing, not everything
                return []
            sql += f" WHERE agent_name IN ({','.join('?' * len(agents))})"
            args.extend(agents)
        sql += " ORDER BY graded_at DESC"
        rows = self._conn.execute(sql, args).fetchall()
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
        human_deductions: list[dict] | None = None,
    ) -> bool:
        """Persist a human override. `human_criteria` (a {criterion_id: verdict} diff vs the
        AI's verdicts) records ScoreBuddy-style per-criterion changes; `human_deductions` is
        a list of analyst manual deductions ([{category, points, note}]). Pass None for both
        on a plain score override, which clears any prior criterion changes/deductions."""
        cur = self._conn.execute(
            """UPDATE grades
               SET human_score=?, override_reason=?, overridden_by=?, overridden_at=?,
                   human_criteria=?, human_deductions=?
               WHERE conversation_id=?""",
            (
                human_score, reason.strip(), overridden_by, _now(),
                json.dumps(human_criteria) if human_criteria else None,
                json.dumps(human_deductions) if human_deductions else None,
                conversation_id,
            ),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def accuracy_stats(self, agents: list[str] | None = None) -> dict:
        """AI-vs-human accuracy metrics for the traceability dashboard."""
        sql = ("SELECT conversation_id, overall_score, human_score, agent_name, "
               "override_reason, overridden_by, overridden_at, graded_at FROM grades")
        args: list[object] = []
        if agents is not None:
            if not agents:
                sql += " WHERE 0"
            else:
                sql += f" WHERE agent_name IN ({','.join('?' * len(agents))})"
                args.extend(agents)
        rows = self._conn.execute(sql, args).fetchall()

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
