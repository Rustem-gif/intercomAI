"""Command-line entry point.

  intercom-summary fetch  --agent ada@co.com --since 2026-05-01 --out export.xlsx
  intercom-summary review --agent ada@co.com --since 2026-05-01 --out qa_report.xlsx
  intercom-summary collect-batch msgbatch_01...   # save a Claude batch whose run died
  intercom-summary sync-kb                        # refresh the Help Center knowledge base now
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
            rid = ruleset_for(convo.assignee_name, convo.created_at)
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
    from intercom_summary.web.auth import users as web_users
    report_xlsx(grades, out, web_users.display_names())
    md = out.with_suffix(".md")
    md.write_text(report_markdown(grades), encoding="utf-8")
    log.info("Wrote QA report to %s and %s", out, md)


async def _collect_batch(args: argparse.Namespace):
    """Save the grades of a Claude batch whose review job was interrupted (a server restart
    while it waited). The batch ran — and was billed — at Anthropic regardless."""
    settings.require_qa("api")
    from anthropic import Anthropic

    from intercom_summary.qa import batch as batch_mod
    from intercom_summary.qa.grader import Grader
    from intercom_summary.qa.pricing import sum_usage
    from intercom_summary.qa.rulesets import agent_ruleset_resolver
    from intercom_summary.storage.conversations_store import ConversationsStore
    from intercom_summary.storage.grades_store import GradesStore

    client = Anthropic(api_key=settings.anthropic_api_key)
    log.info("Waiting for batch %s to end…", args.batch_id)
    batch_mod.wait(client, [args.batch_id], on_status=lambda st: log.info("Batch: %s", st["counts"]))

    # A batch holds one ruleset's requests. Unless told, resolve each chat the way the run that
    # submitted it did (its agent's group and creation date).
    resolve = (lambda _a, _c=None: args.ruleset) if args.ruleset else agent_ruleset_resolver()
    graders: dict[str, Grader] = {}
    cstore, gstore = ConversationsStore(), GradesStore()
    saved = fallbacks = failed = 0
    usage = []
    try:
        for custom_id, result in batch_mod.results(client, [args.batch_id]):
            convo = cstore.get(custom_id)
            if convo is None:
                log.warning("%s: not in the conversation cache — skipped", custom_id)
                failed += 1
                continue
            rid = resolve(convo.assignee_name, convo.created_at)
            grader = graders.get(rid) or graders.setdefault(rid, Grader(ruleset_id=rid, client=client))
            try:
                grade, fell_back = batch_mod.grade_result(
                    grader, convo, result, live_fallback=not args.no_live_fallback)
            except Exception as exc:  # noqa: BLE001 — report and carry on with the rest
                log.warning("%s: %s", custom_id, exc)
                failed += 1
                continue
            gstore.save(grade)
            usage.append(grade.usage)
            saved += 1
            fallbacks += fell_back
    finally:
        cstore.close()
        gstore.close()
    total = sum_usage(usage)
    log.info("Saved %d grade(s) from %s (%d re-graded live, %d failed), $%.4f",
             saved, args.batch_id, fallbacks, failed, total["cost_usd"])


async def _sync_kb(args: argparse.Namespace):
    """Re-fetch the brands' published Help Center articles (the v4.1 grader's KB/T&C)."""
    settings.require_intercom()
    from intercom_summary.qa import knowledge_base

    counts = knowledge_base.sync()
    for brand, kb in knowledge_base.load_all(refresh=False).items():
        log.info("%s: %d articles, %d chars, version %s", brand, kb.articles, len(kb.text), kb.version)
    missing = [b for b, n in counts.items() if not n]
    if missing:
        log.info("No published articles (graded without a KB): %s", ", ".join(missing))


async def _calibrate(args: argparse.Namespace):
    """Grade a frozen calibration sample into a new run (never into live grades)."""
    settings.require_qa("api")
    from intercom_summary import service

    # run_calibration drives its own event loops, so it runs off this one.
    result = await asyncio.to_thread(
        service.run_calibration, args.sample, ruleset_id=args.ruleset, batch=args.batch,
        pilot_only=args.pilot, created_by="cli",
    )
    log.info("Run %s: graded %d of %d member(s); %d ticket(s), %d unavailable, %d failed, $%.4f",
             result["run_id"], result["graded"], result["members"], result["tickets"],
             result["unavailable"], result["failed"], (result.get("usage") or {}).get("cost_usd", 0))


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

    b = sub.add_parser("collect-batch",
                       help="Save the grades of a Claude batch whose review run was interrupted.")
    b.add_argument("batch_id", help="msgbatch_… id (shown in the failed job's error).")
    b.add_argument("--ruleset", help="Ruleset the batch was submitted under, when the run forced "
                                     "one (a pilot); default: resolve per chat as a run does.")
    b.add_argument("--no-live-fallback", action="store_true",
                   help="Skip unusable items instead of re-grading them live at full price.")
    b.set_defaults(func=_collect_batch)

    c = sub.add_parser("calibrate",
                       help="Grade a calibration sample with Claude into a separate run.")
    c.add_argument("sample", help="Calibration sample id, e.g. kb-v41-180.")
    c.add_argument("--ruleset", default="kb-v41")
    c.add_argument("--batch", action="store_true", help="Message Batches API: half price, slower.")
    c.add_argument("--pilot", action="store_true", help="Only the sample's Pilot-20 members.")
    c.set_defaults(func=_calibrate)

    k = sub.add_parser("sync-kb", help="Refresh the Help Center knowledge base used for v4.1 accuracy.")
    k.set_defaults(func=_sync_kb)
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    asyncio.run(args.func(args))


if __name__ == "__main__":
    main()
