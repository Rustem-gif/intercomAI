"""Web auth: cookie sessions + a small user file, roles reused from the Slack model.

Users live in `config/web_users.yaml`:

    users:
      alice:
        password_hash: "$2b$12$...."   # bcrypt
        role: admin                     # admin | analyst | viewer

Roles:
  • admin / analyst → full access (fetch, review, edit rules)
  • viewer          → read-only (browse, overview, export)
"""
from __future__ import annotations

from pathlib import Path

import bcrypt
import yaml
from fastapi import Depends, HTTPException, Request, status

from intercom_summary.settings import settings

WRITE_ROLES = {"admin", "analyst"}
READ_ROLES = WRITE_ROLES | {"viewer"}


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def _verify(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode(), hashed.encode())
    except (ValueError, TypeError):
        return False


class UserStore:
    def __init__(self, path: str | Path | None = None) -> None:
        self._path = Path(path) if path else settings.web_users_path
        self._users: dict[str, dict] = {}
        self.reload()

    def reload(self) -> None:
        if self._path.exists():
            data = yaml.safe_load(self._path.read_text()) or {}
            self._users = data.get("users", {}) or {}
        else:
            self._users = {}

    def authenticate(self, username: str, password: str) -> dict | None:
        u = self._users.get(username)
        if not u:
            return None
        # Accept either a plaintext `password:` or a bcrypt `password_hash:`.
        if "password" in u:
            ok = password == str(u["password"])
        else:
            ok = _verify(password, u.get("password_hash", ""))
        if not ok:
            return None
        return {"username": username, "role": u.get("role", "viewer")}


users = UserStore()


# ── FastAPI dependencies ─────────────────────────────────────────────────────
def current_user(request: Request) -> dict:
    user = request.session.get("user")
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    return user


def require_write(user: dict = Depends(current_user)) -> dict:
    if user.get("role") not in WRITE_ROLES:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This action requires the analyst role.")
    return user


def require_admin(user: dict = Depends(current_user)) -> dict:
    if user.get("role") != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This action requires the admin role.")
    return user
