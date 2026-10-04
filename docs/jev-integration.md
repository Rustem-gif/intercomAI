# Jev in the middle of scoring — plan and schema

Code: `src/intercom_summary/qa/jev_verifier.py` (questions, thresholds and policy all live there),
wired in `qa/grader.py`. Tests: `tests/test_jev_verifier.py`.

## Why

A v4.1 score is dominated by a handful of verdicts: one Gate 1 `fail` zeroes the chat, one Gate 2
Major `fail` caps it at 75. Those are exactly the verdicts where a single misjudgement by the
grading model costs the agent the most, and where the model has been seen to stack penalties for
one mistake (manual §10). Jev (TypeSafe's System One model) is a cheap, calibrated second opinion
on **those claims only**. It does not grade, it does no arithmetic, and it never moves a score by
itself.

## Where it sits

```
 transcript ─► code: timing / tags / CSAT headers, transcript trimming
            ─► Sonnet 5.5   (JUDGE)   Step 0 + a verdict and evidence per criterion
            ─► verdict_guard          code overturns verdicts the transcript contradicts
            ─► Jev           (CHECK)  ONE /v1/systemone request per chat       ◄── "the middle"
            ─► code policy   (DECIDE) agree → keep │ disagree → note / flag / re-ask Sonnet
            ─► score_gated()          caps, floor, deductions — pure code
            ─► grade saved, with the Jev block in payload_json["jev"]
```

Only `kb-v41` (gated) grades are checked — the definitions are written against its criteria.

## Request schema (one request per chat)

`state` — only what the questions need (Jev loses accuracy on irrelevant context):

```json
{
  "transcript": "<agent/player messages, same trimming as the grader, no header block>",
  "graded": [
    {"criterion": "resp-no-ghost", "definition": "<manual's FAIL condition>", "evidence": "<Sonnet's ev>"}
  ]
}
```

`questions` — question ids are for code only; every question refers to state by path:

| Question id | Asked when | Type | Asks | Code uses it for |
|---|---|---|---|---|
| `blacklist.<crit-id>` ×5 | always | Noul | Did an AGENT message do the §5.1 blacklisted thing? Independent of Sonnet. | `gate1_missed`, second signal on a Sonnet critical fail |
| `support.<n>` | Sonnet failed a Gate 1 or Gate 2 Major criterion | Noul | Does the transcript show the failure in `graded[n].definition`? (`graded[n].evidence` is what the grader cited) | `gate1_unsupported` / `major_unsupported` |
| `same_episode.<n>` | both halves of a double-count pair failed | Noul | Are `graded[a]` and `graded[b]` one mistake counted twice? | `double_count` |
| `rg_signal` | always | Noul | Does the player show a gambling-harm signal? | `rg_signal_missed` |
| `case_type` | always | Choice (9) | Step 0 case type | `case_type_mismatch` (note) |
| `risk_flag` | always | Choice (5) | Step 0 risk flag, RG first | `risk_flag_mismatch` (note) |

Double-count pairs are only those where counting twice **changes the score**: a Major caps flatly,
so two Majors on one mistake cost nothing extra, but a Major plus a deduction does —
`{resp-no-ghost, action-escalation-missed, financial-case-abandoned, res-no-fake-close} +
ownership-effort` (manual §7 note) and `accuracy-minor + info-completeness`.

Before any support question is trusted, **code** checks the evidence (`evidence_holds`): every
fragment the grader put in quotes must really appear in the conversation. Omission failures
("the question was never answered") may describe the gap without quoting — an absence cannot be
quoted; commission failures (Gate 1, accuracy, invented statuses) must cite real text.

## Policy (code, `qa/jev_verifier.py`)

| Finding | Fires when | Effect in `flag` mode |
|---|---|---|
| `gate1_missed` | blacklist P ≥ **0.80** and Sonnet did not fail it | manual review — never auto-zero |
| `gate1_unsupported` | Sonnet critical fail, and the quote is not real, or support P < **0.30**, or blacklist P < 0.30 | manual review — never auto-un-zero |
| `major_unsupported` | Sonnet Major fail, and the quote is not real or support P < **0.30** | manual review |
| `double_count` | same-episode P ≥ **0.70** | manual review |
| `rg_signal_missed` | rg_signal P ≥ **0.80** and Sonnet's `risk_flag` ≠ RG | manual review |
| `case_type_mismatch` / `risk_flag_mismatch` | Jev picks differently with confidence ≥ **0.70** | note only |

Thresholds are starting points to tune on our data, not constants. Model is pinned to
`jev-1.13.0` (`JEV_MODEL`) so a silent alias move cannot shift the calibration.

## Modes (`JEV_MODE`)

| Mode | What happens |
|---|---|
| `off` | Jev is not called. |
| `shadow` (**default**) | Findings are recorded on the grade and shown in the grade panel as "recorded only". Nothing else changes. |
| `flag` | Flag-worthy findings set `manual_review_needed`, with reasons `jev:<rule>(<criterion>)`. The score is untouched. |
| `reconcile` | Sonnet is asked **once** (effort `high`) to re-examine only the disputed criteria — keep a verdict it can back with a verbatim quote, change the rest. The revised verdicts are scored and Jev checks again; anything still disputed goes to manual review. The before→after changes are stored. |

A Jev error is stored as `jev.error` and never fails, skips or delays a grade beyond the call.

## Stored schema (`grades.payload_json["jev"]`, no migration)

```json
{
  "model": "jev-1.13.0",
  "mode": "shadow",
  "checked_at": "2026-10-04T14:02:11+00:00",
  "answers": {"blacklist.crit-data-care": {"type": "noul", "noul": 0.02},
              "case_type": {"type": "choice", "choice": "Bonus", "confidence": 0.97}},
  "findings": [{"rule": "double_count", "criterion": "resp-no-ghost+ownership-effort",
                "p": 0.82, "detail": "one mistake penalised twice (manual §10)"}],
  "usage": {"input_tokens": 3324, "output_tokens": 40},
  "error": "",
  "reconcile": {"disputed": [...], "changed": {"ownership-effort": ["fail", "n/a"]}}
}
```

`grade_history` archives the whole payload, so every Jev block a chat ever had is kept.

## Cost and latency

~2–5k input tokens per chat × $0.042/Mtok ≈ **$0.0001–0.0002 per chat** (output is free), one extra
HTTP request (~1 s). Sonnet itself is ~$0.013 per chat (half that in a batch run); `reconcile`
adds a second Sonnet call only on disputed chats, and it is on the grade's `usage`.

## First live run (2026-10-04, 10 chats, shadow, nothing saved)

`scripts/dry_run_grades.py -n 10 --jev shadow`:
- 7/10 chats: Jev agrees with every high-impact verdict.
- 1 chat: `double_count` on `resp-no-ghost+ownership-effort` (P 0.82) and
  `financial-case-abandoned+ownership-effort` (P 0.74) — the −6 for ownership was the same
  missed answer already capping the chat. A QA analyst had scored that chat 100.
- 1 chat: `risk_flag_mismatch` (note).
- A first version flagged omission failures as "evidence not found" because their evidence is
  a description, not a quote; fixed by `evidence_holds` before this run.

## Rollout and measurement plan

1. **Shadow** (now): every new v4.1 grade carries a Jev block. Nothing changes for anyone.
2. **Measure** after ~2 weeks of new grades, per finding type:
   - precision: of chats Jev flagged, how many did a QC manager change (override or verdict edit)?
   - recall: of chats a QC manager changed, how many had a Jev finding?
   - same for the 1,360 historically overridden chats, using the dry-run script in no-save mode
     (Sonnet ≈ $0.018/chat — ask before running a large set);
   - split by chat language: Jev is strongest in English; non-English chats need their own numbers.
3. **Tune** thresholds per finding on those numbers; drop finding types that don't predict changes.
4. **Switch to `flag`** only after the numbers are reviewed. Consider `reconcile` for
   `double_count` first — it is the one rule that can fix a score instead of queueing it.

## Known limits

- Jev reads literally; definitions are written as exact conditions with boundary cases in the
  criteria (e.g. "last 4 digits are allowed", "being upset about a delay is not a harm signal").
- Sonnet's verdicts vary run to run (the same chat scored 64 / 69 / 75 across three runs), and the
  v4.1 cap makes one flipped Major a 20-point swing. Jev narrows this only where it disagrees; the
  variance itself is a grader property to track separately.
- `accuracy-material` used to come back `cannot_determine` on most chats because no KB/T&C was
  supplied. The grader now gets the brand's Help Center (`qa/knowledge_base.py`), and Jev's support
  question for an `accuracy-material` fail gets the same KB in its state (only then — `KB_JUDGED`).
  What still fills the manual-review queue is `action-escalation-missed` without a system trace.
