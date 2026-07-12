"""Ruleset registry — which criteria + system prompt a conversation is graded against.

A ruleset is a system prompt (the text Qwen follows) plus its criteria catalogue (ids,
titles, deduction points, which ones are critical). There are two:

    default  standard support        rules/qa_system_prompt.txt      (seed: casino_prompt.py)
    vip      the VIP department      rules/qa_system_prompt_vip.txt  (seed: vip_prompt.py)

Which one applies is decided by the assigned agent's group (see storage/agent_groups_store.py):
an agent in the `vip` group gets the `vip` ruleset for all of their conversations, chat or email.

Two things are deliberate here:

`version` hashes the PROMPT TEXT ONLY, not the criteria catalogue. That keeps the default
ruleset's version byte-identical to what it was before this registry existed, so adding VIP
does not mark every historical grade stale and trigger a full re-grade. It is also correct:
the deduction the model actually applies comes from the table inside the prompt text (it
returns a `ded` per criterion and qa/schema.py sums those). The catalogue below is used for
*manual* re-scoring and for rendering the UI — hence validate_ruleset(), which catches the
two copies of the numbers drifting apart.

The criteria catalogue lives in config/rulesets.yaml (bootstrapped on first use from the
seed constants) so it can be edited without a code change.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from intercom_summary.logging_setup import get_logger
from intercom_summary.settings import settings

log = get_logger(__name__)

DEFAULT_RULESET_ID = "default"
VIP_RULESET_ID = "vip"

# Agent groups. An agent with no row in `agent_groups` is standard.
GROUP_STANDARD = "standard"
GROUP_VIP = "vip"

# group → ruleset. Kept 1:1 on purpose; a group exists precisely to select a ruleset.
GROUP_RULESETS: dict[str, str] = {
    GROUP_STANDARD: DEFAULT_RULESET_ID,
    GROUP_VIP: VIP_RULESET_ID,
}


@dataclass
class QaRuleset:
    id: str
    name: str
    prompt_path: Path
    prompt_text: str
    version: str                 # short SHA-256 of prompt_text — stored as rules_version
    criteria: list[dict]         # ordered [{id, title, deduction, critical}]
    manual_deductions: list[dict]

    @property
    def titles(self) -> dict[str, str]:
        return {c["id"]: c.get("title", c["id"]) for c in self.criteria}

    @property
    def deductions(self) -> dict[str, int]:
        return {c["id"]: int(c.get("deduction", 0)) for c in self.criteria}

    @property
    def critical(self) -> frozenset[str]:
        return frozenset(c["id"] for c in self.criteria if c.get("critical"))

    @property
    def manual_deduction_ids(self) -> frozenset[str]:
        return frozenset(d["id"] for d in self.manual_deductions)


def _seed_config() -> dict:
    """Build config/rulesets.yaml from the in-code seed constants (first run only)."""
    from intercom_summary.qa.casino_prompt import (
        CRITERION_DEDUCTIONS,
        CRITERION_TITLES,
        CRITICAL_CRITERIA,
        MANUAL_DEDUCTION_CATALOG,
    )
    from intercom_summary.qa.vip_prompt import (
        VIP_CRITERION_DEDUCTIONS,
        VIP_CRITERION_TITLES,
        VIP_CRITICAL_CRITERIA,
        VIP_MANUAL_DEDUCTION_CATALOG,
    )

    def _criteria(deductions: dict[str, int], titles: dict[str, str],
                  critical: frozenset[str]) -> list[dict]:
        out = []
        for cid, ded in deductions.items():
            entry: dict = {"id": cid, "title": titles.get(cid, cid), "deduction": int(ded)}
            if cid in critical:
                entry["critical"] = True
            out.append(entry)
        return out

    return {
        "rulesets": {
            DEFAULT_RULESET_ID: {
                "name": "Standard Support",
                "prompt_path": str(settings.qa_prompt_path),
                "criteria": _criteria(CRITERION_DEDUCTIONS, CRITERION_TITLES, CRITICAL_CRITERIA),
                "manual_deductions": MANUAL_DEDUCTION_CATALOG,
            },
            VIP_RULESET_ID: {
                "name": "VIP",
                "prompt_path": str(settings.vip_prompt_path),
                "criteria": _criteria(
                    VIP_CRITERION_DEDUCTIONS, VIP_CRITERION_TITLES, VIP_CRITICAL_CRITERIA
                ),
                "manual_deductions": VIP_MANUAL_DEDUCTION_CATALOG,
            },
        }
    }


def _seed_prompt_text(ruleset_id: str) -> str:
    if ruleset_id == VIP_RULESET_ID:
        from intercom_summary.qa.vip_prompt import VIP_QA_SYSTEM_PROMPT

        return VIP_QA_SYSTEM_PROMPT
    from intercom_summary.qa.casino_prompt import CASINO_QA_SYSTEM_PROMPT

    return CASINO_QA_SYSTEM_PROMPT


def _load_config() -> dict:
    """Read config/rulesets.yaml, writing the seed on first use."""
    p = settings.rulesets_path
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            yaml.safe_dump(_seed_config(), sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
        log.info("Bootstrapped ruleset catalogue at %s", p)
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    rulesets = data.get("rulesets") or {}
    if DEFAULT_RULESET_ID not in rulesets:
        raise RuntimeError(f"{p} must define a '{DEFAULT_RULESET_ID}' ruleset.")
    return rulesets


# Cache keyed by ruleset id, invalidated when either backing file changes on disk. Without
# this, get_ruleset() would re-read + re-parse YAML for every conversation (is_graded calls
# it per row), and admin edits still need to take effect without a restart.
_cache: dict[str, tuple[float, float, QaRuleset]] = {}


def _mtime(p: Path) -> float:
    try:
        return p.stat().st_mtime
    except OSError:
        return 0.0


def get_ruleset(ruleset_id: str | None = None) -> QaRuleset:
    rid = ruleset_id or DEFAULT_RULESET_ID
    cfg = _load_config()
    if rid not in cfg:
        log.warning("Unknown ruleset '%s' — falling back to '%s'", rid, DEFAULT_RULESET_ID)
        rid = DEFAULT_RULESET_ID

    entry = cfg[rid]
    prompt_path = Path(entry.get("prompt_path") or settings.qa_prompt_path)

    cached = _cache.get(rid)
    stamps = (_mtime(settings.rulesets_path), _mtime(prompt_path))
    if cached and (cached[0], cached[1]) == stamps:
        return cached[2]

    if not prompt_path.exists():
        prompt_path.parent.mkdir(parents=True, exist_ok=True)
        prompt_path.write_text(_seed_prompt_text(rid), encoding="utf-8")
        log.info("Bootstrapped %s QA prompt at %s", rid, prompt_path)
        stamps = (stamps[0], _mtime(prompt_path))

    text = prompt_path.read_text(encoding="utf-8")
    rs = QaRuleset(
        id=rid,
        name=entry.get("name", rid),
        prompt_path=prompt_path,
        prompt_text=text,
        version=hashlib.sha256(text.encode("utf-8")).hexdigest()[:12],
        criteria=list(entry.get("criteria") or []),
        manual_deductions=list(entry.get("manual_deductions") or []),
    )
    _cache[rid] = (stamps[0], stamps[1], rs)
    return rs


def list_rulesets() -> list[QaRuleset]:
    return [get_ruleset(rid) for rid in _load_config()]


def ruleset_id_for_group(group_id: str | None) -> str:
    return GROUP_RULESETS.get(group_id or GROUP_STANDARD, DEFAULT_RULESET_ID)


def ruleset_id_for_agent(agent_name: str | None) -> str:
    """The ruleset an agent's conversations are graded against — the single resolution point."""
    from intercom_summary.storage.agent_groups_store import AgentGroupsStore

    return ruleset_id_for_group(AgentGroupsStore().get_group(agent_name))


