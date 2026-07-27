#!/usr/bin/env bash
# Wipe all cached conversations (and their grades, comments, acknowledgments,
# coaching items, and agent review tokens) from the local SQLite database.
# The database file itself is kept; only the row data is deleted.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DB="${DB_PATH:-$ROOT/data/grades.db}"

if [[ ! -f "$DB" ]]; then
  echo "Database not found: $DB"
  exit 1
fi

# Ensure all tables exist before we try to count or delete from them.
# (The Python backend creates them on first connect; the shell may run first.)
sqlite3 "$DB" "
  CREATE TABLE IF NOT EXISTS conversation_comments (
    id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL,
    author TEXT NOT NULL, text TEXT NOT NULL, created_at TEXT NOT NULL
  );
  CREATE TABLE IF NOT EXISTS review_acknowledgments (
    token TEXT NOT NULL, conversation_id TEXT NOT NULL,
    acknowledged_at TEXT NOT NULL, PRIMARY KEY (token, conversation_id)
  );
  CREATE TABLE IF NOT EXISTS coaching_items (
    session_id TEXT NOT NULL, conversation_id TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '', PRIMARY KEY (session_id, conversation_id)
  );
  CREATE TABLE IF NOT EXISTS coaching_sessions (
    id TEXT PRIMARY KEY, agent_name TEXT NOT NULL, title TEXT NOT NULL,
    notes TEXT NOT NULL DEFAULT '', due_date TEXT, status TEXT NOT NULL DEFAULT 'open',
    created_by TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
  );
  CREATE TABLE IF NOT EXISTS agent_review_tokens (
    token TEXT PRIMARY KEY, agent_name TEXT NOT NULL, tag TEXT,
    label TEXT NOT NULL, created_by TEXT NOT NULL, created_at TEXT NOT NULL,
    expires_at TEXT, session_id TEXT
  );
  CREATE TABLE IF NOT EXISTS iconic_cases (
    conversation_id TEXT PRIMARY KEY, added_by TEXT NOT NULL,
    added_at TEXT NOT NULL, manager_comment TEXT NOT NULL DEFAULT ''
  );
  CREATE TABLE IF NOT EXISTS deleted_conversations (
    conversation_id TEXT PRIMARY KEY, agent_name TEXT, subject TEXT, created_at TEXT,
    deleted_at TEXT NOT NULL, deleted_by TEXT NOT NULL, snapshot_json TEXT NOT NULL,
    blacklist INTEGER NOT NULL DEFAULT 1
  );
"

echo "Database: $DB"
echo ""
echo "Tables to be cleared:"
sqlite3 "$DB" "
  SELECT '  conversations:          ' || COUNT(*) FROM conversations;
  SELECT '  grades:                 ' || COUNT(*) FROM grades;
  SELECT '  conversation_comments:  ' || COUNT(*) FROM conversation_comments;
  SELECT '  review_acknowledgments: ' || COUNT(*) FROM review_acknowledgments;
  SELECT '  coaching_items:         ' || COUNT(*) FROM coaching_items;
  SELECT '  coaching_sessions:      ' || COUNT(*) FROM coaching_sessions;
  SELECT '  agent_review_tokens:    ' || COUNT(*) FROM agent_review_tokens;
  SELECT '  iconic_cases:           ' || COUNT(*) FROM iconic_cases;
  SELECT '  deleted_conversations:  ' || COUNT(*) FROM deleted_conversations;
"
echo ""
echo "Note: clearing deleted_conversations (the Trash) also lifts the block that stops"
echo "      previously deleted conversations from being re-imported by an Intercom fetch."

echo ""
read -r -p "Delete all of the above? [y/N] " confirm
if [[ "$confirm" != "y" && "$confirm" != "Y" ]]; then
  echo "Aborted."
  exit 0
fi

sqlite3 "$DB" "
  DELETE FROM conversation_comments;
  DELETE FROM review_acknowledgments;
  DELETE FROM coaching_items;
  DELETE FROM coaching_sessions;
  DELETE FROM agent_review_tokens;
  DELETE FROM iconic_cases;
  DELETE FROM grades;
  DELETE FROM conversations;
  DELETE FROM deleted_conversations;
  VACUUM;
"

echo "Done — all conversation data cleared."
