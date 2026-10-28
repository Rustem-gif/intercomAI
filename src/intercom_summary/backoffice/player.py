"""A player's back-office facts: fetched from the BO pages and parsed into plain data.

The player page (/backend/players/<id>) renders status, locks, groups and staff comments; the
lists the grader needs most load lazily from sibling pages, which are fetched directly:
payments, issued bonuses and free spins, responsible-gaming limits, tags and duplicates.
Every table cell carries a `col-<field>` class, so rows are read by class, not by header text
(two tables on one page can share header names).

Only what a QA verdict needs is kept — no addresses, documents, IPs or card numbers.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from bs4 import BeautifulSoup

# The lazily loaded panels, by the page each one fetches.
LAZY = {
    "payments": "payments?order=created_at_desc",
    "bonuses": "bonus_issues",
    "freespins": "freespin_issues",
    "limits": "limits",
    "tags": "tags",
    "duplicates": "duplicates_v2",
}
_XHR = {"X-Requested-With": "XMLHttpRequest", "Accept": "text/html, */*; q=0.01",
        "Sec-Fetch-Mode": "cors", "Sec-Fetch-Dest": "empty", "Sec-Fetch-Site": "same-origin"}


@dataclass
class PlayerFacts:
    player_id: str
    fetched_at: str = ""
    status: str = ""                                  # enable / disable / suspend / …
    locks: list[dict] = field(default_factory=list)   # closure / self-exclusion records
    limits: list[dict] = field(default_factory=list)  # RG limits: cooling off, loss, deposit…
    tags: list[str] = field(default_factory=list)     # e.g. the VIP manager's tag
    groups: list[str] = field(default_factory=list)   # marketing segments
    duplicates: int | None = None                     # linked duplicate accounts
    payments: list[dict] = field(default_factory=list)
    bonuses: list[dict] = field(default_factory=list)
    freespins: list[dict] = field(default_factory=list)
    comments: list[dict] = field(default_factory=list)  # staff comments on the player
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "PlayerFacts":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


def _text(el) -> str:
    return re.sub(r"\s+", " ", el.get_text(" ", strip=True)).strip() if el is not None else ""


def _rows(html_or_soup, wanted: dict[str, str]) -> list[dict]:
    """The rows of the first table holding the `wanted` columns, read by `col-<field>` class."""
    soup = html_or_soup if hasattr(html_or_soup, "select") else BeautifulSoup(html_or_soup, "lxml")
    for table in soup.select("table"):
        rows = []
        for tr in table.select("tbody tr"):
            cells = {}
            for td in tr.select("td"):
                for c in td.get("class") or []:
                    if c.startswith("col-"):
                        cells[c[4:]] = _text(td)
            if cells:
                rows.append({out: cells.get(src, "") for out, src in wanted.items()})
        if rows and any(any(r.values()) for r in rows) and \
                set(wanted.values()) & set().union(*({c[4:] for td in table.select("tbody tr td")
                                                      for c in (td.get("class") or [])
                                                      if c.startswith("col-")},)):
            return rows
    return []


def _panel(soup, title: str):
    for p in soup.select("div.panel"):
        h = p.find("h3")
        if h and re.sub(r"\s*\(\d+\)$", "", _text(h)) == title:
            return p
    return None


def parse_player_page(html: str, facts: PlayerFacts) -> None:
    soup = BeautifulSoup(html, "lxml")
    status = soup.select_one("select.user_status, select[name=user_status]")
    if status is not None:
        chosen = status.select_one("option[selected]")
        facts.status = (chosen.get("value") or _text(chosen)) if chosen else ""
    locks = _panel(soup, "Player Locks")
    if locks is not None:
        for tr in locks.select("tbody tr"):
            cell = {c[4:]: _text(td) for td in tr.select("td")
                    for c in (td.get("class") or []) if c.startswith("col-")}
            for kind in ("cs", "sb"):
                if cell.get(f"{kind}_reason") or (kind == "cs" and cell.get("comment")):
                    facts.locks.append({"kind": "casino" if kind == "cs" else "sportsbook",
                                        "reason": cell.get(f"{kind}_reason", ""),
                                        "comment": cell.get("comment", ""),
                                        "created_at": cell.get("created_at", "")})
    groups = _panel(soup, "Groups")
    if groups is not None:
        facts.groups = [_text(s).removeprefix("Group:").strip()
                        for s in groups.select("span.player-tag")]
    for c in soup.select(".active_admin_comment"):
        meta = _text(c.select_one(".active_admin_comment_meta"))
        when = re.search(r"\d{4}-\d\d-\d\d[ T]\d\d:\d\d(:\d\d)?", meta)
        body = _text(c.select_one(".active_admin_comment_body"))[:400]
        if body:
            facts.comments.append({"created_at": when.group(0) if when else "", "text": body})


def parse_lazy(name: str, html: str, facts: PlayerFacts) -> None:
    soup = BeautifulSoup(html, "lxml")
    if name == "payments":
        facts.payments = _rows(soup, {
            "action": "action", "source": "source", "success": "success",
            "approval": "source_of_approval", "manual": "manual", "return_type": "return_type",
            "comments": "comments", "created_at": "created_at", "finished_at": "finished_at",
            "amount": "amount"})
    elif name == "bonuses":
        facts.bonuses = _rows(soup, {
            "title": "id", "issued_at": "issued_at", "amount": "amount",
            "strategy": "strategy", "stage": "stage", "wager": "wager",
            "expiry": "expiry_date"})
    elif name == "freespins":
        facts.freespins = _rows(soup, {
            "title": "id", "issued_at": "issued_at", "stage": "stage",
            "win": "win_amount", "expiry": "expiry_date"})
    elif name == "limits":
        facts.limits = _rows(soup, {
            "type": "type_name", "status": "status", "period": "period_name",
            "duration": "duration", "created_at": "created_at", "modified_at": "modified_at",
            "disable_at": "disable_at"})
    elif name == "tags":
        facts.tags = [_text(o) for o in soup.select("option[selected]")]
    elif name == "duplicates":
        m = re.search(r"with (\d+) duplicate", _text(soup))
        facts.duplicates = int(m.group(1)) if m else 0


def fetch(bo, player_id: int | str) -> PlayerFacts:
    """Every panel for one player. A panel that fails is recorded in `errors`, not raised."""
    pid = str(int(player_id))
    facts = PlayerFacts(player_id=pid,
                        fetched_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    parse_player_page(bo.player_page(pid), facts)
    for name, sub in LAZY.items():
        try:
            resp = bo._get(f"/backend/players/{pid}/{sub}", _XHR)
            resp.raise_for_status()
            parse_lazy(name, resp.text, facts)
        except Exception as exc:  # noqa: BLE001 — one panel must not cost the others
            facts.errors.append(f"{name}: {type(exc).__name__}: {str(exc)[:120]}")
    return facts
