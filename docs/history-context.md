# Historical context for the grader

**Status:** test build (Phases 1–2 of the plan). Off by default (`HISTORY_CONTEXT=off`).

## The problem it solves

The grader (`claude-sonnet-5-5`, `qa/grader.py`) sees one transcript. From it alone it cannot
tell whether the player was told something different last week, whether "I escalated this" was
true, whether the promise the agent repeats was already broken, or how QA ruled the last time an
agent handled the same situation. Those are the questions behind the two biggest manual-review
drivers (`accuracy-material`, `action-escalation-missed`) and behind most run-to-run flips.

73% of chats come from players with more than one chat, so "this player's earlier chats" is
available for most grades. QA's own verdicts exist too (overrides, comments, disputes, the
Calibration tab) and were never shown to the grader.

The client's brief asked for a local model, a labelled history and a knowledge base the grader
queries. What was built:

| Brief | Built |
|---|---|
| Local model labels 6–7 months of history | Claude Haiku 5.5 labels each chat (a local LLM takes 80–140 s/chat on this Mac). A local Hugging Face embedding model was verified feasible (`multilingual-e5-small`, 200 digests in 0.5 s) and is the optional Phase 6 |
| Categories, situations, errors, correct handling | `chat_labels`: case type, subtype, requests, agent actions, promises, escalation, outcome, errors, correct handling, player state, keywords, a ≤60-word English digest |
| Searchable KB the grader queries | SQLite FTS5 (`chat_labels_fts`) + structured filters, in `data/grades.db`. Retrieval is pre-computed per chat and put in the prompt (the Batch API has no tool loop) |
| Biweekly refresh | `intercom-summary history refresh` (Phase 4 adds the in-app job and the 14-day trigger) |
| Before/after on human-confirmed data | Two calibration runs differing only in `history_context`; **needs QA verdicts, of which there are none yet** |

## What the grader gets

A block in the user turn, in front of the transcript (`Grader.messages_for`), so the two cached
system blocks (rules, Help Center) stay byte-identical — `tests/test_token_optimisation.py` and
`tests/test_history_context.py` check that.

```
=== HISTORICAL CONTEXT (reference only — not evidence) ===
Rules: verdicts must still be proven by AGENT lines in THIS transcript. Use the player's earlier
chats to judge consistency with what they were told before and promises made. Use QA precedents
to calibrate severity the way QA does; never copy their verdicts.
This player's earlier chats (newest first):
- 2026-10-01 · Withdrawal / withdrawal pending · Asked again about the withdrawal; escalated. · outcome: Pending-legitimate (AI) · QA 75 (AI 100): "fine"
- 2026-09-20 · Withdrawal / withdrawal pending · Withdrawal pending; agent promised 1-3 days. · promised: 1-3 business days · outcome: Pending-legitimate (AI)
QA precedents (similar cases, QA-confirmed):
- Withdrawal / withdrawal pending · Withdrawal delayed, bank transfer pending; agent gave timeframe. · AI-labelled issues: did not confirm escalation · QA 75 (AI 100): "escalation not confirmed in a note" · criteria: action-escalation-missed=fail
=== END HISTORICAL CONTEXT ===
```

- **The player's earlier chats**: up to `HISTORY_MAX_PRIOR` (5) within `HISTORY_DAYS` (180),
  through their labels. The AI outcome is marked `(AI)`; a QA verdict is shown when one exists.
- **Precedents**: up to `HISTORY_MAX_PRECEDENTS` (3) similar chats of the same brand that carry a
  **human** verdict (`case_verdicts`). AI-graded chats are never shown as precedents, or the
  grader would be fed its own earlier verdicts.
- ≤ ~900 tokens; precedents are dropped first, then the player's oldest chats.
- No line starts with `AGENT` or `CUSTOMER` and no agent is quoted verbatim, so nothing in the
  block can be cited as evidence.

### Bounds the evaluation relies on
- A chat never sees itself, and never anything created at or after its own date (`as_of`).
  A re-grade and a calibration run read the history that existed when the chat happened.
