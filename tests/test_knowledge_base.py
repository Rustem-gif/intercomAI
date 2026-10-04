"""The Help Center knowledge base behind v4.1 accuracy (qa/knowledge_base.py)."""
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from intercom_summary.qa import knowledge_base as kb_mod
from intercom_summary.qa.grader import Grader
from intercom_summary.qa.jev_verifier import JevVerifier, build_request
from intercom_summary.settings import settings
from test_grader import FakeClaude, _convo, _v41, reply

COLLECTIONS = [
    {"id": 1360, "help_center_id": 248, "name": "Payment questions"},
    {"id": 1760, "help_center_id": 248, "name": "Game questions"},
    {"id": 9000, "help_center_id": 8189, "name": "Tomb payments"},
]
ARTICLES = [
    {"id": 2, "state": "published", "title": "Min and max withdrawal", "parent_ids": [1360],
     "body": "<p>The minimum withdrawal is <b>40&nbsp;EUR</b>.</p><ul><li><p>Bank: 300 EUR</p></li>"
             "<li>Crypto: 0.0004 BTC</li></ul>"},
    {"id": 1, "state": "published", "title": "Wagering", "parent_id": 1760,
     "body": "<table><tr><td>Slots</td><td>100%</td></tr></table>"},
    {"id": 3, "state": "draft", "title": "Unreleased promo", "parent_ids": [1360], "body": "secret"},
    {"id": 4, "state": "published", "title": "Orphan", "parent_ids": [], "body": "nowhere"},
]


def _kb(text="## KNOWLEDGE BASE — King Billy Help Center\nMin withdrawal 40 EUR.\n"):
    return {"Betncare": kb_mod.KnowledgeBase("Betncare", text, "abc123def456", 1)}


# ── text ───────────────────────────────────────────────────────────────────────────────
def test_html_becomes_readable_plain_text():
    text = kb_mod.html_to_text(ARTICLES[0]["body"])
    assert text == "The minimum withdrawal is 40 EUR.\n- Bank: 300 EUR\n- Crypto: 0.0004 BTC"
    assert kb_mod.html_to_text(ARTICLES[1]["body"]) == "| Slots | 100%"


def test_each_brand_gets_only_its_own_published_articles_in_a_stable_order():
    text, n = kb_mod.build_text("Betncare", ARTICLES, COLLECTIONS)
    assert n == 2
    assert "Unreleased promo" not in text and "Orphan" not in text
    # Sorted by collection, then title — the input order must not matter.
    assert text.index("Game questions — Wagering") < text.index("Payment questions — Min")
    again, _ = kb_mod.build_text("Betncare", list(reversed(ARTICLES)), COLLECTIONS)
    assert again == text
    assert kb_mod.build_text("Tomb Riches", ARTICLES, COLLECTIONS) == ("", 0)
    assert kb_mod.build_text("Unknown brand", ARTICLES, COLLECTIONS) == ("", 0)


# ── snapshots ──────────────────────────────────────────────────────────────────────────
def _fake_intercom():
    def handler(request):
        page = {"pages": {"total_pages": 1}}
        if request.url.path == "/articles":
            return httpx.Response(200, json={"data": ARTICLES, **page})
        return httpx.Response(200, json={"data": COLLECTIONS, **page})
    return httpx.Client(base_url="https://api.test", transport=httpx.MockTransport(handler))


@pytest.fixture
def kb_dir(tmp_path):
    old = settings.kb_dir
    object.__setattr__(settings, "kb_dir", tmp_path / "kb")
    yield settings.kb_dir
    object.__setattr__(settings, "kb_dir", old)


def test_sync_writes_snapshots_that_load_with_a_content_hash(kb_dir):
    assert kb_mod.sync(_fake_intercom()) == {"Betncare": 2, "Tomb Riches": 0}
    loaded = kb_mod.load_all(refresh=False)
    assert set(loaded) == {"Betncare"}          # no published articles → no KB block
    kb = loaded["Betncare"]
    assert kb.articles == 2 and len(kb.version) == 12
    assert "40 EUR" in kb.text and "synced" not in kb.text.lower()   # no dates in the block


