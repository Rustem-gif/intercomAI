"""VIP cases — the unit the VIP QA Scorecard v1.6 grades.

The scorecard (§1) grades a *case*: one player's request followed across emails and days, not a
single Intercom thread. In this inbox one request routinely spans threads — a player writes
again instead of replying, a manager writes first and the player answers in a new thread — so
336 of the 538 kept threads (1–14 Oct) have a sibling from the same player on the same topic.
Grading them apart would mark "player re-asked, manager answered in the other thread" as
ghosting in one thread and as an unprompted reply in the other.

Grouping is deterministic and free:

* same player (Intercom contact id) and same topic (the Haiku label from vip.py);
* sorted by start; a thread joins the open case when it starts within CHAIN_DAYS of that case's
  last message, otherwise it opens a new case;
* topic "Other" is never chained — it is the classifier's catch-all, not a shared subject.

`case_id` is "vc-<first thread id>", stable while the first thread is. `case_version` hashes the
member threads and their message counts, so a grade goes stale when a case gains a thread or a
thread gains a message (storage/vip_cases_store.is_current).

`case_conversation(case)` merges the threads into one `CaseConversation` — an ordinary
`Conversation` the existing grading engine accepts, plus the case metadata the case transcript
(qa/vip_transcript.py) needs.
"""
from __future__ import annotations

import hashlib
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from intercom_summary import vip_staff
from intercom_summary.intercom.models import Admin, Contact, Conversation, Message
from intercom_summary.logging_setup import get_logger
from intercom_summary.vip import MIN_MEANINGFUL_WORDS, meaningful_words

log = get_logger(__name__)

CHAIN_DAYS = 7
NEVER_CHAINED = frozenset({"Other"})
_PLAYER = frozenset({"user", "contact", "lead"})
# "Sent from my iPhone" carries three words and no request — a thank-you with that signature
# is still only a thank-you. Same for the mail apps' other footers.
_SIGNATURE = re.compile(
    r"\b(sent from my \w+( \w+)?|get outlook for \w+|obtenir outlook pour \w+"
    r"|von meinem \w+ gesendet)", re.IGNORECASE)
# A sign-off ("Thanks, have a nice weekend!") expects no answer. vip.meaningful_words already
# drops "thanks", "ok", greetings; these are the rest of a typical closing line.
_SIGN_OFF = frozenset("""
have nice day days weekend evening morning night too again wonderful lovely appreciate
appreciated welcome all same take care soon talk later will do much
""".split())


def _asks_something(text: str) -> bool:
    words = [w for w in re.findall(r"[^\W\d_]{2,}", _SIGNATURE.sub("", text).lower())
             if w not in _SIGN_OFF]
    return meaningful_words(" ".join(words)) >= MIN_MEANINGFUL_WORDS


def _dt(s: str | None) -> datetime | None:
    return datetime.fromisoformat(s) if s else None


def is_public_reply(m: Message) -> bool:
    """A staff message the player saw. Intercom carries text on more part types than
    `comment` — `open` (a manager-first email) and `assignment` (reply-and-assign) too."""
    return m.author_type == "admin" and m.part_type != "note" and bool((m.text or "").strip())


def is_player_message(m: Message) -> bool:
    return m.author_type in _PLAYER and bool((m.text or "").strip())


@dataclass
class Case:
    case_id: str
    contact_id: str
    contact_name: str
    contact_email: str
    brand: str
    topic: str
    first_at: str
    last_at: str
    threads: list[dict]                       # vip_conversations rows (with "payload")
    managers: list[str] = field(default_factory=list)
    primary_manager: str = ""
    case_version: str = ""

    @property
    def thread_ids(self) -> list[str]:
        return [t["id"] for t in self.threads]

    def thread_summaries(self) -> list[dict]:
        return [_thread_summary(t) for t in self.threads]

    def as_row(self) -> dict:
        return {
            "case_id": self.case_id, "contact_id": self.contact_id,
            "contact_name": self.contact_name, "contact_email": self.contact_email,
            "brand": self.brand, "topic": self.topic, "first_at": self.first_at,
            "last_at": self.last_at, "thread_count": len(self.threads),
            "threads": self.thread_summaries(), "managers": self.managers,
            "primary_manager": self.primary_manager, "case_version": self.case_version,
        }


