"""Who is a VIP manager, and when they are on shift — config/vip_staff.yaml.

The VIP case transcript (qa/vip_transcript.py) labels every admin line VIP MANAGER or OTHER
STAFF: other departments write inside VIP threads ("Pay Dep", "Head of CS"…), and the scorecard
forbids penalising a VIP manager for them. The list comes from Intercom — admins whose
`team_ids` include a VIP team (vip.VIP_TEAM_IDS) — via `intercom-summary vip-staff --refresh`,
and stays hand-editable. Without the file, the assignees of the collected VIP threads stand in.

`shifts` is the shift schedule the SLA criteria need (scorecard §8.1: SLAs count only in the
manager's working hours). It is empty until the client supplies one, and while it is empty the
timing criteria are not assessable (qa/vip_scoring.py). Shape, when filled:

    shifts:
      Victoria Vance: ["2026-10-01T06:00/2026-10-01T15:00", …]   # UTC intervals
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

from intercom_summary.logging_setup import get_logger
from intercom_summary.settings import settings

log = get_logger(__name__)


def _mtime(p: Path) -> float:
    try:
        return p.stat().st_mtime
    except OSError:
        return 0.0


@lru_cache(maxsize=4)
def _load(path: str, mtime: float) -> dict:
    if not mtime:
        return {}
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return data if isinstance(data, dict) else {}


def config() -> dict:
    p = settings.vip_staff_path
    return _load(str(p), _mtime(p))


def staff_names(fallback: list[str] | None = None) -> set[str]:
    """Lower-cased names of the VIP managers. `fallback` (the threads' assignees) is used
    only when the config lists nobody."""
    names = {str(n).strip().lower() for n in config().get("staff") or [] if str(n).strip()}
    if not names and fallback:
        names = {n.strip().lower() for n in fallback if n and n.strip()}
    return names


def shifts() -> dict:
    s = config().get("shifts") or {}
    return s if isinstance(s, dict) else {}


def shifts_known() -> bool:
    return bool(shifts())


async def refresh_from_intercom() -> list[str]:
    """Rewrite the `staff` list from Intercom's admins (keeping `shifts`). Returns the names."""
    from intercom_summary.intercom.client import IntercomClient
    from intercom_summary.vip import VIP_TEAM_IDS

    teams = {str(t) for t in VIP_TEAM_IDS}
    async with IntercomClient() as ic:
        admins = await ic.list_admins()
    names = sorted(
        a.get("name", "").strip() for a in admins
        if a.get("name") and teams & {str(t) for t in a.get("team_ids") or []}
    )
    p = settings.vip_staff_path
    data = dict(config())
    data["staff"] = names
    data.setdefault("shifts", {})
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        "# VIP managers (Intercom teams 'VIP managers' / 'pre-VIP') and their shifts — see\n"
        "# src/intercom_summary/vip_staff.py. `staff` is refreshed by `intercom-summary "
        "vip-staff --refresh`.\n"
        + yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    log.info("Wrote %d VIP staff to %s", len(names), p)
    return names
