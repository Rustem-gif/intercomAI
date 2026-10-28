"""The back office's record of a player (backoffice/player.py), cached between gradings.

Keyed by (brand, BO player id). A row is the BO as it was at `fetched_at` — the grader is told
that, since a player's status and tags can change after the chat.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from intercom_summary.backoffice.player import PlayerFacts
from intercom_summary.settings import settings
from intercom_summary.storage.db import connect


class BoPlayersStore:
    def __init__(self, db_path: str | Path | None = None) -> None:
        self._conn: sqlite3.Connection = connect(db_path or settings.db_path)

    def close(self) -> None:
        self._conn.close()

    def get(self, brand: str, player_id: str) -> PlayerFacts | None:
        row = self._conn.execute(
            "SELECT facts_json FROM bo_players WHERE brand=? AND player_id=?",
            (brand, str(player_id))).fetchone()
        return PlayerFacts.from_dict(json.loads(row["facts_json"])) if row else None

    def fresh(self, brand: str, player_id: str, max_age: timedelta) -> bool:
        row = self._conn.execute(
            "SELECT fetched_at FROM bo_players WHERE brand=? AND player_id=?",
            (brand, str(player_id))).fetchone()
        if not row:
            return False
        try:
            return datetime.now(timezone.utc) - datetime.fromisoformat(row["fetched_at"]) < max_age
        except ValueError:
            return False

    def put(self, brand: str, facts: PlayerFacts) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO bo_players (brand, player_id, facts_json, fetched_at) "
            "VALUES (?,?,?,?)",
            (brand, facts.player_id, json.dumps(facts.to_dict()), facts.fetched_at))
        self._conn.commit()

    def count(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM bo_players").fetchone()[0]
