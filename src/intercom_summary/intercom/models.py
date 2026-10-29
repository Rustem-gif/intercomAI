"""Normalised data shapes for Intercom objects.

The raw Intercom API returns large, deeply-nested JSON. We flatten the bits we care
about into small dataclasses so the rest of the codebase (export, QA, Slack) never has
to know the wire format.
"""
from __future__ import annotations

import re
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


# Conversation parts that mean "this thread was handed to someone". Intercom emits them with
# no body, so they never appear in a transcript — they are only useful as timestamps.
ASSIGNMENT_PARTS: frozenset[str] = frozenset(
    {"assignment", "default_assignment", "message_strategy_assignment", "assign_and_reopen"}
)


# The agents' inactivity, closing and courtesy macros — fixed strings in this workspace (English
# and German), measured as the commonest agent lines after a player's last message. A reply made
# only of these answers nothing, so `Conversation.ending` does not count it as a substantive
# reply. Matched at the start of the message, case-insensitively.
_AGENT_MACRO = re.compile(
    r"^\s*(?:"
    r"i see no reply from your side|in that case, i will close the chat"
    r"|looks like the chat has become inactive|if you don'?t have any additional questions"
    r"|are we still connected|please let me know, are you still (?:available|there)"
    r"|are you still (?:there|available|with me)|i am here, i am waiting"
    r"|is there anything else i can help|are there any additional questions"
    r"|do you have any (?:other|unresolved) questions|do you have any unresolved"
    r"|thank you for (?:visiting king billy|chatting with us)"
    r"|my pleasure|you(?:'re| are) welcome|anytime\b"
    r"|ich sehe keine antwort von ihrer seite|es scheint, dass (?:der )?chat inaktiv"
    r"|haben sie (?:noch offene fragen|vielleicht noch zus)|vielen dank, dass sie sich heute"
    r")",
    re.IGNORECASE,
)


