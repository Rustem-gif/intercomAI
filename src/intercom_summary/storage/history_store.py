"""Persistence for the grader's historical context: chat labels, their full-text index, and
every human verdict gathered into one table.

`chat_labels` is the structured digest of a chat (qa/history_labeller.py): what the player
asked, what the agent did, how it ended, in short normalised English. `chat_labels_fts` is the
FTS5 index over the searchable part of it. `case_verdicts` is rebuilt from the tables where
humans actually record their opinion (grade overrides, Calibration-tab verdicts, analyst
comments, resolved disputes) so a precedent can be looked up with one join.

Two things the queries here guarantee, because the eval depends on them:
- `for_contact` and `search` only return chats created strictly BEFORE the `before` bound, and
  never the ids in `exclude` — so a chat can never see itself, or the future, as context.
- `search(human_only=True)` returns only chats that carry a human verdict.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from intercom_summary.intercom.models import Conversation
from intercom_summary.settings import settings
from intercom_summary.storage.conversations_store import tags_are_ignored

_TOKEN = re.compile(r"[^\W_]{2,}", re.UNICODE)
# Words that carry no retrieval signal in a support-chat digest; everything else is kept.
_STOP = frozenset("""
the a an and or of to in on for with at by from as is are was were be been has have had do
does did not no yes it its this that these those he she they them his her their we you your
our i me my but if then than so very can could would should will may might also into about
player agent customer support chat asked asks ask said says say told tells tell wants want
""".split())
_MAX_QUERY_TOKENS = 40


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def fts_query(text: str) -> str:
    """An FTS5 MATCH expression from free text: each distinct word quoted and OR-ed, so bm25
    ranks by overlap. Empty when nothing is left — callers skip the search then."""
    seen: list[str] = []
    for tok in _TOKEN.findall((text or "").lower()):
        if tok in _STOP or tok in seen:
            continue
        seen.append(tok)
        if len(seen) >= _MAX_QUERY_TOKENS:
            break
    return " OR ".join(f'"{t}"' for t in seen)


def searchable_text(label: dict) -> str:
    """The part of a label the index covers: digest, requests, keywords, subtype, errors."""
    parts = [
        label.get("summary_short", ""),
        label.get("subtype", ""),
        " ".join(label.get("keywords") or []),
        " ".join(label.get("player_requests") or []),
        " ".join(label.get("agent_errors") or []),
        label.get("case_type", ""),
    ]
    return "\n".join(p for p in parts if p)


class HistoryStore:
    def __init__(self, db_path: str | Path | None = None) -> None:
        from intercom_summary.storage.db import connect
        self._conn = connect(db_path or settings.db_path)

    def close(self) -> None:
        self._conn.close()

    # ── labels ───────────────────────────────────────────────────────────────────────────
    def save_label(self, row: dict) -> None:
        """Upsert one label row (`label` is the parsed label dict) and its FTS entry."""
        label = row["label"]
        c = self._conn
        c.execute(
            """INSERT INTO chat_labels
                 (conversation_id, source, brand, contact_id, agent_name, created_at, tags,
                  language, case_type, subtype, risk_flag, outcome, player_state,
                  summary_short, label_json, label_version, model, cost_usd, labelled_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(conversation_id) DO UPDATE SET
                 source=excluded.source, brand=excluded.brand, contact_id=excluded.contact_id,
                 agent_name=excluded.agent_name, created_at=excluded.created_at,
                 tags=excluded.tags, language=excluded.language, case_type=excluded.case_type,
                 subtype=excluded.subtype, risk_flag=excluded.risk_flag,
                 outcome=excluded.outcome, player_state=excluded.player_state,
                 summary_short=excluded.summary_short, label_json=excluded.label_json,
                 label_version=excluded.label_version, model=excluded.model,
                 cost_usd=excluded.cost_usd, labelled_at=excluded.labelled_at""",
            (
                row["conversation_id"], row["source"], row.get("brand", ""),
                row.get("contact_id", ""), row.get("agent_name", ""), row.get("created_at"),
                row.get("tags", ""), label.get("language"), label.get("case_type"),
                label.get("subtype"), label.get("risk_flag"), label.get("outcome"),
                label.get("player_state"), label.get("summary_short", ""),
                json.dumps(label, ensure_ascii=False), row["label_version"],
                row.get("model"), row.get("cost_usd"), _now(),
            ),
        )
        c.execute("DELETE FROM chat_labels_fts WHERE conversation_id = ?", (row["conversation_id"],))
        c.execute("INSERT INTO chat_labels_fts (conversation_id, text) VALUES (?, ?)",
                  (row["conversation_id"], searchable_text(label)))
        c.commit()

    def get_label(self, conversation_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM chat_labels WHERE conversation_id = ?", (conversation_id,)
        ).fetchone()
        return self._row(row) if row else None

    def labelled_ids(self, label_version: str | None = None) -> set[str]:
        if label_version:
            rows = self._conn.execute(
                "SELECT conversation_id FROM chat_labels WHERE label_version = ?", (label_version,))
        else:
            rows = self._conn.execute("SELECT conversation_id FROM chat_labels")
        return {r[0] for r in rows.fetchall()}

    def unlabelled_cache_ids(self, label_version: str, since: str | None = None,
                             limit: int | None = None) -> list[str]:
        """Cached conversations with no label at this version, oldest first. Chats with an
        ignored tag are skipped — they are never graded, so they are never context either."""
        sql = """SELECT c.id, c.tags FROM conversations c
                 LEFT JOIN chat_labels l ON l.conversation_id = c.id AND l.label_version = ?
                 WHERE l.conversation_id IS NULL"""
        params: list = [label_version]
        if since:
            sql += " AND c.created_at >= ?"
            params.append(since)
        sql += " ORDER BY c.created_at"
        out = []
        for r in self._conn.execute(sql, params).fetchall():
            if tags_are_ignored((r["tags"] or "").split(",")):
                continue
            out.append(r["id"])
            if limit and len(out) >= limit:
                break
        return out

    def load_conversations(self, ids: list[str]) -> list[Conversation]:
        out = []
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            rows = self._conn.execute(
                f"SELECT payload_json FROM conversations WHERE id IN ({','.join('?' * len(chunk))})",
                chunk).fetchall()
            out.extend(Conversation.from_dict(json.loads(r[0])) for r in rows)
        return out

    def cached_ids(self) -> set[str]:
        return {r[0] for r in self._conn.execute("SELECT id FROM conversations").fetchall()}

    # ── retrieval ────────────────────────────────────────────────────────────────────────
    def for_contact(self, contact_id: str, *, before: str, exclude: set[str] | None = None,
                    days: int = 180, limit: int = 5) -> list[dict]:
        """The player's earlier chats, newest first — strictly before `before` (ISO) and no
        older than `days` before it."""
        if not contact_id or not before:
            return []
        exclude = exclude or set()
        try:
            floor = (datetime.fromisoformat(before) - timedelta(days=days)).isoformat()
        except ValueError:
            return []
        sql = """SELECT * FROM chat_labels WHERE contact_id = ? AND created_at < ?
                 AND created_at >= ?"""
        params: list = [contact_id, before, floor]
        if exclude:
            sql += f" AND conversation_id NOT IN ({','.join('?' * len(exclude))})"
            params.extend(exclude)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        return [self._row(r) for r in self._conn.execute(sql, params).fetchall()]

    def search(self, query: str, *, brand: str | None = None, case_type: str | None = None,
               before: str | None = None, exclude: set[str] | None = None,
               human_only: bool = False, limit: int = 3) -> list[dict]:
        """Labels most similar to `query` (free text → FTS5 bm25), filtered to the brand and
        case type, created strictly before `before`, optionally only chats with a human verdict."""
        match = fts_query(query)
        if not match:
            return []
        exclude = exclude or set()
        where = ["chat_labels_fts MATCH ?"]
        params: list = [match]
        if brand:
            where.append("l.brand = ?")
            params.append(brand)
        if case_type:
            where.append("l.case_type = ?")
            params.append(case_type)
        if before:
            where.append("l.created_at < ?")
            params.append(before)
        if exclude:
            where.append(f"l.conversation_id NOT IN ({','.join('?' * len(exclude))})")
            params.extend(exclude)
        if human_only:
            where.append("l.conversation_id IN (SELECT conversation_id FROM case_verdicts)")
        sql = f"""SELECT l.*, bm25(chat_labels_fts) AS rank
                  FROM chat_labels_fts JOIN chat_labels l
                    ON l.conversation_id = chat_labels_fts.conversation_id
                  WHERE {' AND '.join(where)}
                  ORDER BY rank LIMIT ?"""
        params.append(limit)
        try:
            rows = self._conn.execute(sql, params).fetchall()
        except sqlite3.OperationalError:
            # A query the tokenizer cannot parse must not break grading.
            return []
        return [self._row(r) for r in rows]

    def rebuild_fts(self) -> int:
        c = self._conn
        c.execute("DELETE FROM chat_labels_fts")
        rows = c.execute("SELECT conversation_id, label_json FROM chat_labels").fetchall()
        c.executemany(
            "INSERT INTO chat_labels_fts (conversation_id, text) VALUES (?, ?)",
            [(r[0], searchable_text(json.loads(r[1]))) for r in rows],
        )
        c.commit()
        return len(rows)

    # ── verdicts ─────────────────────────────────────────────────────────────────────────
    def rebuild_verdicts(self) -> dict[str, int]:
        """Gather every human verdict into case_verdicts. Idempotent: the table is a derived
        view of the four source tables and is rebuilt from scratch."""
        c = self._conn
        c.execute("DELETE FROM case_verdicts")
        c.execute(
            """INSERT INTO case_verdicts
                 (conversation_id, source, source_ref, ruleset_id, ai_score, human_score,
                  criteria_json, note, reviewed_by, reviewed_at)
               SELECT conversation_id, 'override', '', ruleset_id, overall_score, human_score,
                      NULLIF(human_criteria, ''), COALESCE(override_reason, ''),
                      overridden_by, overridden_at
                 FROM grades WHERE human_score IS NOT NULL"""
        )
        c.execute(
            """INSERT INTO case_verdicts
                 (conversation_id, source, source_ref, ruleset_id, ai_score, human_score,
                  criteria_json, note, reviewed_by, reviewed_at)
               SELECT r.conversation_id, 'calibration', r.run_id, u.ruleset_id, r.overall_score,
                      r.human_score, NULLIF(r.human_criteria, ''), COALESCE(r.human_note, ''),
                      r.reviewed_by, r.reviewed_at
                 FROM calibration_results r JOIN calibration_runs u ON u.id = r.run_id
                WHERE r.human_score IS NOT NULL OR COALESCE(r.human_note, '') <> ''
                   OR COALESCE(r.human_criteria, '') NOT IN ('', '{}')"""
        )
        c.execute(
            """INSERT INTO case_verdicts
                 (conversation_id, source, source_ref, ruleset_id, ai_score, human_score,
                  criteria_json, note, reviewed_by, reviewed_at)
               SELECT m.conversation_id, 'comment', m.id, g.ruleset_id, g.overall_score,
                      g.human_score, NULL, m.text, m.author, m.created_at
                 FROM conversation_comments m LEFT JOIN grades g ON g.conversation_id = m.conversation_id
                WHERE TRIM(m.text) <> ''"""
        )
        c.execute(
            """INSERT INTO case_verdicts
                 (conversation_id, source, source_ref, ruleset_id, ai_score, human_score,
                  criteria_json, note, reviewed_by, reviewed_at)
               SELECT d.conversation_id, 'dispute', d.status, g.ruleset_id, g.overall_score,
                      g.human_score, NULL,
                      'Agent disputed: ' || d.reason || ' — ' || d.status || ': '
                        || COALESCE(d.resolution_note, ''),
                      d.resolved_by, d.resolved_at
                 FROM grade_disputes d LEFT JOIN grades g ON g.conversation_id = d.conversation_id
                WHERE d.status IN ('accepted', 'rejected')"""
        )
        c.commit()
        counts = c.execute(
            "SELECT source, COUNT(*) FROM case_verdicts GROUP BY source").fetchall()
        return {r[0]: r[1] for r in counts}

    def verdicts_for(self, ids: list[str]) -> dict[str, list[dict]]:
        if not ids:
            return {}
        rows = self._conn.execute(
            f"""SELECT * FROM case_verdicts
                WHERE conversation_id IN ({','.join('?' * len(ids))})
                ORDER BY conversation_id, reviewed_at""", list(ids)).fetchall()
        out: dict[str, list[dict]] = {}
        for r in rows:
            d = dict(r)
            d["criteria"] = json.loads(d["criteria_json"]) if d.get("criteria_json") else {}
            out.setdefault(d["conversation_id"], []).append(d)
        return out

    # ── bookkeeping ──────────────────────────────────────────────────────────────────────
    def get_meta(self, key: str) -> str | None:
        row = self._conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def set_meta(self, key: str, value: str) -> None:
        self._conn.execute(
            """INSERT INTO meta (key, value, updated_at) VALUES (?,?,?)
               ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at""",
            (key, value, _now()))
        self._conn.commit()

    def index_version(self) -> str:
        """A short hash that changes whenever a label or a verdict is added, so a grade can
        record which snapshot of the index it read."""
        c = self._conn
        labels = c.execute(
            "SELECT COUNT(*), MAX(labelled_at), MAX(label_version) FROM chat_labels").fetchone()
        verdicts = c.execute("SELECT COUNT(*), MAX(reviewed_at) FROM case_verdicts").fetchone()
        raw = json.dumps([list(labels), list(verdicts)], default=str)
        return hashlib.sha256(raw.encode()).hexdigest()[:12]

    def stats(self) -> dict:
        c = self._conn
        def grouped(col: str) -> list[dict]:
            return [{"key": r[0] or "", "n": r[1]} for r in c.execute(
                f"SELECT {col}, COUNT(*) AS n FROM chat_labels GROUP BY {col} ORDER BY n DESC"
            ).fetchall()]
        total = c.execute("SELECT COUNT(*), SUM(cost_usd), MIN(created_at), MAX(created_at) "
                          "FROM chat_labels").fetchone()
        verdict_counts = {r[0]: r[1] for r in c.execute(
            "SELECT source, COUNT(*) FROM case_verdicts GROUP BY source").fetchall()}
        labelled_with_verdict = c.execute(
            "SELECT COUNT(DISTINCT v.conversation_id) FROM case_verdicts v "
            "JOIN chat_labels l ON l.conversation_id = v.conversation_id").fetchone()[0]
        contacts = c.execute(
            "SELECT COUNT(DISTINCT contact_id) FROM chat_labels WHERE contact_id <> ''").fetchone()[0]
        return {
            "labels": total[0] or 0,
            "label_cost_usd": round(total[1] or 0.0, 4),
            "first": total[2], "last": total[3],
            "contacts": contacts,
            "by_source": grouped("source"),
            "by_brand": grouped("brand"),
            "by_case_type": grouped("case_type"),
            "by_language": grouped("language"),
            "verdicts": verdict_counts,
            "verdict_chats_labelled": labelled_with_verdict,
            "index_version": self.index_version(),
            "last_refresh_at": self.get_meta("history.last_refresh_at"),
        }

    @staticmethod
    def _row(row: sqlite3.Row) -> dict:
        d = dict(row)
        d["label"] = json.loads(d.pop("label_json"))
        return d
