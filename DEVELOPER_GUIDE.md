# Developer Guide — How to Change Things

A plain-language map of this project so you can find **what to edit** and **how to ship it**,
without reading all the code. Read the first two sections once; use the rest as a lookup table.

---

## 1. The 60-second mental model

The product does four things, in this order:

```
   Intercom  ──fetch──►  SQLite cache  ──grade──►  grades  ──show──►  Web dashboard
  (support                (data/grades.db)        (Claude Sonnet     (+ Slack bot)
   chats)                                          5.5 via API)
```

1. **Fetch** support conversations from Intercom and store them locally.
2. **Grade** each conversation against your rulebook using Claude (Sonnet 5.5, via the Claude API).
3. **Show** the results in a web dashboard (and a Slack bot).
4. Humans can **override** AI scores, leave comments, run coaching, etc.

Everything lives under `src/intercom_summary/`. There are **three "front doors"** into the same
shared logic:

| Front door | Entry file | What it is |
|---|---|---|
| Web dashboard | `web/api.py` | The website you use day-to-day (FastAPI backend + React frontend) |
| Slack bot | `slack/app.py` | The Slack interface |
| Command line | `cli.py` | Terminal commands for fetch/review/export |

All three call into **`service.py`**, which holds the real work (`fetch_and_store`,
`review_and_store`, `build_overview`). **If you change business logic, you usually change
`service.py` — not three places.**

---

## 2. Where everything lives

```
intercomSummary/
├── .env                      ← SECRETS & SETTINGS (tokens, model choice, ports). EDIT THIS for config.
├── restart.sh                ← Restart the live website after a change.
├── rules/
│   ├── support_rules.md      ← The QA rulebook (what counts as good/bad support). Editable in the UI too.
│   └── qa_system_prompt.txt  ← The AI grader's instructions/persona.
├── config/
│   ├── web_users.yaml        ← Who can log into the website + their role (admin/analyst/viewer).
│   │                           Gitignored (holds password hashes) — see web_users.example.yaml.
│   └── roles.yaml            ← Who can use the Slack bot.
├── data/
│   ├── grades.db             ← THE DATABASE (conversations, grades, jobs, comments — everything).
│   └── backups/              ← Automatic DB backups.
├── scripts/                  ← Helper shell scripts (run app, restart Ollama, backups, etc.).
└── src/intercom_summary/
    ├── settings.py           ← Reads .env into one `settings` object used everywhere.
    ├── service.py            ← THE BRAIN. Shared fetch/grade/overview logic.
    ├── cli.py                ← Terminal commands.
    │
    ├── intercom/             ← Talking to Intercom (downloading chats).
    │   ├── client.py         ← Raw API calls.
    │   ├── fetch.py          ← Pulling conversations.
    │   ├── archive.py        ← Reading the raw client exports (older history, never imported).
    │   └── models.py         ← The shape of a Conversation/Message/Admin in our code.
    │
    ├── qa/                   ← The AI grading engine.
    │   ├── backends.py       ← Chooses which grader to use (Claude API vs Qwen).
    │   ├── grader.py         ← Grades using the Claude API (the default — Sonnet 5.5).
    │   ├── ollama_grader.py  ← Grades using local Qwen (optional; slow on this Mac).
    │   ├── grading_common.py ← What both graders share: transcript trimming, validity check.
    │   ├── casino_prompt.py  ← The scoring rubric + JSON schema the AI must fill in. ⚠️ delicate.
    │   ├── schema.py          ← Turns the AI's answer into a score (does the math itself).
    │   ├── verdict_guard.py  ← Overturns greeting/name verdicts the transcript contradicts.
    │   ├── gated_rules.py    ← v4.1: turns detected problems / requests / notes into verdicts by rule.
    │   ├── history_labeller.py ← Haiku labels a chat for the history index (digest, errors, outcome…).
    │   ├── history_context.py ← The player's earlier chats + QA precedents, put in front of the transcript.
    │   ├── agent.py / chat.py ← The "Ask Qwen" chat assistant in the UI.
    │   └── rules.py          ← Loads/versions the rulebook.
    │
    ├── storage/              ← Reading/writing the database. One file per table.
    │   ├── conversations_store.py
    │   ├── grades_store.py    ← Grades + human overrides.
    │   ├── jobs_store.py      ← Background jobs (fetch/review progress).
    │   ├── history_store.py   ← Chat labels, their text index, every human verdict in one table.
    │   └── …                  ← comments, coaching, iconic cases, agent links, etc.
    │
    ├── export/               ← XLSX + transcript file generation.
    │
    ├── slack/                ← The Slack bot (app, handlers, button/block UI, auth).
    │
    └── web/
        ├── api.py            ← ALL backend website endpoints (the /api/... URLs).
        ├── auth.py           ← Login + role checks (require_write / require_admin).
        ├── schemas.py        ← The shape of data sent between frontend and backend.
        └── frontend/         ← The React website (what the browser shows).
            ├── src/pages/    ← One file per screen (Overview, Conversations, Evaluation…).
            ├── src/components/← Reusable pieces (the conversation drawer, grade panel, chat button…).
            └── src/lib/api.ts ← The frontend's list of backend calls + data types.
```

### The website screens (pages) and their files

| Screen (URL) | File | What it shows |
|---|---|---|
| Overview (`/`) | `pages/Overview.tsx` | KPIs, charts, leaderboard |
| Support chats (`/conversations`) | `pages/Conversations.tsx` | Browse/search the support chats (VIP emails are on their own tab) |
| Needs Attention (`/needs-attention`) | `pages/NeedsAttention.tsx` | The QC review queue: notes, AI-flagged, FAIL, low CSAT (`qa/review_queue.py`) |
| Agents (`/agents`) | `pages/Agents.tsx` | Per-agent performance |
| Evaluation (`/evaluation`) | `pages/Evaluation.tsx` | Run grading, see progress (Ollama restart only when Qwen is picked) |
| Calibration (`/calibration`) — **temporary**; back in the menu 2026-10-21 for the VIP sample (hidden 2026-10-16 – 10-21) | `pages/Calibration.tsx` | Claude's grades of a frozen sample next to the QA managers' own verdicts: the 180 support chats (v4.1) and the 70 VIP chats (`vip-v16`, graded per case); the newest sample opens first, a selector switches |
| VIP emails (`/vip`) | `pages/Vip.tsx` | The VIP managers' email threads (Threads), the cases they group into with their VIP Scorecard grades (Cases), and the Exception Report (Exceptions) |
| AI Accuracy (`/accuracy`) | `pages/Accuracy.tsx` | AI-vs-human override stats |
| Knowledge Base (`/knowledge-base`) | `pages/KnowledgeBase.tsx` | Reference content |
| Coaching (`/coaching`) | `pages/Coaching.tsx` | Coaching sessions |
| Ruleset (`/ruleset`) | `pages/Ruleset.tsx` | Edit the rulebook in-browser |
| Storage (`/storage`) | `pages/Storage.tsx` | Admin only: disk usage, Trash size, compact DB |
| Login | `pages/Login.tsx` | Sign-in |
| Agent review link | `pages/AgentReview.tsx` | Public token page for agents |

Screens are wired to URLs in `frontend/src/App.tsx`, and the left-hand menu is in
`frontend/src/components/AppShell.tsx`.

---

## 3. The golden workflow (read before touching anything)

You almost never edit the running server directly. The loop is:

1. **Make a branch first** — never edit on `main`:
   ```bash
   git checkout -b fix/describe-the-change
   ```
2. **Edit the files.**
3. **If you changed the frontend** (anything under `frontend/`), rebuild it:
   ```bash
   cd src/intercom_summary/web/frontend && npm run build
   ```
4. **Apply it to the live site:**
   ```bash
   ./restart.sh
   ```
5. **Check it works**, then commit, push, open a PR, and merge.

> **Two rules that bite people here:**
> - Frontend changes do **nothing** until you run `npm run build` — the server serves the
>   pre-built files in `frontend/dist/`, not your raw edits.
> - Backend (Python) changes do **nothing** until you run `./restart.sh` — and it is worse than
>   "nothing". Imports here are lazy (inside the function that needs them), so a running server
>   keeps the old version of every module it has already imported, while reading *new* code off
>   disk for any module it hasn't. That mix can fail in ways neither version would: after the
>   v4.1 change, a four-day-old server had the old `qa/rulesets.py` cached from ordinary page
>   hits but loaded the new `qa/ollama_grader.py` on the first review, and every review died on
>   `cannot import name 'output_schema_for'` until it was restarted. If something fails in a way
>   that makes no sense against the code in front of you, check the server's start time first:
>   `ps -eo pid,lstart,command | grep intercom-web`.

### Faster while developing (live reload, no rebuild needed)

```bash
./scripts/dev.sh
```
This runs the backend on `http://127.0.0.1:8000` and the frontend on `http://localhost:5173`
with **instant reload** as you type. Use this to experiment; use the build+restart flow above to
ship for real.

---

## 4. "I want to change ___" — the cookbook

### Change the scoring rules (what good/bad support means)
- **Easiest:** open the website → **Ruleset** page → edit → save. No code, no restart.
- **In files:** `rules/support_rules.md`.
- Editing the rules creates a new "rules version". It applies **only to chats graded from then on**:
  existing grades keep their scores and are **never re-graded** (client instruction, 2026-10-20 —
  `GradesStore.is_current`).

### Change the AI grader's behavior / scoring weights
- The rubric, the per-criterion JSON the AI fills in, and the scoring math live in
  `qa/casino_prompt.py` and `qa/schema.py`. **These are delicate** — small changes can break
  grading (the model gets confused or scores everything wrong). Change carefully and re-test on a
  few conversations before a big run.
