"""Per-conversation Markdown transcripts for easy human reading."""
from __future__ import annotations

from pathlib import Path

from intercom_summary.intercom.models import Conversation


def conversation_to_markdown(convo: Conversation) -> str:
    lines = [
        f"# Conversation {convo.id}",
        "",
        f"- **Subject:** {convo.subject or '(none)'}",
        f"- **Agent:** {convo.assignee_name or '(unassigned)'}",
        f"- **Customer:** {convo.contact.name} <{convo.contact.email}>",
        f"- **State:** {convo.state}",
        f"- **Created:** {convo.created_at.isoformat() if convo.created_at else '?'}",
        f"- **Messages:** {convo.message_count}",
    ]
    if convo.csat_rating is not None:
        lines.append(f"- **CSAT:** {convo.csat_rating}/5 — {convo.csat_remark or ''}")
    if convo.tags:
        lines.append(f"- **Tags:** {', '.join(convo.tags)}")
    lines += ["", "---", ""]

    for m in convo.messages:
        when = m.created_at.isoformat() if m.created_at else "?"
        role = "🧑‍💼 Agent" if m.author_type == "admin" else (
            "🙋 Customer" if m.author_type in ("user", "contact") else f"⚙️ {m.author_type}"
        )
        suffix = f" _({m.part_type})_" if m.part_type and m.part_type != "comment" else ""
        lines.append(f"**{role} · {m.author_name}** · {when}{suffix}")
        lines.append("")
        lines.append(m.text or "_(no text)_")
        lines.append("")
    return "\n".join(lines)


def export_transcripts(conversations: list[Conversation], out_dir: str | Path) -> list[Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for convo in conversations:
        p = out / f"conversation-{convo.id}.md"
        p.write_text(conversation_to_markdown(convo), encoding="utf-8")
        written.append(p)
    return written
