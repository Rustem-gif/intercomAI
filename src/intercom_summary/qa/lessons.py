"""QA lessons — turn QA's re-scores into proposed grading-rule changes, weekly.

The AI grader learns from QA only through its rules: the prompt's QA PRACTICE section and the
fail verifier's NOT-A-FAIL boundaries (qa/fail_verifier.py). This module closes that loop:

    collect_feedback(since)   every human verdict since the last sync — re-scores (grades),
                              calibration reviews and reference labels, agent disputes — as
                              per-criterion disagreements with the AI ("flips")
    draft_lessons(flips)      one Sonnet call per criterion QA corrected ≥ QA_LESSONS_MIN_CASES
                              times: a general rule, worded for the prompt, citing the chats
    sync()                    both, stored as drafts in qa_lessons (storage/lessons_store.py)
    approve(id)               an admin's yes: the rule is added to the live prompt's QA PRACTICE
                              section, and a NOT-A-FAIL rule to the verifier's boundaries

Nothing changes until an admin approves, and an approved rule applies only to chats graded
after it — existing grades are never re-graded (GradesStore.is_current).

A re-score that names no criteria ("no mistakes") still teaches something when it reverses the
AI's result: every criterion the AI failed is counted as fail → pass, marked `inferred`.
"""
from __future__ import annotations

import json
import re
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from intercom_summary.logging_setup import get_logger
from intercom_summary.settings import settings

log = get_logger(__name__)

PRACTICE_HEADING = "## QA PRACTICE — WHAT IS NOT A FAILURE"
LEARNED_HEADING = "### Learned from QA re-scores (approved QA lessons)"
_MAX_EXAMPLES = 6
_TRANSCRIPT_CHARS = 3500


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _verdicts(payload: dict) -> dict[str, tuple[str, str]]:
    return {r.get("rule_id"): (r.get("verdict"), r.get("evidence") or "")
            for r in payload.get("rule_results") or [] if r.get("rule_id")}


def _flips(ai: dict[str, tuple[str, str]], human: dict[str, str], ai_result: str | None,
           human_result: str | None) -> list[dict]:
    """Criteria on which QA and the AI part. With no per-criterion verdicts, a reversed result
    counts every criterion the AI failed (or every... none, for a FAIL QA found) as inferred."""
    out = []
    if human:
        for cid, hv in human.items():
            av, ev = ai.get(cid, (None, ""))
            if hv != av:
                out.append({"criterion_id": cid, "ai": av, "human": hv, "ai_evidence": ev,
                            "inferred": False})
    elif ai_result == "FAIL" and human_result == "PASS":
        for cid, (av, ev) in ai.items():
            if av in ("fail", "cannot_determine"):
                out.append({"criterion_id": cid, "ai": av, "human": "pass", "ai_evidence": ev,
                            "inferred": True})
    return out


