#!/usr/bin/env bash
# Make one backup now — the same thing the web app does every BACKUP_HOURS (backup.py):
# DB snapshot + config/rules into BACKUP_DIR, then rclone to RCLONE_REMOTE.
#
# Do NOT schedule this with launchd: macOS refuses a launchd job that runs a script under
# ~/Desktop ("Operation not permitted"), which is how the nightly backup silently stopped on
# 26 Aug 2026. The web app schedules backups itself; this is for a manual run.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
exec "$ROOT/.venv/bin/python" -c "
import json, logging
logging.basicConfig(level=logging.INFO, format='%(message)s')
from intercom_summary.backup import run_backup, sync_journal
from intercom_summary import sheets_export
sync_journal()
print(json.dumps(run_backup(), indent=1))
if sheets_export.enabled():
    print('Google Sheet rows:', sheets_export.export_grades())
"
