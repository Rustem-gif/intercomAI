"""Slack `/intercom` — interactive Block Kit modal + buttons, with a typed shortcut.

  • `/intercom`            → opens a modal (agents, date range, state, action)
  • `/intercom whoami`     → show your role
  • `/intercom help`       → command help
  • `/intercom fetch agent:ada@co.com since:2026-05-01 …`  → typed shortcut (back-compat)

All data actions are gated by the `analyst` role (see auth.py) and run through the shared
service layer so the web API and Slack execute identical logic.
"""
from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path

from intercom_summary import service
from intercom_summary.export.xlsx import export_xlsx
from intercom_summary.logging_setup import get_logger
from intercom_summary.settings import settings
from intercom_summary.slack.auth import NO_ACCESS_MESSAGE, roles
from intercom_summary.slack.blocks import (
    MODAL_CALLBACK,
    build_modal,
    parse_modal_submission,
    result_message,
)
from intercom_summary.storage.conversations_store import ConversationsStore

log = get_logger("slack.handlers")

HELP_TEXT = (
    "*Intercom bot*\n"
    "• `/intercom` — open the interactive panel (agents, dates, action)\n"
    "• `/intercom fetch agent:<name|email> [since:YYYY-MM-DD] [until:YYYY-MM-DD] [state:closed]`\n"
    "• `/intercom list agent:<name|email> …`  ·  `/intercom review agent:<name|email> …`\n"
    "• `/intercom whoami` — show your role"
)


# ── typed-arg parsing (back-compat shortcut) ─────────────────────────────────────
def parse_args(text: str) -> tuple[str, dict[str, str]]:
    parts = text.strip().split()
    if not parts:
        return "", {}
    sub = parts[0].lower()
    kv: dict[str, str] = {}
    for token in parts[1:]:
        if ":" in token:
            k, v = token.split(":", 1)
            kv[k.lower()] = v
    return sub, kv


def _agents(kv: dict[str, str]) -> list[str]:
    return [a for a in (x.strip() for x in kv.get("agent", "").split(",")) if a]


# ── shared work (used by modal, buttons, and typed path) ─────────────────────────
def _fetch_store(agents, since, until, state) -> dict:
    settings.require_intercom()
    return asyncio.run(
        service.fetch_and_store(agents=agents, since=since, until=until, state=state)
    )


def _upload_xlsx(client, channel, conversation_ids, agents) -> None:
    store = ConversationsStore()
    try:
        convos = [c for cid in conversation_ids if (c := store.get(cid))]
    finally:
        store.close()
    if not convos:
        return
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "intercom_export.xlsx"
        export_xlsx(convos, out)
        client.files_upload_v2(
            channel=channel, file=str(out), filename="intercom_export.xlsx",
            title=f"Intercom export — {', '.join(agents)}",
            initial_comment=f":white_check_mark: {len(convos)} conversation(s) exported.",
        )


def run_action(action: str, params: dict, *, client, channel: str, user_id: str) -> None:
    """Execute fetch / list / review and post results to `channel`. Posts errors too."""
    try:
        agents = params.get("agents") or []
        since, until, state = params.get("since"), params.get("until"), params.get("state")

        if action in ("fetch", "list", "review") and not agents and action != "review":
            client.chat_postMessage(channel=channel, text=":warning: Please specify at least one agent.")
            return

        if action == "fetch":
            summary = _fetch_store(agents, since, until, state)
            _upload_xlsx(client, channel, summary.get("conversation_ids", []), agents)
            client.chat_postMessage(channel=channel, blocks=result_message("fetch", summary, params))

        elif action == "list":
            _fetch_store(agents, since, until, state)
            store = ConversationsStore()
            try:
                rows, total = store.query(agents=agents, since=since, until=until, state=state, limit=25)
            finally:
                store.close()
            lines = [f":inbox_tray: *{total} conversation(s)*"]
            for r in rows:
                lines.append(
                    f"• `{r['id']}` — {r['state']} — {r['message_count']} msgs — "
                    f"{r['agent_name']} ↔ {r['customer_name'] or r['customer_email']} — {(r['subject'] or '')[:60]}"
                )
            client.chat_postMessage(channel=channel, text="\n".join(lines))

        elif action == "review":
            settings.require_qa()
            if agents:  # ensure conversations are cached before grading
                _fetch_store(agents, since, until, state)
            summary = service.review_and_store(agents=agents or None, since=since, until=until, state=state)
            client.chat_postMessage(channel=channel, blocks=result_message("review", summary, params))
    except Exception as e:  # noqa: BLE001 - surface to user, log full
        log.exception("%s action failed", action)
        client.chat_postMessage(channel=channel, text=f":x: {action.title()} failed: {e}")


# ── registration ────────────────────────────────────────────────────────────────
def register(app) -> None:
    @app.command("/intercom")
    def handle_command(ack, command, respond, client):
        ack()
        user_id = command["user_id"]
        sub, kv = parse_args(command.get("text", ""))

        if sub == "help":
            respond(HELP_TEXT)
            return
        if sub == "whoami":
            respond(f"You are *{roles.role_for(user_id)}*.")
            return

        if not roles.can_use_data(user_id):
            respond(NO_ACCESS_MESSAGE)
            return

        # No subcommand → open the interactive modal (with a live agent pick-list).
        if sub == "":
            admins = None
            try:
                admins = asyncio.run(service.list_agents())
            except Exception as e:  # fall back to a text input if Intercom is unreachable
                log.warning("Could not load agents for modal: %s", e)
            client.views_open(
                trigger_id=command["trigger_id"],
                view=build_modal(command["channel_id"], admins),
            )
            return

        # Typed shortcut.
        if sub in ("fetch", "list", "review"):
            respond(f":hourglass_flowing_sand: Running `{sub}`…")
            run_action(
                sub,
                {"agents": _agents(kv), "since": kv.get("since"), "until": kv.get("until"),
                 "state": kv.get("state")},
                client=client, channel=command["channel_id"], user_id=user_id,
            )
        else:
            respond(f":warning: Unknown `{sub}`.\n{HELP_TEXT}")

    @app.view(MODAL_CALLBACK)
    def handle_modal_submit(ack, body, client):
        ack()
        user_id = body["user"]["id"]
        params = parse_modal_submission(body["view"])
        channel = params.get("channel_id") or user_id  # fall back to DM
        if not roles.can_use_data(user_id):
            client.chat_postMessage(channel=user_id, text=NO_ACCESS_MESSAGE)
            return
        run_action(params["action"], params, client=client, channel=channel, user_id=user_id)

    @app.action("run_qa")
    def handle_run_qa(ack, body, client):
        ack()
        user_id = body["user"]["id"]
        channel = body.get("channel", {}).get("id") or user_id
        if not roles.can_use_data(user_id):
            client.chat_postMessage(channel=channel, text=NO_ACCESS_MESSAGE)
            return
        params = json.loads(body["actions"][0]["value"])
        client.chat_postMessage(channel=channel, text=":hourglass_flowing_sand: Running QA…")
        run_action("review", params, client=client, channel=channel, user_id=user_id)
