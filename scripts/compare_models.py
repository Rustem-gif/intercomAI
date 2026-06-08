"""One-off: compare qwen2.5:7b vs the stored qwen2.5:14b grades on a sample.

Regrades a handful of already-graded conversations with the 7b model and prints a
side-by-side of overall score, PASS/FAIL, per-dimension scores, and summary so we can
judge whether 7b is "good enough" to swap in.
"""
from __future__ import annotations

import json
import sqlite3
import time

from intercom_summary.storage.conversations_store import ConversationsStore
from intercom_summary.qa.ollama_grader import OllamaGrader

SAMPLE = 8
SMALL_MODEL = "qwen2.5:7b"


def stored_14b() -> list[dict]:
    c = sqlite3.connect("data/grades.db")
    rows = c.execute(
        "select conversation_id, overall_score, summary, payload_json "
        "from grades order by graded_at desc limit ?",
        (SAMPLE,),
    ).fetchall()
    c.close()
    out = []
    for cid, score, summary, payload in rows:
        p = json.loads(payload) if payload else {}
        out.append({
            "id": cid,
            "score": score,
            "result": p.get("overall_result", ""),
            "summary": summary or "",
            "scorecard": p.get("scorecard_raw", {}),
        })
    return out


def dim_scores(scorecard: dict) -> dict:
    return {k: v.get("score") for k, v in (scorecard or {}).items() if isinstance(v, dict)}


def main() -> None:
    convos = ConversationsStore()
    grader = OllamaGrader(model=SMALL_MODEL)
    base = stored_14b()
    print(f"Comparing {len(base)} conversations: qwen2.5:14b (stored) vs {SMALL_MODEL}\n")

    deltas = []
    times = []
    for row in base:
        cid = row["id"]
        convo = convos.get(cid)
        if convo is None:
            print(f"{cid}: (conversation not in store, skipping)")
            continue
        t0 = time.time()
        try:
            g = grader.grade(convo)
        except Exception as exc:
            print(f"{cid}: 7b grading FAILED: {exc}\n")
            continue
        dt = time.time() - t0
        times.append(dt)
        delta = g.overall_score - row["score"]
        deltas.append(delta)

        print(f"── {cid}  ({dt:.0f}s) ─────────────────────────────")
        print(f"  14b: {row['score']:>3}/100 {row['result']:<4}   "
              f"7b: {g.overall_score:>3}/100 {g.overall_result:<4}   Δ={delta:+d}")
        d14, d7 = dim_scores(row["scorecard"]), dim_scores(g.scorecard_raw)
        for dim in sorted(set(d14) | set(d7)):
            print(f"     {dim:<28} 14b={str(d14.get(dim)):<4} 7b={str(d7.get(dim)):<4}")
        print(f"  14b summary: {row['summary'][:200]}")
        print(f"   7b summary: {g.summary[:200]}\n")

    convos.close()
    if deltas:
        import statistics
        absd = [abs(x) for x in deltas]
        print("════════ SUMMARY ════════")
        print(f"  n={len(deltas)}   mean Δ={statistics.mean(deltas):+.1f}   "
              f"mean |Δ|={statistics.mean(absd):.1f}   max |Δ|={max(absd)}")
        print(f"  7b time: mean={statistics.mean(times):.0f}s  min={min(times):.0f}s  max={max(times):.0f}s")


if __name__ == "__main__":
    main()