def test_a_failed_refresh_keeps_the_previous_snapshot(kb_dir, monkeypatch):
    kb_mod.sync(_fake_intercom())
    meta = kb_dir / "betncare.json"
    m = json.loads(meta.read_text())
    m["synced_at"] = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    meta.write_text(json.dumps(m))

    def down(*_a, **_k):
        raise httpx.ConnectError("intercom down")
    monkeypatch.setattr(kb_mod, "fetch_articles", down)
    assert "40 EUR" in kb_mod.load_all()["Betncare"].text


def test_a_fresh_snapshot_is_not_refetched(kb_dir, monkeypatch):
    kb_mod.sync(_fake_intercom())
    monkeypatch.setattr(kb_mod, "fetch_articles", lambda *_: pytest.fail("refetched"))
    assert kb_mod.load_all()["Betncare"].articles == 2


# ── the grader ─────────────────────────────────────────────────────────────────────────
def test_v41_requests_carry_the_brands_kb_as_a_second_cached_block():
    claude = FakeClaude(reply(_v41()))
    g = Grader(ruleset_id="kb-v41", client=claude, jev=False, knowledge=_kb())
    grade = g.grade(replace(_convo(), brand="Betncare"))
    system = claude.calls[0]["system"]
    assert len(system) == 2
    assert system[1]["text"].startswith("## KNOWLEDGE BASE") and system[1]["cache_control"]
    assert grade.kb_version == "Betncare:abc123def456"


def test_a_brand_without_a_kb_is_graded_without_one():
    claude = FakeClaude(reply(_v41()))
    g = Grader(ruleset_id="kb-v41", client=claude, jev=False, knowledge=_kb())
    grade = g.grade(replace(_convo(), brand="Tomb Riches"))
    assert len(claude.calls[0]["system"]) == 1 and grade.kb_version == ""


def test_the_ruleset_prompt_block_is_shared_across_brands():
    """The prompt is cached on its own block, so a mixed-brand run reads it from one entry."""
    g = Grader(ruleset_id="kb-v41", client=FakeClaude(reply(_v41())), jev=False, knowledge=_kb())
    kb = g.request_params(replace(_convo(), brand="Betncare"))["system"]
    plain = g.request_params(replace(_convo(), brand="Tomb Riches"))["system"]
    assert kb[0] == plain[0]


def test_flat_rulesets_never_load_a_kb(monkeypatch):
    object.__setattr__(settings, "kb_enabled", True)
    monkeypatch.setattr(kb_mod, "load_all", lambda *a, **k: pytest.fail("loaded a KB"))
    for rid in ("default", "vip"):
        assert Grader(ruleset_id=rid, client=FakeClaude(reply(_v41())), jev=False)._kb == {}


def test_the_v41_prompt_judges_accuracy_against_the_kb():
    from intercom_summary.qa.rulesets import get_ruleset

    text = get_ruleset("kb-v41").prompt_text
    assert "## ACCURACY" in text
    assert "never mark accuracy cannot_determine because of it" in text
    assert "Accuracy needs the transcript plus approved KB/T&C or policy. Without it" not in text


# ── Jev ────────────────────────────────────────────────────────────────────────────────
def test_jev_sees_the_kb_only_when_an_accuracy_fail_needs_it():
    fail = [{"id": "accuracy-material", "v": "fail", "ev": '"Min is 10 EUR" contradicts KB'}]
    other = [{"id": "resp-no-ghost", "v": "fail", "ev": "x"}]
    state, questions, _ = build_request("T", fail, "KB TEXT")
    assert state["knowledge_base"] == "KB TEXT"
    assert "`knowledge_base`" in questions["support.0"]["instructions"]
    state, questions, _ = build_request("T", other, "KB TEXT")
    assert "knowledge_base" not in state
    assert "`knowledge_base`" not in questions["support.0"]["instructions"]


def test_the_grader_hands_jev_the_conversations_kb():
    seen = {}

    class Spy(JevVerifier):
        def __init__(self):
            self.mode = "shadow"

        def verify(self, conversation, data, knowledge_base=""):
            seen["kb"] = knowledge_base
            return {"findings": [], "mode": "shadow"}

    g = Grader(ruleset_id="kb-v41", client=FakeClaude(reply(_v41())), jev=Spy(), knowledge=_kb())
    g.grade(replace(_convo(), brand="Betncare"))
    assert seen["kb"].startswith("## KNOWLEDGE BASE")
