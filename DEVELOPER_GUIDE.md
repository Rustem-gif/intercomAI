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
> - Backend (Python) changes do **nothing** until you run `./restart.sh`.

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
- To add a third ruleset: add an entry to `config/rulesets.yaml` (name, `prompt_path`, criteria),
  add the group in `qa/rulesets.py` (`GROUP_RULESETS`), and write the prompt file. Everything else
  — grading, staleness, the UI tabs — picks it up automatically.
- ⚠️ Editing a ruleset's prompt marks **that ruleset's** grades outdated (a re-run re-grades them).
  Editing the VIP prompt does *not* affect standard grades, and vice versa.

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
