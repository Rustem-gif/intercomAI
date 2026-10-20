"""AI ↔ human agreement on PASS/FAIL — the number the client holds us to (≥ 95%).

QA does not review a random sample: it opens the chats the AI failed (and those flagged for
manual review) far more often than the ones it passed. A plain "agreed / reviewed" ratio over
those reviews therefore measures the AI's FAIL calls, not the AI. The population estimate
weights each side by how often the AI actually gives it:

    agreement ≈ P(AI PASS) · agree | AI PASS  +  P(AI FAIL) · agree | AI FAIL

where each conditional rate comes from the human reviews of that side (an override, an
"Agree" confirmation, or a calibration verdict). A side with no reviews is reported as
unknown rather than assumed perfect.

The dev / holdout split keeps prompt tuning honest: rules are written from the dev chats
only and the holdout is used once, to check them.
"""
from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass

PASS_THRESHOLD = 85
HOLDOUT_SHARE = 0.3


def split_of(conversation_id: str, holdout_share: float = HOLDOUT_SHARE) -> str:
    """'dev' or 'holdout' — stable for an id, whatever else is in the set."""
    h = int(hashlib.sha256(f"qa-accuracy:{conversation_id}".encode()).hexdigest()[:8], 16)
    return "holdout" if h / 0xFFFFFFFF < holdout_share else "dev"


def result_of(score: int | None, result: str | None = None) -> str | None:
    """PASS/FAIL from a stored result, else from the score."""
    if result in ("PASS", "FAIL"):
        return result
    if score is None:
        return None
    return "PASS" if score >= PASS_THRESHOLD else "FAIL"


def wilson(agree: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson interval for a proportion, as fractions."""
    if n == 0:
        return (0.0, 1.0)
    p = agree / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


@dataclass
class Side:
    """Human reviews of the chats the AI put on one side (PASS or FAIL)."""
    ai_result: str
    population: int = 0     # every AI grade on this side
    reviewed: int = 0       # of which a human reviewed
    agreed: int = 0         # of which the human gave the same result

    @property
    def rate(self) -> float | None:
        return self.agreed / self.reviewed if self.reviewed else None

    def as_dict(self) -> dict:
        lo, hi = wilson(self.agreed, self.reviewed)
        return {"ai_result": self.ai_result, "population": self.population,
                "reviewed": self.reviewed, "agreed": self.agreed,
                "rate": None if self.rate is None else round(self.rate, 4),
                "ci95": [round(lo, 4), round(hi, 4)] if self.reviewed else None}


def population_agreement(rows: list[dict]) -> dict:
    """`rows`: one per AI grade, {"ai": PASS|FAIL, "human": PASS|FAIL|None}.

    Returns both sides, the weighted estimate (None while a side is unreviewed) and the
    naive reviewed-only ratio, so the two can be shown side by side."""
    sides = {r: Side(r) for r in ("PASS", "FAIL")}
    for row in rows:
        side = sides.get(row.get("ai"))
        if side is None:
            continue
        side.population += 1
        if row.get("human") in ("PASS", "FAIL"):
            side.reviewed += 1
            side.agreed += row["human"] == row["ai"]
    total = sum(s.population for s in sides.values())
    estimate = None
    if total and all(s.rate is not None or s.population == 0 for s in sides.values()):
        estimate = sum(s.population / total * (s.rate or 0.0) for s in sides.values())
    reviewed = sum(s.reviewed for s in sides.values())
    agreed = sum(s.agreed for s in sides.values())
    # The weighted estimate's uncertainty: the Wilson bounds of each side, weighted the same way.
    ci = None
    if estimate is not None:
        bounds = [(s.population / total, wilson(s.agreed, s.reviewed)) for s in sides.values()
                  if s.population]
        ci = [round(sum(w * b[0] for w, b in bounds), 4), round(sum(w * b[1] for w, b in bounds), 4)]
    return {
        "total": total,
        "sides": {k: s.as_dict() for k, s in sides.items()},
        "estimate": None if estimate is None else round(estimate, 4),
        "ci95": ci,
        "reviewed_only": round(agreed / reviewed, 4) if reviewed else None,
        "known_disagreements": reviewed - agreed,
    }


def paired_agreement(a: dict[str, str], b: dict[str, str]) -> dict:
    """Result agreement between two labelings of the same chats (AI vs human, or run vs run)."""
    common = [k for k in a if k in b and a[k] and b[k]]
    same = sum(a[k] == b[k] for k in common)
    lo, hi = wilson(same, len(common))
    return {"n": len(common), "same": same,
            "rate": round(same / len(common), 4) if common else None,
            "ci95": [round(lo, 4), round(hi, 4)] if common else None,
            "a_fail_b_pass": sorted(k for k in common if a[k] == "FAIL" and b[k] == "PASS"),
            "a_pass_b_fail": sorted(k for k in common if a[k] == "PASS" and b[k] == "FAIL")}