@dataclass
class CaseConversation(Conversation):
    """A case's threads merged into one chronological Conversation. `id` is the case id.
    `message_thread[i]` is the thread `messages[i]` came from."""
    case_id: str = ""
    topic: str = ""
    case_version: str = ""
    threads: list[dict] = field(default_factory=list)
    message_thread: list[str] = field(default_factory=list)
    staff: frozenset[str] = frozenset()       # lower-cased VIP manager names

    def is_vip_manager(self, name: str) -> bool:
        return (name or "").strip().lower() in self.staff

    # A calibration run stores the conversation it graded (calibration_results); the case
    # fields ride along so the run's drawer can show the case transcript the grader saw.
    def to_dict(self) -> dict:
        return {**super().to_dict(), "case_id": self.case_id, "topic": self.topic,
                "case_version": self.case_version, "threads": self.threads,
                "message_thread": self.message_thread, "staff": sorted(self.staff)}

    @classmethod
    def from_dict(cls, d: dict) -> "CaseConversation":
        base = Conversation.from_dict(d)
        return cls(**{f: getattr(base, f) for f in Conversation.__dataclass_fields__},
                   case_id=d.get("case_id", base.id), topic=d.get("topic", ""),
                   case_version=d.get("case_version", ""), threads=d.get("threads") or [],
                   message_thread=d.get("message_thread") or [],
                   staff=frozenset(d.get("staff") or []))


def _messages(row: dict) -> list[dict]:
    return (row.get("payload") or {}).get("messages") or []


def _last_activity(row: dict) -> str:
    """When a thread last saw a human message (bookkeeping parts and bots don't count)."""
    times = [m["created_at"] for m in _messages(row)
             if m.get("created_at") and (m.get("text") or "").strip()
             and m.get("author_type") != "bot"]
    return max(times) if times else row["created_at"]


def _opened_by(row: dict) -> str:
    first = next((m for m in _messages(row) if (m.get("text") or "").strip()
                  and m.get("author_type") != "bot"), None)
    if first is None:
        return ""
    return "player" if first["author_type"] in _PLAYER else "staff"


def _thread_summary(row: dict) -> dict:
    return {
        "id": row["id"], "created_at": row["created_at"], "last_at": _last_activity(row),
        "subject": row.get("subject") or "", "agent_name": row.get("agent_name") or "",
        "delivered_as": row.get("delivered_as") or "", "opened_by": _opened_by(row),
        "message_count": len(_messages(row)),
    }


def case_version(threads: list[dict]) -> str:
    key = "|".join(f"{t['id']}:{len(_messages(t))}" for t in threads)
    return hashlib.sha256(key.encode()).hexdigest()[:12]


def _staff(rows: list[dict]) -> set[str]:
    return vip_staff.staff_names(fallback=[r.get("agent_name") or "" for r in rows])


def _managers(threads: list[dict], staff: set[str]) -> tuple[list[str], str]:
    """VIP managers who replied publicly in the case, most replies first, and the primary one
    (most replies; ties go to the assignee of the latest thread)."""
    replies: Counter[str] = Counter()
    for t in threads:
        for m in _messages(t):
            if (m.get("author_type") == "admin" and m.get("part_type") != "note"
                    and (m.get("text") or "").strip()
                    and (m.get("author_name") or "").strip().lower() in staff):
                replies[m["author_name"].strip()] += 1
    latest_assignee = next((t.get("agent_name") for t in reversed(threads)
                            if t.get("agent_name")), "")
    ranked = sorted(replies, key=lambda n: (-replies[n], n != latest_assignee, n))
    primary = ranked[0] if ranked else latest_assignee or ""
    return ranked, primary


