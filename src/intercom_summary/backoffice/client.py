"""Read-only HTTP client for the SoftSwiss back office (Rails ActiveAdmin under /backend).

How the BO works (explored 2026-10-28):

- Login is ONE form post to /backend/login: admin_user[email], admin_user[password],
  admin_user[otp_attempt] (the 6-digit TOTP code) and the page's authenticity_token. The client
  computes the code from BO_KB_OTP_SECRET itself, so it logs in unattended. The session is the
  `_casino_session` cookie (~20 min idle); it is kept on disk so a run does not log in each time,
  and a redirect back to /login triggers one silent re-login.
- Player pages (/backend/players/<id>) are server-rendered HTML. The BO refuses them to a
  script-style request ("You are not authorized to perform this action.") but serves a normal
  page navigation, so every page GET carries browser navigation headers.
- Some lists have a JSON API under /api/v1/admin/... behind the same session (payments,
  reports/bonus_issued, reports/freespin_issued).
- The player id is the Intercom contact's `cleanid` attribute (external_id "c<id>").

The BO is production and its player page carries controls that change a player the moment they
are used (a status select: disable / suspend / restrict…). This client therefore only ever
sends GET — the login form is the single POST — and refuses any path that names an action.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any

import httpx

from intercom_summary.backoffice import totp
from intercom_summary.logging_setup import get_logger
from intercom_summary.settings import settings

log = get_logger(__name__)

_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
       "Chrome/146.0.0.0 Safari/537.36")
_NAVIGATE = {
    "User-Agent": _UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}
_JSON = {"User-Agent": _UA, "Accept": "application/json, text/plain, */*",
         "Sec-Fetch-Dest": "empty", "Sec-Fetch-Mode": "cors", "Sec-Fetch-Site": "same-origin"}

# Path segments that name an action rather than a page. A GET to them is refused outright.
_ACTION_WORDS = re.compile(
    r"/(update|edit|new|destroy|delete|create|issue|repush|disable|enable|suspend|restrict|"
    r"activate|lock|unlock|approve|decline|cancel|mark|discard|merge|upload|link)[a-z_]*(/|$|\?)",
    re.I)
_TOKEN = re.compile(r'name="authenticity_token"\s+value="([^"]+)"')
_NOT_AUTHORIZED = "You are not authorized to perform this action"


class BackOfficeError(RuntimeError):
    pass


class LoginFailed(BackOfficeError):
    pass


class NotAuthorized(BackOfficeError):
    """The BO answered a page with its 'not authorized' redirect — a permission, not a session."""


class BackOfficeClient:
    def __init__(self, base_url: str | None = None, email: str | None = None,
                 password: str | None = None, otp_secret: str | None = None,
                 session_file: Path | None = None, timeout: float = 30.0) -> None:
        self.base = (base_url or settings.bo_kb_url).rstrip("/")
        self.origin = re.match(r"https?://[^/]+", self.base).group(0)
        self._email = email if email is not None else settings.bo_kb_email
        self._password = password if password is not None else settings.bo_kb_password
        self._secret = otp_secret if otp_secret is not None else settings.bo_kb_otp_secret
        self._session_file = session_file or (settings.bo_dir / "session.json")
        self._http = httpx.Client(timeout=timeout, follow_redirects=True, http2=False)
        self._lock = threading.Lock()
        self._last_code = ""
        self._load_session()

    # ── session ──────────────────────────────────────────────────────────────────────────
    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "BackOfficeClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _load_session(self) -> None:
        try:
            for c in json.loads(self._session_file.read_text()):
                self._http.cookies.set(c["name"], c["value"], domain=c["domain"], path=c["path"])
        except (OSError, ValueError, KeyError):
            pass

    def _save_session(self) -> None:
        self._session_file.parent.mkdir(parents=True, exist_ok=True)
        jar = [{"name": c.name, "value": c.value, "domain": c.domain, "path": c.path}
               for c in self._http.cookies.jar]
        tmp = self._session_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(jar))
        os.chmod(tmp, 0o600)
        tmp.replace(self._session_file)

    def login(self) -> None:
        """Email + password + a fresh TOTP code. Never reuses a code: the BO rejects a replay."""
        if not (self._email and self._password and self._secret):
            raise LoginFailed("BO_KB_EMAIL / BO_KB_PASSWORD / BO_KB_OTP_SECRET are not all set")
        with self._lock:
            page = self._http.get(f"{self.base}/login", headers=_NAVIGATE)
            token = _TOKEN.search(page.text)
            if not token:
                raise LoginFailed(f"no login form at {self.base}/login (HTTP {page.status_code})")
            # A code about to expire, or the one already spent, would be refused: wait it out.
            while totp.seconds_left() < 4 or totp.code(self._secret) == self._last_code:
                time.sleep(1)
            self._last_code = totp.code(self._secret)
            resp = self._http.post(
                f"{self.base}/login",
                headers={**_NAVIGATE, "Origin": self.origin, "Referer": f"{self.base}/login",
                         "Content-Type": "application/x-www-form-urlencoded"},
                data={"authenticity_token": token.group(1),
                      "admin_user[email]": self._email,
                      "admin_user[password]": self._password,
                      "admin_user[otp_attempt]": self._last_code,
                      "commit": "Login"},
            )
            if resp.url.path.rstrip("/").endswith("/login"):
                flash = re.findall(r'class="flash[^"]*"[^>]*>([^<]+)<', resp.text)
                raise LoginFailed("login refused: " + ("; ".join(f.strip() for f in flash)
                                                       or f"HTTP {resp.status_code}"))
            self._save_session()
            log.info("BO: logged in to %s", self.origin)

    # ── reads ────────────────────────────────────────────────────────────────────────────
    def _url(self, path: str) -> str:
        if _ACTION_WORDS.search(path):
            raise BackOfficeError(f"refusing {path!r}: the path names an action, not a page")
        return path if path.startswith("http") else f"{self.origin}{path}"

    def _get(self, path: str, headers: dict[str, str], params: dict | None = None) -> httpx.Response:
        url = self._url(path)
        for attempt in (1, 2):
            resp = self._http.get(url, headers={**headers, "Referer": f"{self.base}/"},
                                  params=params)
            if resp.url.path.rstrip("/").endswith("/login") or resp.status_code == 401:
                if attempt == 1:
                    self.login()
                    continue
                raise LoginFailed("still on the login page after logging in")
            return resp
        raise AssertionError("unreachable")

    def page(self, path: str) -> str:
        """A server-rendered page as a browser navigation would receive it."""
        resp = self._get(path, _NAVIGATE)
        if _NOT_AUTHORIZED in resp.text and resp.url.path.rstrip("/") != path.rstrip("/"):
            raise NotAuthorized(f"{path}: {_NOT_AUTHORIZED}")
        resp.raise_for_status()
        return resp.text

    def api(self, path: str, params: dict | None = None) -> Any:
        """A JSON endpoint under /api/v1/admin/…"""
        resp = self._get(path, _JSON, params)
        resp.raise_for_status()
        return resp.json()

    def player_page(self, player_id: int | str) -> str:
        return self.page(f"/backend/players/{int(player_id)}")
