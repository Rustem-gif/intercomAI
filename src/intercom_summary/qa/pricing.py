"""What a grade cost: token usage summed over every call behind it, priced in USD.

Rates are $ per million tokens from the Claude pricing page (checked 2026-10-04). The Batch
API halves every line, cache reads and writes included — the discounts stack.
"""
from __future__ import annotations

# input, 5-minute cache write, 1-hour cache write, cache read, output — $ / Mtok
PRICES: dict[str, dict[str, float]] = {
    "claude-sonnet-5-5": {"in": 2.00, "write_5m": 2.50, "write_1h": 4.00, "read": 0.20, "out": 10.00},
    "claude-sonnet-5":   {"in": 2.00, "write_5m": 2.50, "write_1h": 4.00, "read": 0.20, "out": 10.00},
    "claude-opus-5-5":   {"in": 4.00, "write_5m": 5.00, "write_1h": 8.00, "read": 0.20, "out": 20.00},
    "claude-haiku-4-5":  {"in": 1.00, "write_5m": 1.25, "write_1h": 2.00, "read": 0.10, "out": 5.00},
}
BATCH_DISCOUNT = 0.5

USAGE_KEYS = ("input", "cache_write_5m", "cache_write_1h", "cache_read", "output")


def _rates(model: str) -> dict[str, float]:
    if model in PRICES:
        return PRICES[model]
    # Dated ids ("claude-haiku-4-5-20251001") and fallback-served variants price as their family.
    for name, rates in PRICES.items():
        if model.startswith(name):
            return rates
    return PRICES["claude-sonnet-5-5"]


def usage_dict(usage) -> dict[str, int]:
    """One API response's `usage` as plain counts. The 5m/1h split of cache writes comes from
    `usage.cache_creation` when the API reports it; otherwise every write counts as 5-minute."""
    g = lambda obj, k: (getattr(obj, k, None) if not isinstance(obj, dict) else obj.get(k)) or 0
    if usage is None:
        return dict.fromkeys(USAGE_KEYS, 0)
    written = g(usage, "cache_creation_input_tokens")
    split = getattr(usage, "cache_creation", None) if not isinstance(usage, dict) else usage.get("cache_creation")
    w1h = g(split, "ephemeral_1h_input_tokens") if split else 0
    return {
        "input": g(usage, "input_tokens"),
        "cache_write_5m": max(written - w1h, 0),
        "cache_write_1h": w1h,
        "cache_read": g(usage, "cache_read_input_tokens"),
        "output": g(usage, "output_tokens"),
    }


def cost_usd(counts: dict, model: str, batch: bool = False) -> float:
    r = _rates(model or "")
    dollars = (
        counts.get("input", 0) * r["in"]
        + counts.get("cache_write_5m", 0) * r["write_5m"]
        + counts.get("cache_write_1h", 0) * r["write_1h"]
        + counts.get("cache_read", 0) * r["read"]
        + counts.get("output", 0) * r["out"]
    ) / 1e6
    return dollars * (BATCH_DISCOUNT if batch else 1.0)


class UsageMeter:
    """Accumulates usage over the calls made for one grade (retries, a reconcile follow-up,
    a live re-grade after a failed batch item). Batch and live calls are priced separately."""

    def __init__(self) -> None:
        self.counts = dict.fromkeys(USAGE_KEYS, 0)
        self.calls = 0
        self.batch_calls = 0
        self.cost = 0.0

    def add(self, usage, model: str, batch: bool = False) -> None:
        one = usage_dict(usage)
        for k in USAGE_KEYS:
            self.counts[k] += one[k]
        self.calls += 1
        self.batch_calls += int(batch)
        self.cost += cost_usd(one, model, batch)

    def as_dict(self) -> dict:
        return {**self.counts, "calls": self.calls, "batch_calls": self.batch_calls,
                "cost_usd": round(self.cost, 6)}


def cache_hit_share(counts: dict) -> float:
    """Share of the prompt that came out of the cache (0–1)."""
    prompt = (counts.get("input", 0) + counts.get("cache_write_5m", 0)
              + counts.get("cache_write_1h", 0) + counts.get("cache_read", 0))
    return counts.get("cache_read", 0) / prompt if prompt else 0.0


def sum_usage(blocks) -> dict:
    """Total a run's per-grade usage blocks."""
    total = {**dict.fromkeys(USAGE_KEYS, 0), "calls": 0, "batch_calls": 0, "cost_usd": 0.0}
    for b in blocks:
        for k in total:
            total[k] += b.get(k, 0) or 0
    total["cost_usd"] = round(total["cost_usd"], 4)
    return total