def group_cases(rows: list[dict], staff: set[str] | None = None) -> list[Case]:
    """Kept vip_conversations rows (with payload) → cases, oldest first."""
    staff = staff if staff is not None else _staff(rows)
    by_key: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in rows:
        contact = (r.get("payload") or {}).get("contact") or {}
        who = contact.get("id") or (r.get("customer_email") or "").lower() or r["id"]
        by_key[(who, r.get("topic") or "Other")].append(r)

    cases: list[Case] = []
    for (_who, topic), threads in by_key.items():
        threads.sort(key=lambda r: r["created_at"] or "")
        chains: list[list[dict]] = []
        for t in threads:
            if chains and topic not in NEVER_CHAINED:
                last = max(_last_activity(x) for x in chains[-1])
                if _dt(t["created_at"]) - _dt(last) <= timedelta(days=CHAIN_DAYS):
                    chains[-1].append(t)
                    continue
            chains.append([t])
        for chain in chains:
            first = chain[0]
            contact = (first.get("payload") or {}).get("contact") or {}
            managers, primary = _managers(chain, staff)
            cases.append(Case(
                case_id=f"vc-{first['id']}",
                contact_id=contact.get("id", ""),
                contact_name=contact.get("name") or first.get("customer_name") or "",
                contact_email=contact.get("email") or first.get("customer_email") or "",
                brand=first.get("brand") or "",
                topic=topic,
                first_at=first["created_at"],
                last_at=max(_last_activity(x) for x in chain),
                threads=chain,
                managers=managers,
                primary_manager=primary,
                case_version=case_version(chain),
            ))
    cases.sort(key=lambda c: c.first_at or "")
    return cases


def case_conversation(case: Case, staff: set[str] | None = None) -> CaseConversation:
    """The case's threads as one chronological Conversation for the grader."""
    staff = staff if staff is not None else _staff(case.threads)
    tagged: list[tuple[str, int, int, Message]] = []
    convos = []
    for t_index, t in enumerate(case.threads):
        c = Conversation.from_dict(t["payload"])
        convos.append(c)
        for m in c.messages:
            stamp = m.created_at.isoformat() if m.created_at else t["created_at"]
            tagged.append((stamp, t_index, m.seq, m))
    tagged.sort(key=lambda x: (x[0], x[1], x[2]))
    messages, message_thread = [], []
    for seq, (_stamp, t_index, _s, m) in enumerate(tagged):
        messages.append(Message(seq=seq, author_type=m.author_type, author_name=m.author_name,
                                created_at=m.created_at, text=m.text, part_type=m.part_type))
        message_thread.append(case.threads[t_index]["id"])

    assignee = None
    for c in reversed(convos):
        if c.assignee and c.assignee.name == case.primary_manager:
            assignee = c.assignee
            break
    if assignee is None and case.primary_manager:
        assignee = Admin(id="", name=case.primary_manager)
    last = convos[-1]
    # The player's VIP segment from the newest thread that has one (intercom/segments.py).
    segmented = next((c.contact for c in reversed(convos) if c.contact.segment), None)
    return CaseConversation(
        id=case.case_id,
        created_at=convos[0].created_at,
        updated_at=max((c.updated_at for c in convos if c.updated_at), default=None),
        state=last.state,
        channel="email",
        subject=convos[0].subject,
        assignee=assignee,
        contact=Contact(id=case.contact_id, name=case.contact_name, email=case.contact_email,
                        segment=segmented.segment if segmented else "",
                        loyalty_level=segmented.loyalty_level if segmented else ""),
        messages=messages,
        tags=sorted({tag for c in convos for tag in c.tags}),
        brand=case.brand,
        case_id=case.case_id,
        topic=case.topic,
        case_version=case.case_version,
        threads=case.thread_summaries(),
        message_thread=message_thread,
        staff=frozenset(staff),
    )


# ── Build + persist ──────────────────────────────────────────────────────────────────────────
def rebuild(db_path=None) -> dict:
    """Regroup every kept VIP thread into cases and store them. Free — no API calls."""
    from intercom_summary.storage.vip_cases_store import VipCasesStore
    from intercom_summary.storage.vip_store import VipStore

    vs = VipStore(db_path)
    try:
        rows = vs.kept_with_payload()
    finally:
        vs.close()
    cases = group_cases(rows)
    store = VipCasesStore(db_path)
    try:
        store.replace_cases(cases)
    finally:
        store.close()
    sizes = Counter(len(c.threads) for c in cases)
    log.info("VIP cases rebuilt: %d thread(s) → %d case(s)", len(rows), len(cases))
    return {
        "threads": len(rows), "cases": len(cases),
        "multi_thread_cases": sum(1 for c in cases if len(c.threads) > 1),
        "sizes": dict(sorted(sizes.items())),
        "no_manager": sum(1 for c in cases if not c.primary_manager),
    }


