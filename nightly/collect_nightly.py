#!/usr/bin/env python3
"""Zero-token nightly collector for the agents-unite ledger.

Deterministic Python. No LLM call anywhere. For each ticker in nightly/sp500.csv
and one US session date D it gathers only free, public, keyless sources:

  * Google News RSS search per ticker (news.google.com/rss/search), bounded with
    after:/before: and then filtered locally to items published within the 72
    hours before the D close. Date-only stamps count as published at the end of
    their day, so a date-only item dated D is dropped (it may be after the close).
  * Yahoo Finance chart API daily bars dated on or before D (close, change,
    volume, 52-week range).
  * SEC EDGAR submissions JSON (data.sec.gov) for 8-K, 10-Q and 10-K filings
    dated in the same window.

Scoring is a fixed word list: VADER plus a finance lexicon (lexicon-vader-fin-v1).
Reports state facts only: dated headlines with links, the price move, the 52-week
range, filings and the lexicon score. No opinions, no model text.

Usage:
  python nightly/collect_nightly.py --session auto --ledger /path/to/ledger --out /path/to/ledger
  python nightly/collect_nightly.py --session 2026-11-24 --backfill ...
"""
from __future__ import annotations

import argparse
import csv
import email.utils
import hashlib
import html
import json
import os
import random
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, time as dtime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
NY = ZoneInfo("America/New_York")
PT = ZoneInfo("America/Los_Angeles")
HARNESS = "agents-unite-nightly-script"
METHOD = "lexicon-vader-fin-v1+nightly-script-v1"
UA = "Mozilla/5.0 (X11; Linux x86_64) agents-unite-nightly/1.0 (+https://github.com/rahiakil/agents-unite-agents)"
# SEC fair-access policy wants a name and a contact email in the User-Agent.
# SEC_CONTACT may be "Name email@domain" (preferred), or just an email.
# SEC_CONTACT_NAME and SEC_CONTACT_EMAIL can be set instead. Default is
# Parag Paul <parag@rigai.co>. The SEC step is skipped only if the contact
# has no email after those are applied.
DEFAULT_SEC_NAME = "Parag Paul"
DEFAULT_SEC_EMAIL = "parag@rigai.co"
EMAIL_RE = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


def parse_sec_contact(raw: str | None = None, name: str | None = None, email: str | None = None) -> tuple[str, str] | None:
    raw = (os.environ.get("SEC_CONTACT", "") if raw is None else raw).strip()
    name = (os.environ.get("SEC_CONTACT_NAME", "") if name is None else name).strip()
    email = (os.environ.get("SEC_CONTACT_EMAIL", "") if email is None else email).strip()
    if raw:
        parts = raw.split()
        emails = [p.strip("<>") for p in parts if EMAIL_RE.fullmatch(p.strip("<>"))]
        names = [p for p in parts if not EMAIL_RE.fullmatch(p.strip("<>"))]
        if emails:
            email = email or emails[-1]
        if names and not name:
            name = " ".join(names)
        elif not email and EMAIL_RE.fullmatch(raw.strip("<>")):
            email = raw.strip("<>")
    if not email:
        if raw or name:
            return None
        email = DEFAULT_SEC_EMAIL
    if not name:
        name = DEFAULT_SEC_NAME
    return name, email


def sec_user_agent(raw: str | None = None, name: str | None = None, email: str | None = None) -> str | None:
    parsed = parse_sec_contact(raw=raw, name=name, email=email)
    if not parsed:
        return None
    return f"{parsed[0]} {parsed[1]}"
WINDOW_H = 72
SEC_FORMS = {"8-K", "8-K/A", "10-Q", "10-K", "10-K/A"}

# NYSE full closures and 1:00 pm ET early closes (nyse.com holiday calendar).
NYSE_HOLIDAYS = {
    "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25", "2026-06-19",
    "2026-07-03", "2026-09-07", "2026-11-26", "2026-12-25",
    "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26", "2027-05-31", "2027-06-18",
    "2027-07-05", "2027-09-06", "2027-11-25", "2027-12-24",
}
NYSE_HOLIDAYS |= {
    "2024-01-01", "2024-01-15", "2024-02-19", "2024-03-29", "2024-05-27", "2024-06-19", "2024-07-04",
    "2024-09-02", "2024-11-28", "2024-12-25",
    "2025-01-01", "2025-01-09", "2025-01-20", "2025-02-17", "2025-04-18", "2025-05-26", "2025-06-19",
    "2025-07-04", "2025-09-01", "2025-11-27", "2025-12-25",
}
# Older holidays are not listed; the collector also skips any day with no S&P 500 bar.
EARLY_CLOSE = {
    "2026-11-27", "2026-12-24", "2027-11-26",
    "2025-07-03", "2025-11-28", "2025-12-24", "2024-07-03", "2024-11-29", "2024-12-24",
    "2023-07-03", "2023-11-24", "2022-11-25", "2021-11-26", "2020-11-27", "2020-12-24",
    "2019-07-03", "2019-11-29", "2019-12-24", "2018-07-03", "2018-11-23", "2018-12-24",
}

