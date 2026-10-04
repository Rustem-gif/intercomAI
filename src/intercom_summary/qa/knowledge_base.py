"""The brand's published Help Center articles, used as the approved KB/T&C for v4.1 accuracy.

QA Manual v4.1 judges `accuracy-material` / `accuracy-minor` against "approved KB/T&C or
policy" and sends the verdict to `cannot_determine` without it. With no KB in the prompt that
was most chats, and every `cannot_determine` sends the chat to a QC manager. The casino's own
Help Center *is* that approved source, so the grader now gets it.

Per brand, because the policies differ: a Tomb Riches chat checked against King Billy's
limits could fail a Major for being right. Brands without a help center (or without
published articles) get no KB block, and their accuracy verdicts fall back to the old rule.

Snapshots live in `data/kb/<brand>.md` (+ `.json` metadata) and are refreshed when older than
KB_MAX_AGE_HOURS (or on `intercom-summary sync-kb`). The text carries no dates, so it stays
byte-identical — and prompt-cacheable — until an article actually changes; its hash is
stamped on every grade as `kb_version`.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from html import unescape
from html.parser import HTMLParser
from pathlib import Path

import httpx

from intercom_summary.logging_setup import get_logger
from intercom_summary.settings import settings

log = get_logger(__name__)

# Raw Intercom `Brand` value → its help center id (GET /help_center/help_centers; see the
# intercom-multi-brand notes). Tomb Riches' center has no published articles yet.
BRAND_HELP_CENTERS: dict[str, str] = {
    "Betncare": "248",       # King Billy Help Center
    "Tomb Riches": "8189",
}
BRAND_NAMES: dict[str, str] = {"Betncare": "King Billy"}


@dataclass(frozen=True)
class KnowledgeBase:
    brand: str
    text: str            # the prompt block
    version: str         # sha256[:12] of `text`
    articles: int


# ── HTML → plain text ──────────────────────────────────────────────────────────────────
class _Text(HTMLParser):
    _BLOCK = {"p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "ul", "ol", "table",
              "blockquote", "section"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "br" or tag in self._BLOCK:
            self.out.append("\n")
        elif tag == "li":
            self.out.append("\n- ")
        elif tag in ("td", "th"):
            self.out.append(" | ")

    def handle_endtag(self, tag):
        if tag in self._BLOCK or tag == "li":
            self.out.append("\n")

    def handle_data(self, data):
        self.out.append(data)


def html_to_text(html: str) -> str:
    p = _Text()
    p.feed(html or "")
    p.close()
    text = unescape("".join(p.out)).replace("\xa0", " ").replace("\u200b", "")
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln and ln != "|"]
    # A list item whose content sits in its own <p> comes out as "-" then the text.
    merged: list[str] = []
    for ln in lines:
        if merged and merged[-1] == "-":
            merged[-1] = f"- {ln}"
        else:
            merged.append(ln)
    return "\n".join(ln for ln in merged if ln != "-").strip()


# ── fetching ───────────────────────────────────────────────────────────────────────────
def _get_all(client: httpx.Client, path: str) -> list[dict]:
    items, page = [], 1
    while True:
        for attempt in range(4):
            r = client.get(path, params={"per_page": 150, "page": page})
            if r.status_code == 429 or r.status_code >= 500:
                time.sleep(min(2 ** attempt, 10))
                continue
            r.raise_for_status()
            break
        else:
            r.raise_for_status()
        body = r.json()
        items += body.get("data") or []
        if page >= ((body.get("pages") or {}).get("total_pages") or 1):
            return items
        page += 1


def fetch_articles(client: httpx.Client | None = None) -> tuple[list[dict], list[dict]]:
    """(published articles, collections) for the whole workspace."""
    own = client is None
    client = client or httpx.Client(
        base_url=settings.intercom_base_url, timeout=30.0,
        headers={"Authorization": f"Bearer {settings.intercom_token}",
                 "Accept": "application/json",
                 "Intercom-Version": settings.intercom_api_version})
    try:
        articles = [a for a in _get_all(client, "/articles") if a.get("state") == "published"]
        collections = _get_all(client, "/help_center/collections")
    finally:
        if own:
            client.close()
    return articles, collections


def build_text(brand: str, articles: list[dict], collections: list[dict]) -> tuple[str, int]:
    """The KB prompt block for one brand, and how many articles it holds."""
    hc = BRAND_HELP_CENTERS.get(brand)
    cols = {str(c["id"]): c for c in collections if str(c.get("help_center_id")) == hc}
    mine = []
    for a in articles:
        if a.get("state") != "published":     # a draft is not approved policy
            continue
        parents = [str(p) for p in (a.get("parent_ids") or [])]
        if a.get("parent_id") is not None:
            parents.append(str(a["parent_id"]))
        col = next((cols[p] for p in parents if p in cols), None)
        if col is not None:
            mine.append((col.get("name") or "", a))
    if not mine:
        return "", 0
    # Stable order and no timestamps: the block must not change unless an article does.
    mine.sort(key=lambda x: (x[0], (x[1].get("title") or "").lower(), str(x[1].get("id"))))
    name = BRAND_NAMES.get(brand, brand)
    parts = [
        f"## KNOWLEDGE BASE — {name} Help Center",
        "The brand's published Help Center articles. This is the approved KB/T&C that "
        "accuracy-material and accuracy-minor are judged against. It states general rules; it "
        "says nothing about any one player's account.",
    ]
    for col_name, a in mine:
        body = html_to_text(a.get("body") or "")
        parts.append(f"### {col_name} — {(a.get('title') or '').strip()}\n{body}")
    return "\n\n".join(parts) + "\n", len(mine)


# ── snapshots ──────────────────────────────────────────────────────────────────────────
def _slug(brand: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", brand.lower()).strip("-") or "unbranded"


def _paths(brand: str) -> tuple[Path, Path]:
    d = settings.kb_dir
    return d / f"{_slug(brand)}.md", d / f"{_slug(brand)}.json"


def sync(client: httpx.Client | None = None) -> dict[str, int]:
    """Re-fetch the Help Center and rewrite every brand's snapshot. Returns articles per brand."""
    articles, collections = fetch_articles(client)
    settings.kb_dir.mkdir(parents=True, exist_ok=True)
    counts = {}
    for brand in BRAND_HELP_CENTERS:
        text, n = build_text(brand, articles, collections)
        md, meta = _paths(brand)
        md.write_text(text, encoding="utf-8")
        meta.write_text(json.dumps({
            "brand": brand, "articles": n,
            "version": hashlib.sha256(text.encode("utf-8")).hexdigest()[:12] if text else "",
            "synced_at": datetime.now(timezone.utc).isoformat(),
        }), encoding="utf-8")
        counts[brand] = n
    log.info("Knowledge base synced: %s", ", ".join(f"{b}={n}" for b, n in counts.items()))
    return counts


