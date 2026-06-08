"""Aggregate ConversationGrades into per-agent QA reports (Markdown + XLSX)."""
from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path

from openpyxl import Workbook

from intercom_summary.qa.schema import ConversationGrade


def _avg(nums: list[int]) -> float:
    return round(sum(nums) / len(nums), 1) if nums else 0.0


def aggregate(grades: list[ConversationGrade]) -> dict[str, dict]:
    """Return {agent_name: {count, avg_score, common_violations, scores}}."""
    by_agent: dict[str, list[ConversationGrade]] = defaultdict(list)
    for g in grades:
        by_agent[g.agent_name or "(unknown)"].append(g)

    out: dict[str, dict] = {}
    for agent, gs in by_agent.items():
        scores = [g.overall_score for g in gs]
        violations = Counter(v for g in gs for v in g.violations)
        out[agent] = {
            "count": len(gs),
            "avg_score": _avg(scores),
            "min_score": min(scores) if scores else 0,
            "max_score": max(scores) if scores else 0,
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
        if data["common_violations"]:
            lines.append("- Most common violations:")
            for v, n in data["common_violations"]:
                lines.append(f"  - ({n}×) {v}")
        lines.append("")
        for g in sorted(data["grades"], key=lambda x: x.overall_score):
            lines.append(f"  - `{g.conversation_id}` — **{g.overall_score}/100**: {g.summary}")
        lines.append("")
    return "\n".join(lines)


def report_xlsx(grades: list[ConversationGrade], out_path: str | Path) -> Path:
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()

    overview = wb.active
    overview.title = "Agents"
    overview.append(["Agent", "Graded", "Avg Score", "Min", "Max", "Top Violation"])
    for agent, data in sorted(aggregate(grades).items()):
        top = data["common_violations"][0][0] if data["common_violations"] else ""
        overview.append([agent, data["count"], data["avg_score"],
                         data["min_score"], data["max_score"], top])

    detail = wb.create_sheet("Conversations")
    detail.append(["Conversation ID", "Agent", "Score", "Summary", "Violations", "Suggestions"])
    for g in grades:
        detail.append([
            g.conversation_id, g.agent_name, g.overall_score, g.summary,
            " | ".join(g.violations), " | ".join(g.suggestions),
        ])

    wb.save(out)
    return out