FIN_LEXICON = {
    "beat": 2.0, "beats": 2.0, "tops": 1.5, "topped": 1.5, "surge": 2.5, "surges": 2.5,
    "surged": 2.5, "soar": 2.5, "soars": 2.5, "soared": 2.5, "jump": 1.8, "jumps": 1.8,
    "jumped": 1.8, "rally": 2.0, "rallies": 2.0, "rallied": 2.0, "gain": 1.5, "gains": 1.5,
    "climb": 1.5, "climbs": 1.5, "rebound": 1.5, "rebounds": 1.5, "upgrade": 2.2,
    "upgrades": 2.2, "upgraded": 2.2, "outperform": 1.8, "overweight": 1.5, "bullish": 2.5,
    "record": 1.2, "raises": 1.2, "raised": 1.0, "buyback": 1.5, "approval": 1.8,
    "approved": 1.8, "breakout": 1.5, "strong": 1.5, "upside": 1.5,
    "miss": -2.0, "misses": -2.0, "missed": -2.0, "plunge": -2.8, "plunges": -2.8,
    "plunged": -2.8, "tumble": -2.5, "tumbles": -2.5, "tumbled": -2.5, "sink": -2.0,
    "sinks": -2.0, "slump": -2.2, "slumps": -2.2, "slide": -1.5, "slides": -1.5,
    "drop": -1.5, "drops": -1.5, "dropped": -1.5, "fall": -1.5, "falls": -1.5, "fell": -1.5,
    "decline": -1.5, "declines": -1.5, "selloff": -2.2, "sell-off": -2.2, "downgrade": -2.2,
    "downgrades": -2.2, "downgraded": -2.2, "underperform": -1.8, "underweight": -1.5,
    "bearish": -2.5, "lawsuit": -1.8, "probe": -1.5, "investigation": -1.2, "recall": -1.8,
    "layoffs": -1.5, "cuts": -1.0, "warns": -1.8, "warning": -1.5, "weak": -1.5,
    "downside": -1.5, "bubble": -1.5, "fears": -1.5, "volatile": -0.8, "lower": -0.8,
    "higher": 0.8, "slashes": -1.8, "halts": -1.5, "delay": -1.2, "delays": -1.2,
}
EVENT_PATTERNS = [
    ("earnings", re.compile(r"\b(earnings|quarter|q[1-4]|eps|revenue|results|guidance)\b", re.I)),
    ("analyst action", re.compile(r"\b(upgrade[sd]?|downgrade[sd]?|price target|initiat\w+ coverage|overweight|underweight|outperform)\b", re.I)),
    ("M&A / deal", re.compile(r"\b(acquir\w+|acquisition|merger|deal|stake|takeover|buyout)\b", re.I)),
    ("legal / regulatory", re.compile(r"\b(lawsuit|sue[sd]?|probe|investigation|antitrust|sec\b|ftc|doj|fda|regulator\w*|fine[sd]?)\b", re.I)),
    ("capital return", re.compile(r"\b(dividend|buyback|repurchase)\b", re.I)),
    ("product / launch", re.compile(r"\b(launch\w*|unveil\w*|release[sd]?|rollout)\b", re.I)),
    ("management", re.compile(r"\b(ceo|cfo|steps down|resign\w*|appoint\w*)\b", re.I)),
]
HOLDINGS_SPAM = re.compile(
    r"(\b(shares|stake|holdings|position) (of|in)\b.*\b(sells|buys|acquires|boosts?|trims?|cuts?|raises|lowers|decreased|increased|boosted)\b"
    r"|\b(sells|buys|acquires|purchases) [\d,]+ shares\b|\bholdings (boosted|decreased|increased|lifted|trimmed|cut|raised|lowered)\b"
    r"|\b(position|stake|holdings) (boosted|decreased|increased|lifted|trimmed|cut|raised|lowered|reduced|grown)\b)", re.I)
MARKETBEAT_SPAM = re.compile(
    r"\$[A-Z.]{1,6}\b.{0,12}\b(shares|stock|position|stake|holdings)\b.{0,60}\b(by|acquired|purchased|sold|bought|trims|reduces|decreases|increases|boosts|raises|lowers|cuts|lifts|grows|adds|holdings|stake|position)\b"
    r"|stock price, news, quote|\bshares (acquired|purchased|sold|bought) by\b|\bstake (trimmed|raised|lowered|boosted|cut) by\b", re.I)
