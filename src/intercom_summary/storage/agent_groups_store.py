"""Agent → group membership. The group selects the QA ruleset (see qa/rulesets.py).

Membership is keyed on `agent_name` because that is the join key every other table already
uses (conversations.agent_name, grades.agent_name, coaching_sessions.agent_name, …). We also
store the email and the Intercom admin id so a lookup still resolves if the display name is
punctuated differently in one place than another, mirroring resolve_admin_ids() in
intercom/fetch.py.

An agent with no row here is in the standard group.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from intercom_summary.settings import settings
from intercom_summary.storage.db import connect


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class AgentGroupsStore:
    def __init__(self, db_path: str | Path | None = None) -> None:
        self._conn: sqlite3.Connection = connect(db_path or settings.db_path)

    def close(self) -> None:
        self._conn.close()

    def all_groups(self) -> dict[str, str]:
        """{agent_name: group_id} for every agent with a non-standard group."""
        rows = self._conn.execute("SELECT agent_name, group_id FROM agent_groups").fetchall()
        return {r["agent_name"]: r["group_id"] for r in rows}

    def get_group(self, agent_name: str | None, agent_email: str | None = None) -> str | None:
        """The agent's group, or None (= standard). Case-insensitive; email wins over name."""
        from intercom_summary.qa.rulesets import GROUP_STANDARD

        if agent_email:
            row = self._conn.execute(
                "SELECT group_id FROM agent_groups WHERE lower(agent_email)=lower(?)",
                (agent_email,),
            ).fetchone()
            if row:
                return row["group_id"]
        if not agent_name:
            return GROUP_STANDARD
        row = self._conn.execute(
            "SELECT group_id FROM agent_groups WHERE lower(agent_name)=lower(?)",
            (agent_name,),
        ).fetchone()
        return row["group_id"] if row else GROUP_STANDARD

    def members(self, group_id: str) -> list[str]:
        """Agent names in a group."""
        rows = self._conn.execute(
            "SELECT agent_name FROM agent_groups WHERE group_id=? ORDER BY agent_name",
            (group_id,),
        ).fetchall()
        return [r["agent_name"] for r in rows]

    def set_group(
        self,
        agent_name: str,
        group_id: str,
        agent_email: str = "",
        intercom_admin_id: str = "",
        updated_by: str = "",
    ) -> None:
        self._conn.execute(
            """INSERT INTO agent_groups
               (agent_name, agent_email, intercom_admin_id, group_id, updated_at, updated_by)
               VALUES (?,?,?,?,?,?)
               ON CONFLICT(agent_name) DO UPDATE SET
                   agent_email       = excluded.agent_email,
                   intercom_admin_id = excluded.intercom_admin_id,
                   group_id          = excluded.group_id,
                   updated_at        = excluded.updated_at,
                   updated_by        = excluded.updated_by""",
            (agent_name, agent_email, intercom_admin_id, group_id, _now(), updated_by),
        )
        self._conn.commit()

    def remove(self, agent_name: str) -> bool:
        """Drop the agent back to the standard group."""
        cur = self._conn.execute(
            "DELETE FROM agent_groups WHERE lower(agent_name)=lower(?)", (agent_name,)
        )
        self._conn.commit()
        return cur.rowcount > 0
