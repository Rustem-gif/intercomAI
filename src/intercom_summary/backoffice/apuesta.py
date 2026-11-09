"""Read-only client for the Apuesta Cloud back office (Tomb Riches) — console.apuesta.cloud.

How this BO works (explored 2026-11-09), and why it looks nothing like backoffice/client.py:

- The console is a single-page app over a **gRPC-web** API: every read is
  `POST /api/cloud.apuesta.<service>/<Method>` with a protobuf body framed the gRPC-web way
  (1 flag byte + 4-byte length + message) and `Content-Type: application/grpc-web+proto`.
  There is no JSON variant (the server answers 415). The status comes back as `grpc-status`
  (in the headers for an error, in a trailer frame for a success). Protobuf is encoded and
  decoded here with a few lines of stdlib (`_pb_*`) — only the fields we read are named, in
  backoffice/apuesta_player.py.
- Every operator call carries an `opid` header (the brand's slug, `tombriches`, the first path
  segment of BO_TR_URL). Sign-in calls carry none.
- Login is two calls: AuthService/SignIn (login, password) and, when it answers TOTP,
  AuthService/TwoFactorAuth (the 6-digit code, computed here from BO_TR_OTP_SECRET on the
  server's clock). The session is HttpOnly cookies, kept on disk (0600) so a run does not log
  in each time; `grpc-status 16` (unauthenticated) triggers one silent re-login.
- The player id is the Apuesta customer id (a 20-char xid); the Intercom contact's
  external_id is "t-<that id>" (intercom/segments.py).

The BO account used here can change players (status, balance adjustments, cash payments), so
this client only ever calls the methods in READ_METHODS, all of them Get*/List*; anything else
is refused before a request is made.
"""
from __future__ import annotations

import email.utils
import json
import os
import re
import struct
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx

from intercom_summary.backoffice import totp
from intercom_summary.backoffice.client import BackOfficeError, LoginFailed, NotAuthorized
from intercom_summary.logging_setup import get_logger
from intercom_summary.settings import settings

log = get_logger(__name__)

_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
       "Chrome/146.0.0.0 Safari/537.36")
_P = "cloud.apuesta."
SIGN_IN = _P + "userservice.common.v1.AuthService/SignIn"
TWO_FACTOR = _P + "userservice.common.v1.AuthService/TwoFactorAuth"
# The only operator methods this client will call. Each one is a read.
READ_METHODS = frozenset(_P + m for m in (
    "userservice.bo.v1.OperatorEmployeeService/GetSelfInfo",
    "userservice.bo.v1.OperatorService/ListOperators",
    "userservice.bo.v1.CustomerService/GetCustomer",
    "userservice.bo.v1.CustomerNoteService/ListNotes",
    "billing.bo.v1.WalletService/ListCustomerWallets",
    "paymentgateway.bo.v1.PaymentService/ListCustomerPayments",
    "paymentgateway.bo.v1.PaymentService/ListCustomerWithdrawalOrders",
    "paymentgateway.bo.v1.PaymentLimitService/GetCustomerLimits",
    "paymentgateway.bo.v1.PaymentRoutingService/ListDirectionsBrief",
    "gamification.bo.v1.LoyaltyProgramService/GetCustomerLoyaltyLevel",
    "gamification.bo.v1.LoyaltyProgramService/GetLoyaltyProgram",
    "bonusservice.bo.v1.GameBonusService/ListCustomerGameBonuses",
    "audittrail.bo.v1.ActivityService/ListCustomerTimeline",
))
_READ_NAME = re.compile(r"/(Get|List)[A-Z]\w*$")

# gRPC status codes we act on.
UNAUTHENTICATED, PERMISSION_DENIED = 16, 7


class GrpcError(BackOfficeError):
    def __init__(self, method: str, status: int, message: str) -> None:
        super().__init__(f"{method.rsplit('/', 1)[-1]}: grpc-status {status}: {message}")
        self.status = status


# ── protobuf wire format (just enough) ────────────────────────────────────────────────────
def _varint(n: int) -> bytes:
    n &= (1 << 64) - 1
    out = bytearray()
    while n > 0x7F:
        out.append((n & 0x7F) | 0x80)
        n >>= 7
    out.append(n)
    return bytes(out)


