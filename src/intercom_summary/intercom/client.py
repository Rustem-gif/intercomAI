"""Thin async client over the Intercom REST API.

Handles auth, the pinned API version, 429 rate-limit backoff, and cursor pagination so
callers (`fetch.py`) work with plain Python data only.

Docs: https://developers.intercom.com/docs/references/rest-api/
"""
from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator

import httpx

from intercom_summary.settings import settings
from intercom_summary.logging_setup import get_logger

log = get_logger(__name__)

_MAX_RETRIES = 5


class IntercomError(RuntimeError):
    pass


class IntercomClient:
    def __init__(
        self,
        token: str | None = None,
        base_url: str | None = None,
        api_version: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._token = token if token is not None else settings.intercom_token
        self._base_url = (base_url or settings.intercom_base_url).rstrip("/")
        self._api_version = api_version or settings.intercom_api_version
        # Allow injecting a client (tests use respx against a real AsyncClient).
        self._client = client or httpx.AsyncClient(timeout=30.0)
        self._owns_client = client is None

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Intercom-Version": self._api_version,
        }

    async def __aenter__(self) -> "IntercomClient":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    # ── low-level request with retry ─────────────────────────────────────────
    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        url = path if path.startswith("http") else f"{self._base_url}{path}"
        for attempt in range(_MAX_RETRIES):
            resp = await self._client.request(method, url, headers=self._headers, **kwargs)

            if resp.status_code == 429 or resp.status_code >= 500:
                wait = self._retry_after(resp, attempt)
                log.warning("Intercom %s on %s — retry in %.1fs (attempt %d)",
                            resp.status_code, path, wait, attempt + 1)
                await asyncio.sleep(wait)
                continue

            if resp.status_code >= 400:
                raise IntercomError(f"{resp.status_code} {method} {url}: {resp.text[:500]}")

            return resp.json()

        raise IntercomError(f"Gave up after {_MAX_RETRIES} retries: {method} {url}")

    @staticmethod
    def _retry_after(resp: httpx.Response, attempt: int) -> float:
        header = resp.headers.get("Retry-After")
        if header:
            try:
                return float(header)
            except ValueError:
                pass
        return min(2 ** attempt, 30)  # exponential backoff, capped

    # ── high-level endpoints ─────────────────────────────────────────────────
    async def list_admins(self) -> list[dict[str, Any]]:
        """All teammates (admins) in the workspace."""
        data = await self._request("GET", "/admins")
        return data.get("admins", [])

    async def get_conversation(self, conversation_id: str) -> dict[str, Any]:
        """Full conversation including all conversation_parts."""
        return await self._request(
            "GET", f"/conversations/{conversation_id}", params={"display_as": "plaintext"}
        )

    async def search_conversations(
        self, query: dict[str, Any], per_page: int = 150
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield conversation stubs matching an Intercom search query.

        Follows cursor pagination (`pages.next.starting_after`) until exhausted.
        Note: search returns *partial* conversations — call get_conversation() for the
        full thread.
        """
        starting_after: str | None = None
        while True:
            pagination: dict[str, Any] = {"per_page": per_page}
            if starting_after:
                pagination["starting_after"] = starting_after
            body = {"query": query, "pagination": pagination}
            data = await self._request("POST", "/conversations/search", json=body)

            for convo in data.get("conversations", []):
                yield convo

            nxt = (data.get("pages") or {}).get("next")
            # `next` may be a string (older) or an object with starting_after.
            if isinstance(nxt, dict):
                starting_after = nxt.get("starting_after")
            elif isinstance(nxt, str):
                starting_after = nxt
            else:
                starting_after = None
            if not starting_after:
                break