NAME_SUFFIX = re.compile(
    r"[,.]?\s+(Inc\.?|Incorporated|Corporation|Corp\.?|Company|Co\.?|plc|PLC|Ltd\.?|Limited|N\.V\.|S\.A\.|SE|AG|"
    r"Holdings?|Group|Technologies|\(The\)|\(Class [A-Z]\)|Class [A-Z])$")


def log(msg: str) -> None:
    print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%SZ')}] {msg}", flush=True)


def clean(text: str) -> str:
    """House style: no em or en dashes in anything written to the ledger."""
    return re.sub(r"\s*[\u2014\u2013]\s*", " - ", text).replace("&amp;", "&")


# ---------------------------------------------------------------- calendar
def is_trading_day(d: date) -> bool:
    return d.weekday() < 5 and d.isoformat() not in NYSE_HOLIDAYS


def close_dt(d: date) -> datetime:
    hour = 13 if d.isoformat() in EARLY_CLOSE else 16
    return datetime.combine(d, dtime(hour, 0), NY).astimezone(timezone.utc)


def auto_session(now: datetime | None = None) -> date | None:
    """The ET calendar date of 'now' if it is a trading day whose close has passed."""
    now = now or datetime.now(timezone.utc)
    d = now.astimezone(NY).date()
    if not is_trading_day(d) or now < close_dt(d):
        return None
    return d


# ---------------------------------------------------------------- http with polite limits
class Limiter:
    """Minimum spacing between requests to one host, shared across threads."""

    def __init__(self, interval: float):
        self.interval, self.lock, self.next = interval, threading.Lock(), 0.0

    def wait(self) -> None:
        with self.lock:
            now = time.monotonic()
            slot = max(now, self.next)
            self.next = slot + self.interval * random.uniform(0.8, 1.3)
        time.sleep(max(0.0, slot - now))

    def penalize(self, seconds: float) -> None:
        with self.lock:
            self.next = max(self.next, time.monotonic() + seconds)


LIMITS = {"news.google.com": Limiter(0.45), "query1.finance.yahoo.com": Limiter(0.15), "data.sec.gov": Limiter(0.15)}
STATS = {"requests": 0, "throttled": 0, "cache_hits": 0, "errors": 0}
_STATS_LOCK = threading.Lock()


def _bump(k: str) -> None:
    with _STATS_LOCK:
        STATS[k] += 1


def http_get(url: str, *, headers: dict | None = None, cache: Path | None = None, retries: int = 4, timeout: int = 25) -> bytes:
    if cache:
        cp = cache / (hashlib.sha256(url.encode()).hexdigest()[:24])
        if cp.is_file():
            _bump("cache_hits")
            return cp.read_bytes()
    host = urllib.parse.urlparse(url).netloc
    lim = LIMITS.get(host)
    last: Exception | None = None
    for attempt in range(retries + 1):
        if lim:
            lim.wait()
        _bump("requests")
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read()
            if cache:
                cache.mkdir(parents=True, exist_ok=True)
                cp.write_bytes(body)
            return body
        except urllib.error.HTTPError as exc:
            last = exc
            if exc.code in (400, 401, 403, 404):
                break
            if exc.code in (429, 503):
                _bump("throttled")
                back = min(90.0, 5.0 * 2 ** attempt) * random.uniform(0.8, 1.2)
                if lim:
                    lim.penalize(back)
                time.sleep(back)
                continue
        except Exception as exc:  # noqa: BLE001
            last = exc
        time.sleep(1.5 * (attempt + 1) * random.uniform(0.8, 1.2))
    _bump("errors")
    raise RuntimeError(f"GET failed {urllib.parse.urlparse(url).netloc}: {last}")


# ---------------------------------------------------------------- universe
def strip_name(name: str) -> str:
    n = name.strip()
    for _ in range(3):
        m = NAME_SUFFIX.search(n)
        if not m:
            break
        n = n[: m.start()].strip(" ,.")
    n = re.sub(r"\s*&$", "", n).strip()
    return n or name


def load_universe(path: Path, aliases_path: Path) -> list[dict]:
    aliases = json.loads(aliases_path.read_text()) if aliases_path.is_file() else {}
    out = []
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            t = row["ticker"].strip().upper()
            al = aliases.get(t) or [strip_name(row["name"])]
            out.append({"ticker": t, "name": row["name"].strip(), "aliases": al, "sector": row.get("sector", ""),
                        "cik": (row.get("cik") or "").strip()})
    return out


def yahoo_symbol(t: str) -> str:
    return t.replace(".", "-")


