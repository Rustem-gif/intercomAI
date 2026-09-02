"""Block Kit view + message builders, plus pure parsers (unit-testable, no Slack needed)."""
from __future__ import annotations

from typing import Any

from intercom_summary.settings import settings

MODAL_CALLBACK = "intercom_modal"


def _agents_block(admins: list[dict] | None) -> dict[str, Any]:
    """A multi-select of live Intercom teammates, or a text input as a fallback."""
    if admins:
        # Slack caps a static select at 100 options.
        options = [
            {
                "text": {"type": "plain_text", "text": (a.get("name") or a.get("email") or a["id"])[:75]},
                "value": (a.get("email") or a.get("name") or a["id"])[:75],
            }
            for a in admins[:100]
        ]
        element = {
            "type": "multi_static_select",
            "action_id": "value",
            "placeholder": {"type": "plain_text", "text": "Choose agents"},
            "options": options,
        }
        label = "Agents"
    else:
        element = {
            "type": "plain_text_input",
            "action_id": "value",
            "placeholder": {"type": "plain_text", "text": "ada@co.com, Bob Smith"},
        }
        label = "Agents (name or email, comma-separated)"
    return {
        "type": "input",
        "block_id": "agents",
        "label": {"type": "plain_text", "text": label},
        "element": element,
        "optional": True,
    }


def build_modal(channel_id: str = "", admins: list[dict] | None = None) -> dict[str, Any]:
    """The /intercom modal: agents + date range + state + action."""
    return {
        "type": "modal",
        "callback_id": MODAL_CALLBACK,
        "private_metadata": channel_id,
        "title": {"type": "plain_text", "text": "Intercom"},
        "submit": {"type": "plain_text", "text": "Run"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "blocks": [
            _agents_block(admins),
            {
                "type": "input",
                "block_id": "since",
                "label": {"type": "plain_text", "text": "Since"},
                "element": {"type": "datepicker", "action_id": "value"},
                "optional": True,
            },
            {
                "type": "input",
                "block_id": "until",
                "label": {"type": "plain_text", "text": "Until"},
                "element": {"type": "datepicker", "action_id": "value"},
                "optional": True,
            },
            {
                "type": "input",
                "block_id": "state",
                "label": {"type": "plain_text", "text": "State"},
                "optional": True,
                "element": {
                    "type": "static_select",
                    "action_id": "value",
                    "placeholder": {"type": "plain_text", "text": "Any"},
                    "options": [
                        {"text": {"type": "plain_text", "text": s.title()}, "value": s}
                        for s in ("open", "closed", "snoozed")
                    ],
                },
            },
            {
                "type": "input",
                "block_id": "action",
                "label": {"type": "plain_text", "text": "Action"},
                "element": {
                    "type": "radio_buttons",
                    "action_id": "value",
                    "initial_option": {"text": {"type": "plain_text", "text": "Fetch → XLSX"}, "value": "fetch"},
                    "options": [
                        {"text": {"type": "plain_text", "text": "Fetch → XLSX"}, "value": "fetch"},
                        {"text": {"type": "plain_text", "text": "List (quick summary)"}, "value": "list"},
                        {"text": {"type": "plain_text", "text": "Review (QA grade)"}, "value": "review"},
                    ],
                },
            },
        ],
    }


def parse_modal_submission(view: dict[str, Any]) -> dict[str, Any]:
    """Pull a clean params dict out of a Slack view_submission payload."""
    state = view.get("state", {}).get("values", {})

    def _txt(block: str) -> str:
        return (state.get(block, {}).get("value", {}) or {}).get("value") or ""

    def _date(block: str) -> str | None:
        return (state.get(block, {}).get("value", {}) or {}).get("selected_date")

    def _select(block: str) -> str | None:
        opt = (state.get(block, {}).get("value", {}) or {}).get("selected_option")
        return opt.get("value") if opt else None

    # Agents come from a multi_static_select (selected_options) or a text fallback.
    agent_block = state.get("agents", {}).get("value", {}) or {}
    selected_opts = agent_block.get("selected_options")
    if selected_opts is not None:
        agents = [o["value"] for o in selected_opts]
    else:
        agents = [a.strip() for a in _txt("agents").split(",") if a.strip()]
    return {
        "action": _select("action") or "fetch",
        "agents": agents,
        "since": _date("since"),
        "until": _date("until"),
        "state": _select("state"),
        "channel_id": view.get("private_metadata") or "",
    }


def result_message(action: str, summary: dict, params: dict) -> list[dict]:
    """Block Kit result message with action buttons."""
    if action == "fetch":
        fetched, skipped = summary.get("fetched", 0), summary.get("skipped_deleted", 0)
        if skipped:
            text = (
                f":warning: Fetched *{fetched}* chat(s) but stored only "
                f"*{summary.get('saved', 0)}* — *{skipped}* are in the Trash and are blocked "
                f"from re-import. Restore or purge them to import them again."
            )
        else:
            text = f":white_check_mark: Fetched *{fetched}* chat(s)."
        # Not a warning: tickets are deliberately out of scope, but saying so stops the count
        # looking short against what Intercom reports for the same window.
        tickets = summary.get("skipped_tickets", 0)
        if tickets:
            text += f" (Skipped *{tickets}* ticket(s) — chats only.)"
    elif action == "review":
        text = (
            f":clipboard: Graded *{summary.get('graded', 0)}* "
            f"(skipped {summary.get('skipped', 0)} already-graded)."
        )
    else:
        text = f":inbox_tray: {summary.get('count', 0)} conversation(s)."

    import json

    buttons = [
        {
            "type": "button",
            "text": {"type": "plain_text", "text": "Open dashboard"},
            "url": settings.web_base_url,
        }
    ]
    if action == "fetch":
        buttons.insert(
            0,
            {
                "type": "button",
                "style": "primary",
                "text": {"type": "plain_text", "text": "Run QA on these"},
                "action_id": "run_qa",
                "value": json.dumps(
                    {k: params.get(k) for k in ("agents", "since", "until", "state")}
                ),
            },
        )

    return [
        {"type": "section", "text": {"type": "mrkdwn", "text": text}},
        {"type": "actions", "elements": buttons},
    ]