def collect_feedback(since: str | None, ruleset_id: str = "kb-v41",
                     db_path: str | None = None) -> dict[str, Any]:
    """Every human verdict on a `ruleset_id` grade since `since` (ISO), as chats with flips.

    Returns {"chats": [{conversation_id, source, ai_score, human_score, ai_result, human_result,
    note, flips: [...]}], "agreed": n (Agree clicks), "disputes": [...]}. Read-only."""
    from intercom_summary.qa.agreement import result_of

    since = since or "0000"
    db = sqlite3.connect(f"file:{db_path or settings.db_path}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    chats: list[dict] = []
    try:
        for r in db.execute(
                """SELECT conversation_id, overall_score, human_score, override_reason,
                          human_criteria, payload_json
                     FROM grades
                    WHERE ruleset_id = ? AND human_score IS NOT NULL AND overridden_at >= ?""",
                (ruleset_id, since)):
            p = json.loads(r["payload_json"] or "{}")
            ai_res = result_of(r["overall_score"], p.get("overall_result"))
            hum_res = result_of(r["human_score"])
            human = json.loads(r["human_criteria"]) if r["human_criteria"] else {}
            chats.append({"conversation_id": r["conversation_id"], "source": "re-score",
                          "ai_score": r["overall_score"], "human_score": r["human_score"],
                          "ai_result": ai_res, "human_result": hum_res,
                          "note": r["override_reason"] or "",
                          "flips": _flips(_verdicts(p), human, ai_res, hum_res)})
        # Calibration: QA's review of a run's grade, made in the Calibration tab.
        for r in db.execute(
                """SELECT r.conversation_id, r.overall_score, r.human_score, r.human_note,
                          r.human_criteria, r.payload_json
                     FROM calibration_results r JOIN calibration_runs u ON u.id = r.run_id
                    WHERE u.ruleset_id = ? AND r.human_score IS NOT NULL AND r.reviewed_at >= ?""",
                (ruleset_id, since)):
            p = json.loads(r["payload_json"] or "{}")
            ai_res = result_of(r["overall_score"], p.get("overall_result"))
            hum_res = result_of(r["human_score"])
            human = json.loads(r["human_criteria"]) if r["human_criteria"] else {}
            chats.append({"conversation_id": r["conversation_id"], "source": "calibration",
                          "ai_score": r["overall_score"], "human_score": r["human_score"],
                          "ai_result": ai_res, "human_result": hum_res,
                          "note": r["human_note"] or "",
                          "flips": _flips(_verdicts(p), human, ai_res, hum_res)})
        # Reference labels: QA's verdict on a sample member (imported from their own grading,
        # or from their written feedback), compared with the newest run that graded the chat.
        seen = {c["conversation_id"] for c in chats}
        if _has_column(db, "calibration_sample_items", "labelled_at"):
            for r in db.execute(
                    """SELECT i.conversation_id, i.human_score, i.human_result, i.human_criteria,
                              i.human_note, r.overall_score, r.payload_json
                         FROM calibration_sample_items i
                         JOIN calibration_runs u ON u.sample_id = i.sample_id AND u.ruleset_id = ?
                         JOIN calibration_results r ON r.run_id = u.id
                              AND r.conversation_id = i.conversation_id AND r.status = 'graded'
                        WHERE (i.human_score IS NOT NULL OR i.human_result IS NOT NULL)
                          AND i.labelled_at >= ?
                          AND u.started_at = (SELECT MAX(u2.started_at) FROM calibration_runs u2
                                               JOIN calibration_results r2 ON r2.run_id = u2.id
                                                AND r2.conversation_id = i.conversation_id
                                                AND r2.status = 'graded'
                                              WHERE u2.sample_id = i.sample_id AND u2.ruleset_id = ?)""",
                    (ruleset_id, since, ruleset_id)):
                if r["conversation_id"] in seen:
                    continue
                p = json.loads(r["payload_json"] or "{}")
                ai_res = result_of(r["overall_score"], p.get("overall_result"))
                hum_res = result_of(r["human_score"], r["human_result"])
                human = json.loads(r["human_criteria"]) if r["human_criteria"] else {}
                chats.append({"conversation_id": r["conversation_id"], "source": "reference",
                              "ai_score": r["overall_score"], "human_score": r["human_score"],
                              "ai_result": ai_res, "human_result": hum_res,
                              "note": r["human_note"] or "",
                              "flips": _flips(_verdicts(p), human, ai_res, hum_res)})
        agreed = db.execute(
            "SELECT COUNT(*) FROM grades WHERE ruleset_id = ? AND human_confirmed_at >= ?",
            (ruleset_id, since)).fetchone()[0] if _has_column(db, "grades", "human_confirmed_at") else 0
        disputes = [dict(d) for d in db.execute(
            """SELECT d.conversation_id, d.reason, d.status, d.resolution_note
                 FROM grade_disputes d JOIN grades g ON g.conversation_id = d.conversation_id
                WHERE g.ruleset_id = ? AND d.created_at >= ?""", (ruleset_id, since))] \
            if _has_table(db, "grade_disputes") else []
    finally:
        db.close()
    return {"chats": chats, "agreed": agreed, "disputes": disputes}


def _has_table(db, name: str) -> bool:
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                      (name,)).fetchone() is not None


def _has_column(db, table: str, col: str) -> bool:
    return any(r[1] == col for r in db.execute(f"PRAGMA table_info({table})"))