# ---------------------------------------------------------------- prices
_CHART: dict[str, list] = {}
_CHART_LOCK = threading.Lock()


def chart_url(ysym: str, d: date | None = None) -> tuple[str, str]:
    """Recent sessions use range=2y. Older ones use a fixed window from Jan 1 of the
    year before D to Jan 1 of the year after, so a backfill run reuses one fetch
    per symbol for every day in that window (and the URL is cacheable)."""
    q = urllib.parse.quote(ysym)
    today = datetime.now(timezone.utc).date()
    if d is None or (today - d).days < 300:
        return f"{ysym}|2y", f"https://query1.finance.yahoo.com/v8/finance/chart/{q}?range=2y&interval=1d&includePrePost=false"
    p1 = int(datetime(d.year - 1, 1, 1, tzinfo=timezone.utc).timestamp())
    p2 = int(min(datetime(d.year + 1, 1, 1, tzinfo=timezone.utc), datetime.now(timezone.utc)).timestamp())
    return (f"{ysym}|{d.year}", f"https://query1.finance.yahoo.com/v8/finance/chart/{q}?period1={p1}&period2={p2}"
            "&interval=1d&includePrePost=false")


def chart_rows(ysym: str, cache: Path | None, d: date | None = None) -> list:
    key, url = chart_url(ysym, d)
    with _CHART_LOCK:
        if key in _CHART:
            return _CHART[key]
    data = json.loads(http_get(url, cache=cache))
    res = data["chart"]["result"][0]
    q = res["indicators"]["quote"][0]
    rows = []
    for i, t in enumerate(res.get("timestamp") or []):
        c = q["close"][i]
        if c is None:
            continue
        rows.append((datetime.fromtimestamp(t, NY).date(), c, q["volume"][i], q["high"][i], q["low"][i]))
    with _CHART_LOCK:
        _CHART[key] = rows
    return rows


def price_on(ysym: str, d: date, cache: Path | None = None) -> dict:
    out = {"source_url": f"https://finance.yahoo.com/quote/{urllib.parse.quote(ysym)}/history"}
    try:
        rows = [r for r in chart_rows(ysym, cache, d) if r[0] <= d]
        if not rows or rows[-1][0] != d:
            out["error"] = f"no bar for {d}"
            return out
        _, close, vol, _, _ = rows[-1]
        prev = rows[-2][1] if len(rows) > 1 else None
        win = [r for r in rows if r[0] > d - timedelta(days=365)]
        hi = max(r[3] for r in win if r[3] is not None)
        lo = min(r[4] for r in win if r[4] is not None)
        out.update(close=round(close, 4), prev_close=round(prev, 4) if prev else None,
                   change_pct=round((close / prev - 1) * 100, 3) if prev else None,
                   volume=int(vol) if vol is not None else None, high_52w=round(hi, 4), low_52w=round(lo, 4),
                   pos_52w_pct=round((close - lo) / (hi - lo) * 100, 1) if hi > lo else None, bar_date=d.isoformat())
    except Exception as exc:  # noqa: BLE001
        out["error"] = str(exc)[:200]
    return out


# ---------------------------------------------------------------- news
def parse_rss(xml_bytes: bytes) -> list[dict]:
    items = []
    for it in ET.fromstring(xml_bytes).iter("item"):
        title = html.unescape((it.findtext("title") or "").strip())
        link = (it.findtext("link") or "").strip()
        pub = it.findtext("pubDate")
        src_el = it.find("source")
        publisher = src_el.text.strip() if src_el is not None and src_el.text else None
        try:
            published = email.utils.parsedate_to_datetime(pub).astimezone(timezone.utc) if pub else None
        except (TypeError, ValueError):
            published = None
        if publisher and title.endswith(" - " + publisher):
            title = title[: -len(" - " + publisher)]
        if title and link:
            items.append({"title": title, "url": link, "published": published.isoformat() if published else None,
                          "publisher": publisher})
    return items


def analyst_only(title: str, aliases: list[str]) -> bool:
    if "stock:" not in title.lower():
        return False
    head, _, tail = title.partition(":")
    return not any(a.lower() in head.lower() for a in aliases) and any(a.lower() in tail.lower() for a in aliases)


def is_relevant(title: str, ticker: str, aliases: list[str]) -> bool:
    for a in aliases:
        if re.search(r"(?<![A-Za-z])" + re.escape(a) + r"(?![a-z])", title, re.I if len(a) > 3 else 0):
            return True
    t = re.escape(ticker)
    if re.search(r"\$" + t + r"\b|\(" + t + r"\)|:\s?" + t + r"\)", title):
        return True
    return len(ticker) >= 3 and re.search(r"\b" + t + r"\b", title) is not None