# ── drift check ────────────────────────────────────────────────────────────────────────
# The deduction points exist twice: in the prompt text (what the model applies) and in the
# criteria catalogue (what manual re-scoring applies). They must agree.

# Matches a criteria-table row: "open-greet | No greeting… | −2 | …". Anchored on a lowercase
# id followed by a pipe so it can't match the JSON output block or the table headers.
_ROW = re.compile(r"^([a-z][a-z0-9_-]{2,})\s*\|(.*)$")
_DED = re.compile(r"[−-](\d+)")


def _deductions_in_prompt(text: str) -> dict[str, int]:
    found: dict[str, int] = {}
    for line in text.splitlines():
        m = _ROW.match(line.strip())
        if not m:
            continue
        cid, rest = m.group(1), m.group(2)
        d = _DED.search(rest)
        # "score = 0" rows are the critical ones — they carry no point deduction.
        found[cid] = int(d.group(1)) if d else 0
    return found


def validate_ruleset(rs: QaRuleset) -> list[str]:
    """Report where the prompt text and the criteria catalogue disagree.

    Drift here is silent and nasty: the model would deduct one number while an analyst's
    manual re-score deducts another, for the same criterion.
    """
    in_prompt = _deductions_in_prompt(rs.prompt_text)
    catalogue = rs.deductions
    warnings: list[str] = []

    for cid, ded in catalogue.items():
        if cid not in in_prompt:
            warnings.append(f"'{cid}' is in the criteria catalogue but not in the prompt text.")
        elif in_prompt[cid] != ded:
            warnings.append(
                f"'{cid}' deducts {in_prompt[cid]} in the prompt text "
                f"but {ded} in the criteria catalogue."
            )
    for cid in in_prompt:
        if cid not in catalogue:
            warnings.append(f"'{cid}' is in the prompt text but not in the criteria catalogue.")
    return warnings