def pb_varint(num: int, value: int) -> bytes:
    return _varint(num << 3) + _varint(value)


def pb_bytes(num: int, value: bytes | str) -> bytes:
    raw = value.encode() if isinstance(value, str) else value
    return _varint(num << 3 | 2) + _varint(len(raw)) + raw


def pb_parse(buf: bytes) -> dict[int, list]:
    """field number → list of raw values (int for varint/fixed, bytes for length-delimited)."""
    out: dict[int, list] = {}
    i, n = 0, len(buf)
    while i < n:
        key, i = _read_varint(buf, i)
        num, wt = key >> 3, key & 7
        if wt == 0:
            val, i = _read_varint(buf, i)
        elif wt == 1:
            val, i = struct.unpack_from("<q", buf, i)[0], i + 8
        elif wt == 2:
            ln, i = _read_varint(buf, i)
            val, i = buf[i:i + ln], i + ln
        elif wt == 5:
            val, i = struct.unpack_from("<i", buf, i)[0], i + 4
        else:
            raise BackOfficeError(f"protobuf: unsupported wire type {wt}")
        out.setdefault(num, []).append(val)
    return out


def _read_varint(buf: bytes, i: int) -> tuple[int, int]:
    shift = result = 0
    while True:
        b = buf[i]
        i += 1
        result |= (b & 0x7F) << shift
        if not b & 0x80:
            return result, i
        shift += 7


def pb_str(msg: dict, num: int) -> str:
    v = msg.get(num)
    return v[-1].decode("utf-8", "replace") if v and isinstance(v[-1], bytes) else ""


def pb_int(msg: dict, num: int) -> int:
    v = msg.get(num)
    if not v or not isinstance(v[-1], int):
        return 0
    x = v[-1]
    return x - (1 << 64) if x >= 1 << 63 else x   # int64 is two's complement on the wire


def pb_msg(msg: dict, num: int) -> dict:
    v = msg.get(num)
    return pb_parse(v[-1]) if v and isinstance(v[-1], bytes) else {}


def pb_msgs(msg: dict, num: int) -> list[dict]:
    return [pb_parse(v) for v in msg.get(num, []) if isinstance(v, bytes)]


def pb_strs(msg: dict, num: int) -> list[str]:
    return [v.decode("utf-8", "replace") for v in msg.get(num, []) if isinstance(v, bytes)]


def pb_time(msg: dict, num: int) -> str:
    """A google.protobuf.Timestamp field as "YYYY-MM-DD HH:MM:SS" UTC (the BO-block format)."""
    ts = pb_msg(msg, num)
    secs = pb_int(ts, 1)
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(secs)) if secs > 0 else ""


def _frame(message: bytes) -> bytes:
    return b"\x00" + struct.pack(">I", len(message)) + message


def _unframe(body: bytes) -> tuple[bytes, dict[str, str]]:
    data, trailers, i = b"", {}, 0
    while i + 5 <= len(body):
        flag, ln = body[i], struct.unpack_from(">I", body, i + 1)[0]
        chunk = body[i + 5:i + 5 + ln]
        i += 5 + ln
        if flag & 0x80:
            for line in chunk.decode("utf-8", "replace").split("\r\n"):
                if ":" in line:
                    k, v = line.split(":", 1)
                    trailers[k.strip().lower()] = v.strip()
        else:
            data += chunk
    return data, trailers


def _server_skew(resp: httpx.Response) -> float:
    try:
        return email.utils.parsedate_to_datetime(resp.headers["date"]).timestamp() - time.time()
    except (KeyError, TypeError, ValueError):
        return 0.0


