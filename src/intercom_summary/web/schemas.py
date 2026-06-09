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


class DeleteConversationsRequest(BaseModel):
    ids: list[str] | None = None  # None / omitted = delete all


class TagsUpdate(BaseModel):
    tags: list[str]


class OverrideRequest(BaseModel):
    score: int
    reason: str


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


class AgentLinkOut(BaseModel):
    token: str
    agent_name: str
    tag: str | None
    label: str
    created_by: str
    created_at: str
    expires_at: str | None


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
