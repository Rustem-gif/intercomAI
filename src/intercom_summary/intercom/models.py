"""Normalised data shapes for Intercom objects.

The raw Intercom API returns large, deeply-nested JSON. We flatten the bits we care
about into small dataclasses so the rest of the codebase (export, QA, Slack) never has
to know the wire format.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


def ts_to_dt(ts: int | None) -> datetime | None:
    """Intercom timestamps are unix seconds (UTC)."""
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc)


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _parse_iso(s: str | None) -> datetime | None:
    return datetime.fromisoformat(s) if s else None


def fmt_duration(seconds: int | float | None) -> str:
    """Human-readable duration: 45s → "45s", 492 → "8m 12s", 3780 → "1h 03m"."""
    if seconds is None:
        return "—"
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m {seconds % 60:02d}s"
    return f"{seconds // 3600}h {(seconds % 3600) // 60:02d}m"


@dataclass
class Admin:
    id: str
    name: str
    email: str = ""


@dataclass
class Contact:
    id: str = ""
    name: str = ""
    email: str = ""


@dataclass
class Message:
    """A single part of a conversation thread."""
    seq: int
    author_type: str          # "admin" | "user"/"contact" | "bot" | "system"
    author_name: str
    created_at: datetime | None
    text: str                 # cleaned plain text (HTML stripped)
    part_type: str = ""       # e.g. "comment", "note", "assignment", "close"


@dataclass
class Conversation:
    id: str
    created_at: datetime | None
    updated_at: datetime | None
    state: str                       # "open" | "closed" | "snoozed"
    # True when Intercom classes this thread as a *ticket* rather than a chat. Tickets are
    # filtered out at fetch time (see intercom/fetch.is_ticket), so this is False on
    # everything that reaches the cache — it exists so the filter is visible in the data
    # rather than only in the code that applies it.
    is_ticket: bool = False
    subject: str = ""
    assignee: Admin | None = None
    contact: Contact = field(default_factory=Contact)
    messages: list[Message] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    csat_rating: int | None = None   # 1-5 if a rating was left
    csat_remark: str = ""
    # Which brand of the multi-brand workspace this came through, as Intercom names it
    # ("Betncare" is King Billy — see intercom/brands.py). "" when unknown.
    brand: str = ""
    # Selected metrics from the Intercom `statistics` object (seconds).
    first_response_time: int | None = None
    time_to_close: int | None = None

    @property
    def message_count(self) -> int:
        return len(self.messages)

    @property
    def assignee_name(self) -> str:
        return self.assignee.name if self.assignee else ""

    @property
    def closed_by(self) -> str:
        """Who closed the thread: "admin", "bot", or "" when it was never closed.

        In this workspace the bot closes 52.8% of chats after the player goes quiet, yet three
        criteria — res-no-fake-close, close-confirm, close-courtesy — ask the grader to judge
        the agent's closing behaviour, two of them with an N/A clause reading "agent didn't
        close the chat". Nothing in the prompt supplied that fact, so the escape was taken on
        6.8% of verdicts and the agent was penalised for the bot's automation.

        Derived rather than stored: Intercom sends a `close` part whose author names the closer,
        and `normalise_conversation` already keeps it, so every conversation ever cached can
        answer this without a re-fetch or a migration. The part carries no text, so
        `transcript_text` drops it — this property is the only place the fact survives.
        """
        for m in reversed(self.messages):
            if m.part_type == "close":
                return m.author_type
        return ""

    @property
    def display_subject(self) -> str:
        """Subject for display: uses the Intercom subject when set, otherwise
        falls back to the first customer message text (truncated)."""
        if self.subject:
            return self.subject
        for m in self.messages:
            if m.author_type in ("user", "contact") and m.text:
                text = m.text.strip()
                return text[:72] + "…" if len(text) > 72 else text
        return ""

    def web_url(self, base_app_url: str = "https://app.intercom.com") -> str:
        # Best-effort deep link; workspace-id-specific URLs vary, this opens the convo.
        return f"{base_app_url}/a/inbox/_/inbox/conversation/{self.id}"

    @staticmethod
    def _role_of(author_type: str) -> str:
        if author_type == "admin":
            return "AGENT"
        # A "lead" is Intercom's not-yet-identified visitor — a real customer, not a system
        # actor. Labelling them LEAD hid them from the grader as a person and suppressed the
        # "waited" SLA marker below, which only fires when the previous turn was a customer.
        if author_type in ("user", "contact", "lead"):
            return "CUSTOMER"
        return author_type.upper()

    def sla_summary(self, first_response_target: int, followup_target: int) -> dict:
        """SLA facts for this conversation, for the UI and the grader prompt.

        Uses Intercom's authoritative `first_response_time` (seconds) when available.
        Targets are passed in (from settings) so this stays config-free.
        """
        frt = self.first_response_time
        return {
            "first_response_time": frt,
            "first_response_time_human": fmt_duration(frt),
            "time_to_close": self.time_to_close,
            "time_to_close_human": fmt_duration(self.time_to_close),
            "first_response_target": first_response_target,
            "followup_target": followup_target,
            "first_response_breached": (frt is not None and frt > first_response_target),
        }

    def transcript_text(self, include_bots: bool = True) -> str:
        """Flatten the thread into a single readable string.

        Each line shows the message clock time plus the gap since the previous message;
        on an AGENT turn that follows a CUSTOMER turn the gap is the SLA-relevant wait the
        customer experienced, so it's labelled explicitly. Pre-computing these gaps means
        the model judges timeliness from stated numbers instead of doing timestamp math.
        """
        # Drop empty bookkeeping parts (assignment, close, language_detection, sla_applied, …):
        # they're pure token noise. This applies to *every* author, not just bots — an empty
        # `admin (assignment)` part used to survive, so a thread's first AGENT line was
        # routinely blank and the grader read "the agent opened with nothing" where the
        # greeting should be. It also stole the "waited after customer" marker below from the
        # first real reply. A `comment` with no text is kept: that is a human turn that really
        # was empty, which is itself worth seeing.
        meaningful = [
            m for m in self.messages
            if (m.text and m.text.strip()) or m.part_type in ("", "comment")
        ]

        # `include_bots=False` is the grader's view. Automation writes ~41% of the text here
        # and more than half of it in 37% of chats, while the agent writes a median of four
        # turns — and a `BOT Billy Jr.:` byline looks exactly like `AGENT Lenny:`, so the model
        # read the bot's marketing copy and its auto-close line as the agent's own words and
        # deducted for them. Removing the text removes the thing being misattributed.
        #
        # Each removed run leaves a count-only marker. That is not decoration: the marker keeps
        # the timeline honest, and without it `resp-no-ghost` ("a direct player question
        # received no answer") would fire on questions the bot did in fact answer.
        omitted = 0
        if not include_bots:
            kept: list[Message | int] = []
            run = 0
            for m in meaningful:
                if self._role_of(m.author_type) not in ("AGENT", "CUSTOMER"):
                    run += 1
                    continue
                if run:
                    kept.append(run)
                    omitted += run
                    run = 0
                kept.append(m)
            if run:
                kept.append(run)
                omitted += run
            meaningful = kept

        lines: list[str] = []
        prev: Message | None = None
        for m in meaningful:
            if isinstance(m, int):
                lines.append(f"— {m} automated message{'' if m == 1 else 's'} omitted —")
                continue
            when = m.created_at.strftime("%H:%M:%S") if m.created_at else "?"
            role = self._role_of(m.author_type)
            gap = ""
            if prev is not None and prev.created_at and m.created_at:
                secs = int((m.created_at - prev.created_at).total_seconds())
                if secs > 0:
                    after = self._role_of(prev.author_type).lower()
                    waited = " waited" if role == "AGENT" and after == "customer" else ""
                    gap = f" | +{fmt_duration(secs)}{waited} after {after}"
            tag = f" ({m.part_type})" if m.part_type and m.part_type != "comment" else ""
            lines.append(f"[{when}{gap}] {role} {m.author_name}{tag}: {m.text}")
            prev = m
        return "\n".join(lines)

    # ── JSON (de)serialisation for the conversations cache ───────────────────
    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "created_at": _iso(self.created_at),
            "updated_at": _iso(self.updated_at),
            "state": self.state,
            "is_ticket": self.is_ticket,
            "subject": self.display_subject,
            "assignee": vars(self.assignee) if self.assignee else None,
            "contact": vars(self.contact),
            "messages": [
                {**vars(m), "created_at": _iso(m.created_at)} for m in self.messages
            ],
            "tags": self.tags,
            "brand": self.brand,
            "csat_rating": self.csat_rating,
            "csat_remark": self.csat_remark,
            "first_response_time": self.first_response_time,
            "time_to_close": self.time_to_close,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Conversation":
        assignee = (
            Admin(
                id=d["assignee"].get("id", ""),
                name=d["assignee"].get("name", ""),
                email=d["assignee"].get("email", ""),
            )
            if d.get("assignee")
            else None
        )
        contact = Contact(
            id=d["contact"].get("id", "") if d.get("contact") else "",
            name=d["contact"].get("name", "") if d.get("contact") else "",
            email=d["contact"].get("email", "") if d.get("contact") else "",
        )
        messages = [
            Message(
                seq=m["seq"],
                author_type=m["author_type"],
                author_name=m["author_name"],
                created_at=_parse_iso(m.get("created_at")),
                text=m.get("text", ""),
                part_type=m.get("part_type", ""),
            )
            for m in d.get("messages", [])
        ]
        return cls(
            id=d["id"],
            created_at=_parse_iso(d.get("created_at")),
            updated_at=_parse_iso(d.get("updated_at")),
            state=d.get("state", ""),
            # Absent from payloads cached before tickets were split out from chats.
            is_ticket=bool(d.get("is_ticket", False)),
            subject=d.get("subject", ""),
            assignee=assignee,
            contact=contact,
            messages=messages,
            tags=list(d.get("tags", [])),
            # Absent from payloads cached before brand capture existed.
            brand=d.get("brand", ""),
            csat_rating=d.get("csat_rating"),
            csat_remark=d.get("csat_remark", ""),
            first_response_time=d.get("first_response_time"),
            time_to_close=d.get("time_to_close"),
        )
