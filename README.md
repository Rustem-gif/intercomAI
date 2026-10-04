# Intercom Summary & QA Platform

Fetch Intercom support-agent conversations, browse/slice them, and **QA-grade** them
against an editable ruleset with **Claude Opus 4.8** — through three interfaces: a **web
dashboard**, an interactive **Slack bot**, and a **CLI**.

```
 ┌────────────── shared service layer (service.py) ──────────────┐
 │   fetch_and_store · review_and_store · build_overview         │
 └──────┬──────────────────────┬───────────────────────┬─────────┘
  Web (React/shadcn)      Slack (Block Kit)          CLI
  FastAPI + sessions      Socket Mode modal       intercom-summary
        │                      │                       │
        ▼                      ▼                       ▼
   Intercom API (EU)  ·  Claude Opus 4.8  ·  SQLite (conversations + grades + jobs)
```

---

## What is implemented (working today)

### Core — fetch · export · QA (CLI) ✅
- **`intercom/client.py`** — async Intercom REST client: bearer auth, pinned API version,
  **EU base URL** (`https://api.eu.intercom.io`), 429/5xx backoff, cursor pagination.
- **`intercom/fetch.py`** — resolve agents by **name or email** → admin id, search
  (`admin_assignee_id` + date window + state), fetch full threads, normalise, HTML→text.
- **`export/xlsx.py` / `transcript.py`** — **Summary + Messages** workbook; per-conversation
  Markdown transcripts.
- **QA backends** (`QA_BACKEND`): **`api`** (default) grades with **Claude Sonnet 5.5** via the
  Anthropic SDK (`qa/grader.py`, structured outputs + prompt caching); **`ollama`** grades locally
  with **Qwen** (`qa/ollama_grader.py`) and is kept as an optional fallback. `qa/backends.py`
  selects; shared transcript in `qa/prompt.py`. Standard chats from `QA_V41_EFFECTIVE_FROM` on are
  graded under **QA Manual v4.1** (`kb-v41`, gated scoring); older ones keep `default`. **`qa/report.py`** aggregates per-agent.
- **`rules/support_rules.md`** — the **editable ruleset** you own.
- **`storage/`** — SQLite: `grades`, `conversations` (browse cache), `jobs`. Grading is
  **idempotent** per ruleset version.
- **`service.py`** — shared orchestration used by *all* interfaces.
- **CLI:** `intercom-summary fetch …` and `intercom-summary review …`

### Web dashboard (bento-first) ✅
- **Backend** `web/api.py` (FastAPI): cookie-session auth (`web/auth.py`, users in
  `config/web_users.yaml`, roles `admin`/`analyst`/`viewer`), JSON routes for overview,
  conversations (filter/paginate), detail (transcript + grade), agents, ruleset
  view/edit, XLSX export, and background **fetch/review jobs** with polling. Serves the
  built SPA. Console script: **`intercom-web`**.
- **Frontend** `web/frontend/` (React + Vite + TS + Tailwind + shadcn-style components,
  TanStack Query, Recharts; slate+indigo, light/dark):
  - **Overview** — bento grid: KPI stats, score-trend chart, top violations, agent
    leaderboard, "needs attention" list, and **Fetch / Run QA / Grade all** actions.
  - **Conversations** — filterable table → split drawer (chat transcript + per-rule grade).
  - **Agents** — average-score bar chart + breakdown.
  - **Ruleset** — edit `support_rules.md` in-browser (analyst/admin).

### Slack bot (interactive, Socket Mode) ✅
- **`slack/blocks.py`** — Block Kit **modal** (agents + date pickers + state + action) and
  result messages with **buttons** (Run QA, Open dashboard).
- **`slack/handlers.py`** — `/intercom` opens the modal; `view_submission` and the `run_qa`
  button run through `service.py`; typed `/intercom fetch agent:…` still works.
- **`slack/auth.py`** — role gate from `config/roles.yaml`; non-analysts are refused.

### Tests ✅
`pytest` → **34 passing**, fully offline (mocked Intercom via respx, fake Anthropic, FastAPI
TestClient). Covers client pagination/retry, fetch/normalise, export, storage round-trips,
service overview/review, web auth + role gate + jobs, and Slack block builders/parsers.

---

## Setup

```bash
# 1. Python
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env                 # fill in tokens (see table below)
pytest                               # 34 passing, offline

# 2. Frontend (one-time build so intercom-web can serve it)
cd src/intercom_summary/web/frontend
npm install && npm run build
cd -
```

### `.env` — fill in by hand

