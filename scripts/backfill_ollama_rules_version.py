"""One-time repair: relabel Ollama-graded rows to the current QA-prompt version.

Background: `OllamaGrader` used to stamp `grade.rules_version` with the *ruleset*
(support_rules.md) hash while its `.rules_version` property — the value the
Evaluation stats endpoint filters on — returns the *QA prompt* (qa_system_prompt.txt)
hash. The two never matched, so every Ollama grade showed up as "on an older ruleset"
and got needlessly re-graded. The save bug is now fixed; this script repairs grades
written before the fix.

Ollama only grades against qa_system_prompt.txt (no external ruleset is used), so the
correct rules_version for every Ollama grade is the current prompt version. We key on
`model LIKE 'ollama/%'` so we don't have to hardcode the stale hash.

Usage:
    python scripts/backfill_ollama_rules_version.py
"""
from __future__ import annotations

from intercom_summary.qa.casino_prompt import load_qa_prompt
from intercom_summary.settings import settings
from intercom_summary.storage.db import connect


def main() -> None:
    current = load_qa_prompt().version
    conn = connect(settings.db_path)
    try:
        before = conn.execute(
            "SELECT rules_version, COUNT(*) AS n FROM grades "
            "WHERE model LIKE 'ollama/%' GROUP BY rules_version"
        ).fetchall()
        print("Ollama grades by rules_version (before):")
        for r in before:
            print(f"  {r['rules_version']}: {r['n']}")

        cur = conn.execute(
            "UPDATE grades SET rules_version = ? "
            "WHERE model LIKE 'ollama/%' AND rules_version != ?",
            (current, current),
        )
        conn.commit()
        print(f"\nRelabeled {cur.rowcount} grade(s) -> {current}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
