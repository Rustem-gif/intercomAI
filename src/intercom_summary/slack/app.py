"""Slack bot bootstrap (Socket Mode — no public URL needed)."""
from __future__ import annotations

from intercom_summary.settings import settings
from intercom_summary.logging_setup import get_logger

log = get_logger("slack.app")


def build_app():
    """Construct the slack_bolt App with handlers registered."""
    from slack_bolt import App

    from intercom_summary.slack.handlers import register

    settings.require_slack()
    app = App(token=settings.slack_bot_token, signing_secret=settings.slack_signing_secret or None)
    register(app)
    return app


def main() -> None:
    from slack_bolt.adapter.socket_mode import SocketModeHandler

    app = build_app()
    log.info("Starting Intercom Slack bot in Socket Mode…")
    SocketModeHandler(app, settings.slack_app_token).start()


if __name__ == "__main__":
    main()