- The target's own verdicts are never shown.

### Modes (`HISTORY_CONTEXT`, or per run)
- `off` — nothing is retrieved (default).
- `shadow` — retrieved and recorded on the grade, nothing is sent. Lets "what would it have
  seen" be answered for a grade made without it.
- `on` — sent.

Every grade records what it was given in `payload_json["history_context"]`: mode, the index
snapshot (`index_version`), the ids used, the block, where the chat's own label came from
(`cached` / `haiku` / `fallback`) and a token estimate.

## The index

| Table | What |
|---|---|
| `chat_labels` | One label per chat, `label_version`-stamped (`qa/history_labeller.LABEL_VERSION` hashes the prompt + schema; a change re-labels on the next refresh). `source` is `cache` (the conversations table) or `archive` (raw client exports under `EXPORT_DIR`, labelled but **never imported** into `conversations`) |
| `chat_labels_fts` | FTS5 over digest + requests + keywords + subtype + errors; bm25 ranking |
| `case_verdicts` | Every human verdict in one shape, rebuilt on each refresh from `grades` (overrides), `calibration_results`, `conversation_comments`, `grade_disputes` (accepted/rejected) |
| `conversations.contact_id` | Mirrored from the payload with an index — the player lookup |
| `meta` | `history.last_refresh_at`, `history.index_version` |

Commands:
```
intercom-summary history label --source cache|archive|all [--since YYYY-MM-DD] [--limit N] [--batch|--live] [--relabel]
intercom-summary history refresh        # label new cached chats, regather verdicts, rebuild the index
intercom-summary history stats
intercom-summary history collect-batch msgbatch_…   # save the labels of a batch whose process died mid-wait
```
Above 200 chats labelling goes through the Message Batches API (half price). Measured:
**≈ $0.0006 a chat live, ≈ $0.0003 in a batch** — the whole 20k-chat backlog is about $6–12.

Extending history before the cache (2026-07-06): fetch raw payloads with
`scripts/export_client_archive.py --since 2026-04-01 --until 2026-05-27 --only-fetch`, then
`history label --source archive --batch`.

## Running the A/B

Two calibration runs on the frozen sample, same rules/model/effort, one toggle:
```
intercom-summary calibrate kb-v41-180 --batch --history off
intercom-summary calibrate kb-v41-180 --batch --history on
```
or from the Calibration tab (`POST /api/calibration/samples/{id}/runs {"history_context":"on"}`).
Each run records `history_context` and `history_index_version`. On the human-reviewed live
chats instead, without saving:
```
scripts/dry_run_grades.py --only-overridden --ruleset kb-v41 -n 144 --batch --history on
```
Or, on the human-re-scored live chats, both arms in one go with the comparison printed and the
per-chat record saved to `EVAL_DIR/history_ab_<time>.json`:
```
scripts/history_ab.py -n 144 --ruleset kb-v41            # arms off,on · batch · nothing saved to grades
scripts/history_ab.py --arms off,on --effort medium      # the cost question: does context let medium match high?
```
Metrics: agreement with human verdicts where they exist, manual-review rate, severe flips
between paired runs, evidence grounding (every FAIL quote found in the transcript), cost/chat.
**Blocker:** the calibration sample has 0 human verdicts; QA must record them, at least on the
chats the two runs disagree on.

## Where things are
- `qa/history_labeller.py` — the label prompt, schema, parsing, live and batch labelling.
- `qa/history_context.py` — `HistoryProvider` (retrieval, rendering, budget), `HistoryContext`.
- `storage/history_store.py` — tables, FTS, verdict gathering, retrieval queries.
- `intercom/archive.py` — reading the raw client exports.
- `service.label_history` / `refresh_history` / `history_stats`; `review_and_store` and
  `run_calibration` take `history_context=` and `effort=`.
- Tests: `tests/test_history_store.py`, `tests/test_history_labeller.py`,
  `tests/test_history_context.py`. `tests/conftest.py` keeps the history off unless a test
  injects a provider.
