"""Read the raw client archives (`scripts/export_client_archive.py` output) as Conversations.

The archives hold full Intercom payloads for months the conversations cache does not cover.
They are the source the historical context is labelled from for that period — and they stay
out of `conversations` on purpose: importing 8k ungraded old chats would put them on every
dashboard and in the next grading run.
"""
from __future__ import annotations

import gzip
import json
from collections.abc import Iterator
from pathlib import Path

from intercom_summary.intercom.fetch import is_email, is_ticket, normalise_conversation
from intercom_summary.intercom.models import Admin, Conversation
from intercom_summary.logging_setup import get_logger

log = get_logger(__name__)

PAYLOADS = "conversations.jsonl.gz"


def find_archives(export_dir: Path) -> list[Path]:
    """Every `raw/` directory under the exports folder that holds a payload file, oldest first."""
    if not export_dir.exists():
        return []
    return sorted(p.parent for p in export_dir.glob(f"*/raw/{PAYLOADS}"))


def load_admins(root: Path) -> dict[str, Admin]:
    path = root / "admins.json"
    if not path.exists():
        return {}
    return {
        str(a["id"]): Admin(id=str(a["id"]), name=a.get("name", ""), email=a.get("email", ""))
        for a in json.loads(path.read_text(encoding="utf-8")) if a.get("id")
    }


def iter_payloads(root: Path) -> Iterator[dict]:
    path = root / PAYLOADS
    if not path.exists():
        return
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                log.warning("Skipping a corrupt line in %s", path)


def iter_archive(roots: list[Path], *, skip_ids: set[str] | None = None) -> Iterator[Conversation]:
    """Chats from the archives, each id once (archives overlap), tickets and emails left out —
    the same population the cache holds. `skip_ids` are left out too (already cached/labelled)."""
    seen: set[str] = set(skip_ids or ())
    for root in roots:
        admins = load_admins(root)
        n = kept = 0
        for raw in iter_payloads(root):
            n += 1
            cid = str(raw.get("id", ""))
            if not cid or cid in seen:
                continue
            seen.add(cid)
            if is_ticket(raw) or is_email(raw):
                continue
            kept += 1
            yield normalise_conversation(raw, admins)
        log.info("Archive %s: %d payloads, %d new chats", root.parent.name, n, kept)