def date_only(p: datetime) -> bool:
    loc = p.astimezone(PT)
    return loc.hour == 0 and loc.minute == 0 and loc.second == 0


def effective_time(p: datetime) -> datetime:
    if date_only(p):
        return datetime.combine(p.astimezone(PT).date(), dtime(23, 59, 59), PT).astimezone(timezone.utc)
    return p


def fetch_news(ticker: str, aliases: list[str], d: date, cache: Path | None) -> tuple[list[dict], dict]:
    cutoff = close_dt(d)
    since = cutoff - timedelta(hours=WINDOW_H)
    q = f'"{aliases[0]}" stock after:{(d - timedelta(days=4)).isoformat()} before:{(d + timedelta(days=1)).isoformat()}'
    url = "https://news.google.com/rss/search?" + urllib.parse.urlencode({"q": q, "hl": "en-US", "gl": "US", "ceid": "US:en"})
    st = {"query": q, "raw": 0, "no_date": 0, "after_cutoff": 0, "before_window": 0, "irrelevant_or_spam": 0, "dup": 0, "error": None}
    try:
        items = parse_rss(http_get(url, cache=cache))
    except Exception as exc:  # noqa: BLE001
        st["error"] = str(exc)[:200]
        return [], st
    st["raw"] = len(items)
    seen, kept = set(), []
    for it in items:
        if not it["published"]:
            st["no_date"] += 1
            continue
        p = datetime.fromisoformat(it["published"])
        eff = effective_time(p)
        if eff > cutoff:
            st["after_cutoff"] += 1
            continue
        if eff < since:
            st["before_window"] += 1
            continue
        if analyst_only(it["title"], aliases) or HOLDINGS_SPAM.search(it["title"]) or \
                MARKETBEAT_SPAM.search(it["title"]) or not is_relevant(it["title"], ticker, aliases):
            st["irrelevant_or_spam"] += 1
            continue
        key = re.sub(r"\W+", " ", it["title"].lower()).strip()[:80]
        if key in seen:
            st["dup"] += 1
            continue
        seen.add(key)
        it["date_precision"] = "day" if date_only(p) else "timestamp"
        it["published_date"] = (p.astimezone(PT) if date_only(p) else p.astimezone(NY)).date().isoformat()
        kept.append(it)
    kept.sort(key=lambda x: (x["published"], x["title"]), reverse=True)
    return kept[:10], st


# ---------------------------------------------------------------- SEC filings
def fetch_filings(cik: str, d: date, cache: Path | None) -> tuple[list[dict], str | None]:
    ua = sec_user_agent()
    if not cik or not ua:
        return [], None
    c10 = cik.zfill(10)
    try:
        data = json.loads(http_get(f"https://data.sec.gov/submissions/CIK{c10}.json",
                                   headers={"User-Agent": ua, "Accept": "application/json"}, cache=cache))
    except Exception as exc:  # noqa: BLE001
        return [], str(exc)[:160]
    rec = data.get("filings", {}).get("recent", {})
    lo, hi = (d - timedelta(days=3)).isoformat(), d.isoformat()
    out = []
    for i, form in enumerate(rec.get("form", [])):
        fd = rec["filingDate"][i]
        if form not in SEC_FORMS or not (lo <= fd <= hi):
            continue
        acc = rec["accessionNumber"][i]
        doc = rec.get("primaryDocument", [""] * (i + 1))[i]
        url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc.replace('-', '')}/{doc}" if doc else \
            f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={c10}&type={form}"
        items = (rec.get("items") or [""] * (i + 1))[i] if rec.get("items") else ""
        out.append({"form": form, "filing_date": fd, "url": url, "items": items,
                    "description": (rec.get("primaryDocDescription") or [""] * (i + 1))[i]})
    out.sort(key=lambda x: (x["filing_date"], x["form"]), reverse=True)
    return out[:5], None


# ---------------------------------------------------------------- scoring
_ANALYZER = None


def analyzer():
    global _ANALYZER
    if _ANALYZER is None:
        from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
        a = SentimentIntensityAnalyzer()
        a.lexicon.update(FIN_LEXICON)
        _ANALYZER = a
    return _ANALYZER


def score_items(items: list[dict]) -> tuple[float | None, list[dict]]:
    a = analyzer()
    scored = [{**it, "compound": round(a.polarity_scores(it["title"])["compound"], 3)} for it in items]
    if not scored:
        return None, scored
    raw = sum(i["compound"] for i in scored) / len(scored)
    return round(max(-1.0, min(1.0, raw * min(1.0, len(scored) / 5))), 2), scored


