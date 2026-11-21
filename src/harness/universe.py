"""Active ticker universe.

Prefer the ledger checkout when one is available, so a delisted name drops
out as soon as the ledger marks it inactive. The bundled snapshot is the
offline fallback.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BUNDLED = ROOT / "data" / "universe.json"


@dataclass(frozen=True)
class Ticker:
    ticker: str
    name: str
    sector: str
    exchange: str
    active: bool


def load_universe(ledger_root: Path | None = None) -> dict[str, Ticker]:
    path = BUNDLED
    if ledger_root is not None:
        candidate = ledger_root / "tickers" / "universe.json"
        if candidate.is_file():
            path = candidate
    data = json.loads(path.read_text(encoding="utf-8"))
    out: dict[str, Ticker] = {}
    for entry in data.get("tickers") or []:
        symbol = str(entry.get("ticker") or "").upper()
        if not symbol:
            continue
        out[symbol] = Ticker(
            ticker=symbol,
            name=str(entry.get("name") or symbol),
            sector=str(entry.get("sector") or ""),
            exchange=str(entry.get("exchange") or ""),
            active=bool(entry.get("active", True)),
        )
    return out


def active_symbols(universe: dict[str, Ticker]) -> list[str]:
    return sorted(symbol for symbol, item in universe.items() if item.active)
