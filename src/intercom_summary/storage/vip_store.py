"""Persistence for the VIP chats tab: conversations of the Intercom "VIP managers" team.

Kept apart from `conversations` on purpose — every one of them is an email, and that table is
chats-only. Filtered-out threads are stored too (with `keep=0` and the reason), so the tab can
show what was dropped and why instead of silently shrinking the list.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from intercom_summary.settings import settings

_LIST_COLUMNS = (
    "id, agent_name, agent_email, customer_name, customer_email, brand, subject, created_at, "
    "delivered_as, message_count, customer_msgs, agent_replies, keep, filter_stage, reason, topic"
)


class VipStore:
    def __init__(self, db_path: str | Path | None = None) -> None:
        from intercom_summary.storage.db import connect
        self._conn = connect(db_path or settings.db_path)

    def close(self) -> None:
        self._conn.close()

    def save(self, row: dict) -> None:
        """Upsert one thread. `row` carries the table's columns; `payload` is the Conversation dict."""
        self._conn.execute(
            """INSERT INTO vip_conversations
                 (id, agent_name, agent_email, customer_name, customer_email, brand, subject,
                  created_at, delivered_as, message_count, customer_msgs, agent_replies,
                  keep, filter_stage, reason, topic, payload_json, fetched_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET
                 agent_name=excluded.agent_name, agent_email=excluded.agent_email,
                 customer_name=excluded.customer_name, customer_email=excluded.customer_email,
                 brand=excluded.brand, subject=excluded.subject, created_at=excluded.created_at,
                 delivered_as=excluded.delivered_as, message_count=excluded.message_count,
                 customer_msgs=excluded.customer_msgs, agent_replies=excluded.agent_replies,
                 keep=excluded.keep, filter_stage=excluded.filter_stage, reason=excluded.reason,
                 topic=excluded.topic, payload_json=excluded.payload_json,
                 fetched_at=excluded.fetched_at""",
            (
                row["id"], row.get("agent_name", ""), row.get("agent_email", ""),
                row.get("customer_name", ""), row.get("customer_email", ""), row.get("brand", ""),
                row.get("subject", ""), row.get("created_at"), row.get("delivered_as", ""),
                row.get("message_count", 0), row.get("customer_msgs", 0),
                row.get("agent_replies", 0), row.get("keep"), row.get("filter_stage"),
                row.get("reason", ""), row.get("topic", ""), json.dumps(row["payload"]),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        self._conn.commit()

    def classified(self) -> dict[str, dict]:
        """id → {keep, filter_stage, reason, topic} for every thread already decided."""
        rows = self._conn.execute(
            "SELECT id, keep, filter_stage, reason, topic FROM vip_conversations WHERE keep IS NOT NULL"
        ).fetchall()
        return {r["id"]: dict(r) for r in rows}

    def query(self, *, include_excluded: bool = False, agent: str | None = None,
              brand: str | None = None, search: str | None = None,
              since: str | None = None, until: str | None = None) -> list[dict]:
        where, params = [], []
        if not include_excluded:
            where.append("keep = 1")
        if agent:
            where.append("agent_name = ?")
            params.append(agent)
        if brand:
            where.append("brand = ?")
            params.append(brand)
        if since:
            where.append("created_at >= ?")
            params.append(since)
        if until:
            # Inclusive end date: everything before the next day starts.
            where.append("substr(created_at, 1, 10) <= ?")
            params.append(until)
        if search:
            like = f"%{search.lower()}%"
            where.append("(lower(customer_name) LIKE ? OR lower(customer_email) LIKE ? "
                         "OR lower(subject) LIKE ? OR lower(topic) LIKE ? OR id LIKE ?)")
            params += [like, like, like, like, like]
        sql = f"SELECT {_LIST_COLUMNS} FROM vip_conversations"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY created_at DESC"
        return [dict(r) for r in self._conn.execute(sql, params).fetchall()]

    def summary(self) -> dict:
        """Counts for the stat cards: everything fetched, what was kept, why the rest was not."""
        c = self._conn
        total = c.execute("SELECT COUNT(*) FROM vip_conversations").fetchone()[0]
        kept = c.execute("SELECT COUNT(*) FROM vip_conversations WHERE keep = 1").fetchone()[0]
        span = c.execute("SELECT MIN(created_at), MAX(created_at), MAX(fetched_at) "
                         "FROM vip_conversations").fetchone()
        excluded = c.execute(
            """SELECT filter_stage, reason, COUNT(*) AS n FROM vip_conversations
                WHERE keep = 0 GROUP BY filter_stage, reason ORDER BY n DESC"""
        ).fetchall()
        managers = c.execute(
            """SELECT agent_name, COUNT(*) AS total, SUM(keep = 1) AS kept
                 FROM vip_conversations GROUP BY agent_name ORDER BY kept DESC"""
        ).fetchall()
        return {
            "total": total, "kept": kept, "excluded": total - kept,
            "first": span[0], "last": span[1], "last_fetched": span[2],
            # Stage-1 reasons are a short fixed list; AI reasons are free text, so they are
            # grouped as one line here and shown per row in the table.
            "excluded_by_rule": [dict(r) for r in excluded if r["filter_stage"] == "rule"],
            "excluded_by_ai": sum(r["n"] for r in excluded if r["filter_stage"] == "ai"),
            "managers": [dict(r) for r in managers],
        }

    def get(self, conversation_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM vip_conversations WHERE id = ?", (conversation_id,)
        ).fetchone()
        if not row:
            return None
        out = dict(row)
        out["payload"] = json.loads(out.pop("payload_json"))
        return out

    def kept_with_payload(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM vip_conversations WHERE keep = 1 ORDER BY created_at"
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["payload"] = json.loads(d.pop("payload_json"))
            out.append(d)
        return out
