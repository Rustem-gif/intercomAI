"""A/B of the historical context on the chats a human has re-scored, without saving a grade.

Grades the same chats once per arm (`off` / `shadow` / `on`, same ruleset, model and effort —
only the context differs) and compares each arm with the human score and with the other arm:

    .venv/bin/python scripts/history_ab.py                         # 144 kb-v41 overridden chats, batch
    .venv/bin/python scripts/history_ab.py -n 30 --live
    .venv/bin/python scripts/history_ab.py --arms off,on --effort medium
    .venv/bin/python scripts/history_ab.py --ids 123 456 --live

Per arm: mean/median |Δ| vs the human score, PASS rate, manual-review rate, evidence grounding
(every FAIL quote found in the transcript — a drop means the context is being cited as
evidence), output tokens and cost. Between arms: result flips, scores that moved ≥ 10, and the
criteria that flipped most. The full per-chat record goes to EVAL_DIR/history_ab_<time>.json.

Nothing is written to `grades`. Runs through the Message Batches API unless --live.
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import statistics
import time
from collections import Counter
from datetime import datetime, timezone

from intercom_summary.settings import settings
from intercom_summary.storage.conversations_store import ConversationsStore, tags_are_ignored

_WS = re.compile(r"\s+")


def pick_ids(ruleset: str, n: int) -> list[str]:
    db = sqlite3.connect(settings.db_path)
    try:
        return [r[0] for r in db.execute(
            "SELECT conversation_id FROM grades WHERE human_score IS NOT NULL AND ruleset_id = ? "
            "ORDER BY overridden_at DESC LIMIT ?", (ruleset, n))]
    finally:
        db.close()


def human_scores(ids: list[str]) -> dict[str, dict]:
    db = sqlite3.connect(settings.db_path)
    try:
        rows = db.execute(
            f"SELECT conversation_id, human_score, overall_score, human_criteria, override_reason "
            f"FROM grades WHERE conversation_id IN ({','.join('?' * len(ids))})", ids).fetchall()
    finally:
        db.close()
    return {r[0]: {"human": r[1], "stored_ai": r[2],
                   "human_criteria": json.loads(r[3]) if r[3] else {}, "reason": r[4] or ""}
            for r in rows}


def _norm(s: str) -> str:
    return _WS.sub(" ", (s or "").lower()).strip()


def grounding(grade, transcript: str) -> tuple[int, int]:
    """(grounded, total) over the FAIL criteria that carry a quote."""
    t = _norm(transcript)
    total = grounded = 0
    for r in grade.rule_results:
        ev = _norm(r.evidence)
        if r.verdict != "fail" or len(ev) < 12:
            continue
        total += 1
        # The model may quote a fragment; accept the longest 40-char window being present.
        probe = ev[:60] if len(ev) > 60 else ev
        grounded += probe in t or any(ev[i:i + 40] in t for i in range(0, max(len(ev) - 40, 1), 20))
    return grounded, total


def record(c, g, hum: dict, dt: float) -> dict:
    u = g.usage or {}
    gr, tot = grounding(g, c.transcript_text(include_bots=False))
    return {
        "id": c.id, "score": g.overall_score, "result": g.overall_result,
        "human": hum.get("human"), "stored_ai": hum.get("stored_ai"),
        "delta": None if hum.get("human") is None else g.overall_score - hum["human"],
        "fails": sorted(r.rule_id for r in g.rule_results if r.verdict == "fail"),
        "cannot": sorted(r.rule_id for r in g.rule_results if r.verdict == "cannot_determine"),
        "manual_review": bool(g.manual_review_needed),
        "case_type": g.case_type, "outcome": g.outcome_status,
        "grounded": gr, "fail_quotes": tot,
        "cost_usd": u.get("cost_usd", 0.0), "output_tokens": u.get("output", 0),
        "input_tokens": u.get("input", 0), "seconds": round(dt, 1),
        "history": {k: g.history_context.get(k) for k in ("mode", "contact_prior", "precedents",
                                                           "tokens_est", "label_source")}
        if g.history_context else {},
    }


def run_arm(mode: str, convos: list, hum: dict, *, ruleset: str, effort: str | None,
            batch: bool) -> list[dict]:
    from intercom_summary.qa import batch as batch_mod
    from intercom_summary.qa.grader import Grader
    from intercom_summary.qa.history_context import HistoryProvider

    grader = Grader(ruleset_id=ruleset, effort=effort,
                    history=False if mode == "off" else HistoryProvider(mode=mode))
    if grader.history is not None:
        grader.history.prepare(convos)
    out: list[dict] = []
    if not batch:
        for c in convos:
            t0 = time.time()
            try:
                g = grader.grade(c)
            except Exception as exc:  # noqa: BLE001
                print(f"  {c.id}: FAILED — {exc}")
                continue
            out.append(record(c, g, hum.get(c.id, {}), time.time() - t0))
            print(f"  {c.id}: {g.overall_score} {g.overall_result}  human {hum.get(c.id, {}).get('human')}")
        return out
    t0 = time.time()
    warm = batch_mod.warm_up_set(grader, convos) if len(convos) > 1 else []
    for c in warm:
        g = grader.grade(c, batch_shape=True)
        out.append(record(c, g, hum.get(c.id, {}), time.time() - t0))
    rest = [c for c in convos if c not in warm]
    by_id = {c.id: c for c in rest}
    ids = batch_mod.submit(grader, rest)
    print(f"  batch {', '.join(ids)}: {len(rest)} chats — waiting…")
    batch_mod.wait(grader.client, ids, on_status=lambda st: print(f"    {st['counts']}"), poll=20)
    dt = time.time() - t0
    for custom_id, result in batch_mod.results(grader.client, ids):
        c = by_id[custom_id]
        try:
            g, fell_back = batch_mod.grade_result(grader, c, result)
        except Exception as exc:  # noqa: BLE001
            print(f"  {c.id}: FAILED — {exc}")
            continue
        out.append(record(c, g, hum.get(c.id, {}), dt / max(len(rest), 1)))
    print(f"  batch ended after {dt:.0f}s")
    return out


def summarise(arm: str, rows: list[dict]) -> dict:
    deltas = [abs(r["delta"]) for r in rows if r["delta"] is not None]
    quotes = sum(r["fail_quotes"] for r in rows)
    return {
        "arm": arm, "n": len(rows),
        "mean_abs_delta": round(statistics.mean(deltas), 1) if deltas else None,
        "median_abs_delta": statistics.median(deltas) if deltas else None,
        "within_5": round(100 * sum(d <= 5 for d in deltas) / len(deltas)) if deltas else None,
        "pass_rate": round(100 * sum(r["result"] == "PASS" for r in rows) / len(rows)) if rows else None,
        "manual_review_rate": round(100 * sum(r["manual_review"] for r in rows) / len(rows)) if rows else None,
        "cannot_determine_per_chat": round(sum(len(r["cannot"]) for r in rows) / len(rows), 2) if rows else None,
        "grounding": round(100 * sum(r["grounded"] for r in rows) / quotes) if quotes else None,
        "fail_quotes": quotes,
        "mean_output_tokens": round(statistics.mean(r["output_tokens"] for r in rows)) if rows else None,
        "mean_input_tokens": round(statistics.mean(r["input_tokens"] for r in rows)) if rows else None,
        "cost_per_chat": round(sum(r["cost_usd"] for r in rows) / len(rows), 4) if rows else None,
        "with_context": sum(1 for r in rows if (r["history"].get("contact_prior") or r["history"].get("precedents"))),
    }


def compare(a: list[dict], b: list[dict]) -> dict:
    by_a, by_b = {r["id"]: r for r in a}, {r["id"]: r for r in b}
    common = [i for i in by_a if i in by_b]
    flips = [(i, by_a[i]["result"], by_b[i]["result"], by_a[i]["human"])
             for i in common if by_a[i]["result"] != by_b[i]["result"]]
    moved = [(i, by_a[i]["score"], by_b[i]["score"], by_a[i]["human"])
             for i in common if abs(by_a[i]["score"] - by_b[i]["score"]) >= 10]
    crit = Counter()
    for i in common:
        fa, fb = set(by_a[i]["fails"]), set(by_b[i]["fails"])
        for cid in fa ^ fb:
            crit[f"{cid} {'fail→pass' if cid in fa else 'pass→fail'}"] += 1
    closer = sum(1 for i in common if by_a[i]["delta"] is not None
                 and abs(by_b[i]["delta"]) < abs(by_a[i]["delta"]))
    further = sum(1 for i in common if by_a[i]["delta"] is not None
                  and abs(by_b[i]["delta"]) > abs(by_a[i]["delta"]))
    return {"common": len(common), "result_flips": flips, "moved_10": moved,
            "criteria_flips": crit.most_common(12),
            "closer_to_human": closer, "further_from_human": further,
            "manual_review": [sum(by_a[i]["manual_review"] for i in common),
                              sum(by_b[i]["manual_review"] for i in common)]}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-n", type=int, default=144)
    ap.add_argument("--ids", nargs="*")
    ap.add_argument("--ruleset", default="kb-v41")
    ap.add_argument("--arms", default="off,on", help="comma-separated: off, shadow, on")
    ap.add_argument("--effort", choices=["low", "medium", "high", "xhigh", "max"])
    ap.add_argument("--live", action="store_true", help="live calls instead of the Batch API")
    ap.add_argument("--jev", choices=["off", "shadow"], default="off")
    args = ap.parse_args()
    object.__setattr__(settings, "jev_mode", args.jev)

    ids = args.ids or pick_ids(args.ruleset, args.n)
    hum = human_scores(ids)
    cs = ConversationsStore()
    try:
        convos = [c for cid in ids if (c := cs.get(cid)) is not None and not tags_are_ignored(c.tags)]
    finally:
        cs.close()
    arms = [a.strip() for a in args.arms.split(",") if a.strip()]
    print(f"{len(convos)} chats · ruleset {args.ruleset} · model {settings.qa_model} · "
          f"effort {args.effort or settings.qa_effort} · arms {arms} · {'live' if args.live else 'batch'}\n")

    results: dict[str, list[dict]] = {}
    for arm in arms:
        print(f"== arm: history {arm}")
        results[arm] = run_arm(arm, convos, hum, ruleset=args.ruleset, effort=args.effort,
                               batch=not args.live)
    summaries = {arm: summarise(arm, rows) for arm, rows in results.items()}
    pairs = {f"{a}→{b}": compare(results[a], results[b])
             for a, b in zip(arms, arms[1:])}

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
    out = settings.eval_dir / f"history_ab_{stamp}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"ids": ids, "effort": args.effort or settings.qa_effort,
                               "model": settings.qa_model, "ruleset": args.ruleset,
                               "summaries": summaries, "pairs": pairs, "rows": results},
                              indent=1, ensure_ascii=False))

    keys = ["n", "mean_abs_delta", "median_abs_delta", "within_5", "pass_rate", "manual_review_rate",
            "cannot_determine_per_chat", "grounding", "mean_output_tokens", "mean_input_tokens",
            "cost_per_chat", "with_context"]
    print("\n| metric | " + " | ".join(arms) + " |")
    print("|---|" + "---|" * len(arms))
    for k in keys:
        print(f"| {k} | " + " | ".join(str(summaries[a][k]) for a in arms) + " |")
    for name, p in pairs.items():
        print(f"\n{name}: {p['common']} chats · result flips {len(p['result_flips'])} · "
              f"moved ≥10: {len(p['moved_10'])} · closer to human {p['closer_to_human']}, "
              f"further {p['further_from_human']} · manual review {p['manual_review'][0]}→{p['manual_review'][1]}")
        for i, ra, rb, h in p["result_flips"]:
            print(f"   flip {i}: {ra} → {rb} (human {h})")
        for cid, n in p["criteria_flips"]:
            print(f"   {cid}: {n}")
    print(f"\nsaved {out}")


if __name__ == "__main__":
    main()
