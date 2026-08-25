"""Brand identity for a multi-brand Intercom workspace.

One Intercom workspace hosts several casino brands. Each conversation carries the brand it
came through as the `Brand` conversation attribute; `intercom/fetch.py` copies it onto
`Conversation.brand` and we store that raw value verbatim.

Two things to know before touching this:

1. The **default** brand is named after the workspace, not the product — King Billy's
   conversations carry `Brand: "Betncare"`. A naive `brand == "King Billy"` filter matches
   nothing. That is the only reason this module exists: the raw value is the join key,
   `brand_label()` is for display.
2. `Brand` is **not searchable** through Intercom's conversation-search API (it rejects both
   `Brand` and `custom_attributes.Brand` with `invalid_field`), so fetching is necessarily
   brand-blind and all brand filtering happens locally against our own cache.
"""
from __future__ import annotations

# Raw Intercom `Brand` value → the name people actually call it. Brands absent from this map
# display under their raw name, so a newly launched brand needs no code change — add an entry
# only when Intercom's name for it differs from the one you want on screen.
BRAND_LABELS: dict[str, str] = {
    "Betncare": "King Billy",
}

# Shown for rows whose brand we could not determine (cached before brand capture existed, or
# an Intercom payload that carried no Brand attribute).
UNBRANDED_LABEL = "Unbranded"

# Filter token meaning "rows whose brand is empty". A real brand value is never empty, and an
# absent filter already means "every brand", so unbranded rows need a name of their own to be
# selectable at all — relying on an empty query string would make `?brand=` (easy to send by
# accident) silently mean something quite different from omitting it.
UNBRANDED = "__unbranded__"


def brand_filter_value(brand: str | None) -> str | None:
    """Map an API-facing brand filter to the value stored in the `brand` column.

    None (no filter) stays None; the UNBRANDED token becomes "".
    """
    if not brand:
        return None
    return "" if brand == UNBRANDED else brand


def brand_label(raw: str | None) -> str:
    """Display name for a raw Intercom brand value."""
    if not raw:
        return UNBRANDED_LABEL
    return BRAND_LABELS.get(raw, raw)