def _stale() -> bool:
    _, meta = _paths(next(iter(BRAND_HELP_CENTERS)))
    if not meta.exists():
        return True
    try:
        synced = datetime.fromisoformat(json.loads(meta.read_text())["synced_at"])
    except (ValueError, KeyError):
        return True
    age_h = (datetime.now(timezone.utc) - synced).total_seconds() / 3600
    return age_h >= settings.kb_max_age_hours


def load_all(refresh: bool = True) -> dict[str, KnowledgeBase]:
    """Brand → KnowledgeBase, for brands with published articles. Refreshes stale snapshots
    first; a failed refresh keeps the previous snapshot (or none) and grading carries on."""
    if refresh and _stale():
        try:
            sync()
        except Exception as exc:  # noqa: BLE001 — grading must not depend on Intercom being up
            log.warning("Knowledge base refresh failed, using the previous snapshot: %s", exc)
    out: dict[str, KnowledgeBase] = {}
    for brand in BRAND_HELP_CENTERS:
        md, meta = _paths(brand)
        if not md.exists():
            continue
        text = md.read_text(encoding="utf-8")
        if not text.strip():
            continue
        try:
            n = json.loads(meta.read_text()).get("articles", 0)
        except (OSError, ValueError):
            n = 0
        out[brand] = KnowledgeBase(brand, text, hashlib.sha256(text.encode("utf-8")).hexdigest()[:12], n)
    return out