def is_agent_macro(text: str | None) -> bool:
    """True when an agent message is only an inactivity / closing / courtesy macro."""
    return bool(_AGENT_MACRO.match(text or ""))


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
    # VIP | preVIP | regular, from the Intercom contact's attributes (intercom/segments.py);
    # "" = not looked up. Current at fetch time, not as of the chat.
    segment: str = ""
    loyalty_level: str = ""
    # The player's id in the casino back office (the contact's `cleanid`, or external_id
    # "c<id>") and the brand whose BO holds them ("kingbilly"); "" = unknown.
    bo_id: str = ""
    bo_brand: str = ""


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
    # Intercom's `source.type`: "conversation" for a Messenger chat, "email" for an email.
    # Emails are excluded in the search query itself (see intercom/fetch.build_search_query), so
    # anything reaching the cache is a chat — this records the fact in the data rather than
    # leaving it only in the code that applied it. "" on payloads cached before it existed.
    channel: str = ""
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
    def agent_first_reply_seconds(self) -> int | None:
        """How long the agent took to reply once the chat actually reached them.

        NOT the same as `first_response_time`, which is Intercom's `time_to_admin_reply`
        measured from conversation creation. In this workspace a bot is assigned instantly and
        holds the chat — answering the player within a second or two — and only routes to a
        human minutes later, often after the player has gone idle. Charging that window to the
        agent marked 22.3% of conversations as SLA breaches that were comfortably inside target
        from the moment the agent could see them. The reported example was charged 5m 16s for a
        reply sent 13 seconds after routing.

        Measured from the last assignment at or before the agent's first substantive reply, so
        an agent assigned at the start who then ignores the chat still measures the full delay.
        Derived from `messages`, which already carry assignment parts, so every conversation
        ever cached answers this with no migration. None when no agent ever replied.
        """
        reply = self._first_agent_reply
        if reply is None or reply.created_at is None:
            return None
        started = self._assigned_at(reply)
        if started is None:
            return None
        return max(0, int((reply.created_at - started).total_seconds()))

    @property
    def longest_followup_seconds(self) -> int | None:
        """The longest the player waited for a later agent reply, after the first one.

        Measured as the transcript's "+Xm waited after customer" marker is: from the player's
        last line to the agent's next line the player could see (internal notes and automation
        do not answer anyone). None when the agent never replied after a player line again.
        """
        first = self._first_agent_reply
        if first is None:
            return None
        longest: int | None = None
        prev: Message | None = None
        seen_first = False
        for m in self.messages:
            if not (m.text or "").strip() or m.created_at is None:
                continue
            role = self._role_of(m.author_type)
            if role not in ("AGENT", "CUSTOMER") or m.part_type == "note":
                continue
            if m is first:
                seen_first = True
            elif (seen_first and role == "AGENT" and prev is not None
                  and self._role_of(prev.author_type) == "CUSTOMER" and prev.created_at):
                secs = max(0, int((m.created_at - prev.created_at).total_seconds()))
                longest = secs if longest is None else max(longest, secs)
            prev = m
        return longest

    @property
    def _first_agent_reply(self) -> "Message | None":
        """The agent's first message that actually said something, once the player had written.

        A chat opened from a Help Center article starts with that article posted under the
        agent's name before the player says anything; counting it as the reply measured a 3-hour
        wait for the real first answer as 0s (QA feedback, Oct 2026).
        """
        # By position, not clock: the article carries the same second as the player's line.
        start = next((i for i, m in enumerate(self.messages)
                      if self._role_of(m.author_type) == "CUSTOMER"), 0)
        return next(
            (m for m in self.messages[start:]
             if m.author_type == "admin" and (m.text or "").strip()),
            None,
        )

    def _assigned_at(self, reply: "Message") -> datetime | None:
        """When the chat reached the agent who sent `reply`.

        Falls back to the player's first message for the handful of threads that carry no
        assignment event at all — 3 of 6,131 in the cache — which is the old behaviour and the
        right answer when nobody handed the chat over.
        """
        assigns = [
            m.created_at for m in self.messages
            if m.part_type in ASSIGNMENT_PARTS and m.created_at
            and reply.created_at and m.created_at <= reply.created_at
        ]
        if assigns:
            return max(assigns)
        first_player = next(
            (m.created_at for m in self.messages
             if m.author_type in ("user", "contact", "lead") and m.created_at),
            None,
        )
        return first_player

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

    def human_comments(self) -> list[Message]:
        """What the agent and the player actually said to each other, in order.

        Comments only: internal notes (of any author), bookkeeping parts such as `close`, and
        everything automation wrote are left out. `""` counts as a comment because older
        payloads and test fixtures carry no part type."""
        return [
            m for m in self.messages
            if (m.text or "").strip() and m.part_type in ("", "comment")
            and self._role_of(m.author_type) in ("AGENT", "CUSTOMER")
        ]

    @property
    def ending(self) -> dict:
        """Who stopped responding, read off the comment order.

        QA (2026-10-09): the grader confused "the agent did not reply" with "the player did not
        reply". When the agent asks for a bank statement and the player goes quiet, nothing is
        the agent's fault; when the player's last questions get no answer before the chat is
        closed, the agent ghosted them. The transcript shows both, but only as timestamps the
        model has to line up itself — this states it as a fact (qa/prompt.py:_ending_line) and
        gives the derivation rules (qa/gated_rules.py) the same fact to act on.

        `agent_tail_is_template` is True when everything the agent wrote after the player's last
        message is an inactivity / closing / courtesy macro — 30% of agent-last chats end that
        way, and there the player's last message, not the agent's, decides the case.
        """
        comments = self.human_comments()
        agent_idx = [i for i, m in enumerate(comments) if m.author_type == "admin"]
        player_idx = [i for i, m in enumerate(comments) if m.author_type != "admin"]
        last_agent = agent_idx[-1] if agent_idx else -1
        last_player = player_idx[-1] if player_idx else -1
        unanswered = [m for m in comments[last_agent + 1:] if m.author_type != "admin"]
        agent_after = [m for m in comments[last_player + 1:] if m.author_type == "admin"]
        substantive = [comments[i] for i in agent_idx if not is_agent_macro(comments[i].text)]
        return {
            "last_role": (self._role_of(comments[-1].author_type) if comments else None),
            "agent_ever_wrote": bool(agent_idx),
            "unanswered_player": unanswered,
            "agent_after_player": agent_after,
            "agent_tail_is_template": bool(agent_after)
            and all(is_agent_macro(m.text) for m in agent_after),
            "last_player": comments[last_player] if last_player >= 0 else None,
            "last_agent": comments[last_agent] if last_agent >= 0 else None,
            "last_substantive_agent": substantive[-1] if substantive else None,
        }

    @property
    def agent_spoke_last(self) -> bool:
        """True when an agent comment follows the player's last comment."""
        return self.ending["last_role"] == "AGENT"

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

        The target is applied to `agent_first_reply_seconds` — the agent's own latency once the
        chat reached them — not to Intercom's `first_response_time`, which starts at conversation
        creation and so bills the agent for time a bot held the thread and for the player's own
        idle time. Both numbers are returned; only the first decides the breach.
        Targets are passed in (from settings) so this stays config-free.
        """
        frt = self.first_response_time
        agent_frt = self.agent_first_reply_seconds
        followup = self.longest_followup_seconds
        return {
            "agent_first_reply": agent_frt,
            "agent_first_reply_human": fmt_duration(agent_frt),
            "first_response_time": frt,
            "first_response_time_human": fmt_duration(frt),
            "time_to_close": self.time_to_close,
            "time_to_close_human": fmt_duration(self.time_to_close),
            "first_response_target": first_response_target,
            "followup_target": followup_target,
            "first_response_breached": (
                agent_frt is not None and agent_frt > first_response_target
            ),
            "longest_followup": followup,
            "longest_followup_human": fmt_duration(followup),
            "followup_breached": followup is not None and followup > followup_target,
        }

    def menu_choices(self) -> set[int]:
        """`seq` of the customer lines that are bot-menu buttons, not typed words.

        Intercom records a button click as an ordinary customer `comment` carrying the
        button's label ("My deposit did not come"), so the grader read it as a request the
        agent then ignored (QA feedback, Oct 2026). Two patterns identify a click: the bot
        posted a `quick_reply` part (the menu) and the customer's next line is the label; or
        the chat's opening line is a short label the bot answered within two seconds — the
        opening menu leaves no quick_reply part. Checked on 1,500 October chats: 1,429 of the
        1,437 opening lines the bot answered that fast were menu labels.
        """
        out: set[int] = set()
        menu_open = False
        first_seen = False
        for i, m in enumerate(self.messages):
            if m.author_type == "bot":
                if m.part_type == "quick_reply":
                    menu_open = True
                continue
            if self._role_of(m.author_type) != "CUSTOMER":
                menu_open = False
                continue
            if m.part_type not in ("", "comment"):
                continue
            if menu_open:
                out.add(m.seq)
            elif not first_seen:
                nxt = next((n for n in self.messages[i + 1:]
                            if n.author_type != m.author_type and (n.text or "").strip()), None)
                text = (m.text or "").strip()
                if (nxt is not None and nxt.author_type == "bot" and nxt.part_type == "comment"
                        and m.created_at and nxt.created_at
                        and (nxt.created_at - m.created_at).total_seconds() <= 2
                        and len(text.split()) <= 5 and "?" not in text):
                    out.add(m.seq)
            first_seen = True
            menu_open = False
        return out

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
        menu = self.menu_choices()
        prev: Message | None = None
        first_reply = self._first_agent_reply
        for m in meaningful:
            if isinstance(m, int):
                lines.append(f"— {m} automated message{'' if m == 1 else 's'} omitted —")
                continue
            when = m.created_at.strftime("%H:%M:%S") if m.created_at else "?"
            role = self._role_of(m.author_type)
            gap = ""
            # The agent's opening turn is annotated from the moment the chat reached them, so
            # the transcript agrees with the SLA header instead of restating the player's whole
            # wait — most of which the agent could not see. Later agent turns keep the plain
            # gap: those waits are genuinely theirs.
            if m is first_reply and (secs := self.agent_first_reply_seconds) is not None:
                gap = f" | +{fmt_duration(secs)} after the chat reached the agent"
            elif prev is not None and prev.created_at and m.created_at:
                secs = int((m.created_at - prev.created_at).total_seconds())
                if secs > 0:
                    after = self._role_of(prev.author_type).lower()
                    waited = " waited" if role == "AGENT" and after == "customer" else ""
                    gap = f" | +{fmt_duration(secs)}{waited} after {after}"
            tag = f" ({m.part_type})" if m.part_type and m.part_type != "comment" else ""
            if m.seq in menu:
                tag += " (bot-menu choice)"
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
            "channel": self.channel,
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
            # Absent from payloads cached before the segment lookup existed.
            segment=d["contact"].get("segment", "") if d.get("contact") else "",
            loyalty_level=d["contact"].get("loyalty_level", "") if d.get("contact") else "",
            bo_id=d["contact"].get("bo_id", "") if d.get("contact") else "",
            bo_brand=d["contact"].get("bo_brand", "") if d.get("contact") else "",
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
            channel=d.get("channel", ""),
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
