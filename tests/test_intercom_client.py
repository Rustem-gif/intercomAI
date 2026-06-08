import httpx
import pytest
import respx

from intercom_summary.intercom.client import IntercomClient

BASE = "https://api.eu.intercom.io"


@respx.mock
async def test_search_paginates_until_exhausted():
    route = respx.post(f"{BASE}/conversations/search")
    route.side_effect = [
        httpx.Response(200, json={
            "conversations": [{"id": "1"}, {"id": "2"}],
            "pages": {"next": {"starting_after": "CURSOR_A"}},
        }),
        httpx.Response(200, json={
            "conversations": [{"id": "3"}],
            "pages": {"next": None},
        }),
    ]
    async with httpx.AsyncClient() as http:
        client = IntercomClient(token="t", base_url=BASE, client=http)
        ids = [c["id"] async for c in client.search_conversations({"field": "x"})]
    assert ids == ["1", "2", "3"]
    assert route.call_count == 2


@respx.mock
async def test_retries_on_429(monkeypatch):
    # Avoid real sleeping during backoff.
    import intercom_summary.intercom.client as mod
    async def _no_sleep(_):
        return None
    monkeypatch.setattr(mod.asyncio, "sleep", _no_sleep)

    route = respx.get(f"{BASE}/admins")
    route.side_effect = [
        httpx.Response(429, headers={"Retry-After": "0"}),
        httpx.Response(200, json={"admins": [{"id": "9", "name": "Ada"}]}),
    ]
    async with httpx.AsyncClient() as http:
        client = IntercomClient(token="t", base_url=BASE, client=http)
        admins = await client.list_admins()
    assert admins[0]["name"] == "Ada"
    assert route.call_count == 2
