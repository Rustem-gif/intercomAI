"""RFC 6238 TOTP — the 6-digit code the back office asks for at login.

Twenty lines of stdlib rather than a dependency: the BO uses the defaults (SHA-1, 6 digits,
30 s), which is all Google Authenticator exports anyway.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import struct
import time
from urllib.parse import parse_qs, urlparse

PERIOD = 30


def secret_from(value: str) -> str:
    """The base32 key from a bare key or an otpauth:// link, spaces and case normalised."""
    value = (value or "").strip()
    if value.startswith("otpauth://"):
        value = parse_qs(urlparse(value).query).get("secret", [""])[0]
    return value.replace(" ", "").upper()


def code(secret: str, at: float | None = None, digits: int = 6) -> str:
    key = base64.b32decode(secret_from(secret) + "=" * (-len(secret_from(secret)) % 8))
    step = int((time.time() if at is None else at) // PERIOD)
    digest = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(value % 10 ** digits).zfill(digits)


def seconds_left(at: float | None = None) -> float:
    return PERIOD - ((time.time() if at is None else at) % PERIOD)
