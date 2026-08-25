"""Persist and query fetched conversations (the browse/slice cache for the UI)."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from intercom_summary.intercom.brands import brand_filter_value
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


def _agent_sql(agents: list[str], alias: str = "c") -> tuple[str, list[object]]:
    """SQL predicate (+args) matching conversations handled by any of `agents`.

    An agent identifier may be either a display name ("Lenny") or an e-mail — the UI's
    agent picker sends whichever Intercom exposes, preferring the e-mail, while a
    conversation row carries both. Matching only `agent_name` made every agent-scoped
    review, listing and export come back empty. Compared case-insensitively.
    """
    placeholders = ",".join("?" * len(agents))
    lowered: list[object] = [(a or "").strip().lower() for a in agents]
    sql = (
        f"(lower({alias}.agent_name) IN ({placeholders}) "
        f"OR lower({alias}.agent_email) IN ({placeholders}))"
    )
    return sql, [*lowered, *lowered]


def _brand_sql(brand: str | None, alias: str = "c") -> tuple[str, list[object]]:
    """SQL predicate (+args) restricting rows to one brand, or ("", []) for no filter.

    `brand` is the API-facing token: a raw Intercom brand value, the UNBRANDED sentinel, or
    None/"" meaning every brand. Returning an empty predicate for "no filter" keeps unfiltered
    queries byte-identical to what they were before brands existed.
    """
    value = brand_filter_value(brand)
    if value is None:
        return "", []
    return f"{alias}.brand = ?", [value]


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

    def save(self, convo: Conversation) -> bool:
        """Store (or refresh) a conversation. Returns False if it was skipped because an
        analyst blacklisted it — callers must report that, or a fetch looks like it worked
        while importing nothing."""
        # Never re-import a conversation an analyst deliberately deleted: it lives in the
        # trash and Intercom re-fetches would otherwise resurrect it. Bulk cache clears
        # (blacklist=0) do not block re-import.
        if self._conn.execute(
            "SELECT 1 FROM deleted_conversations WHERE conversation_id=? AND blacklist=1 LIMIT 1",
            (convo.id,),
        ).fetchone():
            return False

        # Preserve any custom_tags an analyst has already set on this conversation.
        existing = self._conn.execute(
            "SELECT custom_tags, brand FROM conversations WHERE id=?", (convo.id,)
        ).fetchone()
        custom_tags = existing["custom_tags"] if existing else ""

        # Keep a brand we already know when the incoming payload carries none, so a re-fetch
        # that comes back without the Brand attribute can't wipe a backfilled value.
        brand = convo.brand or (existing["brand"] if existing else "")
        # Mirror the resolved brand into the payload too, so the cached JSON the drawer reads
        # never disagrees with the column the filters use.
        payload = convo.to_dict()
        payload["brand"] = brand

        self._conn.execute(
            """INSERT OR REPLACE INTO conversations
               (id, agent_name, agent_email, customer_name, customer_email, state,
                subject, created_at, updated_at, message_count, csat_rating, tags,
                custom_tags, brand, payload_json, fetched_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
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
                brand,
                json.dumps(payload),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        self._conn.commit()
        return True

    def save_many(self, convos: list[Conversation]) -> int:
        """Returns how many were actually stored — not len(convos); blacklisted ones are skipped."""
        return sum(1 for c in convos if self.save(c))

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
        brand: str | None = None,
        ungraded: bool = False,
        sort: str = "created_at",
        descending: bool = True,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[dict], int]:
        """Return (rows, total). Rows are summary dicts joined with the latest grade.

        An empty `agents` list means "no agent filter" here — callers like the Slack `review`
        command and the run dialog pass [] for "everyone". Callers that need "an empty set of
        agents matches nothing" (the group switcher, before anyone is in the VIP group) must
        short-circuit before calling this.
        """
        where: list[str] = []
        args: list[object] = []
        if agents:
            agent_sql, agent_args = _agent_sql(agents)
            where.append(agent_sql)
            args.extend(agent_args)
        if since:
            where.append("c.created_at >= ?")
            args.append(since)
        if until:
            where.append("c.created_at <= ?")
            args.append(until)
        if state:
            where.append("c.state = ?")
            args.append(state)
        brand_sql, brand_args = _brand_sql(brand)
        if brand_sql:
            where.append(brand_sql)
            args.extend(brand_args)
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
                       c.custom_tags, c.brand,
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

    def agents(self, brand: str | None = None) -> list[str]:
        sql = "SELECT DISTINCT agent_name FROM conversations WHERE agent_name <> ''"
        args: list[object] = []
        brand_sql, brand_args = _brand_sql(brand, "conversations")
        if brand_sql:
            sql += f" AND {brand_sql}"
            args.extend(brand_args)
        rows = self._conn.execute(sql + " ORDER BY agent_name", args).fetchall()
        return [r["agent_name"] for r in rows]

    def brands(self) -> list[dict]:
        """Every brand present in the cache, with its conversation count, busiest first.

        Derived rather than configured, so a brand that has never been seen produces no row and
        a newly seen one needs no code change. Unbranded rows come back with brand ''.
        """
        rows = self._conn.execute(
            "SELECT brand, COUNT(*) AS count FROM conversations GROUP BY brand ORDER BY count DESC"
        ).fetchall()
        return [{"brand": r["brand"], "count": r["count"]} for r in rows]

    def agent_csat(self, since: str | None = None, until: str | None = None,
                   brand: str | None = None) -> list[dict]:
        """Per-agent Intercom CSAT summary over conversations that received a rating.

        Returns one row per agent: `avg_csat` (mean 1-5 rating), `csat_count` (how many
        rated), and `low_csat_count` (ratings <= settings.csat_low_max). `since` and
        `until` are ISO timestamps bounding `created_at` (`until` exclusive); either None
        means unbounded on that side.
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
        if until:
            sql += "AND created_at < ? "
            args.append(until)
        brand_sql, brand_args = _brand_sql(brand, "conversations")
        if brand_sql:
            sql += f"AND {brand_sql} "
            args.extend(brand_args)
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

    def count(self, agents: list[str] | None = None, brand: str | None = None) -> int:
        where: list[str] = []
        args: list[object] = []
        if agents is not None:
            if not agents:
                return 0
            agent_sql, agent_args = _agent_sql(agents, "conversations")
            where.append(agent_sql)
            args.extend(agent_args)
        brand_sql, brand_args = _brand_sql(brand, "conversations")
        if brand_sql:
            where.append(brand_sql)
            args.extend(brand_args)
        clause = f" WHERE {' AND '.join(where)}" if where else ""
        return self._conn.execute(
            f"SELECT COUNT(*) AS n FROM conversations{clause}", args
        ).fetchone()["n"]

    def evaluation_counts(
        self,
        versions: dict[str, str] | None = None,
        vip_agents: list[str] | None = None,
        agents: list[str] | None = None,
        brand: str | None = None,
    ) -> dict:
        """Counts for the Evaluation page over the *gradeable* population (conversations
        without any IGNORE_TAGS).

        Counting graded over the same gradeable population keeps the numbers consistent:
        a chat tagged e.g. 'spam' that was graded in the past is excluded from both total
        and graded, so coverage can still reach 100%.

        `versions` is {ruleset_id: live version} for every ruleset. A grade is *current* when
        it carries the live version of the ruleset that produced it — so editing the VIP prompt
        doesn't mark standard grades stale, and vice versa.

        `vip_agents` are the agent names currently in the VIP group. `wrong_ruleset` counts
        grades produced by a ruleset other than the one their agent's group would use today —
        typically an agent's back catalogue after they move into VIP. Those are deliberately
        NOT counted as stale (they were graded correctly at the time); they're reported
        separately so an analyst can choose to re-grade them.

        `agents` scopes every count to a subset of agents (the UI's group switcher).
        """
        ign_sql, ign_args = _ignore_sql("c")
        where = [f"NOT {ign_sql}"]
        args: list[object] = [*ign_args]
        # Brand scopes the ignored count too, so the Evaluation page's numbers add up within
        # whichever brand is selected.
        brand_sql, brand_args = _brand_sql(brand, "c")
        ignored_where = ign_sql
        ignored_args: list[object] = [*ign_args]
        if brand_sql:
            where.append(brand_sql)
            args.extend(brand_args)
            ignored_where = f"{ign_sql} AND {brand_sql}"
            ignored_args.extend(brand_args)
        if agents is not None:
            if not agents:  # an empty group matches nothing (rather than everything)
                return {"total": 0, "graded": 0, "graded_current": 0,
                        "wrong_ruleset": 0, "ignored": 0}
            agent_sql, agent_args = _agent_sql(agents)
            where.append(agent_sql)
            args.extend(agent_args)
        base_where = " AND ".join(where)

        gradeable = f"FROM conversations c WHERE {base_where}"
        graded_base = (
            f"FROM conversations c JOIN grades g ON g.conversation_id = c.id "
            f"WHERE {base_where}"
        )
        total = self._conn.execute(f"SELECT COUNT(*) AS n {gradeable}", args).fetchone()["n"]
        ignored = self._conn.execute(
            f"SELECT COUNT(*) AS n FROM conversations c WHERE {ignored_where}", ignored_args
        ).fetchone()["n"]
        graded = self._conn.execute(f"SELECT COUNT(*) AS n {graded_base}", args).fetchone()["n"]

        if versions:
            # A grade is current if it matches the live version of its OWN ruleset.
            clauses = " OR ".join(["(g.ruleset_id = ? AND g.rules_version = ?)"] * len(versions))
            vargs = [v for rid, ver in versions.items() for v in (rid, ver)]
            graded_current = self._conn.execute(
                f"SELECT COUNT(*) AS n {graded_base} AND ({clauses})", [*args, *vargs]
            ).fetchone()["n"]
        else:
            graded_current = graded

        # Grades whose ruleset doesn't match what their agent's group would use today.
        if vip_agents:
            names = ",".join("?" * len(vip_agents))
            mismatch = (
                f"((lower(c.agent_name) IN ({names}) AND g.ruleset_id <> 'vip') OR "
                f" (lower(c.agent_name) NOT IN ({names}) AND g.ruleset_id <> 'default'))"
            )
            lowered = [a.lower() for a in vip_agents]
            margs = [*lowered, *lowered]
        else:
            mismatch = "g.ruleset_id <> 'default'"
            margs = []
        wrong_ruleset = self._conn.execute(
            f"SELECT COUNT(*) AS n {graded_base} AND {mismatch}", [*args, *margs]
        ).fetchone()["n"]

        return {
            "total": total,
            "graded": graded,
            "graded_current": graded_current,
            "wrong_ruleset": wrong_ruleset,
            "ignored": ignored,
        }
