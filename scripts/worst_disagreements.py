"""The chats where the AI is furthest from QA — the place to start improving the rules.

    .venv/bin/python scripts/worst_disagreements.py --sample kb-v41-180 [--run <id>] -n 10
    .venv/bin/python scripts/worst_disagreements.py --rescored -n 10          # QA's re-scores
    … --freeze kb-v41-worst10      # also store the picked chats as a frozen calibration sample

Free: it reads grades that already exist — a calibration run's grades against the sample's QA
reference labels (or reviews made in the tab), or the live grades QA re-scored — and grades
nothing. PASS/FAIL disagreements come first (that is the client's 95% bar), then the score gap.
"""
from __future__ import annotations

import argparse
import json
import sqlite3

from intercom_summary.qa.agreement import result_of
from intercom_summary.settings import settings
from intercom_summary.storage.calibration_store import CalibrationStore


def from_sample(sample_id: str, run_id: str | None) -> list[dict]:
    cal = CalibrationStore()
    try:
        run = cal.get_run(run_id) if run_id else cal.latest_run(sample_id)
        if run is None:
            raise SystemExit(f"No calibration run for {sample_id!r}")
        rows = cal.results(run["id"])
    finally:
        cal.close()
    out = []
    for r in rows:
        if r["status"] != "graded":
            continue
        human = r["human_score"] if r["human_score"] is not None else r["label_score"]
        hres = (r["label_result"] if r["human_score"] is None and r["label_result"]
                else result_of(human))
        if hres is None:
            continue
        p = r["payload"] or {}
        out.append({"id": r["conversation_id"], "ai": r["overall_score"],
                    "ai_result": result_of(r["overall_score"], p.get("overall_result")),
                    "human": human, "human_result": hres,
                    "note": r["human_note"] or r["label_note"] or "",
                    "fails": [c["rule_id"] for c in p.get("rule_results") or []
                              if c.get("verdict") == "fail"]})
    print(f"run {run['id']} ({run['rules_version']}) on {sample_id}: {len(out)} chats with a QA verdict")
    return out


def from_rescored(ruleset: str) -> list[dict]:
    db = sqlite3.connect(f"file:{settings.db_path}?mode=ro", uri=True)
    try:
        rows = db.execute(
            "SELECT conversation_id, overall_score, human_score, override_reason, payload_json "
            "FROM grades WHERE ruleset_id = ? AND human_score IS NOT NULL", (ruleset,)).fetchall()
    finally:
        db.close()
    out = []
    for cid, ai, hs, note, pj in rows:
        p = json.loads(pj or "{}")
        out.append({"id": cid, "ai": ai, "ai_result": result_of(ai, p.get("overall_result")),
                    "human": hs, "human_result": result_of(hs), "note": note or "",
                    "fails": [c["rule_id"] for c in p.get("rule_results") or []
                              if c.get("verdict") == "fail"]})
    return out


def rank(rows: list[dict]) -> list[dict]:
    return sorted(rows, key=lambda r: (r["ai_result"] == r["human_result"],
                                       -abs((r["human"] or 0) - (r["ai"] or 0))))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--sample")
    src.add_argument("--rescored", action="store_true")
    ap.add_argument("--run")
    ap.add_argument("--ruleset", default="kb-v41")
    ap.add_argument("-n", type=int, default=10)
    ap.add_argument("--freeze", help="store the picked chats as this frozen calibration sample")
    args = ap.parse_args()

    rows = from_sample(args.sample, args.run) if args.sample else from_rescored(args.ruleset)
    agree = sum(r["ai_result"] == r["human_result"] for r in rows)
    if rows:
        print(f"PASS/FAIL agreement {agree}/{len(rows)} = {100 * agree / len(rows):.1f}%")
    picked = rank(rows)[: args.n]
    for r in picked:
        flag = "" if r["ai_result"] == r["human_result"] else "  ≠"
        print(f"{r['id']}  AI {r['ai']} {r['ai_result']}  QA {r['human']} {r['human_result']}{flag}"
              f"  fails={','.join(r['fails']) or '-'}  “{r['note'][:80]}”")
    if args.freeze and picked:
        cal = CalibrationStore()
        try:
            cal.create(args.freeze, f"Worst {len(picked)} AI-QA disagreements",
                       source=f"scripts/worst_disagreements.py {args.sample or 're-scored'}")
            cal.replace_items(args.freeze, [{"conversation_id": r["id"], "seq": i + 1}
                                            for i, r in enumerate(picked)])
        finally:
            cal.close()
        print(f"Frozen as calibration sample {args.freeze!r}")


if __name__ == "__main__":
    main()