- The grader's persona/instructions: `rules/qa_system_prompt.txt` — this is the **standard**
  ruleset, used for everyone who isn't in the VIP group (also editable in the UI by an admin).
  VIP agents follow `rules/qa_system_prompt_vip.txt` instead — see *"Change what the VIP ruleset
  checks"* below.

### Put an agent in the VIP group (grade them by different rules)
- **Easiest:** website → **Agents** page → click the **Standard / VIP** button next to an agent.
  Admin only. From then on, every conversation assigned to that agent — chat *and* email — is
  graded against the **VIP ruleset** instead of the standard one.
- **Their old grades are left alone.** They were graded correctly against the standard rules at the
  time, so nothing is re-graded automatically. The Evaluation page shows them as *"graded with a
  different ruleset"*, and they stay as they are — old chats are never re-graded.
- The **group switcher** in the top bar (All / Standard / VIP) scopes every page. VIP and standard
  scores come from different criteria, so they are never averaged together — don't compare them.

### Change what the VIP ruleset checks (or add another ruleset)
A **ruleset** = a system prompt (what the grading model follows) + a criteria catalogue (ids, titles, points).
- Prompt text: **Ruleset** page → *VIP* tab (admin), or `rules/qa_system_prompt_vip.txt`.
- Criteria + points: `config/rulesets.yaml`.
- ⚠️ The points live in **both** places: the `Ded` column inside the prompt (what the AI applies
  while grading) and `config/rulesets.yaml` (what a manual re-score applies). They must agree — the
  Ruleset page shows an amber warning if they drift.
- To add a ruleset: add an entry to `config/rulesets.yaml` (name, `prompt_path`, `scoring`,
  criteria), write the prompt file, and — only if a whole agent *group* should use it —
  add the group in `qa/rulesets.py` (`GROUP_RULESETS`). Everything else — grading, staleness,
  the UI tabs — picks it up automatically.
- Editing a ruleset's prompt changes how **new** chats are graded. Existing grades are never
  re-graded.

### The `kb-v41` ruleset and gated scoring (QA Manual v4.1)
The third ruleset does not score like the other two, and the difference is structural rather than
a matter of different numbers.

`default` and `vip` are **flat**: every criterion is a subtraction, `score = 100 − Σ deductions`.
`kb-v41` is **gated**, because the manual's whole point is an ordering a sum cannot express — a
failed outcome must always cost more than any amount of process untidiness, and a compliance
incident must always cost more than a failed outcome.

| Gate | What it asks | What a failure does |
|---|---|---|
| 1 (`crit-*`) | Did something genuinely dangerous happen? | score **0**, evaluation stops |
| 2, severity `major` | Did the player get the outcome they needed? | caps the score at **75**, flat — one Major costs exactly what three do |
| 2, severity `minor` | Was the answer complete and accurate? | ordinary deduction, no cap |
| 3, no group | SLA, tag, ownership | ordinary deduction |
| 3, `group: communication` | Tone, etiquette, language | deducted, but the three together are capped at −5 |

Two consequences that surprise people:
- **Without a Major the score never drops below 76.** Otherwise a chat that served the player but
  collected every small penalty could score below a chat that failed the player outright.
- **A score of 25 is not a score of 0.** Failing the outcome *and* every process check *and*
  maxing the communication penalty is `catastrophic_service_failure` — a service collapse.
  A compliance/RG breach is `critical_fail` — score 0. They look equally bad and mean completely
  different things, so they are separate fields and stay separately filterable. A *partial*
  combination scores normally: Major + two of three process fails + max style = `75−3−6−5 = 61`.

Where things live:
- Formula: `qa/gated_scoring.py`. Tests that pin every branch: `tests/test_gated_score.py`.
  Which formula a ruleset uses is its own `scoring.model` declaration, dispatched in `qa/schema.py`.
- The numbers the manual leaves to the QC manager (`pass_threshold` 85, `major_cap` 75,
  `no_major_floor` 76, `catastrophic_score` 25, the communication `group_caps`) are in the
  `scoring:` block of `config/rulesets.yaml` and are rendered on the **Ruleset** page, so they can
  be recalibrated without a code change.
- Prompt: `rules/qa_system_prompt_kb_v41.txt` (editable), seeded from `qa/kbv41_prompt.py`.

**Four verdicts, not three.** `pass` / `fail` / `n/a` / `cannot_determine`. The last one is a real
answer, not a missing one: `n/a` means there was nothing to check, `cannot_determine` means there
was something to check and the evidence for it (a chat tag, a CRM escalation record, a transaction
status) is not in the data. Only `cannot_determine` sets `manual_review_needed` and sends the chat
to a QC manager, so collapsing the two buries the question instead of asking it. Both cost zero
points. `verdict_guard` enforces this for `tag-chat`, which has no honest `n/a`.

