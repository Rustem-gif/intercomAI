"""Load the editable support ruleset and derive a stable version hash."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from intercom_summary.settings import settings


@dataclass
class Ruleset:
    text: str          # full markdown, fed to the model as cached context
    version: str       # short content hash, stored alongside each grade
    path: Path


def load_ruleset(path: str | Path | None = None) -> Ruleset:
    p = Path(path) if path else settings.rules_path
    if not p.exists():
        raise FileNotFoundError(
            f"Ruleset not found at {p}. Create it (see rules/support_rules.md template)."
        )
    text = p.read_text(encoding="utf-8")
    version = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
    return Ruleset(text=text, version=version, path=p)
