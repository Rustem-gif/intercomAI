"""Convert Intercom HTML message bodies into clean, readable plain text."""
from __future__ import annotations

import re

try:
    from bs4 import BeautifulSoup

    _HAVE_BS4 = True
except Exception:  # pragma: no cover - fall back to regex if bs4 missing
    _HAVE_BS4 = False

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t]+")
_MULTINL_RE = re.compile(r"\n{3,}")


def html_to_text(html: str | None) -> str:
    if not html:
        return ""
    if _HAVE_BS4:
        soup = BeautifulSoup(html, "html.parser")
        # Turn block breaks into newlines before extracting text.
        for br in soup.find_all(["br"]):
            br.replace_with("\n")
        for block in soup.find_all(["p", "div", "li"]):
            block.append("\n")
        text = soup.get_text()
    else:
        text = _TAG_RE.sub(" ", html.replace("<br>", "\n").replace("</p>", "\n"))

    # Decode a few common entities BeautifulSoup may leave / regex path misses.
    text = (
        text.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&#39;", "'")
    )
    text = _WS_RE.sub(" ", text)
    text = _MULTINL_RE.sub("\n\n", text)
    return text.strip()
