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
    -- Which ruleset produced this grade ('default' | 'vip'). rules_version alone is a hash
    -- and doesn't say which ruleset's file it came from; staleness is judged per ruleset.
    ruleset_id      TEXT NOT NULL DEFAULT 'default',
    model           TEXT,
    graded_at       TEXT,        -- ISO timestamp
    payload_json    TEXT         -- full ConversationGrade as JSON
);
CREATE INDEX IF NOT EXISTS idx_grades_agent ON grades(agent_name);
CREATE INDEX IF NOT EXISTS idx_grades_time  ON grades(graded_at);

-- Which group an agent belongs to. Absence of a row means the standard group; the group
-- selects the QA ruleset their conversations are graded against (qa/rulesets.py).
CREATE TABLE IF NOT EXISTS agent_groups (
    agent_name        TEXT PRIMARY KEY,   -- the join key used across conversations/grades
    agent_email       TEXT,
    intercom_admin_id TEXT,               -- stable id, survives a rename in Intercom
    group_id          TEXT NOT NULL,      -- 'vip'
    updated_at        TEXT,
    updated_by        TEXT
);
CREATE INDEX IF NOT EXISTS idx_agent_groups_group ON agent_groups(group_id);

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
    manager_comment  TEXT NOT NULL DEFAULT '',
    snapshot_json    TEXT             -- frozen conversation+grade exemplar (survives deletion)
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

-- Soft-delete trash: deleting a conversation moves it (and its grade) here as a JSON
-- snapshot so it can be restored. Purging removes it for good.
CREATE TABLE IF NOT EXISTS deleted_conversations (
    conversation_id TEXT PRIMARY KEY,
    agent_name      TEXT,
    subject         TEXT,
    created_at      TEXT,           -- original conversation date (for sorting/presets)
    deleted_at      TEXT NOT NULL,
    deleted_by      TEXT NOT NULL,
    snapshot_json   TEXT NOT NULL   -- {conversation: <row>, grade: <row|null>}
);
CREATE INDEX IF NOT EXISTS idx_trash_deleted ON deleted_conversations(deleted_at);

-- Support-agent disputes of the QA grade a conversation received. One active record
-- per conversation; the manager resolves by accepting (and re-scoring via the override
-- flow) or rejecting. Raised via the review portal or the dashboard.
CREATE TABLE IF NOT EXISTS grade_disputes (
    conversation_id  TEXT PRIMARY KEY,
    agent_name       TEXT NOT NULL,
    reason           TEXT NOT NULL,
    created_via      TEXT NOT NULL,                 -- 'portal' | 'dashboard'
    created_by       TEXT NOT NULL,                 -- agent_name (portal) or username (dashboard)
    created_at       TEXT NOT NULL,                 -- ISO timestamp
    status           TEXT NOT NULL DEFAULT 'open',  -- 'open' | 'accepted' | 'rejected'
    resolution_note  TEXT,
    resolved_by      TEXT,
    resolved_at      TEXT
);
CREATE INDEX IF NOT EXISTS idx_disputes_status ON grade_disputes(status);
CREATE INDEX IF NOT EXISTS idx_disputes_agent  ON grade_disputes(agent_name);
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
        # JSON list of analyst manual deductions for things the AI can't verify
        # ([{category, points, note}], e.g. information correctness). NULL = none.
        ("human_deductions", "TEXT"),
        # Which ruleset scored this grade. Existing rows backfill to 'default', which is
        # accurate — they predate the VIP ruleset, so 'default' is what graded them.
        ("ruleset_id", "TEXT NOT NULL DEFAULT 'default'"),
    ]:
        if col not in grade_cols:
            conn.execute(f"ALTER TABLE grades ADD COLUMN {col} {definition}")
    conn.commit()

    # Frozen exemplar snapshot so knowledge-base cases stay viewable after the source
    # conversation/grade is deleted (added after initial release).
    iconic_cols = {row[1] for row in conn.execute("PRAGMA table_info(iconic_cases)").fetchall()}
    if "snapshot_json" not in iconic_cols:
        conn.execute("ALTER TABLE iconic_cases ADD COLUMN snapshot_json TEXT")
        conn.commit()

    # Link a review token to a specific coaching session (NULL = plain review link).
    token_cols = {row[1] for row in conn.execute("PRAGMA table_info(agent_review_tokens)").fetchall()}
    if "session_id" not in token_cols:
        conn.execute("ALTER TABLE agent_review_tokens ADD COLUMN session_id TEXT")
        conn.commit()

    # Soft-delete trash (added after initial release).
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    if "deleted_conversations" not in tables:
        conn.execute("""
            CREATE TABLE deleted_conversations (
                conversation_id TEXT PRIMARY KEY,
                agent_name      TEXT,
                subject         TEXT,
                created_at      TEXT,
                deleted_at      TEXT NOT NULL,
                deleted_by      TEXT NOT NULL,
                snapshot_json   TEXT NOT NULL
            )""")
        conn.execute("CREATE INDEX idx_trash_deleted ON deleted_conversations(deleted_at)")
        conn.commit()

    # Manager comments on conversations (added after initial release).
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

    # Agent → group membership, which selects the QA ruleset (added with the VIP ruleset).
    if "agent_groups" not in tables:
        conn.execute("""
            CREATE TABLE agent_groups (
                agent_name        TEXT PRIMARY KEY,
                agent_email       TEXT,
                intercom_admin_id TEXT,
                group_id          TEXT NOT NULL,
                updated_at        TEXT,
                updated_by        TEXT
            )""")
        conn.execute("CREATE INDEX idx_agent_groups_group ON agent_groups(group_id)")
        conn.commit()


def connect(db_path: str | Path) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    _migrate(conn)
    return conn
