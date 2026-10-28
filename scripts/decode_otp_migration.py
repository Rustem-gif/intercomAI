"""Decode a Google Authenticator export (otpauth-migration://offline?data=...) offline.

Google Authenticator → ⋮ / ☰ → Transfer accounts → Export accounts → pick the BO account →
it shows a QR code. Scan that QR with any QR reader that shows the raw text (the iPhone
camera, or `zbarimg photo.png`) — the text is an `otpauth-migration://offline?data=…` link.

    python scripts/decode_otp_migration.py 'otpauth-migration://offline?data=...'

prints, per account, its name, issuer and the base32 SECRET — that secret is what goes into
.env as BO_KB_OTP_SECRET. Everything runs locally (stdlib only); never paste the link into an
online decoder: it IS the second factor.
"""
from __future__ import annotations

import base64
import sys
from urllib.parse import parse_qs, unquote, urlparse

ALGO = {0: "SHA1", 1: "SHA1", 2: "SHA256", 3: "SHA512", 4: "MD5"}
DIGITS = {0: 6, 1: 6, 2: 8}


def _varint(buf: bytes, i: int) -> tuple[int, int]:
    shift = value = 0
    while True:
        b = buf[i]
        i += 1
        value |= (b & 0x7F) << shift
        if not b & 0x80:
            return value, i
        shift += 7


def _fields(buf: bytes):
    """(field number, wire type, value) for a protobuf message — varints and bytes only."""
    i = 0
    while i < len(buf):
        key, i = _varint(buf, i)
        num, wire = key >> 3, key & 7
        if wire == 0:
            val, i = _varint(buf, i)
        elif wire == 2:
            n, i = _varint(buf, i)
            val, i = buf[i:i + n], i + n
        else:
            raise ValueError(f"unexpected wire type {wire}")
        yield num, wire, val


def decode(link: str) -> list[dict]:
    data = parse_qs(urlparse(link).query)["data"][0]
    payload = base64.b64decode(unquote(data) + "=" * (-len(data) % 4))
    accounts = []
    for num, _wire, val in _fields(payload):
        if num != 1:   # 1 = otp_parameters (repeated); the rest is batch metadata
            continue
        acc = {"secret": "", "name": "", "issuer": "", "algorithm": "SHA1", "digits": 6}
        for f, _w, v in _fields(val):
            if f == 1:
                acc["secret"] = base64.b32encode(v).decode().rstrip("=")
            elif f == 2:
                acc["name"] = v.decode()
            elif f == 3:
                acc["issuer"] = v.decode()
            elif f == 4:
                acc["algorithm"] = ALGO.get(v, "SHA1")
            elif f == 5:
                acc["digits"] = DIGITS.get(v, 6)
        accounts.append(acc)
    return accounts


def main() -> None:
    if len(sys.argv) != 2 or not sys.argv[1].startswith("otpauth-migration://"):
        sys.exit(__doc__)
    for acc in decode(sys.argv[1]):
        print(f"{acc['issuer'] or '-'} / {acc['name']}: secret={acc['secret']} "
              f"({acc['algorithm']}, {acc['digits']} digits)")


if __name__ == "__main__":
    main()