def by_criterion(chats: list[dict]) -> dict[str, list[dict]]:
    """{criterion_id: [flip + its chat's note and result]}, most-corrected first."""
    groups: dict[str, list[dict]] = defaultdict(list)
    for c in chats:
        for f in c["flips"]:
            groups[f["criterion_id"]].append({**f, "conversation_id": c["conversation_id"],
                                              "note": c["note"], "ai_result": c["ai_result"],
                                              "human_result": c["human_result"]})
    return dict(sorted(groups.items(), key=lambda kv: -len(kv[1])))


def summary(chats: list[dict], agreed: int) -> dict[str, Any]:
    reviewed = len(chats) + agreed
    disagree = sum(c["ai_result"] != c["human_result"] for c in chats)
    return {"re_scored": len(chats), "agreed": agreed, "reviewed": reviewed,
            "result_disagreements": disagree,
            "false_fail": sum(c["ai_result"] == "FAIL" and c["human_result"] == "PASS" for c in chats),
            "false_pass": sum(c["ai_result"] == "PASS" and c["human_result"] == "FAIL" for c in chats),
            # Among chats a human looked at: re-scores that kept the result + Agree clicks.
            "reviewed_agreement": round(1 - disagree / reviewed, 4) if reviewed else None}


# ── drafting ─────────────────────────────────────────────────────────────────────────────
DRAFT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["lessons"],
    "properties": {"lessons": {"type": "array", "items": {
        "type": "object",
        "additionalProperties": False,
        "required": ["kind", "text", "rationale", "conversation_ids", "already_covered"],
        "properties": {
            "kind": {"type": "string", "enum": ["not_a_fail", "is_a_fail"]},
            "text": {"type": "string"},
            "rationale": {"type": "string"},
            "conversation_ids": {"type": "array", "items": {"type": "string"}},
            "already_covered": {"type": "boolean"},
        }}}},
}

_DRAFT_TASK = """=== QA LESSONS — DRAFT RULE CHANGES FROM QA'S CORRECTIONS ===
You maintain the grading rules above. QA managers corrected the AI's verdict on criterion `{cid}` in the chats below. Your job: write the smallest GENERAL rule change that makes the AI agree with QA on chats like these, worded as one bullet for the QA PRACTICE section.

Rules for your answer:
- A lesson must be general (a situation, not a chat) and must not excuse what the manual clearly calls a failure. If QA's verdict needs knowledge the AI cannot see (back office, shift events, internal policy), do NOT write a rule for it.
- kind = not_a_fail when QA passed what the AI failed; is_a_fail when QA failed what the AI passed.
- already_covered = true when the rules above (or the existing verifier boundaries listed below) already say it — then the AI misapplied a rule, and the lesson text should sharpen that rule's wording instead.
- At most 2 lessons. Cite the conversation ids each lesson explains. Text ≤ 60 words; rationale ≤ 40 words.

Existing verifier boundaries for `{cid}`:
{boundaries}

The corrections:
{examples}

Return ONLY the JSON object."""


def _example_text(flip: dict, transcript: str) -> str:
    t = transcript[-_TRANSCRIPT_CHARS:] if len(transcript) > _TRANSCRIPT_CHARS else transcript
    return (f"--- chat {flip['conversation_id']}: AI {flip['ai']} → QA {flip['human']}"
            f"{' (inferred from the overall re-score)' if flip['inferred'] else ''}\n"
            f"AI evidence: {flip['ai_evidence'][:300]}\nQA comment: {flip['note'][:300]}\n"
            f"Transcript (end):\n{t}\n")


def draft_request(ruleset, cid: str, flips: list[dict], transcripts: dict[str, str],
                  boundaries: list[str], model: str | None = None) -> dict:
    examples = "\n".join(_example_text(f, transcripts.get(f["conversation_id"], "(not cached)"))
                         for f in flips[:_MAX_EXAMPLES])
    task = _DRAFT_TASK.format(cid=cid, boundaries="\n".join(f"- {b}" for b in boundaries) or "(none)",
                              examples=examples)
    return {
        "model": model or settings.qa_model,
        "max_tokens": 6000,
        "system": [{"type": "text", "text": ruleset.prompt_text,
                    "cache_control": {"type": "ephemeral"}}],
        "messages": [{"role": "user", "content": task}],
        "output_config": {"effort": "medium",
                          "format": {"type": "json_schema", "schema": DRAFT_SCHEMA}},
    }