def label(s: float | None) -> str:
    if s is None:
        return "unscored"
    return "bullish" if s >= 0.25 else "bearish" if s <= -0.25 else "neutral"


def fmt(v, kind: str = "") -> str:
    if v is None:
        return "missing"
    return {"pct": f"{v:+.2f}%", "vol": f"{v:,}", "px": f"{v:,.2f}"}.get(kind, str(v)) if kind else str(v)


def detect_events(titles: list[str]) -> list[tuple[str, str]]:
    out, used = [], set()
    for t in titles:
        for name, rx in EVENT_PATTERNS:
            if name not in used and rx.search(t):
                out.append((name, t))
                used.add(name)
                break
    return out


# ---------------------------------------------------------------- render
def render(rec: dict, d: date, gen_at: str, slug: str, gh_user: str, backfill: bool,
           preliminary: bool = False) -> tuple[str, dict]:
    t, px, items, fil = rec["ticker"], rec["price"], rec["scored"], rec["filings"]
    s = rec["lex"] if rec["lex"] is not None else 0.0
    chash = hashlib.sha256(gh_user.lower().encode()).hexdigest()[:12]
    cutoff = close_dt(d).isoformat().replace("+00:00", "Z")
    pos = [i for i in items if i["compound"] > 0.05]
    neg = [i for i in items if i["compound"] < -0.05]
    fm = ["---", f"ticker: {t}", f"date: {d}", f"github_username: {gh_user}", f"contributor_hash: {chash}",
          "daily_role: research", "focus: sentiment", f"sentiment_score: {s:.2f}", f"lexicon_score: {s:.2f}",
          "score_source: lexicon", "llm_tokens: 0", "detail_level: standard", f"harness: {HARNESS}", f"method: {METHOD}",
          f"backfill: {'true' if backfill else 'false'}", f"preliminary: {'true' if preliminary else 'false'}",
          f"as_of: {d}", f"news_cutoff: {cutoff}",
          f"generated_at: {gen_at}", f"collected_at: {gen_at}", "---", ""]
    close_note = "1:00 pm ET early close" if d.isoformat() in EARLY_CLOSE else "4:00 pm ET close"
    lead = (f"Backfilled report generated {gen_at} from sources published at or before the {d} {close_note}. Not collected live."
            if backfill else f"Collected {gen_at} by a deterministic script from sources published at or before the {d} {close_note}.")
    lex = (f"Lexicon score {s:+.2f} over {len(items)} headline(s) from the {WINDOW_H} hours before the close; "
           f"{len(pos)} read positive, {len(neg)} negative, {len(items) - len(pos) - len(neg)} neutral by word list. ")
    if len(items) < 5:
        lex += "Fewer than five headlines, so the score is shrunk toward zero. "
    lex += "No language model was used. Price is reported below and is not a score input."
    if preliminary:
        lex = ("Preliminary report: no dated news found for this ticker in the 72 hours before the close, so the score "
               "is a 0.00 placeholder, not a reading. Price and filings below are real. A later run may update it.")
    body = ["# Sentiment", "", f"Sentiment score: {s:.2f} ({'preliminary, no dated news' if preliminary else label(s) + ', lexicon'})",
            "", clean(lead), "", clean(lex), "",
            "# Key Themes", ""]
    for it in items[:6]:
        body.append(clean(f"- {it['title'][:180]} ({it.get('publisher') or 'news'}, {it['published_date']})"))
    if not items:
        body.append("- No dated news found in the window.")
    pubs = sorted({i.get("publisher") or "news" for i in items})
    src_line = (f"{len(items)} headline(s) from Google News RSS search, date-bounded and filtered to the {d} close"
                + (f" ({', '.join(pubs[:8])})" if pubs else "")
                + f". Price data from Yahoo Finance daily bars dated on or before {d}.")
    if fil:
        src_line += f" {len(fil)} SEC EDGAR filing(s) dated {fil[-1]['filing_date']} to {fil[0]['filing_date']}."
    src_line += f" Links and publish dates in sources.{slug}.json."
    rng = f"{fmt(px.get('low_52w'), 'px')} to {fmt(px.get('high_52w'), 'px')}"
    body += ["", "# Sources", "", clean(src_line), "", "# Price Snapshot", "", "| Field | Value |", "|-------|-------|",
             f"| Session | {d} (US close) |", f"| Close | {fmt(px.get('close'), 'px')} |",
             f"| Prev close | {fmt(px.get('prev_close'), 'px')} |", f"| Change % | {fmt(px.get('change_pct'), 'pct')} |",
             f"| Volume | {fmt(px.get('volume'), 'vol')} |", f"| 52w range | {rng} |",
             f"| 52w position | {fmt(px.get('pos_52w_pct'))}{'%' if px.get('pos_52w_pct') is not None else ''} |",
             "", "# Notable Events", ""]
    ev = [clean(f"- {k}: {ti[:160]}") for k, ti in detect_events([i["title"] for i in items])[:4]]
    ev += [clean(f"- SEC filing: {f['form']} filed {f['filing_date']}" + (f" (items {f['items']})" if f["items"] else ""))
           for f in fil[:3]]
    body += ev or ["- None identified"]
    body.append("")
    srcs = [{"type": "news", "url": i["url"], "title": clean(i["title"][:200]),
             "snippet": f"{i.get('publisher') or 'news'}, {i['published']}", "publisher": i.get("publisher"),
             "published": i["published"], "published_date": i["published_date"], "date_precision": i["date_precision"],
             "lexicon_compound": i["compound"],
             "sentiment": "bullish" if i["compound"] > 0.05 else "bearish" if i["compound"] < -0.05 else "neutral"}
            for i in items]
    srcs += [{"type": "other", "url": f["url"], "title": clean(f"SEC {f['form']} filed {f['filing_date']}"),
              "snippet": clean(f"items {f['items']}" if f["items"] else (f["description"] or f["form"])),
              "published_date": f["filing_date"], "date_precision": "day", "sentiment": "neutral"} for f in fil]
    srcs.append({"type": "other", "url": px["source_url"], "title": f"{t} daily price history (Yahoo Finance)",
                 "snippet": f"close {px['close']} on {px['bar_date']}", "published_date": px["bar_date"],
                 "date_precision": "day", "sentiment": "neutral"})
    sj = {"ticker": t, "date": str(d), "github_username": gh_user, "focus": "sentiment", "backfill": backfill,
          "as_of": str(d), "news_cutoff": cutoff, "generated_at": gen_at, "collected_at": gen_at, "harness": HARNESS,
          "method": METHOD, "score_source": "lexicon", "llm_tokens": 0, "preliminary": preliminary, "news_query": rec["news_stats"]["query"],
          "news_stats": {k: v for k, v in rec["news_stats"].items() if k != "query"}, "sources": srcs}
    md = "\n".join(fm + body)
    assert "\u2014" not in md and "\u2013" not in md
    return md, sj


