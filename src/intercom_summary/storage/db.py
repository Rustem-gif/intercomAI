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

-- Knowledge base of iconic / representative cases curated by managers.
CREATE TABLE IF NOT EXISTS iconic_cases (
    conversation_id  TEXT PRIMARY KEY,
    added_by         TEXT NOT NULL,
    added_at         TEXT NOT NULL,   -- ISO timestamp
    manager_comment  TEXT NOT NULL DEFAULT ''
);

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

-- Shareable review links: manager generates a token per agent (+ optional tag filter).
-- The token URL is opened by the agent without any login requirement.
CREATE TABLE IF NOT EXISTS agent_review_tokens (
    token       TEXT PRIMARY KEY,  -- secrets.token_urlsafe(24)
    agent_name  TEXT NOT NULL,
    tag         TEXT,              -- optional custom_tag filter (NULL = all convos)
    label       TEXT NOT NULL,     -- friendly name shown on the review page
    created_by  TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    expires_at  TEXT               -- NULL = never expires
);
CREATE INDEX IF NOT EXISTS idx_art_agent ON agent_review_tokens(agent_name);

-- Acknowledgments: agents mark conversations as reviewed through the portal.
CREATE TABLE IF NOT EXISTS review_acknowledgments (
    token           TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    acknowledged_at TEXT NOT NULL,
    PRIMARY KEY (token, conversation_id)
);
CREATE INDEX IF NOT EXISTS idx_ack_token ON review_acknowledgments(token);

-- Coaching sessions: manager groups conversations for an agent with notes + due date.
CREATE TABLE IF NOT EXISTS coaching_sessions (
    id          TEXT PRIMARY KEY,
    agent_name  TEXT NOT NULL,
    title       TEXT NOT NULL,
    notes       TEXT NOT NULL DEFAULT '',
    due_date    TEXT,                            -- YYYY-MM-DD, nullable
    status      TEXT NOT NULL DEFAULT 'open',   -- 'open' | 'done'
    created_by  TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cs_agent ON coaching_sessions(agent_name);

CREATE TABLE IF NOT EXISTS coaching_items (
    session_id      TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    note            TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (session_id, conversation_id)
);

-- Manager comments on individual conversations.
CREATE TABLE IF NOT EXISTS conversation_comments (
    id              TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    author          TEXT NOT NULL,
    text            TEXT NOT NULL,
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cc_conversation ON conversation_comments(conversation_id);
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
        # JSON map of analyst per-criterion verdict changes ({id: "pass"|"fail"|"n/a"}),
        # used by ScoreBuddy-style re-scoring. NULL = score-only / no criterion override.
        ("human_criteria", "TEXT"),
    ]:
        if col not in grade_cols:
            conn.execute(f"ALTER TABLE grades ADD COLUMN {col} {definition}")
    conn.commit()

    # Link a review token to a specific coaching session (NULL = plain review link).
    token_cols = {row[1] for row in conn.execute("PRAGMA table_info(agent_review_tokens)").fetchall()}
    if "session_id" not in token_cols:
        conn.execute("ALTER TABLE agent_review_tokens ADD COLUMN session_id TEXT")
        conn.commit()

    # Manager comments on conversations (added after initial release).
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    if "conversation_comments" not in tables:
        conn.execute("""
            CREATE TABLE conversation_comments (
                id              TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL,
                author          TEXT NOT NULL,
                text            TEXT NOT NULL,
                created_at      TEXT NOT NULL
            )""")
        conn.execute("CREATE INDEX idx_cc_conversation ON conversation_comments(conversation_id)")
        conn.commit()


def connect(db_path: str | Path) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    _migrate(conn)
    return conn
