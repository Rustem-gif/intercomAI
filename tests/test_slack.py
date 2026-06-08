from intercom_summary.settings import settings
from intercom_summary.slack import handlers
from intercom_summary.slack.blocks import (
    MODAL_CALLBACK,
    build_modal,
    parse_modal_submission,
    result_message,
)


def test_build_modal_shape():
    view = build_modal("C123")
    assert view["callback_id"] == MODAL_CALLBACK
    assert view["private_metadata"] == "C123"
    block_ids = {b.get("block_id") for b in view["blocks"]}
    assert {"agents", "since", "until", "state", "action"} <= block_ids


def test_modal_agents_block_uses_admins_when_provided():
    admins = [{"id": "1", "name": "Ada", "email": "ada@co.com"},
              {"id": "2", "name": "Bob", "email": "bob@co.com"}]
    view = build_modal("C1", admins)
    agents_block = [b for b in view["blocks"] if b.get("block_id") == "agents"][0]
    el = agents_block["element"]
    assert el["type"] == "multi_static_select"
    assert {o["value"] for o in el["options"]} == {"ada@co.com", "bob@co.com"}

    # Without admins it falls back to a free-text input.
    fallback = build_modal("C1")
    fb_block = [b for b in fallback["blocks"] if b.get("block_id") == "agents"][0]
    assert fb_block["element"]["type"] == "plain_text_input"


def test_parse_modal_multiselect_agents():
    view = {
        "private_metadata": "C1",
        "state": {"values": {
            "agents": {"value": {"selected_options": [
                {"value": "ada@co.com"}, {"value": "bob@co.com"}]}},
            "action": {"value": {"selected_option": {"value": "fetch"}}},
        }},
    }
    p = parse_modal_submission(view)
    assert p["agents"] == ["ada@co.com", "bob@co.com"]
    assert p["action"] == "fetch"


def test_parse_modal_submission():
    view = {
        "private_metadata": "C999",
        "state": {
            "values": {
                "agents": {"value": {"value": "ada@co.com, Bob"}},
                "since": {"value": {"selected_date": "2026-05-01"}},
                "until": {"value": {"selected_date": None}},
                "state": {"value": {"selected_option": {"value": "closed"}}},
                "action": {"value": {"selected_option": {"value": "review"}}},
            }
        },
    }
    p = parse_modal_submission(view)
    assert p == {
        "action": "review",
        "agents": ["ada@co.com", "Bob"],
        "since": "2026-05-01",
        "until": None,
        "state": "closed",
        "channel_id": "C999",
    }


def test_result_message_fetch_has_run_qa_button():
    blocks = result_message("fetch", {"fetched": 3}, {"agents": ["Ada"]})
    actions = [b for b in blocks if b["type"] == "actions"][0]
    action_ids = [e.get("action_id") for e in actions["elements"]]
    assert "run_qa" in action_ids


class FakeClient:
    def __init__(self):
        self.messages = []
        self.uploads = []

    def chat_postMessage(self, **kwargs):
        self.messages.append(kwargs)

    def files_upload_v2(self, **kwargs):
        self.uploads.append(kwargs)


def test_run_action_fetch(monkeypatch):
    object.__setattr__(settings, "intercom_token", "tok")

    async def fake_fetch_and_store(**kwargs):
        return {"fetched": 2, "conversation_ids": [], "agents": kwargs.get("agents", [])}

    monkeypatch.setattr("intercom_summary.service.fetch_and_store", fake_fetch_and_store)

    client = FakeClient()
    handlers.run_action(
        "fetch", {"agents": ["Ada"], "since": None, "until": None, "state": None},
        client=client, channel="C1", user_id="U1",
    )
    # A result message with the fetched count was posted.
    assert any("blocks" in m for m in client.messages)