# ── the client ────────────────────────────────────────────────────────────────────────────
class ApuestaClient:
    def __init__(self, base_url: str | None = None, login: str | None = None,
                 password: str | None = None, otp_secret: str | None = None,
                 session_file: Path | None = None, timeout: float = 30.0) -> None:
        url = urlparse((base_url or settings.bo_tr_url).rstrip("/"))
        self.origin = f"{url.scheme}://{url.netloc}"
        self.opid = url.path.strip("/").split("/")[0] if url.path.strip("/") else ""
        if not self.opid:
            raise BackOfficeError("BO_TR_URL must name the operator, e.g. "
                                  "https://console.apuesta.cloud/tombriches")
        self._login_name = login if login is not None else settings.bo_tr_email
        self._password = password if password is not None else settings.bo_tr_password
        self._secret = otp_secret if otp_secret is not None else settings.bo_tr_otp_secret
        self._session_file = session_file or (settings.bo_dir / f"session_{self.opid}.json")
        self._http = httpx.Client(timeout=timeout, follow_redirects=False, http2=False)
        self._lock = threading.RLock()
        self._last_code = ""
        self._generation = 0          # bumped by every login: threads re-login only once
        self._load_session()

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "ApuestaClient":
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

    # ── transport ────────────────────────────────────────────────────────────────────────
    def _post(self, method: str, message: bytes, operator: bool = True) -> tuple[dict, httpx.Response]:
        headers = {"Content-Type": "application/grpc-web+proto", "X-Grpc-Web": "1",
                   "Accept": "application/grpc-web+proto", "User-Agent": _UA,
                   "Origin": self.origin, "Referer": f"{self.origin}/{self.opid}/"}
        if operator:
            headers["opid"] = self.opid
        resp = self._http.post(f"{self.origin}/api/{method}", headers=headers,
                               content=_frame(message))
        if resp.status_code != 200:
            raise BackOfficeError(f"{method.rsplit('/', 1)[-1]}: HTTP {resp.status_code}")
        data, trailers = _unframe(resp.content)
        status = resp.headers.get("grpc-status") or trailers.get("grpc-status") or "0"
        if status != "0":
            msg = resp.headers.get("grpc-message") or trailers.get("grpc-message") or ""
            raise GrpcError(method, int(status), httpx.QueryParams(f"m={msg}").get("m") or msg)
        return pb_parse(data), resp

    def login(self) -> None:
        """Login + password, then a fresh TOTP code. Never reuses a code."""
        if not (self._login_name and self._password and self._secret):
            raise LoginFailed("BO_TR_EMAIL / BO_TR_PASSWORD / BO_TR_OTP_SECRET are not all set")
        with self._lock:
            self._http.cookies.clear()
            try:
                answer, resp = self._post(SIGN_IN, pb_bytes(1, self._login_name)
                                          + pb_bytes(2, self._password), operator=False)
            except GrpcError as exc:
                raise LoginFailed(f"login refused: {exc}") from exc
            if pb_int(answer, 1):
                raise LoginFailed("the BO asks for a password change; change it in the console")
            if pb_int(answer, 3):
                raise LoginFailed("the BO asks to set up 2FA; do that in the console first")
            if pb_int(answer, 2):                        # TwoFactorType: 1 = TOTP
                skew = _server_skew(resp)
                if abs(skew) > 20:
                    log.warning("BO TR: host clock is %+.0f s off the server's; using the "
                                "server's", -skew)
                now = lambda: time.time() + skew  # noqa: E731
                while totp.seconds_left(now()) < 4 or totp.code(self._secret, now()) == self._last_code:
                    time.sleep(1)
                self._last_code = totp.code(self._secret, now())
                try:
                    self._post(TWO_FACTOR, pb_bytes(1, self._last_code), operator=False)
                except GrpcError as exc:
                    raise LoginFailed(f"2FA code refused: {exc}") from exc
            self._save_session()
            self._generation += 1
            log.info("BO TR: logged in to %s (%s)", self.origin, self.opid)

    def call(self, method: str, message: bytes = b"") -> dict:
        """One read. `method` is "<package>.<Service>/<Method>" with or without "cloud.apuesta.".

        Raises NotAuthorized when the BO account lacks the permission for that read.
        """
        full = method if method.startswith(_P) else _P + method
        if full not in READ_METHODS or not _READ_NAME.search(full):
            raise BackOfficeError(f"refusing {full!r}: not a whitelisted read")
        for attempt in (1, 2):
            generation = self._generation
            try:
                return self._post(full, message)[0]
            except GrpcError as exc:
                if exc.status == UNAUTHENTICATED and attempt == 1:
                    with self._lock:              # another thread may have logged in meanwhile
                        if self._generation == generation:
                            self.login()
                    continue
                if exc.status == UNAUTHENTICATED:
                    raise LoginFailed("still unauthenticated after logging in") from exc
                if exc.status == PERMISSION_DENIED:
                    raise NotAuthorized(str(exc)) from exc
                raise
        raise AssertionError("unreachable")
