"""Command-line entry point.

  intercom-summary fetch  --agent ada@co.com --since 2026-05-01 --out export.xlsx
  intercom-summary review --agent ada@co.com --since 2026-05-01 --out qa_report.xlsx
"""
from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from intercom_summary.settings import settings
from intercom_summary.export.transcript import export_transcripts
from intercom_summary.export.xlsx import export_xlsx
from intercom_summary.intercom.fetch import fetch_conversations_for_agents
from intercom_summary.logging_setup import get_logger
from intercom_summary.storage.conversations_store import tags_are_ignored

log = get_logger("cli")


def _common_filters(p: argparse.ArgumentParser) -> None:
    p.add_argument("--agent", action="append", required=True, metavar="NAME|EMAIL",
                   help="Agent name or email. Repeat for multiple agents.")
    p.add_argument("--since", help="Only conversations created after this date (YYYY-MM-DD).")
    p.add_argument("--until", help="Only conversations created before this date (YYYY-MM-DD).")
    p.add_argument("--state", choices=["open", "closed", "snoozed"], help="Filter by state.")
    p.add_argument("--limit", type=int, help="Max conversations to fetch (smoke testing).")


async def _fetch(args: argparse.Namespace):
    settings.require_intercom()
    convos = await fetch_conversations_for_agents(
        agents=args.agent, since=args.since, until=args.until,
        state=args.state, limit=args.limit,
    )
    if not convos:
        log.warning("No conversations found for the given filters.")
        return convos

    out = Path(args.out) if args.out else settings.export_dir / "intercom_export.xlsx"
    export_xlsx(convos, out)
    log.info("Wrote %d conversations to %s", len(convos), out)

    if getattr(args, "transcripts", False):
        tdir = out.parent / "transcripts"
        export_transcripts(convos, tdir)
        log.info("Wrote %d transcripts to %s", len(convos), tdir)
    return convos


async def _review(args: argparse.Namespace):
    settings.require_intercom()
    settings.require_qa()
    # Imported lazily so `fetch` works without the QA deps configured.
    from intercom_summary.qa.backends import get_grader
    from intercom_summary.qa.report import report_markdown, report_xlsx
    from intercom_summary.qa.schema import ConversationGrade
    from intercom_summary.storage.grades_store import GradesStore

    convos = await fetch_conversations_for_agents(
        agents=args.agent, since=args.since, until=args.until,
        state=args.state, limit=args.limit,
    )
    if not convos:
        log.warning("No conversations to grade.")
        return

    # Triage/noise chats (tagged spam, empty, test, Jira, Follow-Up, no request) are never
    # graded. The web path drops them in service.review_and_store; this one fetches straight
    # from Intercom and so has to drop them itself, or `review` re-introduces exactly the
    # grades the rest of the system refuses to produce.
    ignored = [c for c in convos if tags_are_ignored(c.tags)]
    if ignored:
        convos = [c for c in convos if not tags_are_ignored(c.tags)]
        log.info("Skipping %d conversation(s) with an ignored tag.", len(ignored))
    if not convos:
        log.warning("No conversations to grade (all carried an ignored tag).")
        return

    from intercom_summary.qa.rulesets import agent_ruleset_resolver

    # One grader per ruleset — a conversation is graded against its assigned agent's ruleset
    # (VIP agents get the VIP ruleset). Built lazily so a run that never sees a VIP agent
    # never loads the VIP prompt. Group membership is read once, not per conversation.
    ruleset_for = agent_ruleset_resolver()
    graders: dict[str, object] = {}

    def _grader_for(ruleset_id: str):
        if ruleset_id not in graders:
            graders[ruleset_id] = get_grader(ruleset_id=ruleset_id)
        return graders[ruleset_id]

    store = GradesStore()
    grades: list[ConversationGrade] = []
    try:
        for convo in convos:
            rid = ruleset_for(convo.assignee_name)
            grader = _grader_for(rid)
            if not args.regrade and store.is_current(convo.id, rid, grader.rules_version):
                cached = store.get(convo.id)
                # from_dict ignores the extra keys the store adds to a stored grade (human_score,
                # the per-criterion `deduction`/`critical` annotations, …). Building the dataclass
                # by splatting the dict instead crashes on them.
                grades.append(ConversationGrade.from_dict(cached))
                log.info("Skipping %s (already graded)", convo.id)
                continue
            grade = grader.grade(convo)
            store.save(grade)
            grades.append(grade)
    finally:
        store.close()

    out = Path(args.out) if args.out else settings.export_dir / "qa_report.xlsx"
    report_xlsx(grades, out)
    md = out.with_suffix(".md")
    md.write_text(report_markdown(grades), encoding="utf-8")
    log.info("Wrote QA report to %s and %s", out, md)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="intercom-summary", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    f = sub.add_parser("fetch", help="Fetch & export conversations to XLSX.")
    _common_filters(f)
    f.add_argument("--out", help="Output .xlsx path.")
    f.add_argument("--transcripts", action="store_true", help="Also write per-conversation .md files.")
    f.set_defaults(func=_fetch)

    r = sub.add_parser("review", help="Fetch + QA-grade conversations (Qwen/Ollama or API).")
    _common_filters(r)
    r.add_argument("--out", help="Output QA report .xlsx path.")
    r.add_argument("--regrade", action="store_true", help="Re-grade even if already graded.")
    r.set_defaults(func=_review)
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    asyncio.run(args.func(args))


if __name__ == "__main__":
    main()
