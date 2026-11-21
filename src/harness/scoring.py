"""Turn a dossier into a bounded sentiment score.

The no-key path uses only numbers and titles that were fetched. Persona
`score_style` changes the weights. It does not invent a catalyst.
"""

from __future__ import annotations

import math
import re

from harness.research import Dossier, Headline

BULLISH = {
    "beat",
    "beats",
    "surge",
    "surges",
    "rally",
    "upgrade",
    "raised",
    "record",
    "growth",
    "strong",
    "gain",
    "gains",
    "jump",
    "jumps",
    "soar",
    "bullish",
    "optimism",
    "buyback",
    "partnership",
    "approval",
    "breakthrough",
    "rises",
    "rise",
}
BEARISH = {
    "miss",
    "misses",
    "fall",
    "falls",
    "drop",
    "drops",
    "cut",
    "cuts",
    "downgrade",
    "weak",
    "lawsuit",
    "probe",
    "recall",
    "layoffs",
    "bearish",
    "decline",
    "slump",
    "warning",
    "investigation",
    "delay",
    "plunge",
    "sinks",
    "falls",
}
_WORD = re.compile(r"[a-zA-Z']+")


def lexicon(text: str) -> tuple[int, int]:
    bullish = 0
    bearish = 0
    for word in _WORD.findall((text or "").lower()):
        if word in BULLISH:
            bullish += 1
        elif word in BEARISH:
            bearish += 1
    return bullish, bearish


def headline_score(items: list[Headline]) -> tuple[float, int, int]:
    bullish = 0
    bearish = 0
    for item in items:
        up, down = lexicon(f"{item.title} {item.summary}")
        bullish += up
        bearish += down
    total = bullish + bearish
    if total == 0:
        return 0.0, 0, 0
    return max(-1.0, min(1.0, (bullish - bearish) / total)), bullish, bearish


def _tanh_return(change_pct: float | None) -> float:
    if change_pct is None:
        return 0.0
    return math.tanh((change_pct / 100.0) / 0.015)


def score_dossier(style: str, dossier: Dossier) -> tuple[float, dict]:
    tape = dossier.tape
    change = tape.change_pct if tape else None
    price = _tanh_return(change)
    news, bullish, bearish = headline_score(dossier.headlines + dossier.social)
    vol = tape.vol_20d if tape else None
    if style == "follow_return":
        raw = 0.8 * price + 0.2 * news
    elif style == "fade_return":
        raw = -0.55 * price + 0.45 * news
    elif style == "headline":
        raw = 0.35 * price + 0.65 * news
    elif style == "range_proxy":
        raw = 0.7 * price + 0.3 * news
        if change is not None and abs(change) >= 2.0:
            raw *= 0.85
    elif style == "vol_aware":
        raw = 0.75 * price + 0.25 * news
        if vol is not None and vol >= 0.025:
            raw *= 0.7
    else:
        raw = 0.45 * price + 0.55 * news
        raw *= 0.75
    if style == "dampened":
        raw *= 0.8
    detail = {
        "price_component": round(price, 4),
        "news_component": round(news, 4),
        "bullish_marks": bullish,
        "bearish_marks": bearish,
        "score_style": style,
        "vol_20d": None if vol is None else round(vol, 4),
    }
    return max(-1.0, min(1.0, raw)), detail