def load_case_conversations(case_ids: list[str], db_path=None) -> dict[str, CaseConversation]:
    """{case_id: CaseConversation} for stored cases, rebuilt from the current thread payloads."""
    from intercom_summary.storage.vip_cases_store import VipCasesStore
    from intercom_summary.storage.vip_store import VipStore

    store, vs = VipCasesStore(db_path), VipStore(db_path)
    try:
        out: dict[str, CaseConversation] = {}
        all_rows = None
        for cid in case_ids:
            meta = store.get_case(cid)
            if meta is None:
                continue
            threads = [r for tid in meta["thread_ids"] if (r := vs.get(tid))]
            if not threads:
                continue
            if all_rows is None:
                all_rows = vs.kept_with_payload()
            managers, primary = _managers(threads, _staff(all_rows))
            case = Case(
                case_id=cid, contact_id=meta.get("contact_id") or "",
                contact_name=meta.get("contact_name") or "",
                contact_email=meta.get("contact_email") or "", brand=meta.get("brand") or "",
                topic=meta.get("topic") or "", first_at=meta.get("first_at") or "",
                last_at=meta.get("last_at") or "", threads=threads, managers=managers,
                primary_manager=primary, case_version=case_version(threads),
            )
            out[cid] = case_conversation(case, _staff(all_rows))
        return out
    finally:
        store.close()
        vs.close()


# ── Exception Report v1 (scorecard Appendix В — the Intercom-derivable events) ───────────────
def exceptions(db_path=None) -> dict:
    """Events a reviewer should see without waiting for a grade. No LLM.

    * unanswered — a player's last meaningful message with no VIP-manager public reply after
      it, in any of that player's kept threads (a reply in a sibling thread counts as an
      answer). Threads the rules dropped ("no manager reply") are left out on purpose: they
      were never AI-checked, and on 1–14 Oct they were mostly CC loops and calendar invites.
    * no_owner — a kept thread with no assignee.
    """
    from intercom_summary.storage.vip_store import VipStore

    vs = VipStore(db_path)
    try:
        rows = vs.kept_with_payload()
        as_of = vs.summary()["last_fetched"]
    finally:
        vs.close()
    staff = _staff(rows)

    by_contact: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        who = (r["payload"].get("contact") or {}).get("id") or r.get("customer_email") or r["id"]
        by_contact[who].append(r)

    unanswered = []
    for _who, threads in by_contact.items():
        last_player = last_reply = None
        for t in threads:
            for m in _messages(t):
                text, at = m.get("text") or "", m.get("created_at")
                if not at or not text.strip():
                    continue
                if (m.get("author_type") in _PLAYER
                        and _asks_something(text)):
                    if last_player is None or at > last_player[0]:
                        last_player = (at, t, text)
                elif (m.get("author_type") == "admin" and m.get("part_type") != "note"
                      and (m.get("author_name") or "").strip().lower() in staff):
                    last_reply = max(last_reply or at, at)
        if last_player and (last_reply is None or last_reply < last_player[0]):
            at, t, text = last_player
            unanswered.append({
                "thread_id": t["id"], "contact_name": t.get("customer_name") or "",
                "contact_email": t.get("customer_email") or "", "brand": t.get("brand") or "",
                "agent_name": t.get("agent_name") or "", "topic": t.get("topic") or "",
                "player_message_at": at, "last_manager_reply_at": last_reply,
                "snippet": " ".join(text.split())[:200],
            })
    unanswered.sort(key=lambda x: x["player_message_at"])

    no_owner = [{
        "thread_id": r["id"], "contact_name": r.get("customer_name") or "",
        "contact_email": r.get("customer_email") or "", "brand": r.get("brand") or "",
        "topic": r.get("topic") or "", "created_at": r["created_at"],
        "subject": r.get("subject") or "",
    } for r in rows if not (r.get("agent_name") or "").strip()]
    no_owner.sort(key=lambda x: x["created_at"] or "")
    return {"as_of": as_of, "unanswered": unanswered, "no_owner": no_owner}