def draft_lessons(client, groups: dict[str, list[dict]], *, ruleset_id: str = "kb-v41",
                  min_cases: int | None = None, meter=None) -> list[dict]:
    """One drafting call per criterion corrected at least `min_cases` times. Returns lesson
    dicts ready for LessonsStore.add (already-covered ones are kept as sharpened wording)."""
    from intercom_summary.qa.fail_verifier import BOUNDARIES
    from intercom_summary.qa.rulesets import get_ruleset
    from intercom_summary.storage.conversations_store import ConversationsStore

    min_cases = settings.qa_lessons_min_cases if min_cases is None else min_cases
    ruleset = get_ruleset(ruleset_id)
    due = {cid: fl for cid, fl in groups.items() if len(fl) >= min_cases}
    if not due:
        return []
    cs = ConversationsStore()
    try:
        transcripts = {}
        for fl in due.values():
            for f in fl[:_MAX_EXAMPLES]:
                c = cs.get(f["conversation_id"])
                if c is not None:
                    transcripts[f["conversation_id"]] = c.transcript_text(include_bots=False)
    finally:
        cs.close()
    out: list[dict] = []
    for cid, fl in due.items():
        params = draft_request(ruleset, cid, fl, transcripts, BOUNDARIES.get(cid, []))
        try:
            resp = client.beta.messages.create(**params)
            if meter is not None:
                meter.add(getattr(resp, "usage", None), params["model"])
            text = "".join(getattr(b, "text", "") for b in resp.content
                           if getattr(b, "type", None) == "text")
            drafted = json.loads(text).get("lessons") or []
        except Exception as exc:  # noqa: BLE001 — one criterion's failure must not stop the sync
            log.warning("QA lessons: drafting %s failed: %s", cid, exc)
            continue
        by_id = {f["conversation_id"]: f for f in fl}
        for d in drafted[:2]:
            cited = [i for i in d.get("conversation_ids") or [] if i in by_id] or list(by_id)[:3]
            out.append({
                "ruleset_id": ruleset_id, "criterion_id": cid,
                "kind": d.get("kind") if d.get("kind") in ("not_a_fail", "is_a_fail") else "not_a_fail",
                "text": (("[sharpens an existing rule] " if d.get("already_covered") else "")
                         + str(d.get("text") or "").strip()),
                "rationale": str(d.get("rationale") or ""),
                "evidence": [{k: by_id[i].get(k) for k in ("conversation_id", "ai", "human", "note",
                                                           "ai_evidence", "inferred")}
                             for i in cited],
            })
    return out


# ── the weekly sync ──────────────────────────────────────────────────────────────────────
def last_sync_at() -> str | None:
    from intercom_summary.storage.lessons_store import LessonsStore

    store = LessonsStore()
    try:
        return store.get_meta("lessons.last_sync_at")
    finally:
        store.close()