# ---------------------------------------------------------------- run
MARKET = {"S&P 500": "^GSPC", "Nasdaq Composite": "^IXIC", "Dow Jones": "^DJI", "Russell 2000": "^RUT",
          "VIX": "^VIX", "US 10y yield": "^TNX", "WTI crude": "CL=F", "Gold": "GC=F"}


def run(args) -> dict:
    t0 = time.time()
    d = auto_session() if args.session == "auto" else date.fromisoformat(args.session)
    if d is None:
        log("no completed NYSE session for today (weekend, holiday, or before the close); nothing to do")
        return {"status": "no_session"}
    if not is_trading_day(d):
        log(f"{d} is not an NYSE trading day; nothing to do")
        return {"status": "no_session", "as_of": d.isoformat()}
    cache_root = Path(args.cache) if args.cache else None
    cache = cache_root / "news" / d.isoformat() if cache_root else None
    shared = cache_root / "shared" if cache_root else None
    spx = price_on("^GSPC", d, shared)
    if spx.get("close") is None:
        log(f"no S&P 500 bar for {d} ({spx.get('error')}); treating as no session")
        return {"status": "no_session", "as_of": d.isoformat()}
    uni = load_universe(Path(args.tickers), Path(args.aliases))
    if args.limit:
        uni = uni[: args.limit]
    out_root = Path(args.out)
    todo = [u for u in uni if not (out_root / "data" / d.isoformat() / u["ticker"] / f"report.{args.slug}.md").exists()]
    log(f"session {d}: {len(uni)} tickers, {len(uni) - len(todo)} already present, {len(todo)} to collect")
    gen_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    def work(u):
        items, st = fetch_news(u["ticker"], u["aliases"], d, cache)
        fil, ferr = fetch_filings(u["cik"], d, shared) if args.sec else ([], None)
        lex, scored = score_items(items)
        return {"ticker": u["ticker"], "name": u["name"], "price": price_on(yahoo_symbol(u["ticker"]), d, shared),
                "items": items, "scored": scored, "lex": lex, "news_stats": st, "filings": fil, "sec_error": ferr}

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        recs = list(ex.map(work, todo))
    news_err = sum(1 for r in recs if r["news_stats"]["error"])
    if recs and news_err > max(5, 0.03 * len(recs)):
        # Google is throttling or blocking. Write nothing so a later run redoes the day cleanly.
        summ = {"status": "throttled", "as_of": d.isoformat(), "tickers_requested": len(uni), "news_errors": news_err,
                "http": dict(STATS), "runtime_s": round(time.time() - t0, 1), "llm_tokens": 0,
                "failures": [f"{r['ticker']}: news {r['news_stats']['error'][:80]}" for r in recs if r["news_stats"]["error"]][:20]}
        if args.summary:
            Path(args.summary).parent.mkdir(parents=True, exist_ok=True)
            Path(args.summary).write_text(json.dumps(summ, indent=2) + "\n")
        log(f"{d}: {news_err} of {len(recs)} news fetches failed; treating as throttled, nothing written")
        return summ
    written, failures, thin, prelim = [], [], 0, 0
    for r in recs:
        px = r["price"]
        if px.get("close") is None or px.get("change_pct") is None:
            failures.append(f"{r['ticker']}: price {px.get('error', 'incomplete')}")
            continue
        if r["news_stats"]["error"]:
            failures.append(f"{r['ticker']}: news {r['news_stats']['error'][:80]}")
            continue
        is_prelim = False
        if len(r["items"]) < args.min_items:
            if not args.preliminary:
                thin += 1
                failures.append(f"{r['ticker']}: {len(r['items'])} dated headline(s) in window; skipped")
                continue
            is_prelim = True
            prelim += 1
        md, sj = render(r, d, gen_at, args.slug, args.github_user, args.backfill, preliminary=is_prelim)
        od = out_root / "data" / d.isoformat() / r["ticker"]
        od.mkdir(parents=True, exist_ok=True)
        (od / f"report.{args.slug}.md").write_text(md, encoding="utf-8")
        (od / f"sources.{args.slug}.json").write_text(json.dumps(sj, indent=2) + "\n", encoding="utf-8")
        written.append(r["ticker"])
    mc = {lab: {k: v for k, v in price_on(sym, d, shared).items() if k in ("close", "change_pct")} for lab, sym in MARKET.items()}
    scores = [r["lex"] for r in recs if r["ticker"] in written and r["lex"] is not None]
    summ = {"status": "ok", "as_of": d.isoformat(), "generated_at": gen_at, "harness": HARNESS, "method": METHOD,
            "backfill": args.backfill, "early_close": d.isoformat() in EARLY_CLOSE,
            "tickers_requested": len(uni), "already_present": len(uni) - len(todo), "tickers_written": len(written),
            "skipped_thin": thin, "preliminary": prelim, "headlines_kept": sum(len(r["items"]) for r in recs if r["ticker"] in written),
            "headlines_raw": sum(r["news_stats"]["raw"] for r in recs),
            "news_errors": sum(1 for r in recs if r["news_stats"]["error"]),
            "sec_filings": sum(len(r["filings"]) for r in recs if r["ticker"] in written),
            "sec_errors": sum(1 for r in recs if r["sec_error"]),
            "bullish": sum(1 for s in scores if s >= 0.25), "bearish": sum(1 for s in scores if s <= -0.25),
            "neutral": sum(1 for s in scores if -0.25 < s < 0.25),
            "avg_sentiment": round(sum(scores) / max(1, len(scores)), 3), "market_context": mc,
            "http": dict(STATS), "runtime_s": round(time.time() - t0, 1), "llm_tokens": 0, "failures": failures}
    if args.summary:
        Path(args.summary).parent.mkdir(parents=True, exist_ok=True)
        Path(args.summary).write_text(json.dumps(summ, indent=2) + "\n")
    return summ


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--session", default="auto", help="YYYY-MM-DD or auto")
    ap.add_argument("--out", required=True, help="ledger checkout to write data/DATE/TICKER/ into")
    ap.add_argument("--tickers", default=str(HERE / "sp500.csv"))
    ap.add_argument("--aliases", default=str(HERE / "aliases.json"))
    ap.add_argument("--github-user", default="rahiakil")
    ap.add_argument("--slug", default="rahiakil-grokbot")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--min-items", type=int, default=1)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--cache", default=os.environ.get("NIGHTLY_CACHE", ""))
    ap.add_argument("--no-sec", dest="sec", action="store_false")
    ap.add_argument("--preliminary", action="store_true",
                    help="write a preliminary report (price, filings, no news) when a ticker has no dated headline")
    ap.add_argument("--backfill", action="store_true", help="mark reports backfill: true (run after the session night)")
    ap.add_argument("--summary", help="write the run summary JSON here")
    return ap.parse_args(argv)


def main(argv=None) -> int:
    s = run(parse_args(argv))
    print(json.dumps({k: v for k, v in s.items() if k != "failures"}), flush=True)
    for f in s.get("failures", [])[:40]:
        print("  " + f)
    return 0


if __name__ == "__main__":
    sys.exit(main())
