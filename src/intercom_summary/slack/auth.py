"""Role-based access control for the Slack bot.

Roles are read from config/roles.yaml. Only users in the `analyst` list may use the data
functions; everyone else is `default` (no access). The mapping is loaded once and can be
reloaded at runtime.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from intercom_summary.settings import settings

DEFAULT_ROLE = "default"


class RoleStore:
    def __init__(self, path: str | Path | None = None) -> None:
        self._path = Path(path) if path else settings.roles_path
        self._roles: dict[str, list[str]] = {}
        self.reload()

    def reload(self) -> None:
        if self._path.exists():
            data = yaml.safe_load(self._path.read_text()) or {}
        else:
            data = {}
        # Normalise: role -> set of user ids (strings, stripped).
        self._roles = {
            role: [str(u).strip() for u in (ids or [])]
            for role, ids in data.items()
        }

    def role_for(self, user_id: str) -> str:
        if user_id in self._roles.get("analyst", []):
            return "analyst"
        if user_id in self._roles.get("admin", []):
            return "analyst"  # admins are also analysts
        return DEFAULT_ROLE

    def is_admin(self, user_id: str) -> bool:
        return user_id in self._roles.get("admin", [])

    def can_use_data(self, user_id: str) -> bool:
        return self.role_for(user_id) == "analyst"


# Single shared instance for handlers.
roles = RoleStore()

NO_ACCESS_MESSAGE = (
    ":lock: Sorry, you don't have access to the Intercom data tools. "
    "Ask a workspace admin to add you to the `analyst` role."
)