def sync(*, since: str | None = None, ruleset_id: str = "kb-v41", draft: bool = True,
         client=None) -> dict[str, Any]:
    """Collect every human verdict since the last sync (or `since`), group the disagreements
    by criterion, draft lessons for the criteria QA corrected repeatedly, store them as drafts.
    `draft=False` (or no Anthropic key) collects and reports only — free."""
    from intercom_summary.qa.pricing import UsageMeter
    from intercom_summary.storage.lessons_store import LessonsStore

    started = _now()
    since = since or last_sync_at() or (
        datetime.now(timezone.utc) - timedelta(days=settings.qa_lessons_days)).isoformat()
    fb = collect_feedback(since, ruleset_id)
    groups = by_criterion(fb["chats"])
    meter = UsageMeter()
    drafted: list[dict] = []
    if draft and groups and settings.anthropic_api_key:
        if client is None:
            from anthropic import Anthropic

            client = Anthropic(api_key=settings.anthropic_api_key)
        drafted = draft_lessons(client, groups, ruleset_id=ruleset_id, meter=meter)
    sync_id = "sync-" + started[:19].replace(":", "").replace("-", "")
    store = LessonsStore()
    try:
        ids = [store.add(**d, sync_id=sync_id) for d in drafted]
        store.set_meta("lessons.last_sync_at", started)
        store.set_meta("lessons.last_sync", json.dumps({
            "at": started, "since": since, "sync_id": sync_id, "drafted": len(ids),
            "summary": summary(fb["chats"], fb["agreed"]),
            "criteria": {cid: len(fl) for cid, fl in groups.items()},
            "disputes": len(fb["disputes"]), "cost_usd": round(meter.cost, 4)}))
    finally:
        store.close()
    log.info("QA lessons sync %s: %d re-scores since %s, %d drafted ($%.4f)",
             sync_id, len(fb["chats"]), since, len(ids), meter.cost)
    return {"sync_id": sync_id, "since": since, "drafted": ids,
            "summary": summary(fb["chats"], fb["agreed"]),
            "criteria": {cid: len(fl) for cid, fl in groups.items()},
            "cost_usd": round(meter.cost, 4)}


# ── approval ─────────────────────────────────────────────────────────────────────────────
def add_to_prompt(prompt: str, lesson: dict) -> str:
    """The prompt with the lesson added under the QA PRACTICE section's learned list (created
    on first use, at the end of that section). A prompt without the section gets it appended."""
    kind = "NOT a failure" if lesson["kind"] == "not_a_fail" else "IS a failure"
    text = re.sub(r"^\[sharpens an existing rule\]\s*", "", lesson["text"]).strip()
    bullet = f"- {lesson['criterion_id']} — {kind}: {text} (QA lesson {lesson['id']})"
    if LEARNED_HEADING in prompt:
        start = prompt.index(LEARNED_HEADING) + len(LEARNED_HEADING)
        nxt = re.search(r"\n#{2,3} ", prompt[start:])
        end = start + nxt.start() if nxt else len(prompt)
        head, tail = prompt[:end].rstrip("\n"), prompt[end:].lstrip("\n")
        return f"{head}\n{bullet}\n" + (f"\n{tail}" if tail else "")
    if PRACTICE_HEADING in prompt:
        start = prompt.index(PRACTICE_HEADING)
        nxt = re.search(r"\n## ", prompt[start + len(PRACTICE_HEADING):])
        end = start + len(PRACTICE_HEADING) + nxt.start() if nxt else len(prompt)
        head, tail = prompt[:end].rstrip("\n"), prompt[end:].lstrip("\n")
        return f"{head}\n\n{LEARNED_HEADING}\n{bullet}\n" + (f"\n{tail}" if tail else "")
    return f"{prompt.rstrip()}\n\n{PRACTICE_HEADING}\n\n{LEARNED_HEADING}\n{bullet}\n"


def approve(lesson_id: str, decided_by: str) -> dict:
    """Write an approved lesson into the live prompt (a new rules_version → new chats only)
    and mark it approved; the fail verifier picks NOT-A-FAIL lessons up from the table."""
    from intercom_summary.qa.rulesets import get_ruleset
    from intercom_summary.storage.lessons_store import LessonsStore

    store = LessonsStore()
    try:
        lesson = store.get(lesson_id)
        if lesson is None:
            raise KeyError(lesson_id)
        if lesson["status"] != "draft":
            raise ValueError(f"lesson {lesson_id} is already {lesson['status']}")
        rs = get_ruleset(lesson["ruleset_id"])
        rs.prompt_path.write_text(add_to_prompt(rs.prompt_text, lesson), encoding="utf-8")
        store.decide(lesson_id, "approved", decided_by)
        new_version = get_ruleset(lesson["ruleset_id"]).version
    finally:
        store.close()
    log.info("QA lesson %s approved by %s → %s rules %s", lesson_id, decided_by,
             lesson["ruleset_id"], new_version)
    return {"id": lesson_id, "rules_version": new_version}
