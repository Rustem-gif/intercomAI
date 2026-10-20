"""Shared builders for the VIP case tests: vip_conversations rows the way vip.collect stores them."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from intercom_summary.settings import settings

T0 = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)
STAFF = {"vera vip", "max vip"}


def at(hours: float) -> str:
    return (T0 + timedelta(hours=hours)).isoformat()


def msg(seq, author_type, name, hours, text, part_type="comment"):
    return {"seq": seq, "author_type": author_type, "author_name": name,
            "created_at": at(hours), "text": text, "part_type": part_type}


def thread(tid, hours, messages, *, contact="c1", topic="Withdrawal", agent="Vera VIP",
           subject="Re: my withdrawal", keep=1, brand="Betncare"):
    return {
        "id": tid, "agent_name": agent, "agent_email": "", "customer_name": "Pat Player",
        "customer_email": "pat@x.com", "brand": brand, "subject": subject,
        "created_at": at(hours), "delivered_as": "customer_initiated",
        "message_count": len(messages), "customer_msgs": 1, "agent_replies": 1,
        "keep": keep, "filter_stage": "ai", "reason": "real dialogue", "topic": topic,
        "payload": {
            "id": tid, "created_at": at(hours), "updated_at": at(hours + 1), "state": "closed",
            "channel": "email", "subject": subject,
            "assignee": {"id": "9", "name": agent, "email": "vera@co.com"} if agent else None,
            "contact": {"id": contact, "name": "Pat Player", "email": "pat@x.com"},
            "messages": messages, "tags": [], "brand": brand,
        },
    }


def pair(tid, hours, **kw):
    """A player question answered by Vera an hour later."""
    return thread(tid, hours, [
        msg(0, "user", "Pat Player", hours, "Where is my withdrawal from yesterday please?"),
        msg(1, "admin", "Vera VIP", hours + 1, "Hi Pat, it is with Payments, I will update you."),
    ], **kw)


def save_threads(db_path, rows):
    from intercom_summary.storage.vip_store import VipStore

    vs = VipStore(db_path)
    try:
        for r in rows:
            vs.save(r)
    finally:
        vs.close()


@pytest.fixture
def vip_env(tmp_path):
    """A temp DB and a vip_staff.yaml naming the two VIP managers, no shifts."""
    staff = tmp_path / "vip_staff.yaml"
    staff.write_text("staff:\n  - Vera VIP\n  - Max VIP\nshifts: {}\n", encoding="utf-8")
    old = settings.db_path, settings.vip_staff_path
    object.__setattr__(settings, "db_path", tmp_path / "vip.db")
    object.__setattr__(settings, "vip_staff_path", staff)
    yield tmp_path
    object.__setattr__(settings, "db_path", old[0])
    object.__setattr__(settings, "vip_staff_path", old[1])
