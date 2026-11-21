"""Fetch grounded inputs: Yahoo daily bars, public RSS, optional social and SEC.

Nothing here invents a URL. A source is recorded only when the fetch returns
it. Callers can pass `http_get` in tests.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from harness.textutil import clean_text

UA = "Mozilla/5.0 (compatible; agents-unite-agents/0.1; +https://github.com/rahiakil/agents-unite-agents)"
SEC_UA = "agents-unite-agents/0.1 (research; github.com/rahiakil/agents-unite-agents)"


@dataclass
class Headline:
    title: str
    url: str
    publisher: str
    published_at: str
    source_type: str
    summary: str


@dataclass
class Tape:
    ticker: str
    last_close: float | None
    prev_close: float | None
    change_pct: float | None
    volume: float | None
    avg_volume_20: float | None
    return_20d_pct: float | None
    vol_20d: float | None
    position_52w: float | None
    as_of: str | None
    history_url: str
    spy_corr_20d: float | None = None
    bar_count: int = 0
    closes: list[tuple[str, float]] = field(default_factory=list)


@dataclass
class Dossier:
    ticker: str
    name: str
    sector: str
    tape: Tape | None
    headlines: list[Headline]
    social: list[Headline]
    filings: list[Headline]
    errors: list[str]
    searched: list[str]


def http_get(url: str, *, timeout: float = 12.0, headers: dict[str, str] | None = None) -> bytes:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": UA, "Accept": "*/*", **(headers or {})},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def yahoo_symbol(ticker: str) -> str:
    return ticker.upper().replace(".", "-")


def _iso(value: str | None) -> str:
    if not value:
        return ""
    text = value.strip()
    try:
        return parsedate_to_datetime(text).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError, OverflowError):
        pass
    if "T" in text:
        return text.replace("+00:00", "Z")
    return text


def _local(tag: str) -> str:
    if "}" in tag:
        return tag.rsplit("}", 1)[-1]
    return tag


def parse_feed(payload: bytes, *, source_type: str, publisher: str, limit: int = 6) -> list[Headline]:
    try:
        root = ET.fromstring(payload)
    except ET.ParseError:
        return []
    items: list[Headline] = []
    for node in root.iter():
        if _local(node.tag) not in {"item", "entry"}:
            continue
        title = ""
        url = ""
        published = ""
        summary = ""
        source_name = publisher
        for child in list(node):
            name = _local(child.tag)
            text = clean_text(child.text or "")
            if name == "title" and text:
                title = text
            elif name == "link":
                href = child.attrib.get("href") or text
                if href.startswith("http"):
                    url = href
            elif name in {"pubDate", "published", "updated"} and text and not published:
                published = _iso(text)
            elif name in {"description", "summary"} and text and not summary:
                summary = text[:400]
            elif name == "source" and text:
                source_name = text
        if title and url.startswith("http"):
            items.append(
                Headline(
                    title=title[:240],
                    url=url,
                    publisher=source_name or publisher,
                    published_at=published,
                    source_type=source_type,
                    summary=(summary or title)[:400],
                )
            )
        if len(items) >= limit:
            break
    return items


def _mean(values: list[float]) -> float:
    return sum(values) / len(values)


def _corr(left: list[float], right: list[float]) -> float | None:
    pairs = list(zip(left, right))
    if len(pairs) < 10:
        return None
    xs = [item[0] for item in pairs]
    ys = [item[1] for item in pairs]
    mx = _mean(xs)
    my = _mean(ys)
    var_x = sum((x - mx) ** 2 for x in xs)
    var_y = sum((y - my) ** 2 for y in ys)
    if var_x <= 0 or var_y <= 0:
        return None
    cov = sum((x - mx) * (y - my) for x, y in pairs)
    return cov / (var_x ** 0.5 * var_y ** 0.5)


def _returns(closes: list[tuple[str, float]]) -> dict[str, float]:
    ordered = sorted(closes)
    out: dict[str, float] = {}
    previous: float | None = None
    for day, close in ordered:
        if previous:
            out[day] = close / previous - 1.0
        previous = close
    return out


def parse_chart(payload: bytes, ticker: str) -> Tape | None:
    try:
        data = json.loads(payload.decode("utf-8"))
    except json.JSONDecodeError:
        return None
    result = ((data.get("chart") or {}).get("result") or [None])[0]
    if not isinstance(result, dict):
        return None
    quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
    timestamps = result.get("timestamp") or []
    closes = quote.get("close") or []
    highs = quote.get("high") or []
    lows = quote.get("low") or []
    volumes = quote.get("volume") or []
    series: list[tuple[str, float, float | None, float | None, float | None]] = []
    for index, stamp in enumerate(timestamps):
        close = closes[index] if index < len(closes) else None
        if close is None:
            continue
        high = highs[index] if index < len(highs) else None
        low = lows[index] if index < len(lows) else None
        volume = volumes[index] if index < len(volumes) else None
        day = datetime.fromtimestamp(int(stamp), timezone.utc).date().isoformat()
        series.append((day, float(close), None if high is None else float(high), None if low is None else float(low), None if volume is None else float(volume)))
    if len(series) < 2:
        return None
    last_day, last_close, _, _, last_volume = series[-1]
    prev_close = series[-2][1]
    change_pct = ((last_close - prev_close) / prev_close * 100.0) if prev_close else None
    volume_window = [row[4] for row in series[-21:-1] if row[4]]
    avg_volume = _mean(volume_window) if volume_window else None
    close_pairs = [(row[0], row[1]) for row in series]
    if len(close_pairs) > 21 and close_pairs[-21][1]:
        return_20d = (last_close / close_pairs[-21][1] - 1.0) * 100.0
    else:
        return_20d = None
    daily = list(_returns(close_pairs).values())
    recent = daily[-20:]
    if len(recent) >= 5:
        mu = _mean(recent)
        vol_20d = (sum((item - mu) ** 2 for item in recent) / (len(recent) - 1)) ** 0.5
    else:
        vol_20d = None
    high_values = [row[2] for row in series if row[2] is not None]
    low_values = [row[3] for row in series if row[3] is not None]
    if high_values and low_values and max(high_values) > min(low_values):
        position = (last_close - min(low_values)) / (max(high_values) - min(low_values))
    else:
        position = None
    symbol = yahoo_symbol(ticker)
    return Tape(
        ticker=ticker,
        last_close=last_close,
        prev_close=prev_close,
        change_pct=change_pct,
        volume=last_volume,
        avg_volume_20=avg_volume,
        return_20d_pct=return_20d,
        vol_20d=vol_20d,
        position_52w=position,
        as_of=last_day,
        history_url=f"https://finance.yahoo.com/quote/{symbol}/history",
        bar_count=len(series),
        closes=close_pairs,
    )


def _get(url: str, getter, headers: dict[str, str] | None = None) -> bytes:
    return getter(url, headers=headers) if headers is not None else getter(url)


def fetch_tape(ticker: str, getter) -> Tape | None:
    symbol = urllib.parse.quote(yahoo_symbol(ticker))
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=1y&interval=1d&includePrePost=false"
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            return parse_chart(getter(url), ticker)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt == 0:
                time.sleep(0.4)
    if last_error:
        raise last_error
    return None


def fetch_news(ticker: str, getter) -> list[Headline]:
    symbol = urllib.parse.quote(yahoo_symbol(ticker))
    query = urllib.parse.quote(f"{ticker} stock when:7d")
    urls = [
        (
            f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={symbol}&region=US&lang=en-US",
            "Yahoo Finance",
        ),
        (
            f"https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en",
            "Google News",
        ),
    ]
    found: list[Headline] = []
    seen: set[str] = set()
    for url, publisher in urls:
        try:
            batch = parse_feed(getter(url), source_type="news", publisher=publisher, limit=4)
        except (urllib.error.URLError, TimeoutError):
            continue
        for item in batch:
            if item.url in seen:
                continue
            seen.add(item.url)
            found.append(item)
    return found[:8]


def fetch_reddit(ticker: str, getter) -> list[Headline]:
    query = urllib.parse.quote(ticker)
    url = f"https://www.reddit.com/search.rss?q={query}&sort=new&t=week"
    try:
        return parse_feed(getter(url), source_type="reddit", publisher="Reddit", limit=4)
    except (urllib.error.URLError, TimeoutError):
        return []


def fetch_stocktwits(ticker: str, getter) -> list[Headline]:
    symbol = urllib.parse.quote(yahoo_symbol(ticker))
    url = f"https://api.stocktwits.com/api/2/streams/symbol/{symbol}.json"
    try:
        payload = json.loads(getter(url).decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, UnicodeDecodeError):
        return []
    messages = payload.get("messages") or []
    items: list[Headline] = []
    for message in messages[:5]:
        if not isinstance(message, dict):
            continue
        body = clean_text(str(message.get("body") or ""))
        user = ((message.get("user") or {}).get("username")) or "stocktwits"
        message_id = message.get("id")
        if not body or not message_id:
            continue
        link = f"https://stocktwits.com/{user}/message/{message_id}"
        created = str(message.get("created_at") or "")
        items.append(
            Headline(
                title=body[:180],
                url=link,
                publisher="Stocktwits",
                published_at=_iso(created),
                source_type="other",
                summary=body[:400],
            )
        )
    return items


def fetch_filings(ticker: str, getter) -> list[Headline]:
    symbol = urllib.parse.quote(ticker)
    url = (
        "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany"
        f"&CIK={symbol}&type=8-K&owner=include&count=5&output=atom"
    )
    try:
        payload = getter(url, headers={"User-Agent": SEC_UA, "Accept": "application/atom+xml"})
    except TypeError:
        try:
            payload = getter(url)
        except (urllib.error.URLError, TimeoutError):
            return []
    except (urllib.error.URLError, TimeoutError):
        return []
    return parse_feed(payload, source_type="other", publisher="SEC", limit=3)


def _spy_corr(tape: Tape, getter) -> float | None:
    if tape.ticker.upper() == "SPY":
        return None
    try:
        spy = fetch_tape("SPY", getter)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return None
    if spy is None:
        return None
    left = _returns(tape.closes)
    right = _returns(spy.closes)
    days = sorted(set(left) & set(right))[-20:]
    return _corr([left[day] for day in days], [right[day] for day in days])


def gather(
    ticker: str,
    search: list[str] | tuple[str, ...],
    *,
    name: str = "",
    sector: str = "",
    getter=http_get,
) -> Dossier:
    wanted = list(search)
    errors: list[str] = []
    tape: Tape | None = None
    headlines: list[Headline] = []
    social: list[Headline] = []
    filings: list[Headline] = []
    if "bars" in wanted:
        try:
            tape = fetch_tape(ticker, getter)
            if tape is None:
                errors.append("bars: empty chart")
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            errors.append(f"bars: {type(exc).__name__}")
    if "news" in wanted:
        try:
            headlines = fetch_news(ticker, getter)
        except (urllib.error.URLError, TimeoutError) as exc:
            errors.append(f"news: {type(exc).__name__}")
    if "reddit" in wanted:
        social.extend(fetch_reddit(ticker, getter))
    if "stocktwits" in wanted:
        social.extend(fetch_stocktwits(ticker, getter))
    if "sec" in wanted:
        filings = fetch_filings(ticker, getter)
    if tape is not None and "spy" in wanted:
        tape.spy_corr_20d = _spy_corr(tape, getter)
    return Dossier(
        ticker=ticker,
        name=name or ticker,
        sector=sector,
        tape=tape,
        headlines=headlines,
        social=social,
        filings=filings,
        errors=errors,
        searched=wanted,
    )


def has_sources(dossier: Dossier) -> bool:
    if dossier.tape is not None:
        return True
    return bool(dossier.headlines or dossier.social or dossier.filings)
