"""Import a frozen calibration sample from the QA department's .docx list.

    python scripts/import_calibration_sample.py Vybirka_180_Chativ_King_Billy.docx \
        --id kb-v41-180 --name "King Billy v4.1 calibration (180)"

The document is the authority on membership, so the import is a straight transcription of its
table — no filtering, no de-duplication against what happens to be in the cache. It then
*reports* which members cannot be graded rather than dropping them, because a short sample is
a finding, not a detail: a member can be absent from the cache, or sitting in the trash, where
it silently blocks its own re-import from Intercom.

Re-running against an existing frozen sample refuses to change it. That is deliberate — the
comparability of two measurement runs is the only thing a calibration set is for.
"""
from __future__ import annotations

import argparse
import re
import sys
import zipfile
from xml.etree import ElementTree as ET

from intercom_summary.storage.calibration_store import CalibrationStore, SampleFrozenError

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

# The chat id column is the only one we must read exactly; everything else is descriptive.
_ID = re.compile(r"^\d{6,}$")
# The document is Ukrainian; "Так" is the Pilot column's yes.
_YES = {"так", "yes", "y", "+", "true"}


def _cell_text(cell) -> str:
    return " ".join(
        "".join(t.text or "" for t in p.iter(W + "t")) for p in cell.findall(W + "p")
    ).strip()


def rows_from_docx(path: str) -> list[list[str]]:
    root = ET.fromstring(zipfile.ZipFile(path).read("word/document.xml"))
    out: list[list[str]] = []
    for table in root.iter(W + "tbl"):
        for row in table.findall(W + "tr"):
            out.append([_cell_text(c) for c in row.findall(W + "tc")])
    return out


def parse_items(rows: list[list[str]]) -> list[dict]:
    """Pull the chat rows out of every table in the document.

    Columns are read positionally after the id, which is how the source document is laid out:
    №, Chat ID, date, agent, category, pilot. A row without a numeric chat id in the second
    column is a heading, a progress table or a note, and is skipped.
    """
    items: list[dict] = []
    seen: set[str] = set()
    for r in rows:
        if len(r) < 2 or not _ID.match(r[1]):
            continue
        cid = r[1]
        if cid in seen:            # the document lists each chat once; flag a duplicate loudly
            print(f"  ! duplicate chat id in the document, keeping the first: {cid}")
            continue
        seen.add(cid)
        items.append({
            "conversation_id": cid,
            "seq": int(r[0]) if r[0].isdigit() else len(items) + 1,
            "chat_date": r[2] if len(r) > 2 else None,
            "agent_name": r[3] if len(r) > 3 else None,
            "category": r[4] if len(r) > 4 else None,
            "pilot": len(r) > 5 and r[5].strip().lower() in _YES,
        })
    return items


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("docx", help="the .docx list of chats")
    ap.add_argument("--id", required=True, help="sample id, used to scope a review run")
    ap.add_argument("--name", required=True)
    ap.add_argument("--created-by", default="import script")
    ap.add_argument("--force", action="store_true",
                    help="replace the membership of an already-frozen sample")
    ap.add_argument("--dry-run", action="store_true", help="parse and report, write nothing")
    args = ap.parse_args()

    items = parse_items(rows_from_docx(args.docx))
    if not items:
        print(f"No chat rows found in {args.docx}", file=sys.stderr)
        return 1
    pilot = sum(1 for i in items if i["pilot"])
    print(f"Parsed {len(items)} chats ({pilot} marked pilot) from {args.docx}")

    if args.dry_run:
        for i in items[:5]:
            print(" ", i)
        print("  … (dry run, nothing written)")
        return 0

    store = CalibrationStore()
    try:
        store.create(args.id, args.name, source=args.docx, created_by=args.created_by)
        try:
            n = store.replace_items(args.id, items, force=args.force)
        except SampleFrozenError as e:
            print(f"{e}", file=sys.stderr)
            return 2
        print(f"Stored {n} chats as calibration sample {args.id!r}")

        missing = store.missing_conversations(args.id)
        trashed = store.trashed_conversations(args.id)
        if missing:
            print(f"\n{len(missing)} of them are NOT in the conversations cache and cannot be "
                  f"graded until they are fetched:")
            for cid in missing:
                print(f"  {cid}{'   (in the trash)' if cid in set(trashed) else ''}")
        if trashed:
            print(f"\n{len(trashed)} are in the trash. A trashed conversation silently blocks "
                  f"its own re-import from Intercom — restore them before fetching.")
        if not missing and not trashed:
            print("All sample chats are present in the cache.")
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
