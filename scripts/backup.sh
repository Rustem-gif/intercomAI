#!/usr/bin/env bash
# Create a timestamped local snapshot of the DB + config/rules.
# Optionally syncs to a rclone remote (set RCLONE_REMOTE below or in .env).
#
# Usage:
#   ./scripts/backup.sh              # local snapshot only
#   RCLONE_REMOTE=gdrive:intercom-backup ./scripts/backup.sh
#
# Configuration (env vars or .env):
#   BACKUP_KEEP_DAYS   Days of local snapshots to keep (default: 7)
#   RCLONE_REMOTE      rclone destination, e.g. "gdrive:intercom-backup"
#                      Leave empty to skip cloud upload.
#   GPG_PASSPHRASE     If set, .env is encrypted before cloud upload.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# Load .env if present (for GPG_PASSPHRASE / RCLONE_REMOTE overrides).
# macOS ships bash 3.2 where `source <(...)` runs in a subshell and variables
# don't propagate back — use a temp file instead.
if [[ -f "$ROOT/.env" ]]; then
  _tmp_env=$(mktemp)
  grep -v '^#' "$ROOT/.env" | grep -E '^[A-Z_]+=' > "$_tmp_env"
  set -a
  # shellcheck disable=SC1090
  source "$_tmp_env"
  set +a
  rm -f "$_tmp_env"
fi

BACKUP_KEEP_DAYS="${BACKUP_KEEP_DAYS:-7}"
RCLONE_REMOTE="${RCLONE_REMOTE:-}"
GPG_PASSPHRASE="${GPG_PASSPHRASE:-}"

DB_PATH="$ROOT/data/grades.db"
BACKUP_DIR="$ROOT/data/backups"
STAMP="$(date +%Y%m%d_%H%M%S)"
SNAPSHOT_DIR="$BACKUP_DIR/$STAMP"

mkdir -p "$SNAPSHOT_DIR"

echo "==> Backup started: $STAMP"

# ── 1. SQLite hot-copy (safe while the app is running) ──────────────────────
if [[ -f "$DB_PATH" ]]; then
  echo "    DB snapshot…"
  sqlite3 "$DB_PATH" ".backup '$SNAPSHOT_DIR/grades.db'"
  echo "    $(du -sh "$SNAPSHOT_DIR/grades.db" | cut -f1)  grades.db"
else
  echo "    WARNING: DB not found at $DB_PATH — skipping"
fi

# ── 2. Config + rules tar ───────────────────────────────────────────────────
echo "    Config/rules archive…"
tar -czf "$SNAPSHOT_DIR/config.tar.gz" \
  -C "$ROOT" \
  config/ rules/ \
  2>/dev/null || true
echo "    $(du -sh "$SNAPSHOT_DIR/config.tar.gz" | cut -f1)  config.tar.gz"

# ── 3. Optional: prune old local snapshots ──────────────────────────────────
echo "    Pruning snapshots older than ${BACKUP_KEEP_DAYS} days…"
find "$BACKUP_DIR" -maxdepth 1 -mindepth 1 -type d \
  -mtime +"$BACKUP_KEEP_DAYS" -exec rm -rf {} + 2>/dev/null || true
REMAINING=$(find "$BACKUP_DIR" -maxdepth 1 -mindepth 1 -type d | wc -l | tr -d ' ')
echo "    $REMAINING snapshot(s) retained locally"

# ── 4. Optional: cloud upload via rclone ────────────────────────────────────
if [[ -n "$RCLONE_REMOTE" ]]; then
  if ! command -v rclone &>/dev/null; then
    echo "    WARNING: RCLONE_REMOTE is set but rclone is not installed."
    echo "             Run: brew install rclone && rclone config"
  else
    echo "    Uploading to $RCLONE_REMOTE …"

    # Encrypt .env before upload if GPG_PASSPHRASE is set
    if [[ -f "$ROOT/.env" && -n "$GPG_PASSPHRASE" ]]; then
      echo "    Encrypting .env…"
      gpg --batch --yes --passphrase "$GPG_PASSPHRASE" \
        --symmetric --cipher-algo AES256 \
        --output "$SNAPSHOT_DIR/env.gpg" \
        "$ROOT/.env"
    fi

    rclone copy "$SNAPSHOT_DIR" "$RCLONE_REMOTE/$STAMP" \
      --transfers 4 --quiet
    echo "    Uploaded → $RCLONE_REMOTE/$STAMP"
  fi
fi

echo "==> Done. Snapshot: $SNAPSHOT_DIR"
