"""Path setup and a fixture dossier for offline tests."""

from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

LEDGER = Path(os.environ.get("LEDGER_ROOT", "/tmp/agents-unite"))


def fixture_gather(ticker, search, name="", sector="", getter=None):
    from harness.research import Dossier, Headline, Tape

    digest = int(hashlib.sha256(ticker.encode("utf-8")).hexdigest()[:4], 16)
    change = ((digest % 40) - 15) / 10.0
    symbol = ticker.replace(".", "-")
    tape = Tape(
        ticker=ticker,
        last_close=100.0 + (digest % 50),
        prev_close=100.0,
        change_pct=change,
        volume=1_200_000.0,
        avg_volume_20=900_000.0,
        return_20d_pct=round(change * 3, 2),
        vol_20d=0.018,
        position_52w=0.62,
        as_of="2026-11-20",
        history_url=f"https://finance.yahoo.com/quote/{symbol}/history",
        spy_corr_20d=0.41 if "spy" in search and ticker != "SPY" else None,
        bar_count=120,
    )
    headlines = [
        Headline(
            title=f"{name or ticker} shares rise after a supplier update",
            url=f"https://news.google.com/rss/search?q={symbol}",
            publisher="Google News",
            published_at="2026-11-20T15:00:00Z",
            source_type="news",
            summary=f"Fetched headline for {ticker}.",
        )
    ]
    social = []
    if "reddit" in search:
        social.append(
            Headline(
                title=f"Daily thread mentions {ticker}",
                url=f"https://www.reddit.com/search/?q={symbol}",
                publisher="Reddit",
                published_at="2026-11-20T14:00:00Z",
                source_type="reddit",
                summary=f"Reddit item for {ticker}.",
            )
        )
    if "stocktwits" in search:
        social.append(
            Headline(
                title=f"Watching {ticker} into the close",
                url=f"https://stocktwits.com/example/message/{digest}",
                publisher="Stocktwits",
                published_at="2026-11-20T14:30:00Z",
                source_type="other",
                summary=f"Stocktwits item for {ticker}.",
            )
        )
    filings = []
    if "sec" in search:
        filings.append(
            Headline(
                title=f"{ticker} 8-K",
                url=f"https://www.sec.gov/cgi-bin/browse-edgar?CIK={symbol}&type=8-K",
                publisher="SEC",
                published_at="2026-11-20T13:00:00Z",
                source_type="other",
                summary=f"SEC atom entry for {ticker}.",
            )
        )
    return Dossier(
        ticker=ticker,
        name=name or ticker,
        sector=sector,
        tape=tape,
        headlines=headlines,
        social=social,
        filings=filings,
        errors=[],
        searched=list(search),
    )
