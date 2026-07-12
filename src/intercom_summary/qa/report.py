"""Aggregate ConversationGrades into per-agent QA reports (Markdown + XLSX)."""
from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path

from openpyxl import Workbook

from intercom_summary.qa.schema import ConversationGrade


def _avg(nums: list[int]) -> float:
    return round(sum(nums) / len(nums), 1) if nums else 0.0


def aggregate(grades: list[ConversationGrade]) -> dict[str, dict]:
    """Return {agent_name: {count, avg_score, common_violations, scores}}.

    Scores on `effective_score` — the analyst's override where one exists, else the AI's score.
    This matches what the dashboard shows (COALESCE(human_score, overall_score)); reporting the
    superseded AI number here would contradict it.
    """
    by_agent: dict[str, list[ConversationGrade]] = defaultdict(list)
    for g in grades:
        by_agent[g.agent_name or "(unknown)"].append(g)

    out: dict[str, dict] = {}
    for agent, gs in by_agent.items():
        scores = [g.effective_score for g in gs]
        violations = Counter(v for g in gs for v in g.violations)
        out[agent] = {
            "count": len(gs),
            "avg_score": _avg(scores),
            "min_score": min(scores) if scores else 0,
            "max_score": max(scores) if scores else 0,
            "overridden": sum(1 for g in gs if g.is_overridden),
            "common_violations": violations.most_common(5),
            "grades": gs,
        }
    return out


def report_markdown(grades: list[ConversationGrade]) -> str:
    agg = aggregate(grades)
    lines = ["# Support QA Report", ""]
    for agent, data in sorted(agg.items()):
        lines.append(f"## {agent}")
        lines.append(
            f"- Conversations graded: **{data['count']}**  |  "
            f"Average score: **{data['avg_score']}/100**  "
            f"(min {data['min_score']}, max {data['max_score']})"
        )
        if data["overridden"]:
            lines.append(
                f"- Analyst overrides applied: **{data['overridden']}** "
                "(scores below reflect the override)"
            )
        if data["common_violations"]:
            lines.append("- Most common violations:")
            for v, n in data["common_violations"]:
                lines.append(f"  - ({n}×) {v}")
        lines.append("")
        for g in sorted(data["grades"], key=lambda x: x.effective_score):
            note = f" _(AI scored {g.overall_score}, overridden)_" if g.is_overridden else ""
            lines.append(
                f"  - `{g.conversation_id}` — **{g.effective_score}/100**{note}: {g.summary}"
            )
        lines.append("")
    return "\n".join(lines)


def report_xlsx(grades: list[ConversationGrade], out_path: str | Path) -> Path:
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()

    overview = wb.active
    overview.title = "Agents"
    overview.append(["Agent", "Graded", "Avg Score", "Min", "Max", "Overrides", "Top Violation"])
    for agent, data in sorted(aggregate(grades).items()):
        top = data["common_violations"][0][0] if data["common_violations"] else ""
        overview.append([agent, data["count"], data["avg_score"],
                         data["min_score"], data["max_score"], data["overridden"], top])

    detail = wb.create_sheet("Conversations")
    # "Score" is the effective score (override if any). The AI's original is kept alongside it so
    # the override is visible rather than silently replacing the number.
    detail.append([
        "Conversation ID", "Agent", "Score", "AI Score", "Overridden By",
        "Summary", "Violations", "Suggestions",
    ])
    for g in grades:
        detail.append([
            g.conversation_id, g.agent_name, g.effective_score, g.overall_score,
            g.overridden_by if g.is_overridden else "", g.summary,
            " | ".join(g.violations), " | ".join(g.suggestions),
        ])

    wb.save(out)
    return out
