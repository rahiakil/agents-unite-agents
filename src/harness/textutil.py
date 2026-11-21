"""Shared text cleanup. Fetched headlines sometimes contain dash characters."""

from __future__ import annotations

import re

EM_DASH = "\u2014"
EN_DASH = "\u2013"

_WS = re.compile(r"\s+")
_TAGS = re.compile(r"<[^>]+>")
_NUM = re.compile(r"(?<![A-Za-z])[+-]?\d+(?:\.\d+)?")


def clean_text(text: str) -> str:
    if not text:
        return ""
    cleaned = (
        text.replace(EM_DASH, ", ")
        .replace(EN_DASH, "-")
        .replace("\u2018", "'")
        .replace("\u2019", "'")
        .replace("\u201c", '"')
        .replace("\u201d", '"')
    )
    cleaned = _TAGS.sub(" ", cleaned)
    cleaned = _WS.sub(" ", cleaned).strip()
    return cleaned


def numbers_in(text: str) -> set[str]:
    found = set()
    for match in _NUM.finditer(text or ""):
        raw = match.group(0).lstrip("+")
        found.add(raw)
        if "." in raw:
            found.add(raw.rstrip("0").rstrip("."))
        else:
            found.add(f"{raw}.0")
    return {item for item in found if item}


def format_score(score: float) -> str:
    """Format a score so both ledger validators accept it.

    The validator on main only matches 0.x, -0.x, 1, 1.0, -1, and -1.0.
    Scores stay inside (-1, 1) so a two-decimal print cannot become 1.00.
    """
    value = max(-0.95, min(0.95, float(score)))
    text = f"{value:.2f}"
    if text in {"-0.00", "0.00"}:
        return "0.00"
    return text
