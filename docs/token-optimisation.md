# Token optimisation for Claude grading

What a grade costs, what was done to cut it, and what was deliberately left alone, so the next
person doesn't have to work it out again. Rates come from the Claude pricing page (checked
2026-10-04): Sonnet 5.5 costs $2 per Mtok for input, $2.50 for a 5-minute cache write, $4 for a
1-hour cache write, $0.20 for a cache read and $10 for output. The Batch API halves every line,
cache reads and writes included.

## Where the money goes (v4.1, effort `medium`, live)

Per chat, measured on 10 real chats (`scripts/dry_run_grades.py`):

| Part | Tokens | Billed as | $ / chat |
|---|---:|---|---:|
| System prompt (the v4.1 manual) | ~5.9k | cache read | ~0.0012 |
| Transcript + header | 0.75–3.9k | input | ~0.003 |
| Output: adaptive thinking + the JSON grade | ~915 | output | ~0.009 |
| **Total** | | | **~0.013** |

In a **batch run**, the same 10 chats finished in 139 s with all 10 succeeding and no live
fallbacks. Chats that read the cache cost about $0.006 each. In a small batch, a few requests
run before the first cache entry exists and each pays the 1-hour write (~$0.012 each); here 4
of 10 did, for $0.0111 per chat overall. In a large batch that write is spread thin, so expect
about $0.0065 per chat.

Output is about 70% of the bill. Input is mostly the transcript itself, which can't be cached
because it's different for every chat.

## What was done

| Lever | Effect | Where |
|---|---|---|
| **Prompt caching.** The manual is one cached system block, identical for every chat of a ruleset. | Reads it at 0.1× the input price. Verified: 5,916 tokens read from the cache on the second call. | `qa/grader.py` `request_params` |
| **Warm-up before fan-out.** A live run grades one chat alone, then the rest in parallel. | A cache entry is readable only once the first response starts, so 10 parallel first calls would each pay for a write. Now each run pays for one write. | `service.review_and_store` `run_live` |
| **No evidence on passes (v4.1).** `ev` is `""` for pass/n/a, 25 words at most for a fail, 12 words for cannot_determine. | Output fell from 1,370 to 915 tokens per chat (−33%). Cost fell from $0.0178 to $0.0134 (−25%). On the 5 human-reviewed chats, the mean gap to the human score was 12.2, against 16.2 before. | `rules/qa_system_prompt_kb_v41.txt` (+ `qa/kbv41_prompt.py` seed) |
| **Batch option.** A Batch API run (Evaluation page checkbox, `batch=true`). | Half price for everything. Batch requests use the 1-hour cache TTL, because a batch can take longer than 5 minutes. Unusable items are re-graded live, so no chat is lost. | `qa/batch.py` |
| **Cost on every grade.** `payload_json["usage"]` records input, cache writes (5m/1h), cache reads, output, the number of calls and USD, summed over retries, the reconcile call and live fallbacks. Each run shows its total and cache-hit share. | Production cost is visible per chat and per run, which is how any future change gets measured. | `qa/pricing.py` |
| **`QA_CONCURRENCY`** (default 10, was a hard-coded 5). | About 60 chats a minute live. The ceiling is the account's rate-limit tier, and the SDK backs off on 429s. | `settings.py` |

## Deliberately not done

| Lever | Why not |
|---|---|
| 1-hour TTL for live runs | Requests in a run start seconds apart, so the 5-minute entry stays warm. Runs are hours apart, so even a 1-hour entry wouldn't survive between them, and it costs 2× to write. |
| Caching the transcript for the `reconcile` follow-up | That would add a 1.25× write on every chat to save on a call that only happens in `reconcile` mode, and rarely. |
| A separate pre-warm call | Nobody is waiting on a background run's first token, and the warm-up step already avoids the duplicate writes. |
| Trimming the transcript further | Automation is already removed and empty parts dropped. The header is about 150 tokens. What's left is the conversation being graded. |
| Moving the manual behind a tool | It's cached at $0.20/Mtok and every chat needs all of it. |
| Briefer evidence for the `default` / `vip` rulesets | Editing their prompts changes `rules_version` and marks their 8,820 existing grades stale. Do it only together with a planned re-grade. |
| A cheaper model | Evaluation is fixed on Sonnet 5.5. |

## Effort: measured, kept at `medium`

Thinking is part of the output bill, and `QA_EFFORT` controls it. On 2026-10-04, 30
human-reviewed chats were graded at `low` and at `medium`, each as one batch run ($0.40 in
total, nothing saved):

| Effort | $ / chat (batch) | Output tokens | Mean gap to human | PASS/FAIL agrees with human |
|---|---:|---:|---:|---:|
| `low` | 0.0066 | 940 | 13.3 | 18 / 30 |
| `medium` | 0.0067 | 970 | 12.0 | 19 / 30 |

At `medium`, adaptive thinking already spends very little on this task. The output is
almost entirely the JSON grade, so `low` saves about 3% while scoring slightly further from the
human. 12% of verdicts differed between the two runs, which is the same as the run-to-run
noise at a single effort level. **Keep `medium`; there's nothing to gain here.** Re-check only
after a model or prompt change, with the same commands:

    .venv/bin/python scripts/dry_run_grades.py --batch --effort low    --ids <30 reviewed chats>
    .venv/bin/python scripts/dry_run_grades.py --batch --effort medium --ids <same chats>

The same run confirmed the batch price at scale: **$0.0067 per chat** at `medium`, about 38%
of the original $0.0178 live price. Human scores were given under the old `default` ruleset,
so the gap to them is only indicative.

## Checking caching after a change

Any byte that differs per conversation before the transcript turns every cache read into a
write. `tests/test_token_optimisation.py` asserts that the system prompt, model and output
format are identical across conversations. `scripts/cache_probe.py` checks it against the real
API (about $0.03) and exits 1 if the second call reads nothing from the cache.

## A batch run whose server restarted

The batch keeps running at Anthropic and is already paid for. The failed job's error names it,
and this saves it:

    intercom-summary collect-batch msgbatch_… [--ruleset kb-v41]
