"""Tests for scripts/export_client_archive.py — the one-off client deliverable.

The script lives in scripts/ rather than the package, so it is loaded by path.
"""
import gzip
import importlib.util
import json
import sys
import zipfile
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[1] / "scripts" / "export_client_archive.py"
_spec = importlib.util.spec_from_file_location("export_client_archive", _PATH)
archive = importlib.util.module_from_spec(_spec)
sys.modules["export_client_archive"] = archive
_spec.loader.exec_module(archive)


def _raw(cid: str, ticket=None) -> dict:
    return {
        "id": cid,
        "type": "conversation",
        "ticket": ticket,
        "created_at": 1_785_000_000,
        "updated_at": 1_785_000_500,
        "state": "closed",
        "source": {"type": "conversation", "body": "<p>Help me</p>",
                   "author": {"type": "user", "name": "Cara", "email": "cara@x.com"}},
        "conversation_parts": {"conversation_parts": [
            {"part_type": "comment", "body": "<p>Sure</p>", "created_at": 1_785_000_100,
             "author": {"type": "admin", "name": "Ada"}},
        ]},
    }


@pytest.fixture
def cache(tmp_path):
    """A raw cache holding two chats and one ticket."""
    c = archive.RawCache(tmp_path / "raw")
    with gzip.open(c.payloads, "wt", encoding="utf-8") as fh:
        for raw in (_raw("chat-1"), _raw("tkt-1", {"type": "ticket", "ticket_state": "resolved"}),
                    _raw("chat-2")):
            fh.write(json.dumps(raw) + "\n")
    c.save_admins([{"id": "1", "name": "Ada", "email": "ada@co.com"}])
    return c


def _transcripts(zip_path: Path) -> set[str]:
    with zipfile.ZipFile(zip_path) as zf:
        return {n for n in zf.namelist() if n.endswith(".md")}


def test_build_excludes_tickets_already_sitting_in_the_cache(cache, tmp_path):
    # A cache filled before the chats-only rule (or by --resume against one) still holds
    # tickets; --only-build must not put them back into the deliverable.
    out = tmp_path / "out"
    out.mkdir()
    counts = archive.phase_build(cache, out, "2026-07-01", "2026-07-31",
                                 split_months=False, redact=False)

    assert sum(counts.values()) == 2
    zip_path = out / next(iter(counts))
    assert {n.split("_")[-1] for n in _transcripts(zip_path)} == {"chat-1.md", "chat-2.md"}
    with zipfile.ZipFile(zip_path) as zf:
        assert "Chats only" in zf.read("README.txt").decode()


def test_include_tickets_restores_the_old_behaviour(cache, tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    counts = archive.phase_build(cache, out, "2026-07-01", "2026-07-31",
                                 split_months=False, redact=False, include_tickets=True)

    assert sum(counts.values()) == 3
    with zipfile.ZipFile(out / next(iter(counts))) as zf:
        assert "Includes both chats and Intercom tickets" in zf.read("README.txt").decode()


async def test_stub_sweep_drops_ticket_ids():
    stubs = [
        {"id": "chat-1", "ticket": None},
        {"id": "tkt-1", "ticket": {"type": "ticket"}},
        {"id": "chat-2", "ticket": None},
    ]

    class _Client:
        async def search_conversations(self, query, per_page=150):
            for s in stubs:
                yield s

    # window_days=90 collapses the range to a single query (the sequential branch);
    # window_days=7 fans it out into the parallel sweep, which dedupes across windows.
    for window_days in (90, 7):
        ids = await archive._collect_stub_ids(
            _Client(), "2026-07-01", "2026-07-31",
            per_agent=False, limit=None, window_days=window_days,
        )
        assert sorted(ids) == ["chat-1", "chat-2"], window_days
