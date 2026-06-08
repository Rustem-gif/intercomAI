"""SQLite connection + schema. One small DB stores QA grades over time."""
from __future__ import annotations

import sqlite3
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS grades (
    conversation_id TEXT PRIMARY KEY,
    agent_name      TEXT,
    agent_email     TEXT,
    overall_score   INTEGER,
    summary         TEXT,
    rules_version   TEXT,
    model           TEXT,
    graded_at       TEXT,        -- ISO timestamp
    payload_json    TEXT         -- full ConversationGrade as JSON
);
CREATE INDEX IF NOT EXISTS idx_grades_agent ON grades(agent_name);
CREATE INDEX IF NOT EXISTS idx_grades_time  ON grades(graded_at);

-- Cache of fetched conversations so the web UI can browse / slice without re-hitting
-- the Intercom API. payload_json holds the full normalised Conversation.
CREATE TABLE IF NOT EXISTS conversations (
    id              TEXT PRIMARY KEY,
    agent_name      TEXT,
    agent_email     TEXT,
    customer_name   TEXT,
    customer_email  TEXT,
    state           TEXT,
    subject         TEXT,
    created_at      TEXT,        -- ISO timestamp
    updated_at      TEXT,
    message_count   INTEGER,
    csat_rating     INTEGER,
    tags            TEXT,        -- comma-separated (Intercom tags)
    custom_tags     TEXT NOT NULL DEFAULT '',  -- comma-separated (analyst tags)
    payload_json    TEXT,        -- full Conversation as JSON
    fetched_at      TEXT
);
CREATE INDEX IF NOT EXISTS idx_convo_agent ON conversations(agent_name);
CREATE INDEX IF NOT EXISTS idx_convo_created ON conversations(created_at);
CREATE INDEX IF NOT EXISTS idx_convo_state ON conversations(state);

-- Background jobs (fetch / review) so the UI and Slack can poll progress.
CREATE TABLE IF NOT EXISTS jobs (
    id           TEXT PRIMARY KEY,
    kind         TEXT,           -- "fetch" | "review"
    status       TEXT,           -- "queued" | "running" | "done" | "error"
    params_json  TEXT,
    result_json  TEXT,
    error        TEXT,
    created_at   TEXT,
    updated_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
"""


def _migrate(conn: sqlite3.Connection) -> None:
    """Idempotent migrations for databases created before schema additions."""
    convo_cols = {row[1] for row in conn.execute("PRAGMA table_info(conversations)").fetchall()}
    if "custom_tags" not in convo_cols:
        conn.execute("ALTER TABLE conversations ADD COLUMN custom_tags TEXT NOT NULL DEFAULT ''")
        conn.commit()

    grade_cols = {row[1] for row in conn.execute("PRAGMA table_info(grades)").fetchall()}
    for col, definition in [
        ("human_score",    "INTEGER"),
        ("override_reason","TEXT"),
        ("overridden_by",  "TEXT"),
        ("overridden_at",  "TEXT"),
    ]:
        if col not in grade_cols:
            conn.execute(f"ALTER TABLE grades ADD COLUMN {col} {definition}")
    conn.commit()


def connect(db_path: str | Path) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    _migrate(conn)
    return conn
