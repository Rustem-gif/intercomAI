"""Persist and query fetched conversations (the browse/slice cache for the UI)."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from intercom_summary.intercom.models import Conversation
from intercom_summary.settings import settings
from intercom_summary.storage.db import connect


# Conversations carrying any of these (native Intercom) tags are triage/noise and must
# never be graded — they're excluded from the Evaluation population and skipped by the
# grader. Compared case-insensitively. Keep this the single source of truth.
IGNORE_TAGS: frozenset[str] = frozenset(
    {"empty", "spam", "test", "jira", "follow-up", "no request"}
)


def tags_are_ignored(tags: "list[str] | None") -> bool:
    """True if any of the conversation's tags is in IGNORE_TAGS (case-insensitive)."""
    return any((t or "").strip().lower() in IGNORE_TAGS for t in (tags or []))


def _ignore_sql(alias: str = "c") -> tuple[str, list[object]]:
    """SQL predicate (+args) matching conversations that carry any IGNORE_TAGS in their
    native `tags` column, case-insensitively. Mirrors `tags_are_ignored`."""
    ors = [f"(',' || lower({alias}.tags) || ',') LIKE ?" for _ in IGNORE_TAGS]
    args: list[object] = [f"%,{t},%" for t in sorted(IGNORE_TAGS)]
    return "(" + " OR ".join(ors) + ")", args


