#!/usr/bin/env python
"""Start the Intercom Slack bot (Socket Mode).

Usage:
    python scripts/run_slack_bot.py
Requires SLACK_BOT_TOKEN and SLACK_APP_TOKEN in your environment / .env.
"""
import sys
from pathlib import Path

# Make `src/` importable when run as a plain script.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from intercom_summary.slack.app import main  # noqa: E402

if __name__ == "__main__":
    main()
