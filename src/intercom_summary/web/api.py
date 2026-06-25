"""FastAPI backend for the Intercom QA dashboard.

Exposes JSON routes under /api and serves the built React SPA for everything else.
Long-running fetch/review run as background jobs the frontend polls via /api/jobs/{id}.
"""
from __future__ import annotations

import asyncio
import base64
import secrets
import tempfile
import threading
from pathlib import Path

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware

from intercom_summary import service
from intercom_summary.logging_setup import get_logger
from intercom_summary.settings import settings
from intercom_summary.storage.conversations_store import ConversationsStore
from intercom_summary.storage.grades_store import GradesStore
from intercom_summary.storage.iconic_cases_store import IconicCasesStore
from intercom_summary.storage.jobs_store import JobsStore
from intercom_summary.web import auth
from intercom_summary.web.schemas import (
    AgentLinkCreate,
    AgentLinkOut,
    AiChatRequest,
    CoachingItemIn,
    CoachingSessionCreate,
    CoachingSessionUpdate,
    CommentIn,
    DeleteConversationsRequest,
    FetchRequest,
    IconicCaseCommentUpdate,
    IconicCaseIn,
    JobOut,
    LoginRequest,
    OverrideRequest,
    ReviewRequest,
    RulesIn,
    TagsUpdate,
    UserOut,
)

log = get_logger("web.api")

FRONTEND_DIST = Path(__file__).parent / "frontend" / "dist"

# ── Cancel registry: maps review job_id → threading.Event ────────────────────
_review_cancel_events: dict[str, threading.Event] = {}


class BasicAuthMiddleware(BaseHTTPMiddleware):
    """Optional HTTP Basic Auth gate in front of the entire app.

    Enabled by setting WEB_BASIC_AUTH=username:password in the environment.

    First visit: the browser shows its native credential dialog. On success a
    long-lived _gate cookie is set. All subsequent requests — including every
    AJAX call from the SPA — pass by presenting the cookie, so the browser
    never prompts again after the initial entry.

    The cookie value is a deterministic HMAC of the credentials + the app
    secret key, so it survives server restarts and is automatically invalidated
    if WEB_BASIC_AUTH or WEB_SECRET_KEY changes.
    """

    _COOKIE = "_gate"

    def __init__(self, app, username: str, password: str, secret: str) -> None:
        super().__init__(app)
        self._username = username
        self._password = password
        import hashlib
        self._token = hashlib.sha256(
            f"{username}:{password}:{secret}".encode()
        ).hexdigest()

    async def dispatch(self, request: Request, call_next):
        # Fast path: browser already has the gate cookie from a previous auth.
        if request.cookies.get(self._COOKIE) == self._token:
            return await call_next(request)

        # Slow path: validate the Basic Auth header.
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Basic "):
            try:
                decoded = base64.b64decode(auth_header[6:]).decode("utf-8", errors="replace")
                supplied_user, _, supplied_pass = decoded.partition(":")
                user_ok = secrets.compare_digest(supplied_user, self._username)
                pass_ok = secrets.compare_digest(supplied_pass, self._password)
                if user_ok and pass_ok:
                    response = await call_next(request)
                    # Set the gate cookie so the browser skips the dialog from now on.
                    response.set_cookie(
                        self._COOKIE,
                        self._token,
                        max_age=365 * 24 * 3600,
                        httponly=True,
                        samesite="lax",
                    )
                    return response
            except Exception:
                pass

        return Response(
            status_code=401,
            headers={"WWW-Authenticate": 'Basic realm="Intercom QA Dashboard"'},
            content="Unauthorized",
        )


