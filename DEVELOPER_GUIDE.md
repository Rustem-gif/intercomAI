# Developer Guide — How to Change Things

A plain-language map of this project so you can find **what to edit** and **how to ship it**,
without reading all the code. Read the first two sections once; use the rest as a lookup table.

---

## 1. The 60-second mental model

The product does four things, in this order:

```
   Intercom  ──fetch──►  SQLite cache  ──grade──►  grades  ──show──►  Web dashboard
  (support                (data/grades.db)        (local Qwen        (+ Slack bot)
   chats)                                          via Ollama)
```

1. **Fetch** support conversations from Intercom and store them locally.
2. **Grade** each conversation against your rulebook using a local AI model (Qwen, run by Ollama).
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
    │   └── models.py         ← The shape of a Conversation/Message/Admin in our code.
    │
    ├── qa/                   ← The AI grading engine.
    │   ├── backends.py       ← Chooses which grader to use (Qwen vs Claude API).
    │   ├── ollama_grader.py  ← Grades using local Qwen (the default).
    │   ├── grader.py         ← Grades using the Claude API (fallback, needs a key).
    │   ├── casino_prompt.py  ← The scoring rubric + JSON schema the AI must fill in. ⚠️ delicate.
    │   ├── schema.py          ← Turns the AI's answer into a score (does the math itself).
    │   ├── verdict_guard.py  ← Overturns greeting/name verdicts the transcript contradicts.
    │   ├── agent.py / chat.py ← The "Ask Qwen" chat assistant in the UI.
    │   └── rules.py          ← Loads/versions the rulebook.
    │
    ├── storage/              ← Reading/writing the database. One file per table.
    │   ├── conversations_store.py
    │   ├── grades_store.py    ← Grades + human overrides.
    │   ├── jobs_store.py      ← Background jobs (fetch/review progress).
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
| Conversations (`/conversations`) | `pages/Conversations.tsx` | Browse/search all chats |
| Needs Attention (`/needs-attention`) | `pages/NeedsAttention.tsx` | Flagged low scores |
| Agents (`/agents`) | `pages/Agents.tsx` | Per-agent performance |
| Evaluation (`/evaluation`) | `pages/Evaluation.tsx` | Run grading, see progress, Ollama restart |
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
- ⚠️ **Important side effect:** editing the rules creates a new "rules version," which marks
  **all existing grades as outdated** and a re-run will re-grade everything (slow). Only edit when
  you intend to re-grade.

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
  different ruleset"*. To convert them, run a review over that agent with **re-grade** enabled.
- The **group switcher** in the top bar (All / Standard / VIP) scopes every page. VIP and standard
  scores come from different criteria, so they are never averaged together — don't compare them.

### Change what the VIP ruleset checks (or add another ruleset)
A **ruleset** = a system prompt (what Qwen follows) + a criteria catalogue (ids, titles, points).
- Prompt text: **Ruleset** page → *VIP* tab (admin), or `rules/qa_system_prompt_vip.txt`.
- Criteria + points: `config/rulesets.yaml`.
- ⚠️ The points live in **both** places: the `Ded` column inside the prompt (what the AI applies
  while grading) and `config/rulesets.yaml` (what a manual re-score applies). They must agree — the
  Ruleset page shows an amber warning if they drift.
- To add a ruleset: add an entry to `config/rulesets.yaml` (name, `prompt_path`, `scoring`,
  criteria), write the prompt file, and — only if a whole agent *group* should use it —
  add the group in `qa/rulesets.py` (`GROUP_RULESETS`). Everything else — grading, staleness,
  the UI tabs — picks it up automatically.
- ⚠️ Editing a ruleset's prompt marks **that ruleset's** grades outdated (a re-run re-grades them).
  Editing the VIP prompt does *not* affect standard grades, and vice versa.

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

**Piloting a scoring model before adopting it.** Ruleset selection is normally by agent group, but
a review run can be forced onto one ruleset: `POST /api/review {"ruleset_id": "kb-v41", "sample":
"kb-v41-180"}`. Grades are stored under the ruleset that produced them, so a pilot cannot disturb
the scores or staleness of anything graded by another ruleset. The eventual cutover is one line —
`GROUP_RULESETS[GROUP_STANDARD]` in `qa/rulesets.py` — and old grades keep `ruleset_id='default'`
rather than being re-graded, exactly as the VIP rollout behaved.

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
- Browse one: `/api/conversations?sample=<id>`. List them with their gradeable counts:
  `/api/calibration/samples`.

### Switch which AI model does the grading
- In `.env`: `OLLAMA_MODEL=qwen2.5:14b` (current). This Mac only comfortably runs the 14b model;
  smaller ones grade worse. Then `./restart.sh`.
- To use the Claude API instead of local Qwen: set `QA_BACKEND=api` and `ANTHROPIC_API_KEY=...` in
  `.env`. (Costs money; local Qwen is free.)

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

### Change a setting (tokens, ports, model, timeouts)
- Everything configurable is an environment variable in **`.env`** (see `.env.example` for the full
  annotated list). `settings.py` just reads them. After editing `.env`, run `./restart.sh`.
- Common ones: `OLLAMA_MODEL`, `QA_BACKEND`, `INTERCOM_ACCESS_TOKEN`, `WEB_PORT`,
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

⚠️ **Editing any prompt file re-grades everything.** `rules_version` is a hash of the whole
prompt file (`qa/rulesets.py`), so a one-word change marks every existing grade stale. Nothing
re-grades on its own — `review_and_store` only runs when a human triggers it from the dashboard
or Slack — but the Evaluation page's "graded" count will drop until someone does.

### Exclude a kind of conversation from grading (e.g. follow-ups)
Triage/noise chats are never graded and never count towards an agent's score. They're picked
out by their **native Intercom tag**, listed in one place:

- `storage/conversations_store.py` → **`IGNORE_TAGS`** — currently `empty`, `spam`, `test`,
  `jira`, `follow-up`, `no request`. Add or remove a tag here and nothing else needs changing;
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
  changes it and makes old grades "stale."
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
