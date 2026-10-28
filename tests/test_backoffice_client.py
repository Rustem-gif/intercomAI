"""backoffice/totp.py and backoffice/client.py against a fake BO (httpx.MockTransport)."""
from __future__ import annotations

import base64
from urllib.parse import parse_qs

import httpx
import pytest

from intercom_summary.backoffice import totp
from intercom_summary.backoffice.client import (
    BackOfficeClient,
    BackOfficeError,
    LoginFailed,
    NotAuthorized,
)

# RFC 6238 appendix B: the SHA-1 seed "12345678901234567890".
RFC_SECRET = base64.b32encode(b"12345678901234567890").decode()


@pytest.mark.parametrize("at, expected", [(59, "287082"), (1111111109, "081804"),
                                          (1234567890, "005924"), (2000000000, "279037")])
def test_totp_matches_rfc_6238(at, expected):
    assert totp.code(RFC_SECRET, at=at) == expected


def test_secret_from_an_otpauth_link_or_a_spaced_key():
    assert totp.secret_from("otpauth://totp/kb:me?secret=jbswy3dpehpk3pxp&issuer=kb") == \
        "JBSWY3DPEHPK3PXP"
    assert totp.secret_from("jbsw y3dp ehpk 3pxp") == "JBSWY3DPEHPK3PXP"


BASE = "https://bo.test/backend"
LOGIN_FORM = ('<form action="/backend/login"><input type="hidden" name="authenticity_token" '
              'value="tok123" /></form>')


class FakeBO:
    """Logged-in iff the session cookie is set; the player page wants navigation headers."""

    def __init__(self, accept_code: bool = True):
        self.accept_code = accept_code
        self.posts: list[dict] = []
        self.gets: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        logged_in = "_casino_session=ok" in request.headers.get("cookie", "")
        if request.method == "POST":
            form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
            self.posts.append(form)
            if self.accept_code and form.get("authenticity_token") == "tok123":
                return httpx.Response(302, headers={"Location": f"{BASE}/dashboard",
                                                    "Set-Cookie": "_casino_session=ok; Path=/"})
            return httpx.Response(200, text='<div class="flash flash_alert">Invalid code.</div>'
                                  + LOGIN_FORM, headers={"Content-Type": "text/html"})
        self.gets.append(path)
        if path == "/backend/login":
            return httpx.Response(200, text=LOGIN_FORM)
        if not logged_in:
            return httpx.Response(302, headers={"Location": f"{BASE}/login"})
        if path == "/backend/dashboard":
            return httpx.Response(200, text="<h3>Dashboard</h3>")
        if path.startswith("/backend/players/"):
            if request.headers.get("sec-fetch-mode") != "navigate":
                return httpx.Response(302, headers={"Location": f"{BASE}/dashboard?denied"})
            return httpx.Response(200, text="<h3>Status</h3><h3>Player Locks</h3>")
        if path.startswith("/api/v1/admin/"):
            return httpx.Response(200, json={"collection": [{"id": 1, "user": {"id": 5}}]})
        return httpx.Response(404)


def _client(tmp_path, fake: FakeBO) -> BackOfficeClient:
    bo = BackOfficeClient(base_url=BASE, email="bot@kb", password="pw", otp_secret=RFC_SECRET,
                          session_file=tmp_path / "session.json")
    bo._http = httpx.Client(transport=httpx.MockTransport(fake), follow_redirects=True)
    return bo


def test_a_first_read_logs_in_by_itself_with_a_totp_code(tmp_path, monkeypatch):
    monkeypatch.setattr(totp, "seconds_left", lambda at=None: 20)
    fake = FakeBO()
    bo = _client(tmp_path, fake)
    html = bo.player_page(1745500)
    assert "Player Locks" in html
    form = fake.posts[0]
    assert form["admin_user[email]"] == "bot@kb" and form["admin_user[password]"] == "pw"
    assert form["admin_user[otp_attempt]"] == totp.code(RFC_SECRET)
    assert (tmp_path / "session.json").exists()
    assert oct((tmp_path / "session.json").stat().st_mode)[-3:] == "600"


def test_the_saved_session_is_reused_without_a_new_login(tmp_path, monkeypatch):
    monkeypatch.setattr(totp, "seconds_left", lambda at=None: 20)
    fake = FakeBO()
    _client(tmp_path, fake).player_page(1)
    fake2 = FakeBO()
    bo2 = _client(tmp_path, fake2)
    bo2._load_session()
    assert bo2.api("/api/v1/admin/reports/freespin_issued.json")["collection"]
    assert fake2.posts == []


def test_a_refused_code_is_a_clear_error(tmp_path, monkeypatch):
    monkeypatch.setattr(totp, "seconds_left", lambda at=None: 20)
    bo = _client(tmp_path, FakeBO(accept_code=False))
    with pytest.raises(LoginFailed, match="Invalid code"):
        bo.login()


def test_missing_credentials_never_post(tmp_path):
    fake = FakeBO()
    bo = _client(tmp_path, fake)
    bo._password = ""
    with pytest.raises(LoginFailed, match="not all set"):
        bo.login()
    assert fake.posts == []


@pytest.mark.parametrize("path", [
    "/backend/players/1/update_tags", "/backend/players/1/disable", "/backend/players/1/edit",
    "/backend/players/1/issue_bonus", "/backend/players/1/suspend?x=1", "/backoffice/bonuses/new",
])
def test_action_paths_are_refused_before_any_request(tmp_path, path):
    fake = FakeBO()
    bo = _client(tmp_path, fake)
    with pytest.raises(BackOfficeError, match="names an action"):
        bo.page(path)
    assert fake.gets == []


def test_a_not_authorized_redirect_is_reported(tmp_path, monkeypatch):
    monkeypatch.setattr(totp, "seconds_left", lambda at=None: 20)

    class Denying(FakeBO):
        def __call__(self, request):
            logged_in = "_casino_session=ok" in request.headers.get("cookie", "")
            if logged_in and request.url.path.startswith("/backend/players/"):
                return httpx.Response(302, headers={"Location": f"{BASE}/dashboard"})
            if logged_in and request.url.path == "/backend/dashboard":
                return httpx.Response(200, text="You are not authorized to perform this action.")
            return super().__call__(request)

    bo = _client(tmp_path, Denying())
    with pytest.raises(NotAuthorized):
        bo.page("/backend/players/7")


def test_the_code_follows_the_servers_clock_not_the_hosts(tmp_path, monkeypatch):
    """The Mac mini was 111 s behind; the BO rejected every code until the client used Date."""
    import email.utils
    import time as _time

    class Skewed(FakeBO):
        def __call__(self, request):
            resp = super().__call__(request)
            if request.method == "GET" and request.url.path == "/backend/login":
                resp.headers["Date"] = email.utils.formatdate(_time.time() + 111, usegmt=True)
            return resp

    fake = Skewed()
    bo = _client(tmp_path, fake)
    monkeypatch.setattr(totp, "seconds_left", lambda at=None: 20)
    bo.login()
    sent = fake.posts[0]["admin_user[otp_attempt]"]
    server_codes = {totp.code(RFC_SECRET, _time.time() + 111 + d) for d in (-2, 0, 2)}
    assert sent in server_codes