def create_app() -> FastAPI:
    app = FastAPI(title="Intercom QA Dashboard")
    app.add_middleware(SessionMiddleware, secret_key=settings.web_secret_key, same_site="lax")

    if settings.web_basic_auth:
        if ":" not in settings.web_basic_auth:
            raise RuntimeError("WEB_BASIC_AUTH must be in 'username:password' format")
        ba_user, _, ba_pass = settings.web_basic_auth.partition(":")
        app.add_middleware(BasicAuthMiddleware, username=ba_user, password=ba_pass,
                           secret=settings.web_secret_key)
        log.info("HTTP Basic Auth gate enabled (user: %s)", ba_user)

    @app.on_event("startup")
    def _reconcile_orphaned_jobs() -> None:
        """A restart kills any in-flight background job, but its DB row still says
        'running'. Mark those as errored so they don't block new runs or hang the UI."""
        js = JobsStore()
        try:
            for j in js.list_recent(limit=50):
                if j["status"] in ("running", "queued", "cancelling"):
                    js.update(j["id"], status="error", error="interrupted by server restart")
        finally:
            js.close()

    # ── Auth ──────────────────────────────────────────────────────────────────
    @app.post("/api/auth/login", response_model=UserOut)
    def login(body: LoginRequest, request: Request):
        user = auth.users.authenticate(body.username, body.password)
        if not user:
            raise HTTPException(401, "Invalid username or password")
        request.session["user"] = user
        return user

    @app.post("/api/auth/logout")
    def logout(request: Request):
        request.session.clear()
        return {"ok": True}

    @app.get("/api/auth/me", response_model=UserOut)
    def me(user: dict = Depends(auth.current_user)):
        return user

    # ── Overview / data (read) ──────────────────────────────────────────────────
    @app.get("/api/overview")
    def overview(user: dict = Depends(auth.current_user)):
        return service.build_overview()

    @app.get("/api/agents")
    def agents(user: dict = Depends(auth.current_user)):
        # Agents present in the local cache (used for filtering existing conversations).
        store = ConversationsStore()
        try:
            return {"agents": store.agents()}
        finally:
            store.close()

    @app.get("/api/intercom/admins")
    async def intercom_admins(user: dict = Depends(auth.current_user)):
        # The full live teammate roster from Intercom (used to pick who to fetch).
        settings.require_intercom()
        return {"admins": await service.list_agents()}

    @app.get("/api/conversations")
    def conversations(
        user: dict = Depends(auth.current_user),
        agent: list[str] | None = Query(None),
        since: str | None = None,
        until: str | None = None,
        state: str | None = None,
        min_score: int | None = None,
        search: str | None = None,
        tag: str | None = None,
        sort: str = "created_at",
        descending: bool = True,
        limit: int = 50,
        offset: int = 0,
    ):
        store = ConversationsStore()
        try:
            rows, total = store.query(
                agents=agent, since=since, until=until, state=state,
                min_score=min_score, search=search, tag=tag, sort=sort,
                descending=descending, limit=limit, offset=offset,
            )
            return {"items": rows, "total": total, "limit": limit, "offset": offset}
        finally:
            store.close()

    @app.get("/api/conversations/{conversation_id}")
    def conversation_detail(conversation_id: str, user: dict = Depends(auth.current_user)):
        cstore = ConversationsStore()
        gstore = GradesStore()
        icstore = IconicCasesStore()
        try:
            convo = cstore.get(conversation_id)
            if not convo:
                raise HTTPException(404, "Conversation not found")
            # Fetch custom_tags from the DB row (not stored in Conversation model).
            row = cstore._conn.execute(
                "SELECT custom_tags FROM conversations WHERE id=?", (conversation_id,)
            ).fetchone()
            convo_dict = convo.to_dict()
            convo_dict["custom_tags"] = row["custom_tags"] if row else ""
            iconic = icstore.get(conversation_id)
            return {
                "conversation": convo_dict,
                "transcript": convo.transcript_text(),
                "grade": gstore.get(conversation_id),
                "sla": convo.sla_summary(
                    settings.sla_first_response_sec, settings.sla_followup_sec
                ),
                "iconic": iconic,
            }
        finally:
            cstore.close()
            gstore.close()
            icstore.close()

    @app.get("/api/tags")
    def list_tags(user: dict = Depends(auth.current_user)):
        store = ConversationsStore()
        try:
            return {"tags": store.all_tags()}
        finally:
            store.close()

    @app.put("/api/conversations/{conversation_id}/tags")
    def update_tags(conversation_id: str, body: TagsUpdate,
                    user: dict = Depends(auth.require_write)):
        store = ConversationsStore()
        try:
            if not store.update_custom_tags(conversation_id, body.tags):
                raise HTTPException(404, "Conversation not found")
        finally:
            store.close()
        return {"tags": body.tags}

    # ── Manager comments on conversations ─────────────────────────────────────
    @app.get("/api/conversations/{conversation_id}/comments")
    def list_comments(conversation_id: str, user: dict = Depends(auth.current_user)):
        from intercom_summary.storage.conversation_comments_store import ConversationCommentsStore

        store = ConversationCommentsStore()
        try:
            return {"comments": store.list(conversation_id)}
        finally:
            store.close()

    @app.post("/api/conversations/{conversation_id}/comments")
    def add_comment(conversation_id: str, body: CommentIn,
                    user: dict = Depends(auth.require_write)):
        if not body.text.strip():
            raise HTTPException(422, "Comment text is required")
        from intercom_summary.storage.conversation_comments_store import ConversationCommentsStore

        cstore = ConversationsStore()
        try:
            if not cstore.get(conversation_id):
                raise HTTPException(404, "Conversation not found")
        finally:
            cstore.close()
        store = ConversationCommentsStore()
        try:
            comment = store.add(conversation_id, user["username"], body.text.strip())
        finally:
            store.close()
        return comment

    @app.delete("/api/conversations/{conversation_id}/comments/{comment_id}")
    def delete_comment(conversation_id: str, comment_id: str,
                       user: dict = Depends(auth.require_write)):
        from intercom_summary.storage.conversation_comments_store import ConversationCommentsStore

        store = ConversationCommentsStore()
        try:
            comment = store.get(comment_id)
            if not comment or comment["conversation_id"] != conversation_id:
                raise HTTPException(404, "Comment not found")
            if user["role"] != "admin" and comment["author"] != user["username"]:
                raise HTTPException(403, "You can only delete your own comments")
            store.delete(comment_id)
        finally:
            store.close()
        return {"ok": True}

    @app.post("/api/conversations/{conversation_id}/override")
    def override_grade(conversation_id: str, body: OverrideRequest,
                       user: dict = Depends(auth.require_write)):
        if not (0 <= body.score <= 100):
            raise HTTPException(422, "Score must be 0–100")
        if not body.reason.strip():
            raise HTTPException(422, "Reason is required")
        gstore = GradesStore()
        try:
            if not gstore.save_override(conversation_id, body.score, body.reason, user["username"]):
                raise HTTPException(404, "No grade found for this conversation — grade it first")
        finally:
            gstore.close()
        return {"ok": True, "human_score": body.score}

    @app.get("/api/accuracy")
    def accuracy(user: dict = Depends(auth.current_user)):
        gstore = GradesStore()
        try:
            return gstore.accuracy_stats()
        finally:
            gstore.close()

    # ── Knowledge base (iconic cases) ─────────────────────────────────────────
    @app.get("/api/iconic-cases")
    def list_iconic_cases(user: dict = Depends(auth.current_user)):
        icstore = IconicCasesStore()
        cstore = ConversationsStore()
        gstore = GradesStore()
        try:
            cases = icstore.list_all()
            enriched = []
            for case in cases:
                cid = case["conversation_id"]
                row = cstore._conn.execute(
                    """SELECT c.id, c.agent_name, c.customer_name, c.subject, c.state,
                              c.created_at, COALESCE(g.human_score, g.overall_score) AS score
                       FROM conversations c
                       LEFT JOIN grades g ON g.conversation_id = c.id
                       WHERE c.id=?""",
                    (cid,),
                ).fetchone()
                enriched.append({
                    **case,
                    "conversation": dict(row) if row else None,
                })
            return {"items": enriched}
        finally:
            icstore.close()
            cstore.close()
            gstore.close()

    @app.post("/api/iconic-cases")
    def add_iconic_case(body: IconicCaseIn, user: dict = Depends(auth.require_write)):
        cstore = ConversationsStore()
        try:
            if not cstore.get(body.conversation_id):
                raise HTTPException(404, "Conversation not found")
        finally:
            cstore.close()
        icstore = IconicCasesStore()
        try:
            icstore.add(body.conversation_id, user["username"], body.comment)
        finally:
            icstore.close()
        return {"ok": True}

    @app.delete("/api/iconic-cases/{conversation_id}")
    def remove_iconic_case(conversation_id: str, user: dict = Depends(auth.require_write)):
        icstore = IconicCasesStore()
        try:
            if not icstore.remove(conversation_id):
                raise HTTPException(404, "Iconic case not found")
        finally:
            icstore.close()
        return {"ok": True}

    @app.put("/api/iconic-cases/{conversation_id}/comment")
    def update_iconic_comment(conversation_id: str, body: IconicCaseCommentUpdate,
                              user: dict = Depends(auth.require_write)):
        icstore = IconicCasesStore()
        try:
            if not icstore.update_comment(conversation_id, body.comment):
                raise HTTPException(404, "Iconic case not found")
        finally:
            icstore.close()
        return {"ok": True}

    @app.post("/api/repair/agent-names", response_model=JobOut)
    def repair_agent_names(background: BackgroundTasks,
                           user: dict = Depends(auth.require_write)):
        settings.require_intercom()
        js = JobsStore()
        job_id = js.create("repair", {})
        js.close()
        background.add_task(_run_repair_job, job_id)
        return JobOut(id=job_id, kind="repair", status="queued")

    @app.delete("/api/conversations/{conversation_id}")
    def delete_conversation(conversation_id: str, user: dict = Depends(auth.require_write)):
        cstore = ConversationsStore()
        gstore = GradesStore()
        try:
            if not cstore.delete(conversation_id):
                raise HTTPException(404, "Conversation not found")
            gstore.delete(conversation_id)
        finally:
            cstore.close()
            gstore.close()
        return {"deleted": 1}

    @app.post("/api/conversations/delete")
    def bulk_delete_conversations(body: DeleteConversationsRequest, user: dict = Depends(auth.require_write)):
        """Bulk delete. Pass ids=[] or omit to delete ALL conversations."""
        cstore = ConversationsStore()
        gstore = GradesStore()
        try:
            if not body.ids:
                ids = [r["id"] for r in cstore.query(limit=100_000)[0]]
            else:
                ids = body.ids
            deleted = cstore.delete_many(ids)
            gstore.delete_many(ids)
        finally:
            cstore.close()
            gstore.close()
        return {"deleted": deleted}

    # ── Jobs (fetch / review) ──────────────────────────────────────────────────
    @app.post("/api/fetch", response_model=JobOut)
    def fetch(body: FetchRequest, background: BackgroundTasks,
              user: dict = Depends(auth.require_write)):
        try:
            settings.require_intercom()
        except RuntimeError as exc:
            raise HTTPException(503, str(exc)) from exc
        js = JobsStore()
        job_id = js.create("fetch", body.model_dump())
        js.close()
        background.add_task(_run_fetch_job, job_id, body.model_dump())
        return JobOut(id=job_id, kind="fetch", status="queued")

    @app.post("/api/review", response_model=JobOut)
    def review(body: ReviewRequest, background: BackgroundTasks,
               user: dict = Depends(auth.require_write)):
        if body.backend and body.backend.lower() not in ("ollama", "api"):
            raise HTTPException(400, f"Unsupported grading backend '{body.backend}'. Use 'ollama' or 'api'.")
        try:
            settings.require_qa()
        except RuntimeError as exc:
            raise HTTPException(503, str(exc)) from exc
        js = JobsStore()
        try:
            # Only one review may run at a time — a single local model can't grade two
            # batches at once, and parallel runs duplicate work and thrash the GPU.
            # If a run is already active, return it instead of starting a duplicate.
            existing = next(
                (j for j in js.list_recent(kind="review", limit=10)
                 if j["status"] in ("queued", "running", "cancelling")),
                None,
            )
            if existing:
                return JobOut(id=existing["id"], kind="review", status=existing["status"],
                              result=existing["result"], error=existing["error"])
            job_id = js.create("review", body.model_dump())
        finally:
            js.close()
        background.add_task(_run_review_job, job_id, body.model_dump())
        return JobOut(id=job_id, kind="review", status="queued")

    @app.get("/api/jobs/{job_id}", response_model=JobOut)
    def job_status(job_id: str, user: dict = Depends(auth.current_user)):
        js = JobsStore()
        try:
            job = js.get(job_id)
        finally:
            js.close()
        if not job:
            raise HTTPException(404, "Job not found")
        return JobOut(id=job["id"], kind=job["kind"], status=job["status"],
                      result=job["result"], error=job["error"])

    # ── Evaluation management ──────────────────────────────────────────────────
    @app.get("/api/jobs")
    def list_jobs(kind: str | None = None, limit: int = 30,
                  user: dict = Depends(auth.current_user)):
        js = JobsStore()
        try:
            jobs = js.list_recent(kind=kind, limit=limit)
        finally:
            js.close()
        return {"jobs": [
            {"id": j["id"], "kind": j["kind"], "status": j["status"],
             "result": j["result"], "error": j["error"],
             "created_at": j.get("created_at"), "updated_at": j.get("updated_at")}
            for j in jobs
        ]}

    @app.get("/api/evaluation/stats")
    def evaluation_stats(user: dict = Depends(auth.current_user)):
        """Counts of conversations vs graded vs active job, used by the Evaluation page."""
        from intercom_summary.qa.backends import get_grader
        cstore = ConversationsStore()
        try:
            try:
                rules_version = get_grader().rules_version
            except Exception:
                rules_version = None
            # Count over the *gradeable* population (conversations without IGNORE_TAGS);
            # triage/noise chats are never graded, so excluding them lets coverage reach
            # 100%. "graded" counts any grade (filtering by the current rules_version
            # would falsely report 0 the moment the prompt is edited); "graded_current"
            # is the subset under the live ruleset, surfaced separately as "stale".
            counts = cstore.evaluation_counts(rules_version)
            total = counts["total"]
            graded = counts["graded"]
            graded_current = counts["graded_current"]
            ignored = counts["ignored"]
        finally:
            cstore.close()
        # Find the active review job (running or queued).
        js = JobsStore()
        try:
            active = next(
                (j for j in js.list_recent(kind="review", limit=5)
                 if j["status"] in ("running", "queued")),
                None,
            )
        finally:
            js.close()
        active_job = None
        if active:
            active_job = {
                "id": active["id"], "status": active["status"],
                "result": active["result"], "error": active["error"],
                "created_at": active.get("created_at"),
                "cancellable": active["id"] in _review_cancel_events,
            }
        return {
            "total": total,
            "graded": graded,
            "pending": max(0, total - graded),
            "stale": max(0, graded - graded_current),
            "ignored": ignored,
            "active_job": active_job,
        }

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel_job(job_id: str, user: dict = Depends(auth.require_write)):
        """Request graceful cancellation of a running review job."""
        event = _review_cancel_events.get(job_id)
        if not event:
            raise HTTPException(404, "Job not found or not cancellable")
        event.set()
        js = JobsStore()
        try:
            js.update(job_id, status="cancelling")
        finally:
            js.close()
        return {"ok": True, "job_id": job_id}

    # ── Ollama service control ────────────────────────────────────────────────
    @app.get("/api/ollama/health")
    def ollama_health(user: dict = Depends(auth.current_user)):
        """Is the local Ollama server reachable? Used to surface a 'restart' button
        when the grading model has crashed (e.g. an OOM/jetsam kill on this Mac)."""
        import httpx as _httpx

        try:
            resp = _httpx.get(f"{settings.ollama_base_url}/api/tags", timeout=3.0)
            resp.raise_for_status()
            models = [m.get("name") for m in resp.json().get("models", [])]
            return {"reachable": True, "models": models, "error": None}
        except Exception as exc:
            return {"reachable": False, "models": [], "error": str(exc)}

    @app.post("/api/ollama/restart")
    def ollama_restart(user: dict = Depends(auth.require_write)):
        """Restart the launchd-managed Ollama service via Homebrew.

        Robust against the crash mode where repeated OOM kills leave the service
        in a 'none' state with no respawn: `brew services restart` re-installs the
        plist and bootstraps it whether it was running, stopped, or gone.
        """
        import shutil
        import subprocess

        brew = shutil.which("brew") or "/opt/homebrew/bin/brew"
        env = {
            "PATH": "/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin",
            "HOME": str(Path.home()),
        }
        try:
            proc = subprocess.run(
                [brew, "services", "restart", "ollama"],
                capture_output=True,
                text=True,
                timeout=60,
                env=env,
            )
        except FileNotFoundError as exc:
            raise HTTPException(500, f"Homebrew not found ({brew}); cannot restart Ollama.") from exc
        except subprocess.TimeoutExpired as exc:
            raise HTTPException(504, "Timed out restarting Ollama (>60s).") from exc

        output = (proc.stdout + proc.stderr).strip()
        if proc.returncode != 0:
            log.error("Ollama restart failed (exit %s): %s", proc.returncode, output)
            raise HTTPException(500, f"Restart failed: {output or 'unknown error'}")
        log.info("Ollama service restarted by %s", user.get("username"))

        # Poll briefly so the UI can report whether the server actually came back.
        import time

        import httpx as _httpx

        reachable = False
        for _ in range(15):
            try:
                _httpx.get(f"{settings.ollama_base_url}/api/tags", timeout=2.0).raise_for_status()
                reachable = True
                break
            except Exception:
                time.sleep(1)
        return {"ok": True, "reachable": reachable, "message": output or "Ollama restart requested."}

    # ── Ruleset ────────────────────────────────────────────────────────────────
    @app.get("/api/rules")
    def get_rules(user: dict = Depends(auth.current_user)):
        from intercom_summary.qa.rules import load_ruleset

        rs = load_ruleset()
        return {"text": rs.text, "version": rs.version}

    @app.put("/api/rules")
    def put_rules(body: RulesIn, user: dict = Depends(auth.require_write)):
        settings.rules_path.write_text(body.text, encoding="utf-8")
        from intercom_summary.qa.rules import load_ruleset

        return {"ok": True, "version": load_ruleset().version}

    @app.get("/api/qa-prompt")
    def get_qa_prompt(user: dict = Depends(auth.current_user)):
        from intercom_summary.qa.casino_prompt import load_qa_prompt

        qp = load_qa_prompt()
        return {"text": qp.text, "version": qp.version}

    @app.put("/api/qa-prompt")
    def put_qa_prompt(body: RulesIn, user: dict = Depends(auth.require_admin)):
        from intercom_summary.qa.casino_prompt import load_qa_prompt

        settings.qa_prompt_path.parent.mkdir(parents=True, exist_ok=True)
        settings.qa_prompt_path.write_text(body.text, encoding="utf-8")
        qp = load_qa_prompt()
        log.info("QA system prompt updated by admin (version %s)", qp.version)
        return {"ok": True, "version": qp.version}

    # ── AI agent (Qwen + MCP-style Intercom tools) ───────────────────────────
    @app.post("/api/ai/agent")
    async def ai_agent(body: AiChatRequest, user: dict = Depends(auth.current_user)):
        from intercom_summary.qa.agent import run_agent

        return StreamingResponse(
            run_agent(body.message, body.history),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # ── AI chat (local Qwen via Ollama) ───────────────────────────────────────
    @app.post("/api/ai/chat")
    async def ai_chat(body: AiChatRequest, user: dict = Depends(auth.current_user)):
        from intercom_summary.qa.chat import stream_chat

        cstore = ConversationsStore()
        try:
            convo = cstore.get(body.conversation_id)
        finally:
            cstore.close()
        if not convo:
            raise HTTPException(404, "Conversation not found")

        return StreamingResponse(
            stream_chat(convo, body.message, body.history),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # ── Exports ────────────────────────────────────────────────────────────────
    @app.get("/api/export/conversations.xlsx")
    def export_conversations(user: dict = Depends(auth.current_user),
                             agent: list[str] | None = Query(None),
                             since: str | None = None, until: str | None = None,
                             state: str | None = None):
        from intercom_summary.export.xlsx import export_xlsx

        store = ConversationsStore()
        try:
            rows, _ = store.query(agents=agent, since=since, until=until,
                                  state=state, limit=10_000)
            convos = [c for r in rows if (c := store.get(r["id"]))]
        finally:
            store.close()
        out = Path(tempfile.mkdtemp()) / "conversations.xlsx"
        export_xlsx(convos, out)
        return FileResponse(out, filename="conversations.xlsx")

    @app.get("/api/export/qa.xlsx")
    def export_qa(user: dict = Depends(auth.current_user)):
        from intercom_summary.qa.report import report_xlsx
        from intercom_summary.qa.schema import ConversationGrade

        gstore = GradesStore()
        try:
            grades = [ConversationGrade.from_dict(d) for d in gstore.all()]
        finally:
            gstore.close()
        out = Path(tempfile.mkdtemp()) / "qa_report.xlsx"
        report_xlsx(grades, out)
        return FileResponse(out, filename="qa_report.xlsx")

    # ── Agent review links (shareable, token-gated) ───────────────────────────
    @app.post("/api/agent-links", response_model=AgentLinkOut)
    def create_agent_link(body: AgentLinkCreate, user: dict = Depends(auth.require_write)):
        from datetime import datetime, timedelta, timezone
        from intercom_summary.storage.agent_tokens_store import AgentTokensStore

        token = secrets.token_urlsafe(24)
        expires_at = None
        if body.expires_in_days:
            expires_at = (
                datetime.now(timezone.utc) + timedelta(days=body.expires_in_days)
            ).isoformat()
        # When linking to a coaching session, validate it exists and grab agent_name.
        agent_name = body.agent_name
        if body.session_id:
            from intercom_summary.storage.coaching_store import CoachingStore
            cs = CoachingStore()
            try:
                session = cs.get_session(body.session_id)
            finally:
                cs.close()
            if not session:
                raise HTTPException(404, "Coaching session not found")
            agent_name = session["agent_name"]

        store = AgentTokensStore()
        try:
            store.create(
                token=token,
                agent_name=agent_name,
                label=body.label,
                created_by=user["username"],
                tag=body.tag or None,
                expires_at=expires_at,
                session_id=body.session_id or None,
            )
            result = store.get(token)
        finally:
            store.close()
        return result

    @app.get("/api/agent-links")
    def list_agent_links(
        agent: str | None = None,
        user: dict = Depends(auth.require_write),
    ):
        from intercom_summary.storage.agent_tokens_store import AgentTokensStore

        store = AgentTokensStore()
        try:
            links = store.list_by_agent(agent) if agent else store.list_all()
        finally:
            store.close()
        return {"items": links}

    @app.delete("/api/agent-links/{token}")
    def delete_agent_link(token: str, user: dict = Depends(auth.require_write)):
        from intercom_summary.storage.agent_tokens_store import AgentTokensStore

        store = AgentTokensStore()
        try:
            if not store.delete(token):
                raise HTTPException(404, "Link not found")
        finally:
            store.close()
        return {"ok": True}

    # ── Public review portal (no session required) ─────────────────────────────
    @app.get("/api/review/{token}")
    def review_portal(token: str):
        from intercom_summary.storage.agent_tokens_store import AgentTokensStore

        tstore = AgentTokensStore()
        try:
            link = tstore.get(token)
        finally:
            tstore.close()
        if not link:
            raise HTTPException(404, "This review link is invalid or has expired.")

        # Coaching mode: return session data + per-item notes instead of a raw list.
        if link.get("session_id"):
            from intercom_summary.storage.coaching_store import CoachingStore
            cs = CoachingStore()
            gstore = GradesStore()
            cstore = ConversationsStore()
            try:
                session = cs.get_session(link["session_id"])
                if not session:
                    raise HTTPException(404, "Coaching session not found.")
                raw_items = cs.get_items(link["session_id"])
                items = []
                for item in raw_items:
                    cid = item["conversation_id"]
                    row = cstore._conn.execute(
                        """SELECT c.id, c.agent_name, c.customer_name, c.subject,
                                  c.state, c.created_at,
                                  COALESCE(g.human_score, g.overall_score) AS score
                           FROM conversations c
                           LEFT JOIN grades g ON g.conversation_id = c.id
                           WHERE c.id=?""",
                        (cid,),
                    ).fetchone()
                    items.append({**item, "conversation": dict(row) if row else None})
            finally:
                cs.close()
                gstore.close()
                cstore.close()
            return {
                "mode": "coaching",
                "agent_name": link["agent_name"],
                "label": link["label"],
                "expires_at": link["expires_at"],
                "session": {
                    "id": session["id"],
                    "title": session["title"],
                    "notes": session["notes"],
                    "due_date": session["due_date"],
                    "status": session["status"],
                },
                "items": items,
                "total": len(items),
                # plain-review fields set to None for type compat
                "tag": None,
                "conversations": [],
            }

        # Plain review mode: filtered conversation list.
        cstore = ConversationsStore()
        try:
            rows, total = cstore.query(
                agents=[link["agent_name"]],
                tag=link["tag"] or None,
                sort="created_at",
                descending=True,
                limit=500,
            )
        finally:
            cstore.close()
        return {
            "mode": "review",
            "agent_name": link["agent_name"],
            "label": link["label"],
            "tag": link["tag"],
            "expires_at": link["expires_at"],
            "conversations": rows,
            "total": total,
            "session": None,
            "items": [],
        }

    @app.post("/api/review/{token}/finish")
    def finish_coaching(token: str):
        """Agent marks the coaching session as done through the portal."""
        from intercom_summary.storage.agent_tokens_store import AgentTokensStore
        from intercom_summary.storage.coaching_store import CoachingStore

        tstore = AgentTokensStore()
        try:
            link = tstore.get(token)
        finally:
            tstore.close()
        if not link:
            raise HTTPException(404, "Invalid or expired link.")
        if not link.get("session_id"):
            raise HTTPException(400, "This link is not tied to a coaching session.")
        cs = CoachingStore()
        try:
            cs.update_session(link["session_id"], status="done")
        finally:
            cs.close()
        return {"ok": True}

    @app.post("/api/review/{token}/conversations/{conversation_id}/acknowledge")
    def acknowledge_conversation(token: str, conversation_id: str):
        from intercom_summary.storage.acknowledgments_store import AcknowledgmentsStore
        from intercom_summary.storage.agent_tokens_store import AgentTokensStore

        tstore = AgentTokensStore()
        try:
            if not tstore.get(token):
                raise HTTPException(404, "Invalid or expired link.")
        finally:
            tstore.close()
        astore = AcknowledgmentsStore()
        try:
            acknowledged = astore.acknowledge(token, conversation_id)
        finally:
            astore.close()
        return {"acknowledged": acknowledged}

    @app.get("/api/review/{token}/acknowledgments")
    def get_acknowledgments(token: str):
        from intercom_summary.storage.acknowledgments_store import AcknowledgmentsStore
        from intercom_summary.storage.agent_tokens_store import AgentTokensStore

        tstore = AgentTokensStore()
        try:
            if not tstore.get(token):
                raise HTTPException(404, "Invalid or expired link.")
        finally:
            tstore.close()
        astore = AcknowledgmentsStore()
        try:
            ids = list(astore.get_acknowledged_ids(token))
        finally:
            astore.close()
        return {"acknowledged_ids": ids}

    @app.get("/api/review/{token}/conversations/{conversation_id}")
    def review_portal_detail(token: str, conversation_id: str):
        from intercom_summary.storage.agent_tokens_store import AgentTokensStore

        tstore = AgentTokensStore()
        try:
            link = tstore.get(token)
        finally:
            tstore.close()
        if not link:
            raise HTTPException(404, "This review link is invalid or has expired.")

        cstore = ConversationsStore()
        gstore = GradesStore()
        try:
            convo = cstore.get(conversation_id)
            if not convo:
                raise HTTPException(404, "Conversation not found")
            # Verify this conversation belongs to the agent the token was issued for.
            db_row = cstore._conn.execute(
                "SELECT agent_name, custom_tags FROM conversations WHERE id=?", (conversation_id,)
            ).fetchone()
            if not db_row or db_row["agent_name"] != link["agent_name"]:
                raise HTTPException(403, "This conversation is not part of your review.")
            convo_dict = convo.to_dict()
            convo_dict["custom_tags"] = db_row["custom_tags"] if db_row else ""
            return {
                "conversation": convo_dict,
                "transcript": convo.transcript_text(),
                "grade": gstore.get(conversation_id),
                "sla": convo.sla_summary(
                    settings.sla_first_response_sec, settings.sla_followup_sec
                ),
                "iconic": None,
            }
        finally:
            cstore.close()
            gstore.close()

    # ── Per-agent score trend ──────────────────────────────────────────────────
    @app.get("/api/agents/trend")
    def agent_trend(agent: str, user: dict = Depends(auth.current_user)):
        gstore = GradesStore()
        try:
            rows = gstore._conn.execute(
                """SELECT DATE(graded_at) AS day,
                          ROUND(AVG(COALESCE(human_score, overall_score)), 1) AS avg_score,
                          COUNT(*) AS count
                   FROM grades
                   WHERE agent_name = ? AND graded_at IS NOT NULL
                   GROUP BY day
                   ORDER BY day ASC""",
                (agent,),
            ).fetchall()
            return {"trend": [dict(r) for r in rows]}
        finally:
            gstore.close()

    # ── Coaching sessions ──────────────────────────────────────────────────────
    @app.get("/api/coaching")
    def list_coaching_sessions(
        agent: str | None = None,
        user: dict = Depends(auth.current_user),
    ):
        from intercom_summary.storage.coaching_store import CoachingStore

        store = CoachingStore()
        try:
            sessions = store.list_sessions(agent_name=agent)
        finally:
            store.close()
        return {"items": sessions}

    @app.post("/api/coaching")
    def create_coaching_session(
        body: CoachingSessionCreate,
        user: dict = Depends(auth.require_write),
    ):
        from intercom_summary.storage.coaching_store import CoachingStore

        store = CoachingStore()
        try:
            session_id = store.create_session(
                agent_name=body.agent_name,
                title=body.title,
                notes=body.notes,
                due_date=body.due_date,
                created_by=user["username"],
            )
            session = store.get_session(session_id)
        finally:
            store.close()
        return session

    @app.get("/api/coaching/{session_id}")
    def get_coaching_session(session_id: str, user: dict = Depends(auth.current_user)):
        from intercom_summary.storage.coaching_store import CoachingStore

        store = CoachingStore()
        cstore = ConversationsStore()
        gstore = GradesStore()
        try:
            session = store.get_session(session_id)
            if not session:
                raise HTTPException(404, "Coaching session not found")
            raw_items = store.get_items(session_id)
            items = []
            for item in raw_items:
                cid = item["conversation_id"]
                row = cstore._conn.execute(
                    """SELECT c.id, c.agent_name, c.customer_name, c.subject, c.state,
                              c.created_at, COALESCE(g.human_score, g.overall_score) AS score
                       FROM conversations c
                       LEFT JOIN grades g ON g.conversation_id = c.id
                       WHERE c.id=?""",
                    (cid,),
                ).fetchone()
                items.append({**item, "conversation": dict(row) if row else None})
        finally:
            store.close()
            cstore.close()
            gstore.close()
        return {**session, "items": items}

    @app.put("/api/coaching/{session_id}")
    def update_coaching_session(
        session_id: str,
        body: CoachingSessionUpdate,
        user: dict = Depends(auth.require_write),
    ):
        from intercom_summary.storage.coaching_store import CoachingStore

        store = CoachingStore()
        try:
            if not store.update_session(
                session_id,
                title=body.title,
                notes=body.notes,
                due_date=body.due_date,
                status=body.status,
            ):
                raise HTTPException(404, "Coaching session not found")
            session = store.get_session(session_id)
        finally:
            store.close()
        return session

    @app.delete("/api/coaching/{session_id}")
    def delete_coaching_session(session_id: str, user: dict = Depends(auth.require_write)):
        from intercom_summary.storage.coaching_store import CoachingStore

        store = CoachingStore()
        try:
            if not store.delete_session(session_id):
                raise HTTPException(404, "Coaching session not found")
        finally:
            store.close()
        return {"ok": True}

    @app.post("/api/coaching/{session_id}/items")
    def add_coaching_item(
        session_id: str,
        body: CoachingItemIn,
        user: dict = Depends(auth.require_write),
    ):
        from intercom_summary.storage.coaching_store import CoachingStore

        cstore = ConversationsStore()
        try:
            if not cstore.get(body.conversation_id):
                raise HTTPException(404, "Conversation not found")
        finally:
            cstore.close()
        store = CoachingStore()
        try:
            if not store.get_session(session_id):
                raise HTTPException(404, "Coaching session not found")
            store.add_item(session_id, body.conversation_id, body.note)
        finally:
            store.close()
        return {"ok": True}

    @app.delete("/api/coaching/{session_id}/items/{conversation_id}")
    def remove_coaching_item(
        session_id: str,
        conversation_id: str,
        user: dict = Depends(auth.require_write),
    ):
        from intercom_summary.storage.coaching_store import CoachingStore

        store = CoachingStore()
        try:
            if not store.remove_item(session_id, conversation_id):
                raise HTTPException(404, "Item not found")
        finally:
            store.close()
        return {"ok": True}

    # ── Serve the built SPA (must be mounted last) ──────────────────────────────
    if FRONTEND_DIST.exists():
        app.mount("/", SPAStaticFiles(directory=FRONTEND_DIST, html=True), name="spa")
    else:
        @app.get("/")
        def not_built():
            return JSONResponse(
                {"detail": "Frontend not built. Run `npm install && npm run build` in "
                           "src/intercom_summary/web/frontend."},
                status_code=200,
            )

    return app


class SPAStaticFiles(StaticFiles):
    """Serve index.html for unknown paths so client-side routing works.

    API paths are never caught by this fallback — they propagate the 404 so
    FastAPI (or a real 404 handler) can respond with JSON instead of HTML.
    """

    async def get_response(self, path: str, scope):  # type: ignore[override]
        from starlette.exceptions import HTTPException as StarletteHTTPException

        # Strip any leading slash that Starlette may or may not include.
        norm = path.lstrip("/")
        if norm == "api" or norm.startswith("api/"):
            # Let FastAPI routes answer; never silently return index.html.
            raise StarletteHTTPException(status_code=404)

        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code == 404:
                return await super().get_response("index.html", scope)
            raise


# ── Background job runners ───────────────────────────────────────────────────────
def _run_fetch_job(job_id: str, params: dict) -> None:
    js = JobsStore()
    js.update(job_id, status="running")
    try:
        filtered = {k: v for k, v in params.items() if k in
                    ("agents", "since", "until", "state", "limit")}

        def on_progress(fetched: int, total: int) -> None:
            js.update(job_id, result={"fetched": fetched, "total": total, "partial": True})

        result = asyncio.run(service.fetch_and_store(**filtered, on_progress=on_progress))
        js.update(job_id, status="done", result=result)
    except Exception as e:  # surface to UI, keep server alive
        log.exception("fetch job failed")
        js.update(job_id, status="error", error=str(e))
    finally:
        js.close()


def _run_review_job(job_id: str, params: dict) -> None:
    cancel_event = threading.Event()
    _review_cancel_events[job_id] = cancel_event
    js = JobsStore()
    js.update(job_id, status="running")
    try:
        def on_progress(graded: int, skipped: int, total: int) -> None:
            js.update(job_id, result={
                "graded": graded, "skipped": skipped, "total": total, "partial": True,
            })

        result = service.review_and_store(**params, on_progress=on_progress,
                                          cancel_event=cancel_event)
        if result.get("backend_unreachable"):
            # Backend (Ollama) died mid-run — don't pass this off as a completed review.
            js.update(job_id, status="error",
                      error="Grading backend (Ollama) became unreachable — run aborted. "
                            "Start Ollama and re-run; ungraded conversations will be retried.",
                      result=result)
        else:
            final_status = "cancelled" if result.get("cancelled") else "done"
            js.update(job_id, status=final_status, result=result)
    except Exception as e:
        log.exception("review job failed")
        js.update(job_id, status="error", error=str(e))
    finally:
        _review_cancel_events.pop(job_id, None)
        js.close()


def _run_repair_job(job_id: str) -> None:
    js = JobsStore()
    js.update(job_id, status="running")
    try:
        result = asyncio.run(service.repair_agent_names())
        js.update(job_id, status="done", result=result)
        # Invalidation hint for the UI — overview KPIs will be stale until refresh.
    except Exception as e:
        log.exception("repair job failed")
        js.update(job_id, status="error", error=str(e))
    finally:
        js.close()


app = create_app()


def main() -> None:
    import uvicorn

    log.info("Starting Intercom QA dashboard on %s:%d", settings.web_host, settings.web_port)
    uvicorn.run(app, host=settings.web_host, port=settings.web_port)


if __name__ == "__main__":
    main()
