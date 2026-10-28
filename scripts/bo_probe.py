"""First live check of the back-office client: log in unattended and save raw player pages.

    .venv/bin/python scripts/bo_probe.py 1745500 1321289

For each BO player id (the Intercom contact's `cleanid`) it saves, under data/bo/samples/<id>/
(git-ignored — the pages hold personal data):
  player.html            the player page (status, locks, tags, groups, payments, bonuses…)
  tags.html              the lazily loaded tags panel
  bonus_issued.json      issued bonuses for the player (JSON API)
  freespin_issued.json   issued free spins (JSON API)
and prints only what worked — page titles' panel names and counts, never personal data. The
parser (backoffice/player.py) is written against these samples.
"""
from __future__ import annotations

import json
import re
import sys
from datetime import date, timedelta

from intercom_summary.backoffice.client import BackOfficeClient, BackOfficeError
from intercom_summary.settings import settings


def _panels(html: str) -> list[str]:
    return [re.sub(r"\s+", " ", t).strip()
            for t in re.findall(r"<h3[^>]*>(.*?)</h3>", html, re.S)][:60]


def main() -> None:
    ids = [a for a in sys.argv[1:] if a.isdigit()]
    if not ids:
        sys.exit(__doc__)
    out_root = settings.bo_dir / "samples"
    since = (date.today() - timedelta(days=365)).isoformat()
    with BackOfficeClient() as bo:
        bo.login()
        print("login: ok")
        for pid in ids:
            out = out_root / pid
            out.mkdir(parents=True, exist_ok=True)
            try:
                html = bo.player_page(pid)
                (out / "player.html").write_text(html)
                print(f"{pid}: player page {len(html)} bytes, panels: {', '.join(_panels(html))}")
            except BackOfficeError as exc:
                print(f"{pid}: player page FAILED — {exc}")
                continue
            for name, path in (("tags.html", f"/backend/players/{pid}/tags"),):
                try:
                    (out / name).write_text(bo.page(path))
                    print(f"  {name}: ok")
                except Exception as exc:  # noqa: BLE001 — a probe reports, it does not stop
                    print(f"  {name}: {exc}")
            for name, path, params in (
                ("bonus_issued.json", "/api/v1/admin/reports/bonus_issued.json",
                 {"filter[user_id_eq]": pid, "filter[date_received_at_from]": since,
                  "filter[date_received_at_to]": date.today().isoformat(),
                  "order[id]": "desc", "page": 1, "per_page": 50}),
                ("freespin_issued.json", "/api/v1/admin/reports/freespin_issued.json",
                 {"filter[user_id_eq]": pid, "order[id]": "desc", "page": 1, "per_page": 50}),
            ):
                try:
                    data = bo.api(path, params)
                    (out / name).write_text(json.dumps(data))
                    rows = data.get("collection") or data.get("data") or []
                    users = {str((r.get("user") or {}).get("id", r.get("user_id", "")))
                             for r in rows if isinstance(r, dict)}
                    print(f"  {name}: {len(rows)} row(s); user ids in result: {sorted(users)[:3]}")
                except Exception as exc:  # noqa: BLE001
                    print(f"  {name}: {exc}")
    print(f"saved under {out_root}")


if __name__ == "__main__":
    main()
