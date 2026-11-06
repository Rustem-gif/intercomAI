"""Re-apply QA's journaled actions (re-scores, Agree, comments, disputes, tags…) onto the DB.

Run after restoring a backup, from the repo root, with the service stopped:

    .venv/bin/python scripts/replay_journal.py --since 2026-11-06T12:55:41 --dry-run
    .venv/bin/python scripts/replay_journal.py --since 2026-11-06T12:55:41

--since: the snapshot's time — `journal_through` in its backup result (Storage page), or the
snapshot folder's stamp (UTC). Overlap is harmless: the replay is idempotent.
--from-remote: first copy the offsite journal (RCLONE_REMOTE/journal) into JOURNAL_DIR, for a
machine whose local journal was lost too.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys

from intercom_summary.settings import settings
from intercom_summary.storage.journal_replay import replay


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--since", help="UTC ISO timestamp; default: the whole journal")
    p.add_argument("--until", help="UTC ISO timestamp (exclusive)")
    p.add_argument("--dry-run", action="store_true", help="Count what would change; write nothing")
    p.add_argument("--from-remote", action="store_true",
                   help="rclone copy RCLONE_REMOTE/journal into JOURNAL_DIR first")
    args = p.parse_args()

    if args.from_remote:
        from intercom_summary.backup import _rclone

        rclone = _rclone()
        if not (rclone and settings.rclone_remote):
            sys.exit("--from-remote needs rclone and RCLONE_REMOTE")
        subprocess.run([rclone, "copy", f"{settings.rclone_remote}/journal",
                        str(settings.journal_dir)], check=True)

    result = replay(args.since, args.until, dry_run=args.dry_run)
    print(json.dumps(result, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