**The result is a rule, and so are the verdicts it reads (client calibration, 2026-10-08).**
The model used to decide pass/fail per criterion on its own, and two runs on the same chats
disagreed — Excellent in one, Major in the other — while some grades described a problem and
still scored 100. The chain is now *detected problem → fail → severity (the criterion's gate) →
score → result*:
- The model writes `problems` (`[{id, ev}]`) **before** any verdict, and a neutral
  `what_happened` **last** (no verdicts, no "no defects found"). `violations` is not asked for;
  `qa/schema.py` builds it, and `summary` = the verdict line built from the *final* verdicts
  (e.g. `FAIL · Major: Request Coverage · Minor: Response SLA.`) + `what_happened`. So the summary
  can never say "no defects" on a FAIL (QA, 2026-10-09). Undetermined criteria are not in the
  verdict line — on a PASS they read as defects; they are in `manual_review_reason`.
- **Who stopped responding** (QA, 2026-10-09). `Conversation.ending` (`intercom/models.py`) reads
  the comment order — notes, bots and bookkeeping parts excluded — and `qa/prompt.py:_ending_line`
  states it in the grader's header (`Ending: the player's last 2 message(s) … got no agent reply`
  / `… the agent wrote 3 message(s), all inactivity/closing prompts` / …). The agents' macros are
  matched by `is_agent_macro`; add a macro there if a new one appears. A request the player left
  waiting on is `pending` (new request status), never `unresolved`.
- `qa/gated_rules.py` (`check_verdicts`, run by both graders before scoring) applies, in order:
  a. every listed problem → `fail` on its criterion (then `verdict_guard` still drops bot/player quotes);
  b. an `unresolved` request, by who wrote last: the **player** (their last messages never
     answered, not just a "thanks") → `resp-no-ghost` fail, material or not; the **agent** (the
     player went quiet) and nothing failed that explains it → the request is re-read as `pending`
     and the verdicts are left alone;
  c. outcome Unresolved/Abandoned with no Major → `resp-no-ghost` cannot_determine (or
     `res-no-fake-close` fail when the agent closed it);
  d. an escalation the model says happened (outcome Escalated correctly, a request `escalated`, or
     a pass with evidence) with **no internal note** → `action-escalation-missed` cannot_determine
     — the agent's words are not a trace;
  f. an escalation that was **required** (`expected_handling` = Internal escalation, decided against
     the prompt's MANDATORY ESCALATION list) and neither performed nor claimed, with no note →
     `action-escalation-missed` **fail**, citing `escalation_case`;
  e. a pass on `action-escalation-missed` with no `ev`: cites the chat's internal note if there is
     one, else cannot_determine.
  Every change is a `rule:` flag in `signal_flags`. Tests: `tests/test_gated_rules.py`.
- Result (`score_gated`): Critical → **FAIL 0**; any Major → **FAIL**, `major_outcome_failure`,
  capped 75, whatever `pass_threshold` says; otherwise PASS at ≥ 85. `cannot_determine` costs
  nothing and does not block a PASS; it (or Insufficient data) only sets `manual_review_needed`.
- **`churn-rg-signal-ignored` — explicit words only (QA, 2026-10-09).** A churn signal is a plain
  "I'm leaving / close my account / I'll play elsewhere" (or an RG signal). "I never win", "the bonus
  gave nothing", "my account is jinxed" are NOT churn: a cold reply to them is comm-tone at most.
  This reverses the 10-08 widening to "open dissatisfaction", which failed ordinary complaints.
  Answering the main request is still not a reaction to an explicit statement. A fail is a Major
  and always sets `manual_review_needed` (`MANUAL_REVIEW_ON_FAIL` in `qa/gated_rules.py`).
- **MANDATORY ESCALATION (QA, 2026-10-09).** The prompt carries the list of cases the agent must
  pass on (VIP/personal manager asked for, deposit not credited after proof, …); the model used to
  decide on its own that no escalation was needed. QA owns the list — edit it in
  `rules/qa_system_prompt_kb_v41.txt` (Ruleset page) **and** in `KBV41_QA_SYSTEM_PROMPT`;
  `tests/test_kbv41_ruleset.py` fails if the two differ.
- **Withdrawn the same day (client points 4 and 6).** The first cut also had a `CANNOT DETERMINE`
  result (any cannot_determine or Insufficient data → never PASS), required every
  `accuracy-*`/`financial-case-abandoned`/`severe-business-trust` pass to cite a source, made
  accuracy cannot_determine with no KB, and made the agent's statements about *this player's
  account* (admin data) cannot_determine. On the 180-chat sample that put ~85% of chats in CANNOT
  DETERMINE (runs `7c39f98ace17`, `95a0886c9af4`), and QA asked for exactly those points to go.
  Don't reintroduce them without a fresh calibration. Old runs still store `CANNOT DETERMINE`, so
  the UI badge and the calibration counts keep handling it.
- Consistency check after any grader change: run the sample twice and
  `python scripts/compare_calibration_runs.py RUN_A RUN_B [--list]`.

**v4.1 is live for standard chats — by date.** `GROUP_RULESETS[GROUP_STANDARD]` is `kb-v41`, but
only for conversations **created on or after `QA_V41_EFFECTIVE_FROM`** (`.env`, a `YYYY-MM-DD`
date, judged in UTC). Older standard chats keep `default`. That is manual §9 ("a historical chat is
never graded automatically under a newer version of the rules"), and it is also what stops the
switch from re-grading the whole backlog: their existing `default` grades stay current. VIP is not
part of the cutover. The rule lives in `_in_effect()` / `PREVIOUS_RULESET` in `qa/rulesets.py`;
every resolver call passes the chat's `created_at`.
- To move the cutover: change `QA_V41_EFFECTIVE_FROM` and `./restart.sh`. Moving it *earlier* makes
  the chats in between pending under `kb-v41`, so the next review grades them (≈ $0.02 each).
- **QC's answers to the contradictions (21 Oct 2026)** are in the prompt and in
  `docs/qa-contradictions.md` (each point has its answer and the rule it became): closure
  requests by reason and segment, VIP manager requests, the GDPR carve-out (bet / transaction
  history → email is a pass), duplicate-account bonus refusals (cannot_determine — BO only),
  "NEVER coming back" is churn, the check-in after a resolved issue (comm-etiquette, not a
  Major), scam chats → manual review. Several of them read the **Player segment** header line
  (see "The player's VIP segment" below).
- **Forced manual review on FAIL:** `MANUAL_REVIEW_ON_FAIL` in `qa/gated_rules.py` — churn,
  action-escalation-missed and financial-case-abandoned (QC: "better an extra FAIL to check than
  a missed escalation"); the reason text per criterion is `REVIEW_REASON`.
- **The mandatory yellow note, by date:** `QA_ESCALATION_NOTE_FROM` (`.env`, `YYYY-MM-DD`, empty
  = off). From that chat date, an escalation told to the player with no internal note is a FAIL
  (and goes to manual review) instead of cannot_determine — `escalation_note_mandatory()` in
  `qa/gated_rules.py`. QA confirmed 1 November (2026-10-28), so prod runs with
  `QA_ESCALATION_NOTE_FROM=2026-11-01`. Older chats are untouched either way.
- **A late reply with nothing cited is a fail:** rule g in `derive_after_guards`. When the TIMING
  header says the agent's first reply or a later reply was BREACHED and `resp-sla` came back
  pass / n/a with no evidence, it becomes a fail citing the times. A pass that quotes the delay
  message or names the reason stays. The later-reply figure is
  `Conversation.longest_followup_seconds` (player's line → the agent's next visible line, notes and
  bots skipped); the targets are `SLA_FIRST_RESPONSE_SEC` / `SLA_FOLLOWUP_SEC`. The fail is marked
  `src: timing` and the fail verifier does not re-read it.
- **EMPTY and "no request" are tags:** `PLACEHOLDER_TAGS` in `qa/prompt.py` adds a note to the
  Chat tags line, and a `tag-chat` fail that only says "no tag" on a tagged chat becomes
  `cannot_determine` (`_says_untagged` in `qa/verdict_guard.py`).
- **The review queue (Needs Attention):** `qa/review_queue.py` lists why a chat needs a QC
  manager: an internal note (QA, until agents are used to notes), the AI's own manual review, a
  FAIL or critical fail, a CSAT of 1–2. `/api/conversations?review=true` returns those rows with
  `review_reasons`; a chat leaves once a person scores or confirms its grade. The note reason is
  computed on read, so grades made before the rule are in the queue without a re-grade.
- **Forcing a run onto one ruleset** still overrides all of this, for pilots and calibration:
  `POST /api/review {"ruleset_id": "kb-v41", "sample": "kb-v41-180"}`. Grades are stored under the
  ruleset that produced them, so a pilot cannot disturb anything graded by another ruleset.

### The knowledge base behind v4.1 accuracy (Help Center)
- The manual judges `accuracy-material` / `accuracy-minor` against "approved KB/T&C" and says
  `cannot_determine` without it — and every `cannot_determine` sends a chat to manual review. Before
  the KB, that was ~2 in 3 chats. The brand's **published Intercom Help Center** is that KB: the
  v4.1 grader gets it as a second cached system block (`qa/knowledge_base.py`), per brand
  (`BRAND_HELP_CENTERS`: Betncare/King Billy → help center 248; Tomb Riches → 8189, no articles yet,
  so its chats are graded without a KB and keep the old rule).
- Snapshots: `data/kb/<brand>.md`, refreshed when older than `KB_MAX_AGE_HOURS` (24) or by
  `intercom-summary sync-kb`. A failed refresh keeps the previous snapshot. The text has no dates,
  so it only changes — and only re-writes the prompt cache — when an article does. Every grade
  records the snapshot it used: `kb_version` (`Betncare:<hash>`). `KB_ENABLED=0` switches it off.
- The rule (prompt section `## ACCURACY`): a general rule/fact the agent states is checked against
  the KB and the transcript (fail if contradicted, pass if it agrees, n/a if no such statement);
  what the agent reports about *this player's own account* is not an accuracy question; and
  `cannot_determine` is only for a decisive rule the KB does not cover. Measured on 30 reviewed chats:
  `accuracy-material` undetermined 20 → 6, chats sent to manual review 29 → 22.
- Cost: ~15k more cached tokens per request (+~$0.004/chat live, +~$0.0025 batch). Batch runs grade
  one chat per brand live first (`batch.warm_up_set`) so the batch reads the cache instead of every
  request paying to write it.
- What still fills manual review: `action-escalation-missed` / `ownership-effort` — the manual
  requires a system trace (an internal note or CRM record) and most chats have neither.

### Historical context for the grader (the player's earlier chats and QA precedents)
The grader sees one transcript; this gives it what the transcript cannot show — what the same
player was told before, promises made, whether "I escalated" was true, and how QA ruled on
similar cases. Full write-up: `docs/history-context.md`. Off by default.
- **Switch:** `HISTORY_CONTEXT` in `.env`: `off` · `shadow` (retrieve and record on the grade,
  send nothing) · `on`. Per run: `calibrate --history on`, the Calibration tab, or
  `/api/review {"history_context": "on"}`. Every grade records what it got in
  `payload_json["history_context"]`.
- **The index:** `chat_labels` (one Haiku label per chat, ≈$0.0006 live / $0.0003 batch),
  `chat_labels_fts` (SQLite full-text), `case_verdicts` (every human verdict, rebuilt on refresh).
  Build it: `intercom-summary history label --source all --batch`; keep it fresh:
  `intercom-summary history refresh`; look at it: `intercom-summary history stats`.
- **Older history** lives in the raw client exports under `data/exports/*/raw/` and is labelled
  from there (`--source archive`) — deliberately **not** imported into `conversations`, which
  would put thousands of old ungraded chats on every dashboard and in the next grading run.
  Fetch more with `scripts/export_client_archive.py --since … --until … --only-fetch`.
- **Where it goes in the prompt:** the user turn, in front of the transcript (`Grader.messages_for`),
  so the cached rules + Help Center blocks never change. Precedents are human-confirmed only;
  a chat never sees itself or anything after its own date.
- **A/B:** two calibration runs differing only in `--history`; compare in the tab. Needs QA
  verdicts on the sample (none recorded as of 2026-10-19).
- **Changing the label** (`qa/history_labeller.py` prompt or schema) changes `LABEL_VERSION`;
  the next `history refresh`/`label` re-labels everything, so do it deliberately.
- Tests run offline: `tests/conftest.py` keeps it off unless a test injects a `HistoryProvider`.

### Jev — a second opinion on the verdicts that matter most
After Sonnet grades a v4.1 chat and before the score is computed, **Jev** (TypeSafe) checks the
claims that move a score the most: the Gate 1 blacklist (independently of Sonnet), whether each
Gate 1 / Gate 2 Major `fail` is really shown by the transcript, whether a Major and a deduction
are one mistake counted twice (manual §10), and Step 0. One request per chat, ~$0.0001.
- Code: `qa/jev_verifier.py` — questions, thresholds and the policy are all there. Full plan,
  request/stored schema and measurement plan: `docs/jev-integration.md`.
- `JEV_MODE` in `.env`: `off` · `shadow` (default — record only) · `flag` (disagreements set
  `manual_review_needed`) · `reconcile` (Sonnet re-examines the disputed criteria once first).
  `JEV_API_KEY` must be set; `JEV_MODEL` is pinned to `jev-1.13.0`.
- Jev never changes a score by itself, and a Jev error never fails a grade (`jev.error`).
- Findings are stored in `payload_json["jev"]` and shown in the grade panel as "Jev check".
- Try it on live chats without saving: `scripts/dry_run_grades.py -n 10 --jev shadow`.
- Tests run offline: `tests/conftest.py` switches Jev off unless a test injects a fake.

### VIP emails tab (the VIP managers' email threads)
The VIP managers (Alisa, Jasmine, Eric, Taylor, Victoria, Walter, Sam) are not tracked agents. Their
work lives in the Intercom teams **VIP managers** (`50006396`) and **pre-VIP** (`50006577`), listed in
`vip.VIP_TEAM_IDS`. "VIP Players Chats" / "(DE)" hold no conversations, so ignore them.
The collector fetches threads assigned to those teams **or to a VIP manager personally**
(`vip.collect_query`): in Oct 2026, 617 of the 1,508 emails assigned to a manager carried no
team at all (outbound offers and follow-ups mostly), so a team-only search had missed them.
**Every one of these threads is an email**, so they are stored in their own table,
`vip_conversations` (`storage/vip_store.py`), and never in `conversations`. Nothing there reaches
the Overview, the grading queue or agent stats. They are graded per **case**, on their own
scorecard, into their own table — see *VIP cases and the VIP Scorecard v1.6* below.

- Collect a range: `intercom-summary vip-collect --since 2026-10-01 --until 2026-10-14` (UTC, end
  inclusive). It is safe to re-run: rows are upserted, and the AI verdicts already given are reused
  unless you pass `--reclassify`.
- Filtering is in two stages, in `vip.py`. Every fetched thread is stored, the dropped ones with
  their reason, and the tab's "show filtered-out" box shows them.
  1. **Rules (free).** A thread is dropped for one of these reasons:
     - `duplicate thread`: one email often becomes several conversations. The manager answers in
       one of them and leaves a note with that thread's id on each copy.
     - `no player message`: an outbound offer nobody answered.
     - `one-word reply`: the player wrote under 3 words beyond pleasantries and signatures.
       Quoted reply text does not count.
     - `only internal notes` / `no manager reply`: the latter is mostly system mail, e.g. Plane or
       Calendar notifications.
  2. **Meaning (Claude Haiku 5.5, `vip.CLASSIFIER_MODEL`).** Is this a genuine player ↔ manager
     exchange? The check also returns a topic label. It costs about $0.11 for 550 threads.
- 1–14 Oct 2026: 916 fetched, **538 kept**. Rules dropped 185 duplicates, 115 with no player
  message, 31 one-word replies and 36 with no manager reply. The AI dropped 11.

### VIP cases and the VIP Scorecard v1.6 (`vip-v16`)
The client's `VIP-QA-Scorecard_King_Billy_v1.6.docx` grades a **case**, not a thread: one player's
request followed across emails and days. On 1–14 Oct, 538 kept threads make 278 cases (78 span
several threads; the biggest is 20 — serial bonus requesters chain for two weeks).

- **Grouping** — `vip_cases.py`, free: same Intercom contact + same topic (the Haiku label), sorted
  by start; a thread joins the open case if it starts within `CHAIN_DAYS` (7) of that case's last
  human message. Topic `Other` is never chained. `case_id` = `vc-<first thread id>`;
  `case_version` hashes the threads and their message counts, so a grade goes stale when the case
  grows. Rebuild: `intercom-summary vip-cases` (also done before every grading run). Tables
  `vip_cases`, `vip_case_threads`.
- **Who is a VIP manager** — `config/vip_staff.yaml` (`vip_staff.py`). `staff:` is refreshed from
  Intercom by `intercom-summary vip-staff --refresh` (admins in the two VIP teams). Everyone else
  who writes in a thread ("Pay Dep", "Head of CS", support agents) is **OTHER STAFF** and is never
  graded. `shifts:` is empty until the client sends the schedule; while it is empty the timing
  criteria (`proc-first-response`, `proc-next-response`) are **not assessable**. Shape is in the
  module docstring.
- **What the grader reads** — `qa/vip_transcript.py`: header (threads, managers, other staff,
  evidence scope, shift note), a computed **REPLY GAPS** table, then dated UTC lines labelled
  `VIP MANAGER` / `OTHER STAFF` / `PLAYER` / `(internal note)`, with a separator per thread.
  Bots are dropped. The drawer shows exactly this text.
- **Ruleset** — `vip-v16`: catalogue + prompt + output schema in `qa/vip_v16_prompt.py`, live
  prompt `rules/qa_system_prompt_vip_v16.txt` (after editing the Python constant, delete the .txt
  so it re-seeds — `validate_ruleset` catches drift). It is no agent group's ruleset.
- **Score** — `qa/vip_scoring.py` (`vip-gated`): Critical → provisional 0 (a reviewer confirms;
  intent criticals are listed); otherwise `cap − quality − process − min(6, style)`, cap
  100 → 75 → 65 → 55 → 45 for 0/1/2/3/4+ Majors; PASS ≥ 85; `service_failure_flag` at 3+ Majors.
  `partial` costs the `partial_deduction` on the four two-level criteria. A `cannot_determine` on
  a criterion whose evidence is outside Intercom (`scope: external`), or on a timing criterion
  without shifts, becomes **`not_assessable`**: no deduction and no manual review.
  `tests/test_vip_scoring.py` holds every worked example from the scorecard.
- **Guard** — `verdict_guard.apply_vip_guards`: a fail/partial quoting a bot, OTHER STAFF or the
  player (outside the few criteria where that is the right evidence) is reset to n/a, with a flag.
- **Grading (paid)** — `intercom-summary vip-grade [--batch] [--case vc-…]`, or the admin
  **Grade cases** button in VIP → Cases (batch). Grades go to `vip_grades`
  (`storage/vip_cases_store.py`), never `grades`. Rough cost: ~$0.05 per case live, half by batch.
  The override (Re-score in the drawer) needs a written reason and keeps the AI original; **Agree**
  (`POST|DELETE /api/vip/grades/<case>/confirm`, `vip_grades.human_confirmed_*`) records that a
  reviewer checked the grade and it stands.
- **Never re-graded on a rules change** (the client's rule, as for chats): `VipCasesStore.is_current`
  ignores `rules_version`. A case is graded again only when it **grew** (a new thread → new
  `case_version`) and nobody re-scored or agreed with its grade; the replaced grade is kept in
  `vip_grade_history`. The majority vote (`QA_VOTES`) applies to VIP grades too; the fail verifier
  and QA lessons do not (their rules are written for the chat criteria).
- **The VIP loop** — `service.vip_auto_cycle`: collect the threads assigned to a VIP team or to a VIP manager for yesterday–today,
  regroup, grade the cases without a grade (batch). `VIP_AUTO=on` runs it in the web app every
  `VIP_AUTO_HOURS` (24); it is **off by default** because every turn is paid. By hand:
  `intercom-summary vip-auto [--since … --until …] [--live]` or `POST /api/vip/auto` (admin).
- **Export** — the **Export XLSX** button on each view downloads what the view shows under its
  current filters (`export/vip_xlsx.py`). Threads: `GET /api/vip/export.xlsx` (same query
  params as `/api/vip/conversations`, plus `topic`), one row per thread with the transcript and,
  with `include_excluded=true`, why a thread was dropped. Cases: `GET /api/vip/cases/export.xlsx`
  (same params as `/api/vip/cases`), one row per case with the effective score and PASS/FAIL (the
  re-score when there is one, the AI's alongside), flags, failed criteria and the grader's
  transcript, plus a **Criteria** sheet with every verdict and its evidence. Nothing is graded.
- **Exception Report** — `vip_cases.exceptions()`, `GET /api/vip/exceptions`: a player's last
  message with no VIP-manager reply after it in any of their threads, and kept threads with no
  owner. Rule-based (sign-offs and mail-app footers are ignored), so expect the odd thank-you.
- **Calibration** — the sample `vip-v16-70` comes from the spreadsheet:
  `python scripts/import_calibration_sample.py "Untitled spreadsheet (1).xlsx" --lines --id vip-v16-70 --name "VIP Scorecard v1.6 calibration (70)" --ruleset vip-v16`.
  A run on it grades each member's whole case (63 cases for 70 threads); members of one case
  share the grade.
  QA's verdicts on the sample go in with `--labels` (same script); the Calibration tab then shows
  PASS/FAIL agreement per run. No VIP verdicts exist yet (Oct 2026): until they do, the VIP rules
  are the scorecard as written, unmeasured against QA.
- **The §14 sample** (`vip-v16-s14-v1`): `python scripts/build_vip_s14_sample.py --dry-run`, then
  without `--dry-run`. Cases started 1–15 Oct; complex = topic (Complaint, Withdrawal, Deposit,
  RG) or text heuristics (closure / SE, lost email, 10 000+, vip_cs / Payments, refusal met with
  anger) or an Exception-Report "unanswered"; complex capped at 100, plus as many routine; under
  150 the period is extended up to the last collected day, and a shortfall is written into the
  sample's `source`. One member per case (its first thread). Free, no LLM. On the 1–14 Oct data
  it finds 73 complex + 73 routine = 146 — collect 15–16 Oct (`vip-collect`) to pass 150.
- **The case package** for Head of VIP to sign (§14: "согласовать состав пакета кейса") is
  `docs/vip-case-package.md`: per criterion, what the AI receives and what stays CANNOT DETERMINE.

### AI ↔ QA agreement (the client's ≥ 95% PASS/FAIL bar)
The client's requirement: the AI's PASS/FAIL agrees with QA's on at least 95% of chats.
- **Measured how:** `qa/agreement.py`. QA reviews the AI's FAILs far more often than its PASSes, so a
  plain "agreed / reviewed" ratio mostly measures the FAILs. The estimate weights each side by how
  often the AI gives it: `P(AI PASS)·agree|PASS + P(AI FAIL)·agree|FAIL`, with a 95% range. A side
  nobody has reviewed makes the estimate "unknown" rather than assuming it is perfect.
- **Where:** the Accuracy page's top card (`/api/accuracy` → `result_agreement`, v4.1, last 30 days),
  or `python scripts/agreement_report.py`.
- **The "Agree" button** (grade panel, next to Re-score) records that a QC manager checked the AI
  grade and it stands (`grades.human_confirmed_by/at`, `POST|DELETE /api/conversations/<id>/confirm`).
  Without it only disagreements leave a trace. A re-scored grade can't be agreed with (it already
  has a human verdict). In the Calibration tab, Agree saves a re-score with
  the AI's own verdicts.
- **False-PASS audit:** `scripts/pass_audit_sample.py` freezes 80 random unreviewed AI-PASS chats as
  sample `kb-v41-pass-audit`. QA opens them (`/api/conversations?sample=kb-v41-pass-audit`) and presses
  Agree or Re-score on the **stored** grade. No calibration run — old chats are never re-graded.
- **Measuring a change before shipping it:** `scripts/accuracy_ab.py` grades the human-reviewed chats
  per configuration ("arm": `base`, `hist`, `jev`, `verify`, `vshadow`, joined with `+`). It never
  writes to `grades`. Tune prompts on `--set dev` only and check once on `--set holdout`
  (`agreement.split_of`, a stable 70/30 hash split). Then
  `scripts/agreement_report.py <accuracy_ab_*.json>` projects the population agreement for each arm.
  Run both against a **copy** of the DB (`DB_PATH=…`).

### The fail verifier — a second look at every FAIL
`qa/fail_verifier.py`. QA overturned 130 of the 146 AI FAILs it reviewed (Oct 2026), almost always
over a judgement call, while PASSes are rarely overturned. So when a v4.1 grade is heading for FAIL,
the same model takes one more look, at ~$0.013 per verified chat. It reuses the cached prompt and KB.
It checks each failed criterion against the manual and that criterion's NOT-A-FAIL boundaries
(`BOUNDARIES` in the file), then answers `upheld` or `not_proven`.
- `QA_FAIL_VERIFIER` in `.env`: `on` (default) · `shadow` (record only) · `off`. It applies to chats
  graded from now on; existing grades are never re-graded.
- **`on`:** `not_proven` turns a Gate 2/3 fail into pass, with a `verifier:` flag. A Gate 1 critical is
  never lifted. A disputed critical goes to manual review instead. A verifier error leaves the grade
  unchanged.
- The record is in `payload_json["fail_verifier"]` (checked, answers, overturned).
- When QA reports a new kind of false FAIL, add the boundary to `BOUNDARIES` **and** to the prompt's
  QA PRACTICE section, then re-measure with `accuracy_ab.py`.

### The majority vote — borderline grades are made three times
`Grader._vote` (`qa/grader.py`), `QA_VOTES=3` (1 = off), `QA_VOTE_BAND=70-89`. Two runs of the
same rules disagreed on about one QA-reviewed chat in ten (Oct 2026), nearly all near the 85
threshold. So when the first grading lands in the band, or the fail verifier changed a verdict,
the chat is graded again (two more full passes, guards and verifier included) and the majority
PASS/FAIL wins; the kept grade is the median-scored one on the majority side. `grade.votes`
records every result; the panel shows "Majority of 3 gradings: FAIL 75 · PASS 92 · PASS 95 →
PASS". Extra calls reuse the cached prompt (~$0.013 each) on roughly one chat in five. A warm-up
call and a vote that yields no usable grade are never voted on. Evaluate it with the `vote` arm
of `scripts/accuracy_ab.py`.

### Bot-menu buttons in the transcript
Intercom records a button the player clicks in the bot menu as an ordinary customer message with
the button's label, so "My deposit did not come" looked like a typed request the agent ignored.
`Conversation.menu_choices()` (`intercom/models.py`) finds them — the bot's `quick_reply` part
before the line, or a short opening line the bot answered within 2 s — and `transcript_text`
marks them `CUSTOMER Name (bot-menu choice): …`. The kb-v41 prompt and the fail verifier refer
to that marker. It is a rendering change: stored grades are untouched, new chats see the marker.

### QA lessons — the rules learn from QA's re-scores, weekly
`qa/lessons.py`, `storage/lessons_store.py` (table `qa_lessons`), page **QA lessons** (`/lessons`).
- **Weekly sync** (in-process loop like the history refresh; `QA_LESSONS=on`, `QA_LESSONS_DAYS=7`):
  collects every re-score / calibration review since the last sync, groups the AI-vs-QA
  disagreements per criterion (a "no mistakes" re-score of an AI FAIL counts each failed criterion,
  marked *inferred*), and drafts one general rule per criterion corrected ≥ `QA_LESSONS_MIN_CASES`
  times. By hand: `intercom-summary lessons sync [--since …] [--no-draft]`.
- **Approval** (admin only): the rule is appended to the prompt's QA PRACTICE section under
  "Learned from QA re-scores", and NOT-A-FAIL rules also go to the fail verifier
  (`FailVerifier.learned`). New rules version → new chats only; nothing is re-graded.
- CLI: `intercom-summary lessons list --status draft`, `lessons approve ql-…`, `lessons reject ql-…`.

### Calibration samples (measuring the AI against human graders)
A **calibration sample** is a frozen list of conversations. Frozen is the point: two measurement
runs are only comparable if they graded the same chats, so membership is written down once
(`calibration_samples` / `calibration_sample_items`) and `replace_items` refuses to change a
sample that already has members.

- Import one from the QA department's document:
  `python scripts/import_calibration_sample.py <file.docx> --id kb-v41-180 --name "…"`
  (add `--dry-run` to parse and report without writing).
- The importer **reports** members it cannot grade rather than dropping them: a chat absent from
  the cache, or one sitting in the trash. ⚠️ A trashed conversation silently blocks its own
  re-import from Intercom, so a short sample stays short until someone restores it.
- **QA's reference verdicts:** `python scripts/import_calibration_sample.py <file> --id <sample> --labels`
  reads a .docx/.xlsx/.csv table whose header names the chat-id, score / PASS-FAIL and comment
  columns, and stores them on the members (`calibration_sample_items.human_*`). Every run of the
  sample is then compared against them in the Calibration tab (PASS/FAIL agreement card, "PASS/FAIL
  differs from QA" filter). A review made in the tab on a run overrides the reference for that run.
  QA's written feedback on a run is kept as `docs/qa-feedback/<date>-<sample>.csv` and imported the same way.
- **Where to start:** `python scripts/worst_disagreements.py --sample <id> -n 10` (or `--rescored`)
  ranks the chats furthest from QA from grades that already exist; `--freeze <id>` saves them as a sample.
- Browse one: `/api/conversations?sample=<id>`. List them with their gradeable counts:
  `/api/calibration/samples`.

**Calibration runs: the temporary "Calibration 180" tab.** A run grades every member of a sample
with the Claude grader (`kb-v41`, the same engine as live grading) into **its own tables**
(`calibration_runs` / `calibration_results`), never into `grades`. Live grades, their history and
their human overrides are untouched. Members missing from the cache, including trashed ones, are
fetched from Intercom by id and kept only on the run. They are **not** saved to `conversations`
and they don't leave the trash. Tickets are recorded and not graded.
- Start one from the tab ("Run evaluation"), or `intercom-summary calibrate kb-v41-180 [--pilot] [--batch]`.
  Code: `service.run_calibration`, which calls `review_and_store(conversations=…, save_grade=…)`.
- QA managers open a chat and use the normal Re-score form. It posts to
  `/api/calibration/runs/<run>/conversations/<id>/review` (same scoring as the live override, via
  `_score_override` in `web/api.py`) and is stored on the run. The tab shows Δ and the criteria they disagree on.
  Export: `/api/calibration/samples/<id>/export.xlsx`.
- Each run keeps its own verdicts. A new run starts with none, so pick the older run in the run selector to see them.
- Removing the tab: delete the route in `App.tsx`, the nav entry in `AppShell.tsx` and
  `pages/Calibration.tsx`. The tables can stay as an archive.

### Switch which AI model does the grading
- Default: the **Claude API** (`QA_BACKEND=api`) with `QA_MODEL=claude-sonnet-5-5` and
  `ANTHROPIC_API_KEY` in `.env`. Then `./restart.sh`.
- `QA_EFFORT` (`low`/`medium`/`high`/`xhigh`/`max`, default `high`) is how hard the model thinks
  before grading — higher = slower and more output tokens. `high` was chosen on 2026-10-04 after a
  30-chat comparison (half the gap to human scores of `medium`; `xhigh` and Opus 5.5 cost more for
  no gain — see `docs/token-optimisation.md`). Live at `high`: ~15–30 s and ~$0.02–0.045 per chat
  (v4.1); in a batch run about half that (~$0.014). The system prompt is served from the prompt
  cache after the first chat. `QA_CONCURRENCY` (default 10) is how many chats a live run grades at once.
- **Cost:** every grade stores its tokens and dollars in `payload_json["usage"]` (`qa/pricing.py` —
  update `PRICES` there if Anthropic's rates change); a run's total is on its job result and the
  Evaluation page. **Batch runs** (`batch=true` / the Evaluation checkbox) go through the Message
  Batches API at half price (`qa/batch.py`); if the server restarts mid-batch, the job's error names
  the batch and `intercom-summary collect-batch <id>` saves it. Everything about tokens — what's
  cached, what was trimmed, what was deliberately not done — is in `docs/token-optimisation.md`.
- After changing how the grading request is built, run `scripts/cache_probe.py` (~$0.03): it fails if
  the prompt stops being served from the cache.
- **Before changing model, effort or a prompt, try it on real chats without saving anything:**
  `.venv/bin/python scripts/dry_run_grades.py -n 10 [--ruleset kb-v41] [--effort low]`. It prints
  the new grade next to the stored one and the QA analyst's score, plus cost.
- How the Claude grader works (`qa/grader.py`): the ruleset prompt is the cached system block, the
  transcript block is the user turn, and the answer is constrained to the ruleset's JSON schema with
  structured outputs (`rulesets.strict_schema()`). The model never supplies the score — the same
  `from_model_output` → flat/gated scoring as Ollama computes it. Grades are stamped with the same
  `rules_version` (prompt hash) either backend would use, so switching backend re-grades nothing.
- Sonnet 5.5 rejects `temperature`, forced `tool_choice` and disabled thinking — don't add them back.
- A refused grade is re-run on a fallback model by the API (`QA_REFUSAL_FALLBACK=1`); `grades.model`
  records the model that actually answered.
- Local Qwen is still available (`QA_BACKEND=ollama`, `OLLAMA_MODEL=qwen2.5:14b`), but it takes
  90–140 s per chat on this Mac and grades worse.

### Add or change a website button/screen
- **Existing screen:** edit its file in `frontend/src/pages/`.
- **New backend endpoint:** add it in `web/api.py`; add its call + types in `frontend/src/lib/api.ts`.
- **New screen:** create `frontend/src/pages/Whatever.tsx`, register the URL in `App.tsx`, add a
  menu item in `components/AppShell.tsx`.
- Then `npm run build` + `./restart.sh`.
- (Recent real example: the "Restart Ollama" button = a `POST /api/ollama/restart` endpoint in
  `web/api.py` + a card in `pages/Evaluation.tsx`.)

### Change who can do what (permissions)
- Backend enforces it: in `web/api.py` each endpoint ends with
  `Depends(auth.require_write)` (admin **or** analyst) or `Depends(auth.require_admin)` (admin only).
  Change that line to change who's allowed.
- Frontend hides/shows controls with `canWrite(user?.role)` (see `frontend/src/lib/auth.tsx`).
- The roles themselves are: **admin** (everything), **analyst** (can run/edit/override),
  **viewer** (read-only).

### Add or remove a website login
- Edit `config/web_users.yaml` (username, password hash, role). The file is **gitignored** —
  it never gets committed, so on a fresh clone start from `config/web_users.example.yaml`.
  To make a password hash:
  ```bash
  .venv/bin/python -c "from intercom_summary.web.auth import hash_password; print(hash_password('NEWPASS'))"
  ```
  Then `./restart.sh`.

### Change the name shown for a login
- Add an optional `display_name:` next to the user's `role:` in `config/web_users.yaml`:
  ```yaml
  users:
    analyst:
      password_hash: "$2b$12$..."
      role: analyst
      display_name: Daria
  ```
  Then `./restart.sh` (the user file is read once at import).
- It is **presentation only**. It is shown wherever a person is named: "Signed in as …",
  "Overridden by …", comment authors, disputes, Knowledge Base, Coaching, the trash list and
  the "Overridden By" column of the XLSX export. The **username** is still what gets written
  to the database, so renaming what someone is called never detaches them from grades they
  have already overridden. Unset → the username is shown, exactly as before.
- The sidebar shows the name only. It used to show a role chip next to it ("analyst"), which
  is what labelled people by their role; `user.role` still drives permissions, it is just not
  printed.
- Mechanics: `UserStore.display_name()` / `.display_names()` in `web/auth.py`, served by
  `GET /api/users/display-names`, consumed by `useDisplayName()` in `frontend/src/lib/auth.tsx`.
  Any new UI that renders a username should call that hook. **Frontend changes need
  `npm run build`** in `src/intercom_summary/web/frontend` — the app is served from `dist/`.

### Change a setting (tokens, ports, model, timeouts)
- Everything configurable is an environment variable in **`.env`** (see `.env.example` for the full
  annotated list). `settings.py` just reads them. After editing `.env`, run `./restart.sh`.
- Common ones: `QA_BACKEND`, `QA_MODEL`, `QA_EFFORT`, `QA_CONCURRENCY`, `QA_V41_EFFECTIVE_FROM`, `KB_ENABLED`, `INTERCOM_ACCESS_TOKEN`, `WEB_PORT`,
  `SLA_FIRST_RESPONSE_SEC`, `WEB_BASIC_AUTH`.

### Change how conversations are fetched from Intercom
- `intercom/fetch.py` (what we pull) and `intercom/client.py` (raw API). The data shape is in
  `intercom/models.py`.
- Note that the search returns **tickets** as well as chats and we keep only the chats — see
  *Tickets vs chats* below before changing what the sweep keeps.

### Hand a client a bulk export of their chats
`python scripts/export_client_archive.py --dry-run` to see how many conversations a window
holds, then drop `--dry-run` to run it. It produces **one ZIP per brand** in `EXPORT_DIR`,
each containing an `index.xlsx` and one readable Markdown transcript per conversation,
foldered by month and agent.

- **It deliberately bypasses everything else.** It never writes to the database, so the Trash
  blacklist can't silently drop conversations from the client's copy and the grading tables
  stay clean. It also doesn't use `fetch_conversations_for_agents` — that buffers every
  conversation in memory and loses the lot if the run dies, which is no good at ~27k.
- **It doesn't fetch per agent.** A date-only search returns everything, including the ~24%
  with no human assignee; looping over teammates would miss those. `--per-agent` exists only
  as a fallback.
- **Interrupted runs resume.** Raw payloads are cached under `<out-dir>/raw/`; `--resume`
  continues and retries failures, `--only-build` re-cuts the ZIPs from that cache with no
  network calls. Delete `raw/` before handing the export over.
- **Chats only.** Tickets are filtered out of both the sweep and the build — see
  *Tickets vs chats* below for why the `--dry-run` count is higher than what you get.
  `--include-tickets` puts them back.
- `--redact-emails` masks addresses if the archive leaves the workspace;
  `--include-system-events` keeps the empty bot/automation entries that are hidden by default.

### Tickets, emails and chats (why the numbers are smaller than Intercom's)
**We handle Messenger chats only.** Emails and Intercom tickets are excluded from every export,
every listing and every grade. These are two *different* exclusions and both are needed — they do
not overlap at all:

- **Email is a channel.** `source.type` is `"conversation"` for a Messenger chat and `"email"` for
  an email; those are the only two values this workspace has ever produced, and email is **46%** of
  it. `source.type` is a **searchable** field, so `build_search_query` asks Intercom for chats and
  emails never come back — no stub to filter, no wasted full-thread GET. `--include-emails` on the
  archive script restores them.
- **A ticket is not a channel.** Every ticket is `source.type == "conversation"`, and **no email is
  ever a ticket** (`ticket` is null on all 12,498 of them). So `is_ticket` cannot catch an email,
  which is exactly how emails kept arriving after tickets were excluded. Tickets still have to be
  recognised on the stub and dropped.

Because the two are independent, adding one filter never removes the need for the other.

- **How they're told apart.** Both come back from `/conversations/search` and both say
  `"type": "conversation"`, so neither the endpoint nor the type field separates them. The
  marker is the payload's **`ticket` object**: `null` on a chat, a `{...}` dict on a ticket.
  That's `intercom/fetch.py` → **`is_ticket()`**, the single source of truth.
- **Where it's applied.** `fetch_conversations_for_agents` drops ticket **stubs**, before the
  full-thread GET — so a ticket costs nothing, and on a ticket-heavy agent that's hundreds of
  requests saved. `ConversationsStore.save()` refuses one as a backstop, which is what keeps
  every screen, export and grade run chat-only without a filter in each of them.
  `scripts/export_client_archive.py` filters at both the sweep and the build (a cache from an
  older run still holds tickets; `--only-build` must not put them back).
- **The counts won't match Intercom's.** A window Intercom reports as 3,833 conversations is
  ~3,780 chats. The fetch result carries `skipped_tickets`, and the run dialog, the Slack
  reply and `--dry-run` all state it — otherwise a correct run looks like a short one.
- **Emails fetched before this rule existed.** `scripts/purge_emails.py --dry-run`. Same shape as
  the ticket purge: a cached row carries no channel (`normalise_conversation` read `source.type`
  only as a non-emptiness test and never stored it), so it asks Intercom which ids are emails and
  soft-deletes the matches to the Trash with their grades.
- **Tickets fetched before this rule existed.** `scripts/purge_tickets.py --dry-run` reports
  which cached conversations Intercom calls tickets (it asks `/tickets/search`, because a
  cached row carries no marker); without `--dry-run` it moves them to the **Trash**, which
  snapshots each conversation and its grade first, so it's one Restore away from undone.

### A review link shows the wrong chats
A review link is a record of what was handed to an agent, so its membership is **frozen** at
creation into `agent_review_token_items` and never re-queried.

There used to be a fallback: a link with no frozen membership fell back to an unscoped, undated
live query capped at 500 rows over the agent's whole history. That is how an "August review" link
came to list September chats, and how chats appeared in it as grading caught up — and it was
indistinguishable from a link whose freeze legitimately resolved to nothing. The same fallback
existed as an *access* rule on the detail endpoint, which made every conversation an agent ever
handled reachable by id through any one of their links.

Both are gone. An empty membership now honestly shows nothing. Links created before the freeze
existed were frozen in place by `scripts/backfill_token_items.py` (re-runnable, `--dry-run`
first); if a link ever needs widening, generate a new one.

### A score changed after someone had already reviewed it
This happened for real in September and it is worth understanding, because the mechanism is not
obvious.

`rules_version` is a hash of the **prompt text**. Any edit — one word — produces a new version.
Back then that marked every grade the ruleset produced stale, and the next review run re-graded
them. The model does not return the same score twice for the same chat under different rules, so
scores moved. In September the rulebook was corrected on the 3rd and the 4th; QA had already manually
re-graded the month's red/yellow chats, agents had disputed specific numbers, and those numbers
then changed underneath everyone. Several grading runs had also crashed mid-batch (Ollama
dropping out), so chats acquired scores in dribs over days — and an ungraded chat has no colour,
so it cannot appear in a red/yellow worklist until it does.

Three things now guard against it:

- **An ordinary run never re-grades a chat that already has a grade** — reviewed or not, whatever
  the rules_version says. `is_current` (`storage/grades_store.py`) returns True for any stored
  grade; new rules apply to new chats only. This is the client's instruction (2026-10-20): **old
  chats are never re-graded.** Only an explicit `regrade=True` (a person pressing Re-score on a
  chat, or ticking "Re-grade already-evaluated conversations") replaces a stored grade. Don't do
  that to old chats without the client's say-so.
- **Every overwritten grade is archived** into `grade_history` before the upsert, and shown in the
  grade panel as *"Re-graded N times — see previous scores"*. This is what makes "why is this red
  now when it was green last week?" answerable.
- **The Ruleset page says what saving does**: existing grades keep their scores, only new chats use
  the new text.

To find what QA still owes a manual grade for a period:
```bash
python scripts/pending_manual_review.py --since 2026-08-01 --until 2026-08-31
```

### A conversation scored 0
Only two things zero a score, and the model's opinion is not one of them:

- **A critical criterion failed** — `crit-data-care` or `crit-rg-care` (the set is whatever
  carries `critical: true` in `config/rulesets.yaml`). This is derived from the verdicts, so the
  model can neither invent a critical fail nor hide one: it reported `critical_fail: true` on 24
  grades where no critical criterion had failed, and for 16 of those the flag was the only reason
  the score was 0. `crit-no-unsupported-promises` is **not** critical — it is a −20 deduction.
- **Deductions reaching 100.** That is the honest route to 0 and needs no flag.

`_compute_score` and `score_from_verdicts` now agree on this, so an analyst's manual re-score and
the AI score mean the same thing by "critical".

### Response-time / SLA looks wrong
- **The agent's clock starts when the chat reaches them.** `Conversation.agent_first_reply_seconds`
  measures from the last assignment event before the agent's first substantive reply. It is a
  property derived from cached `messages`, so it needs no migration. `first_response_time` (Intercom's
  `time_to_admin_reply`) is still stored, but it runs from conversation *creation* — a bot is assigned
  instantly, answers in ~1s and holds the chat, so that window is the player's total wait, not the
  agent's latency. Judging on it marked **26.6%** of chats BREACHED; on the agent's clock it is 5.8%.
- **The grader is shown the chat's tags** (`Chat tags:` in the header, `qa/prompt.py:_tags_line`).
  Without it the tag criterion had no source at all and answered from nothing — the same chat came
  back `n/a` on one run and `pass` on the next. Like the TIMING header it is prompt text, so a fail
  that cites it is dropped for every criterion except `tag-chat`, whose source it is.
- **Only `resp-first-reply` records a slow first reply**, from the figure in the TIMING header.
  Everything else must be evidenced from the transcript — `verdict_guard` drops a fail that quotes the
  header instead, which 46 stored verdicts did. `resp-first-reply` is the one criterion exempt from
  that rule, because the header is its legitimate source.
- **The ruleset decides what exists and what it costs.** `_compute_score` ignores a `fail` whose id is
  not in the catalogue and takes the deduction from the catalogue, never from the model. This closes a
  real hole: the model invented a `first-response-time` criterion with its own −20 and the score was
  reduced by it. If a new criterion is genuinely needed, add it to **all** of `qa/casino_prompt.py`
  (row, title, `CRITERION_DEDUCTIONS`), `rules/qa_system_prompt.txt`, `rules/support_rules.md` and
  `config/rulesets.yaml` — `validate_ruleset` catches a copy left behind. For the VIP ruleset the
  pair is `qa/vip_prompt.py` + `rules/qa_system_prompt_vip.txt`; for `kb-v41` it is
  `qa/kbv41_prompt.py` + `rules/qa_system_prompt_kb_v41.txt`, and a criterion there also needs a
  `gate` and a `severity` or the gated formula will not know what a failure costs.
- **Targets** live in `SLA_FIRST_RESPONSE_SEC` / `SLA_FOLLOWUP_SEC` (`.env`, defaults 120s/300s).
  They were absent from `.env.example` for a long time, so the hard-coded defaults had never been
  reviewed by anyone.

### The grader blamed the agent for the bot or the player
This workspace runs a Fin bot ("Billy Jr.") that writes about **41% of the text in a chat** and
closes **52.8% of them**, while the agent writes a median of four turns. Two rules follow:

- **The grader sees agent and player only.** `transcript_text(include_bots=False)` strips
  automation and leaves a count-only marker (`— 3 automated messages omitted —`). The marker is
  load-bearing: it keeps the timing gaps honest and stops `resp-no-ghost` firing on questions the
  bot answered. `qa/prompt.py` and `qa/grader.py` pass `include_bots=False`; **everything the UI
  shows keeps the full thread** — analysts need it, so the default is `True`.
- **`Conversation.closed_by`** (`"admin"` / `"bot"` / `""`) is derived from the last `close`
  part's author. It is a property, not a column, so it works on every conversation ever cached
  with no migration — Intercom always sent it, we just never read it. `qa/prompt.py` puts it in
  the prompt header; without it, `close-confirm` and `close-courtesy` had a correct N/A clause
  ("agent didn't close the chat") that the model could never apply, and used it on 6.8% of
  verdicts while the bot closed half the chats.

`qa/verdict_guard.py` enforces both deterministically: a `fail` whose cited quote is a bot or
player line is dropped, and a closing criterion is dropped when the agent did not close. Note
`PLAYER_EVIDENCED_CRITERIA` — churn detection, RG care and withdrawal sensitivity exist to react
to *what the player said*, so a player quote is correct evidence there and must not be overturned.

### The grader marked something absent that is plainly in the chat
This happened for real with greetings and player names, and the shape of the bug is worth
knowing because it will recur with other criteria.

- **Check what the model was actually given, not what is in Intercom.** `qa/prompt.py`
  `transcript_block()` is the whole of the model's view. Print it for the conversation in
  question before assuming the model is at fault — the greeting bug was largely
  `Customer name: unknown` in that header, because `contacts.contacts` comes back as id-only
  stubs. `intercom/fetch.py` `contact_from_payload()` now recovers the name from the thread's
  own message authors.
- **Watch what `transcript_text()` drops** (`intercom/models.py`). It keeps a turn only if it
  has text or is a `comment`. Empty `admin (assignment)` / `(close)` rows used to survive, so
  the first `AGENT` line was blank and the model read the agent as opening with nothing.
- **Criteria must describe the agent's turn, not the conversation's.** `open-greet` used to
  read *"No greeting at conversation start"*. These chats start with the customer and the Fin
  bot; the agent joins minutes later, so read literally the criterion fires on almost every
  chat. Anchor the wording to *the agent's first message*.
- **Read the evidence the model cites.** In the failing grades, 433 of 434 `open-greet` fails
  quoted the rubric's own FAIL text instead of the chat, and 89 of 112 `open-name-use` fails
  quoted the agent greeting the player *by name*. `qa/verdict_guard.py` now catches both:
  a `fail` whose evidence is not in the transcript becomes `n/a`, and an `open-name-use` `fail`
  is overturned when the agent demonstrably used the name. Corrections are recorded as
  `signal_flags`, never silent. It is scoped to those two criteria on purpose — the same rule
  applied everywhere would move 37% of failing verdicts and shift scores by ~6 points.

**Editing a prompt file never re-grades existing chats.** `rules_version` is a hash of the whole
prompt file (`qa/rulesets.py`), so a one-word change makes a new version — grades made from then
on carry it, and every existing grade keeps its score (`GradesStore.is_current`).

### Exclude a kind of conversation from grading (e.g. follow-ups)
Triage/noise chats are never graded and never count towards an agent's score. They're picked
out by their **native Intercom tag**, listed in one place:

- `storage/conversations_store.py` → **`IGNORE_TAGS`** — currently `spam`, `test`, `jira`,
  `follow-up` (`empty` and `no request` were removed so those chats are graded too). Add or remove a tag here and nothing else needs changing;
  match is case-insensitive, so `Follow-Up` and `follow-up` are the same tag.

The set is enforced in three places, all reading that one constant:
1. **Grading (web)** — `service.review_and_store` drops them before the grader runs.
2. **Grading (CLI)** — `cli.py` `_review` does the same for `intercom-summary review`, which
   fetches straight from Intercom and never goes through `review_and_store`.
3. **Reading** — `storage/grades_store.py` `_not_ignored_sql`, applied to `agent_scores`
   (leaderboard), `all` (XLSX export), `for_agent` and `accuracy_stats`.

Step 3 is the one that's easy to miss. A tag often arrives *after* we graded a chat — support
marks it "Follow-Up" in Intercom days later, and the next fetch refreshes `conversations.tags`
while the stored grade stays put. Filtering on read means such a grade silently stops counting
the moment the tag appears; no re-grade or database cleanup is needed. The grade row is left in
the table on purpose, so that untagging the chat in Intercom brings its score straight back.

### Work with brands (one Intercom workspace, several casinos)
The workspace serves several casino brands. Each conversation carries the brand it arrived
through, and the dashboard's **brand tabs** (under the header) scope every page to one at a
time — two brands are two products, so blending their QA scores describes neither.

- **The names.** `intercom/brands.py` maps the raw Intercom value to what people call it.
  Watch out: King Billy's conversations say **`Betncare`** (Intercom names the default brand
  after the workspace), so filtering on `"King Billy"` matches nothing. The database always
  stores the raw value; the label is display-only.
- **Adding a brand takes no code.** The tabs come from `GET /api/brands`, which is a
  `GROUP BY brand` over the cache — a new brand grows its own tab from the first fetch that
  includes it. Add a `BRAND_LABELS` entry only if Intercom's name for it isn't the name you
  want on screen. The tab strip stays hidden while there is only one brand.
- **Fetching is brand-blind, and has to be.** Intercom's conversation search has no `Brand`
  field (it rejects the query), so we always fetch every brand for the chosen agents and
  record the brand during normalisation (`intercom/fetch.py`). Don't add a brand option to
  the fetch dialog — it would promise a server-side filter that doesn't exist. Filtering
  happens locally, against our own cache.
- **Backfilling old rows.** `scripts/backfill_brands.py` (`--dry-run` first). It writes only
  the `brand` column, never through `ConversationsStore.save()` — a full re-save would
  rewrite `agent_name` from Intercom's current assignee and quietly shift per-agent averages.
- **Adding a brand filter somewhere new.** Thread `brand` through the same way as `agents`:
  `_brand_sql()` in `storage/conversations_store.py`, or the sub-select version in
  `storage/grades_store.py` (`grades` has no brand of its own — the conversation is the
  source of truth). Keep it additive: with no brand selected the SQL must stay exactly as it
  was, which is what keeps existing numbers stable.

### The player's VIP segment (VIP / preVIP / regular)
QC's rules for closures and manager requests depend on whether the player is VIP or preVIP. The
Intercom **contact** carries it (synced from the back office): `custom_attributes.Groups`
(`regular_vip`, `vip_except_ba` → VIP; `previp` → preVIP; else regular), `Profitable`
(`regular_vip`) and `Status` (loyalty level, `new_marquess` → "marquess").
- `intercom/segments.py` — `segment_from_attrs()`, `header_line()`, `ContactSegmenter` (one
  `GET /contacts/{id}` per player per run; a failure leaves the segment empty, never fails a fetch).
- Cached in the `contact_segments` table (`storage/contact_segments_store.py`, 7-day TTL) and
  written into the payload as `contact.segment` / `contact.loyalty_level`. `ConversationsStore.save`
  keeps a known segment when a re-fetch's lookup failed.
- Filled by every Intercom read: `fetch_and_store`, the calibration fetch and `vip.collect`.
- The grader sees it as `Player segment: VIP (loyalty level: marquess) — Intercom contact
  attributes, current, not as of the chat` (`qa/prompt.py`, `qa/vip_transcript.py`); "unknown"
  when there is none, and the prompt then answers a segment-dependent verdict cannot_determine.
- It is the contact's **current** value, not as of the chat.
- **Backfilling old rows:** `scripts/backfill_contact_segments.py --dry-run [--since 2026-10-01]`,
  then without `--dry-run`. It writes only `payload_json` via `json_set` (never `save()`), never
  touches `grades` — nothing is re-graded; only chats graded afterwards see the line. On a copy
  of the DB (Oct 2026): 1 977 players → 1 211 regular, 442 VIP, 136 preVIP, 188 none; of 3 361
  October support chats, 1 168 VIP and 346 preVIP.

### The casino back office (BO) — what the grader could not see in Intercom
Closures, self-exclusions, RG limits, payment status, bonus history, duplicates and the VIP
manager's tag live in the casino's back office (SoftSwiss, Rails ActiveAdmin at
`BO_KB_URL`, King Billy only for now). Since 2026-10-28 the grader gets them as a
**BACK OFFICE block** in front of the transcript, and the kb-v41 / vip-v16 prompts say how to use
it (section "THE BACK OFFICE BLOCK"); without it the old cannot_determine rules apply.
- **Login is unattended:** `backoffice/client.py` posts email + password + a TOTP code it
  computes from `BO_KB_OTP_SECRET` (`backoffice/totp.py`), on the **server's** clock (the Mac
  mini ran 111 s behind and every code was refused). The session lives in
  `data/bo/session.json` (0600); a redirect to /login re-logs in once. Put the four `BO_KB_*`
  values in `.env`; `scripts/decode_otp_migration.py` turns a Google Authenticator export into
  the key, offline. A dedicated read-only BO user is best.
- **Read-only by construction:** GET only (the login form is the single POST), and any path
  naming an action (`update`, `disable`, `issue`, `edit`…) is refused before a request — the
  player page has controls that change the player instantly.
- **Which player:** the Intercom contact's `cleanid` (or `external_id` "c<id>"), picked up with
  the VIP segment (`intercom/segments.py`) into `contact.bo_id` / `contact.bo_brand`;
  `scripts/backfill_contact_segments.py` fills it for cached chats.
- **What is read** (`backoffice/player.py`): the player page (status, locks, groups, staff
  comments) plus its lazy panels — payments (latest 10), issued bonuses and free spins, RG limits,
  tags, duplicates. Cells are read by their `col-<field>` class. No addresses, IPs, documents or
  card numbers are kept.
- **Cache + sync:** `bo_players` table (`storage/bo_players_store.py`); `backoffice/sync.py`
  reads stale/missing players (older than `BO_MAX_AGE_HOURS`, default 6) two at a time. Every
  grading path calls it first (`service._sync_back_office`); `BO_SYNC=off` switches it off.
  By hand: `intercom-summary bo-sync [--since D | --sample ID | --reviewed kb-v41 | --ids …]`.
- **The block** (`backoffice/context.py`): status now, locks and RG limits with dates, tags,
  relevant groups, duplicates, payments from 14 days before the chat to 3 after, bonuses and free
  spins from 30 days before to 3 after, staff comments — each dated row marked before / during /
  after the chat (the BO shows UTC). Status, tags and groups are as of the read.
- **Limits:** the BO lists only the latest 10 payments / bonuses, so an old chat of a very active
  player may see none from its own window. Tests never reach the BO (`tests/conftest.py`).

### Change the Slack bot
- `slack/handlers.py` (what happens on commands/clicks), `slack/blocks.py` (the message UI),
  `slack/auth.py` (who's allowed).

### Change the timing / SLA logic (response-time targets)
- Targets are `.env` vars `SLA_FIRST_RESPONSE_SEC` / `SLA_FOLLOWUP_SEC`. The display logic is in
  `intercom/models.py` (per-message gaps) and the grader prompt in `qa/prompt.py`.

---

## 5. Running, restarting, and checking

| I want to… | Command |
|---|---|
| Restart the live website (after a change) | `./restart.sh` |
| Develop with live reload | `./scripts/dev.sh` |
| Restart the AI model service when it crashes | `./scripts/restart_ollama.sh` (or the **Restart Ollama** button on the Evaluation page) |
| Rebuild the frontend | `cd src/intercom_summary/web/frontend && npm run build` |
| Run the tests (catch breakage before shipping) | `.venv/bin/python -m pytest -q` |
| See which AI models are installed | `ollama list` |

**The live website** runs as a background macOS service (`com.intercom-qa-web`) on port **8099**,
exposed publicly at **qc-intercom.qa-temple-of-serenity.cc**. `./restart.sh` is what reloads it.

**Always run the tests before merging** — they're fast and catch most mistakes.

---

## 6. The database

- One file: **`data/grades.db`** (SQLite). Despite the name it holds *everything*: conversations,
  grades, background jobs, comments, coaching, agent groups, etc.
- You rarely touch it directly. Code reads/writes it through the files in `storage/` (one per table).
- Every grade records **which ruleset scored it** (`grades.ruleset_id`) alongside the rules version.
  That's how a VIP agent's older, standard-ruleset grades survive untouched when they join the group.
- A grade's detail lives in `grades.payload_json`, so adding a field needs no migration. Five v4.1
  fields are *also* mirrored into columns because they are what a QC manager filters on:
  `outcome_status`, `case_type`, `risk_flag`, `catastrophic_service_failure`,
  `manual_review_needed`. They are NULL/0 on flat-ruleset grades, which is correct — only the
  gated model produces them.
- Schema changes are hand-written and idempotent: new tables go in `_SCHEMA` (always
  `CREATE TABLE IF NOT EXISTS`), new columns in `_migrate()` (`PRAGMA table_info` → `ALTER TABLE`).
  There is no Alembic and no version table.
- `conversations.fetched_at` is **last seen**, not first seen — the save is `INSERT OR REPLACE`,
  so re-fetching a period restamps every row in it. Use `first_seen_at` (preserved across the
  upsert) to ask when a conversation actually arrived. Confusing the two is what made the
  September incident look like a late import when nothing had arrived late at all.
- `grade_history` holds every superseded grade. Nothing deletes from it.
- The history index (`chat_labels`, `chat_labels_fts`, `case_verdicts`, `meta`) is derived data:
  `case_verdicts` is rebuilt from the human-verdict tables on every refresh, and `chat_labels`
  can be rebuilt from the chats for the price of the labels. `conversations.contact_id` is
  mirrored out of the payload for the player lookup.
- It's backed up automatically into `data/backups/`.
- To wipe cached conversations and start fresh: `./scripts/clear_conversations.sh`.
- **The Trash blocks re-import.** Deleting a conversation individually moves it to
  `deleted_conversations` *and* marks it `blacklist=1`, so later Intercom fetches skip it —
  otherwise a re-fetch would resurrect what an analyst deliberately removed. Bulk deletes
  ("Delete ALL" / by filter) store `blacklist=0` and can be re-imported. If a fetch reports
  more *fetched* than *stored*, this is why; restore or purge the items from the Trash.
  Trash entries are purged automatically after `TRASH_RETENTION_DAYS` (default 90) at web
  startup, or on demand from the Storage screen. To clear a specific batch:
  `scripts/purge_trash_batch.py --deleted-on YYYY-MM-DD --dry-run`.

---

## 7. Mini-glossary

- **Ollama** — the program that runs the AI model locally on this Mac. If grading fails with
  "connection refused," Ollama crashed — restart it.
- **Qwen** — the specific AI model (`qwen2.5:14b`) that grades conversations. Free, runs locally.
- **Grade / override** — the AI's score vs. a human-corrected score. Analysts and admins can override.
- **Rules version** — a fingerprint of the rulebook. Grades are tagged with it; editing the rules
  changes it for new grades. Old grades keep theirs and are never re-graded.
- **Job** — a long task (fetching or grading a batch) that runs in the background; the Evaluation
  page polls its progress.
- **Backend** — the Python server (`web/api.py`). **Frontend** — the React website
  (`web/frontend/`). They talk over `/api/...` URLs.

---

## 8. When in doubt

1. Figure out **which front door** (web / Slack / CLI) and **which layer** (screen → endpoint →
   service → storage) your change belongs to, using the table in §1 and the map in §2.
2. Make the change on a **branch**.
3. **Rebuild the frontend** (if touched) and **`./restart.sh`**.
4. **Run the tests.**
5. If you get stuck, the safest changes are config (`.env`), rules (Ruleset page), and users
   (`web_users.yaml`) — none of those need code.
