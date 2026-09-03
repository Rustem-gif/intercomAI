"""One-time backfill: recover the customer's name and email on already-cached conversations.

Background: `raw["contacts"]["contacts"]` is a list of *stubs* carrying only an id, so
`normalise_conversation` stored an empty `Contact` — `customer_name` was blank on 100% of cached
conversations. That is not just a gap in the Conversations table: `qa/prompt.py` puts the name in
the grader's prompt, so every grade was made against "Customer name: unknown" while the transcript
byline right below named the player. `intercom/fetch.contact_from_payload()` now falls back to the
thread's own message authors; this fills in the rows fetched before that fix.

Two deliberate choices:

1. **It runs entirely offline.** The names were never missing from the data — every message the
   customer sent carries them, and `payload_json` already holds those messages. So this reads the
   cached payload rather than re-fetching 6,000 conversations from Intercom.
2. **It writes only the customer columns and the payload's contact.** It never goes through
   `ConversationsStore.save()`, whose `INSERT OR REPLACE` would rewrite `agent_name` from
   Intercom's current assignee and quietly shift per-agent QA averages — the same trap
   `scripts/backfill_brands.py` documents. Grades are not touched.

Usage:
    python scripts/backfill_customer_names.py --dry-run     # report only, writes nothing
    python scripts/backfill_customer_names.py               # apply
"""
from __future__ import annotations

import argparse
import json

from intercom_summary.intercom.models import Contact
from intercom_summary.settings import settings
from intercom_summary.storage.db import connect

# Author types that identify the customer. `lead` is Intercom's not-yet-identified visitor.
_CUSTOMER_TYPES = ("user", "contact", "lead")


def _recover(payload: dict) -> Contact:
    """The customer, read back off the cached messages. Mirrors fetch.contact_from_payload()."""
    stored = payload.get("contact") or {}
    name = stored.get("name") or ""
    email = stored.get("email") or ""
    if not (name and email):
        for m in payload.get("messages") or []:
            if m.get("author_type") not in _CUSTOMER_TYPES:
                continue
            author = m.get("author_name") or ""
            # `_author_name` falls back to the address when a customer has no name, so a byline
            # containing "@" is an email standing in for a name, not a name.
            if "@" in author:
                email = email or author
            else:
                name = name or author
            if name and email:
                break
    return Contact(id=stored.get("id", ""), name=name, email=email)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="report what would change, write nothing")
    args = ap.parse_args()

    conn = connect(settings.db_path)
    rows = conn.execute(
        "SELECT id, customer_name, customer_email, payload_json FROM conversations "
        "WHERE customer_name = '' OR customer_email = ''"
    ).fetchall()
    if not rows:
        print("Nothing to do — every cached conversation already has a customer name.")
        return

    print(f"{len(rows)} conversation(s) missing a customer name or email.")

    updates: list[tuple[str, str, str, str]] = []
    got_name = got_email = 0
    for r in rows:
        payload = json.loads(r["payload_json"])
        contact = _recover(payload)
        name = r["customer_name"] or contact.name
        email = r["customer_email"] or contact.email
        if not name and not email:
            continue
        if name and not r["customer_name"]:
            got_name += 1
        if email and not r["customer_email"]:
            got_email += 1
        payload["contact"] = {"id": contact.id, "name": name, "email": email}
        updates.append((name, email, json.dumps(payload), r["id"]))

    print(f"  {got_name} name(s) and {got_email} email(s) recovered; "
          f"{len(rows) - len(updates)} still unresolved.")
    for name, email, _, cid in updates[:5]:
        print(f"    {cid}  {name!r}  {email!r}")

    if args.dry_run:
        print("\n--dry-run: nothing written.")
        return

    # Only the customer columns and the payload's contact — see the module docstring.
    conn.executemany(
        "UPDATE conversations SET customer_name=?, customer_email=?, payload_json=? WHERE id=?",
        updates,
    )
    conn.commit()
    print(f"\nUpdated {len(updates)} row(s) in {settings.db_path}.")


if __name__ == "__main__":
    main()