| Key | Where to get it |
|---|---|
| `INTERCOM_ACCESS_TOKEN` | Intercom → Settings → Developer Hub → your app → Access token |
| `INTERCOM_REGION` | `eu` (your workspace is EU-hosted) |
| `QA_BACKEND` | `api` (default, Claude API) or `ollama` (local Qwen) |
| `ANTHROPIC_API_KEY` | console.anthropic.com → API keys (needed for `QA_BACKEND=api`) |
| `QA_MODEL` / `QA_EFFORT` | `claude-sonnet-5-5` / `medium` |
| `QA_V41_EFFECTIVE_FROM` | `YYYY-MM-DD` — standard chats created on/after it are graded under v4.1 |
| `SLACK_BOT_TOKEN` / `SLACK_APP_TOKEN` | Slack app — bot `xoxb-…` + app-level `xapp-…` (Socket Mode) |
| `WEB_SECRET_KEY` | any long random string (signs session cookies) |
| `WEB_BASE_URL` | public URL of the dashboard (Slack "Open dashboard" buttons) |

---

## Running

```bash
intercom-web                      # web dashboard at WEB_HOST:WEB_PORT (default :8000)
python scripts/run_slack_bot.py   # Slack bot (or: intercom-slack-bot)

# CLI
intercom-summary fetch  --agent ada@co.com --since 2026-05-01 --transcripts
intercom-summary review --agent ada@co.com --since 2026-05-01
```

**Web:** log in (seeded **admin / admin** — change it!), hit **Fetch** on the Overview to
pull conversations, **Run QA** to grade, browse in **Conversations**, edit policy in
**Ruleset**.

**Slack:** `/intercom` opens the panel; pick agents/dates/action → results post back with
buttons. `/intercom whoami`, `/intercom help`, and typed `/intercom fetch agent:…` also work.

### Grading
With `QA_BACKEND=api` (default), **Run QA** / `intercom-summary review` grade with
**Claude Sonnet 5.5** (~9 s and ~$0.02 per chat). Try a model/effort/prompt change on live chats
without saving anything first: `.venv/bin/python scripts/dry_run_grades.py -n 10`.
`QA_BACKEND=ollama` grades with a local Qwen model instead (`brew services start ollama`).
```bash
intercom-web                                   # backend on :8000
cd src/intercom_summary/web/frontend && npm run dev   # Vite on :5173, proxies /api
```

---

## What you must do by hand (one-time)

1. **Intercom token** → `.env` (read access to conversations + admins).
2. **QA backend** → set `ANTHROPIC_API_KEY` (default backend `api`, model `claude-sonnet-5-5`).
   Only needed for `QA_BACKEND=ollama`: a local Ollama server with `qwen2.5:14b` pulled.
3. **Slack app** (api.slack.com/apps):
   - **Socket Mode** ON → App-Level Token (`connections:write`) → `SLACK_APP_TOKEN`.
   - **Interactivity** ON (required for the modal/buttons).
   - **Bot scopes**: `commands`, `chat:write`, `files:write`, `users:read` → install →
     `SLACK_BOT_TOKEN`.
   - Create the `/intercom` **slash command** (Request URL can be a placeholder under
     Socket Mode). Invite the bot to your channel.
   - Add allowed Slack member IDs to **`config/roles.yaml`** under `analyst`.
4. **Web users** → edit **`config/web_users.yaml`**: change the default `admin/admin`,
   add teammates with `analyst` (full) or `viewer` (read-only). Hash a password with
   `python -c "from intercom_summary.web.auth import hash_password; print(hash_password('pw'))"`.
   Add an optional `display_name:` to show a person's name instead of their login
   everywhere in the dashboard (the username is still what gets stored).
5. **Edit `rules/support_rules.md`** to your real policy (changing it re-grades).

## Suggested next steps (optional, not built)
- Scheduled nightly `review` + trend digests.
- Cache `list_admins()` / conversation fetches for large ranges.
- Filter by *participant* admin, not just current assignee.
- Hosting/TLS + moving web users to a real identity provider.

---

## Project layout

```
config/roles.yaml                 # Slack role allowlist (edit me)
config/web_users.yaml             # web logins + roles (edit me)
rules/support_rules.md            # QA ruleset (edit me)
src/intercom_summary/
  settings.py                     # env/config (single source of truth)
  service.py                      # shared orchestration (web + slack + cli)
  intercom/{client,fetch,models,htmltext}.py
  export/{xlsx,transcript}.py
  qa/{rules,schema,prompt,grader,ollama_grader,backends,report}.py
  storage/{db,grades_store,conversations_store,jobs_store}.py
  slack/{app,auth,handlers,blocks}.py
  web/{api,auth,schemas}.py
  web/frontend/                   # React + Vite + Tailwind + shadcn-style UI
  cli.py
scripts/run_slack_bot.py
tests/
```