class ConversationsStore:
    def __init__(self, db_path: str | Path | None = None) -> None:
        self._conn: sqlite3.Connection = connect(db_path or settings.db_path)

    def close(self) -> None:
        self._conn.close()

    def save(self, convo: Conversation) -> None:
        # Preserve any custom_tags an analyst has already set on this conversation.
        existing = self._conn.execute(
            "SELECT custom_tags FROM conversations WHERE id=?", (convo.id,)
        ).fetchone()
        custom_tags = existing["custom_tags"] if existing else ""

        self._conn.execute(
            """INSERT OR REPLACE INTO conversations
               (id, agent_name, agent_email, customer_name, customer_email, state,
                subject, created_at, updated_at, message_count, csat_rating, tags,
                custom_tags, payload_json, fetched_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                convo.id,
                convo.assignee_name,
                convo.assignee.email if convo.assignee else "",
                convo.contact.name,
                convo.contact.email,
                convo.state,
                convo.display_subject,
                convo.created_at.isoformat() if convo.created_at else None,
                convo.updated_at.isoformat() if convo.updated_at else None,
                convo.message_count,
                convo.csat_rating,
                ",".join(convo.tags),
                custom_tags,
                json.dumps(convo.to_dict()),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        self._conn.commit()

    def save_many(self, convos: list[Conversation]) -> int:
        for c in convos:
            self.save(c)
        return len(convos)

    def get(self, conversation_id: str) -> Conversation | None:
        row = self._conn.execute(
            "SELECT payload_json FROM conversations WHERE id=?", (conversation_id,)
        ).fetchone()
        return Conversation.from_dict(json.loads(row["payload_json"])) if row else None

    def update_agent(self, conversation_id: str, agent_name: str, agent_email: str) -> None:
        """Patch agent_name / agent_email on a stored conversation (used by the repair job)."""
        # Also patch the payload_json so the drawer shows the correct assignee.
        row = self._conn.execute(
            "SELECT payload_json FROM conversations WHERE id=?", (conversation_id,)
        ).fetchone()
        if not row:
            return
        payload = json.loads(row["payload_json"])
        if agent_name:
            existing_assignee = payload.get("assignee") or {}
            payload["assignee"] = {
                "id":    existing_assignee.get("id", ""),   # preserve; Admin requires it
                "name":  agent_name,
                "email": agent_email,
            }
        self._conn.execute(
            "UPDATE conversations SET agent_name=?, agent_email=?, payload_json=? WHERE id=?",
            (agent_name, agent_email, json.dumps(payload), conversation_id),
        )
        self._conn.commit()

    def update_custom_tags(self, conversation_id: str, tags: list[str]) -> bool:
        cur = self._conn.execute(
            "UPDATE conversations SET custom_tags=? WHERE id=?",
            (",".join(t.strip() for t in tags if t.strip()), conversation_id),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def all_custom_tags(self) -> list[str]:
        """Distinct non-empty custom tags across all conversations, sorted."""
        rows = self._conn.execute(
            "SELECT custom_tags FROM conversations WHERE custom_tags <> ''"
        ).fetchall()
        seen: set[str] = set()
        for row in rows:
            for t in row["custom_tags"].split(","):
                t = t.strip()
                if t:
                    seen.add(t)
        return sorted(seen)

    def all_tags(self) -> list[str]:
        """Distinct non-empty tags (native Intercom + custom) across all conversations, sorted."""
        rows = self._conn.execute(
            "SELECT tags, custom_tags FROM conversations WHERE tags <> '' OR custom_tags <> ''"
        ).fetchall()
        seen: set[str] = set()
        for row in rows:
            for col in ("tags", "custom_tags"):
                for t in (row[col] or "").split(","):
                    t = t.strip()
                    if t:
                        seen.add(t)
        return sorted(seen)

    def get_empty_agent_ids(self) -> list[str]:
        """IDs of conversations that have no agent_name (need repair)."""
        rows = self._conn.execute(
            "SELECT id FROM conversations WHERE agent_name IS NULL OR agent_name = ''"
        ).fetchall()
        return [r["id"] for r in rows]

    def query(
        self,
        agents: list[str] | None = None,
        since: str | None = None,
        until: str | None = None,
        state: str | None = None,
        min_score: int | None = None,
        max_csat: int | None = None,
        search: str | None = None,
        tag: str | None = None,
        ungraded: bool = False,
        sort: str = "created_at",
        descending: bool = True,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[dict], int]:
        """Return (rows, total). Rows are summary dicts joined with the latest grade."""
        where: list[str] = []
        args: list[object] = []
        if agents:
            where.append(f"c.agent_name IN ({','.join('?' * len(agents))})")
            args.extend(agents)
        if since:
            where.append("c.created_at >= ?")
            args.append(since)
        if until:
            where.append("c.created_at <= ?")
            args.append(until)
        if state:
            where.append("c.state = ?")
            args.append(state)
        if min_score is not None:
            where.append("g.overall_score >= ?")
            args.append(min_score)
        if max_csat is not None:
            where.append("c.csat_rating IS NOT NULL AND c.csat_rating <= ?")
            args.append(max_csat)
        if ungraded:
            where.append("g.conversation_id IS NULL")
        if search:
            where.append(
                "(c.id LIKE ? OR c.subject LIKE ? OR c.customer_name LIKE ? OR c.customer_email LIKE ?)"
            )
            like = f"%{search}%"
            args.extend([like, like, like, like])
        if tag:
            # Match exact tag in either native Intercom tags or analyst-set custom_tags.
            where.append(
                "((',' || c.tags || ',') LIKE ? OR (',' || c.custom_tags || ',') LIKE ?)"
            )
            pattern = f"%,{tag},%"
            args.extend([pattern, pattern])

        clause = f"WHERE {' AND '.join(where)}" if where else ""
        sort_col = {
            "created_at": "c.created_at",
            "score": "g.overall_score",
            "messages": "c.message_count",
            "agent": "c.agent_name",
            "graded_at": "g.graded_at",
        }.get(sort, "c.created_at")
        direction = "DESC" if descending else "ASC"

        base = (
            "FROM conversations c "
            "LEFT JOIN grades g ON g.conversation_id = c.id "
            "LEFT JOIN grade_disputes d ON d.conversation_id = c.id "
            f"{clause}"
        )
        total = self._conn.execute(f"SELECT COUNT(*) AS n {base}", args).fetchone()["n"]
        rows = self._conn.execute(
            f"""SELECT c.id, c.agent_name, c.customer_name, c.customer_email, c.state,
                       c.subject, c.created_at, c.message_count, c.csat_rating, c.tags,
                       c.custom_tags,
                       d.status AS grade_dispute_status,
                       COALESCE(g.human_score, g.overall_score) AS score,
                       g.overall_score AS ai_score,
                       g.human_score,
                       g.summary AS grade_summary,
                       g.graded_at
                {base}
                ORDER BY {sort_col} {direction} NULLS LAST
                LIMIT ? OFFSET ?""",
            [*args, limit, offset],
        ).fetchall()
        return [dict(r) for r in rows], total

    def agents(self) -> list[str]:
        rows = self._conn.execute(
            "SELECT DISTINCT agent_name FROM conversations WHERE agent_name <> '' ORDER BY agent_name"
        ).fetchall()
        return [r["agent_name"] for r in rows]

    def agent_csat(self, since: str | None = None) -> list[dict]:
        """Per-agent Intercom CSAT summary over conversations that received a rating.

        Returns one row per agent: `avg_csat` (mean 1-5 rating), `csat_count` (how many
        rated), and `low_csat_count` (ratings <= settings.csat_low_max). `since` is an ISO
        timestamp filtering on `created_at`; None means all-time.
        """
        sql = (
            "SELECT agent_name AS agent, "
            "       ROUND(AVG(csat_rating), 2) AS avg_csat, "
            "       COUNT(*) AS csat_count, "
            "       SUM(CASE WHEN csat_rating <= ? THEN 1 ELSE 0 END) AS low_csat_count "
            "FROM conversations "
            "WHERE csat_rating IS NOT NULL AND agent_name <> '' "
        )
        args: list[object] = [settings.csat_low_max]
        if since:
            sql += "AND created_at >= ? "
            args.append(since)
        sql += "GROUP BY agent_name ORDER BY avg_csat ASC"
        rows = self._conn.execute(sql, args).fetchall()
        return [dict(r) for r in rows]

    def delete(self, conversation_id: str) -> bool:
        cur = self._conn.execute("DELETE FROM conversations WHERE id=?", (conversation_id,))
        self._conn.commit()
        return cur.rowcount > 0

    def delete_many(self, conversation_ids: list[str]) -> int:
        if not conversation_ids:
            return 0
        placeholders = ",".join("?" * len(conversation_ids))
        cur = self._conn.execute(
            f"DELETE FROM conversations WHERE id IN ({placeholders})", conversation_ids
        )
        self._conn.commit()
        return cur.rowcount

    def count(self) -> int:
        return self._conn.execute("SELECT COUNT(*) AS n FROM conversations").fetchone()["n"]

    def evaluation_counts(self, rules_version: str | None = None) -> dict:
        """Counts for the Evaluation page over the *gradeable* population (conversations
        without any IGNORE_TAGS). Returns total, graded (any ruleset), graded_current
        (under `rules_version`), and ignored (excluded by tag).

        Counting graded over the same gradeable population keeps the numbers consistent:
        a chat tagged e.g. 'spam' that was graded in the past is excluded from both total
        and graded, so coverage can still reach 100%.
        """
        ign_sql, ign_args = _ignore_sql("c")
        gradeable = f"FROM conversations c WHERE NOT {ign_sql}"
        graded_base = (
            f"FROM conversations c JOIN grades g ON g.conversation_id = c.id "
            f"WHERE NOT {ign_sql}"
        )
        total = self._conn.execute(f"SELECT COUNT(*) AS n {gradeable}", ign_args).fetchone()["n"]
        ignored = self._conn.execute(
            f"SELECT COUNT(*) AS n FROM conversations c WHERE {ign_sql}", ign_args
        ).fetchone()["n"]
        graded = self._conn.execute(f"SELECT COUNT(*) AS n {graded_base}", ign_args).fetchone()["n"]
        if rules_version:
            graded_current = self._conn.execute(
                f"SELECT COUNT(*) AS n {graded_base} AND g.rules_version = ?",
                [*ign_args, rules_version],
            ).fetchone()["n"]
        else:
            graded_current = graded
        return {
            "total": total,
            "graded": graded,
            "graded_current": graded_current,
            "ignored": ignored,
        }
