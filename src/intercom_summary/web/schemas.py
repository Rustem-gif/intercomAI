"""Pydantic request/response models for the web API."""
from __future__ import annotations

from pydantic import BaseModel


class LoginRequest(BaseModel):
    username: str
    password: str


class UserOut(BaseModel):
    username: str
    role: str


class FetchRequest(BaseModel):
    agents: list[str]
    since: str | None = None
    until: str | None = None
    state: str | None = None
    limit: int | None = None


class ReviewRequest(BaseModel):
    conversation_ids: list[str] | None = None
    agents: list[str] | None = None
    since: str | None = None
    until: str | None = None
    state: str | None = None
    brand: str | None = None    # grade only one brand of the workspace
    regrade: bool = False
    backend: str | None = None  # override QA_BACKEND setting: "ollama" | "api"


class JobOut(BaseModel):
    id: str
    kind: str
    status: str
    result: dict | None = None
    error: str | None = None


class RulesIn(BaseModel):
    text: str


class AgentGroupIn(BaseModel):
    agent_name: str
    group_id: str                          # "standard" | "vip"
    agent_email: str | None = None
    intercom_admin_id: str | None = None


class DeleteConversationsRequest(BaseModel):
    """Move conversations to the trash. Provide exactly one of:
    - `ids`: explicit conversation ids;
    - filter fields (agent/since/until/state/min_score/search/tag/ungraded): delete every
      conversation matching them (computed server-side);
    - `all=True`: delete everything.
    """
    ids: list[str] | None = None
    all: bool = False
    # Filter-based delete (mirrors GET /api/conversations).
    agent: list[str] | None = None
    since: str | None = None
    until: str | None = None
    state: str | None = None
    min_score: int | None = None
    search: str | None = None
    tag: str | None = None
    # Must mirror the UI's active brand: a filtered delete resolves its own id set
    # server-side, so without this a user scoped to one brand would delete across all of them.
    brand: str | None = None
    ungraded: bool = False


class TrashActionRequest(BaseModel):
    """Restore or purge trashed conversations. None ids + all=True = whole trash."""
    ids: list[str] | None = None
    all: bool = False


class TagsUpdate(BaseModel):
    tags: list[str]


class ManualDeduction(BaseModel):
    category: str          # an id from MANUAL_DEDUCTION_CATALOG (e.g. "info-correctness")
    points: int            # points to subtract (1–100)
    note: str = ""


class OverrideRequest(BaseModel):
    reason: str
    # Manual score override (the slider). Optional when `criteria`/`manual_deductions` given.
    score: int | None = None
    # ScoreBuddy-style per-criterion override: {criterion_id: "pass"|"fail"|"n/a"}.
    # When present (or with manual_deductions), the server recomputes the score and ignores `score`.
    criteria: dict[str, str] | None = None
    # Analyst manual deductions for things the AI can't verify (e.g. information correctness).
    manual_deductions: list[ManualDeduction] | None = None


class GradeDisputeCreate(BaseModel):
    reason: str


class GradeDisputeResolve(BaseModel):
    status: str          # "accepted" | "rejected"
    note: str = ""


class AiChatRequest(BaseModel):
    conversation_id: str
    message: str
    history: list[dict] = []


class IconicCaseIn(BaseModel):
    conversation_id: str
    comment: str = ""


class IconicCaseCommentUpdate(BaseModel):
    comment: str


class AgentLinkCreate(BaseModel):
    agent_name: str
    label: str
    tag: str | None = None
    expires_in_days: int | None = None  # None = never expires
    session_id: str | None = None       # if set, link points to a coaching session


class AgentLinkOut(BaseModel):
    token: str
    agent_name: str
    tag: str | None
    label: str
    created_by: str
    created_at: str
    expires_at: str | None
    session_id: str | None = None


class CoachingSessionCreate(BaseModel):
    agent_name: str
    title: str
    notes: str = ""
    due_date: str | None = None  # YYYY-MM-DD


class CoachingSessionUpdate(BaseModel):
    title: str | None = None
    notes: str | None = None
    due_date: str | None = None
    status: str | None = None  # 'open' | 'done'


class CoachingItemIn(BaseModel):
    conversation_id: str
    note: str = ""


class CommentIn(BaseModel):
    text: str
