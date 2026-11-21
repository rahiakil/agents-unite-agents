"""Render a ledger report and its sources file from a dossier."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from harness.ledger import FOCUS_FILES, branch_name, prompt_hash, simulated_block_reason
from harness.personas import Persona
from harness.providers import Completion, parse_model_json
from harness.research import Dossier, Headline
from harness.scoring import score_dossier
from harness.textutil import clean_text, format_score, numbers_in

DISCLOSURE = (
    "This row is an AI-agent persona operated by the agents-unite-agents harness. "
    "The public ledger report is written in that persona's voice and does not set "
    "simulated:true, because the ledger reserves that flag for the study partition "
    "and blocks it on main."
)


def _pick(items: tuple[str, ...] | list[str], seed: str) -> str:
    if not items:
        return ""
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    return items[int(digest[:8], 16) % len(items)]


def _fmt(value: float | None, digits: int = 2) -> str:
    if value is None:
        return "N/A"
    return f"{value:.{digits}f}"


def _move(change: float | None) -> str:
    if change is None:
        return "no daily change in the fetch"
    if change > 0.05:
        return f"up {_fmt(change)}%"
    if change < -0.05:
        return f"down {_fmt(abs(change))}%"
    return f"flat ({_fmt(change)}%)"


def _volume_ratio(dossier: Dossier) -> float | None:
    tape = dossier.tape
    if tape is None or not tape.volume or not tape.avg_volume_20:
        return None
    return tape.volume / tape.avg_volume_20


def _source_rows(dossier: Dossier) -> list[dict]:
    rows: list[dict] = []
    seen: set[str] = set()

    def add(item: Headline) -> None:
        if item.url in seen or not item.url.startswith("http"):
            return
        kind = item.source_type if item.source_type in {"twitter", "reddit", "news", "other"} else "other"
        seen.add(item.url)
        rows.append(
            {
                "type": kind,
                "url": item.url,
                "title": clean_text(item.title)[:240] or item.url,
                "snippet": clean_text(item.summary)[:400] or clean_text(item.title)[:400],
            }
        )

    tape = dossier.tape
    if tape is not None:
        change = _fmt(tape.change_pct)
        close = _fmt(tape.last_close)
        add(
            Headline(
                title=f"{dossier.ticker} daily price history",
                url=tape.history_url,
                publisher="Yahoo Finance",
                published_at="",
                source_type="other",
                summary=f"Last close {close} as of {tape.as_of or 'the latest bar'}, daily change {change}%.",
            )
        )
    for item in dossier.headlines + dossier.social + dossier.filings:
        add(item)
    return rows


def _fact_lines(persona: Persona, dossier: Dossier) -> list[str]:
    tape = dossier.tape
    lines: list[str] = []
    if tape is None:
        lines.append("The daily bar fetch did not return a close, so the price table is N/A.")
    else:
        ratio = _volume_ratio(dossier)
        ratio_text = "n/a" if ratio is None else f"{ratio:.2f}x the 20-day average"
        lines.append(
            f"{dossier.name} ({dossier.ticker}) last closed at {_fmt(tape.last_close)} "
            f"on {tape.as_of or 'the latest session'}, {_move(tape.change_pct)}. "
            f"Volume was {ratio_text}."
        )
        lines.append(
            f"The 20-day return is {_fmt(tape.return_20d_pct)}% and 20-day daily volatility "
            f"is {_fmt(tape.vol_20d, 4)}. Position in the fetched range is {_fmt(tape.position_52w)}."
        )
        if tape.spy_corr_20d is not None and "spy" in dossier.searched:
            lines.append(
                f"Correlation of daily returns with SPY over the overlapping window is {_fmt(tape.spy_corr_20d)}."
            )
    if dossier.headlines:
        top = dossier.headlines[0]
        lines.append(f"Lead headline: {clean_text(top.title)} ({clean_text(top.publisher)}).")
    elif "news" in dossier.searched:
        lines.append("The news feeds returned no headline for this pass.")
    if "reddit" in dossier.searched or "stocktwits" in dossier.searched:
        lines.append(
            f"Social fetch counts: Reddit {sum(1 for item in dossier.social if item.source_type == 'reddit')}, "
            f"Stocktwits {sum(1 for item in dossier.social if item.publisher == 'Stocktwits')}."
        )
    if "sec" in dossier.searched:
        if dossier.filings:
            lines.append(f"SEC atom feed returned: {clean_text(dossier.filings[0].title)}.")
        else:
            lines.append("The SEC 8-K atom feed returned no filing in this pass.")
    if persona.score_style == "fade_return":
        lines.append("A one-day move without a matching filing is a reason for me to lean against it.")
    elif persona.score_style == "follow_return":
        lines.append("I let the session return carry most of the score.")
    elif persona.score_style == "vol_aware":
        lines.append("I cut the score when recent daily volatility is elevated.")
    elif persona.score_style == "range_proxy":
        lines.append("No listed options feed is in this pass, so the day's range stands in for an IV hint.")
    elif persona.score_style == "headline":
        lines.append("I weight the headlines and posts above the one-day print.")
    else:
        lines.append("I keep the score small unless the headline and the tape agree.")
    return lines


def _themes(persona: Persona, dossier: Dossier, voice_line: str) -> list[str]:
    tape = dossier.tape
    themes = []
    if tape is not None and tape.change_pct is not None:
        themes.append(
            f"{dossier.ticker} is {_move(tape.change_pct)} with a 20-day return of {_fmt(tape.return_20d_pct)}%."
        )
    else:
        themes.append(f"{dossier.ticker} had no daily bar in this fetch.")
    if dossier.headlines:
        themes.append(f"Headline in the fetch: {clean_text(dossier.headlines[0].title)}.")
    else:
        themes.append("No headline was attached to this pass.")
    if dossier.sector:
        themes.append(f"Sector label from the ledger universe: {dossier.sector}.")
    if voice_line:
        themes.append(clean_text(voice_line))
    # Keep at least three concrete bullets.
    while len(themes) < 3:
        themes.append(f"Search list for this pass: {', '.join(dossier.searched)}.")
    return themes[:5]


def _events(dossier: Dossier) -> list[str]:
    events = []
    for item in (dossier.filings + dossier.headlines)[:4]:
        events.append(f"{clean_text(item.publisher)}: {clean_text(item.title)}")
    return events or ["None identified"]


def _fill(template: str, fields: dict[str, str]) -> str:
    class _Safe(dict):
        def __missing__(self, key: str) -> str:
            return ""

    try:
        return template.format_map(_Safe(fields))
    except (IndexError, ValueError):
        return template


def _fields(persona: Persona, dossier: Dossier, score_text: str) -> dict[str, str]:
    tape = dossier.tape
    headline = clean_text(dossier.headlines[0].title) if dossier.headlines else "no headline"
    return {
        "ticker": dossier.ticker,
        "name": dossier.name,
        "sector": dossier.sector or "unspecified",
        "move": _move(tape.change_pct if tape else None),
        "score": score_text,
        "headline": headline,
        "vol": _fmt(tape.vol_20d if tape else None, 4),
        "r20": _fmt(tape.return_20d_pct if tape else None),
    }


def _grounded(text: str, allowed: set[str]) -> bool:
    for number in numbers_in(text):
        if number in allowed or number.lstrip("-") in allowed:
            continue
        bare = number.split(".")[0]
        if bare in allowed or f"{bare}.0" in allowed:
            continue
        return False
    return True


def _allowed_numbers(dossier: Dossier, score_text: str, session: str) -> set[str]:
    allowed = {score_text, session, *session.split("-")}
    tape = dossier.tape
    if tape is not None:
        for value in (
            tape.last_close,
            tape.prev_close,
            tape.change_pct,
            tape.volume,
            tape.avg_volume_20,
            tape.return_20d_pct,
            tape.vol_20d,
            tape.position_52w,
            tape.spy_corr_20d,
            _volume_ratio(dossier),
        ):
            if value is None:
                continue
            allowed.add(f"{value:.0f}")
            allowed.add(f"{value:.2f}")
            allowed.add(f"{value:.4f}")
            allowed.add(str(int(value)) if float(value).is_integer() else f"{value:.2f}")
            if tape.as_of:
                allowed.update(tape.as_of.split("-"))
    for item in dossier.headlines + dossier.social + dossier.filings:
        allowed.update(numbers_in(item.title))
        allowed.update(numbers_in(item.summary))
    allowed.update(numbers_in(" ".join(str(part) for part in allowed)))
    return {item.lstrip("+") for item in allowed if item}


def model_reply_reason(completion: Completion | None, dossier: Dossier, session: str) -> tuple[float | None, str, str]:
    """Return (score, read, "") when the reply can be used, else (None, "", reason)."""
    if completion is None or completion.provider == "rule":
        return None, "", ""
    if completion.error or not completion.text.strip():
        return None, "", completion.error or "empty model reply"
    payload = parse_model_json(completion.text)
    model_score = None
    if payload and "sentiment_score" in payload:
        try:
            model_score = float(payload["sentiment_score"])
        except (TypeError, ValueError):
            model_score = None
    candidate = clean_text(str((payload or {}).get("read") or ""))
    if model_score is None or not (-1.0 <= model_score <= 1.0) or not candidate:
        return None, "", "model reply was not usable JSON"
    if not _grounded(candidate, _allowed_numbers(dossier, format_score(model_score), session)):
        return None, "", "model prose cited numbers that were not in the fetch"
    return model_score, candidate, ""


def render_report(
    persona: Persona,
    dossier: Dossier,
    *,
    session: str,
    slot_id: str,
    focus: str,
    ledger_root: Path,
    completion: Completion | None = None,
) -> dict:
    raw_score, score_detail = score_dossier(persona.score_style, dossier)
    writer = "rule"
    fell_back = False
    model_score, model_read, llm_note = model_reply_reason(completion, dossier, session)
    if model_score is not None and completion is not None:
        raw_score = model_score
        writer = completion.provider
        llm_note = ""
    elif completion is not None and completion.provider != "rule":
        fell_back = True
        model_read = ""
    score_text = format_score(raw_score)
    fields = _fields(persona, dossier, score_text)
    seed = f"{persona.id}|{session}|{slot_id}|{dossier.ticker}"
    opener = clean_text(_fill(_pick(persona.voice.openers, seed + "|opener"), fields))
    read = clean_text(_fill(_pick(persona.voice.reads, seed + "|read"), fields))
    closer = clean_text(_fill(_pick(persona.voice.closers, seed + "|closer"), fields))
    facts = " ".join(_fact_lines(persona, dossier))
    themes = _themes(persona, dossier, read)
    events = _events(dossier)
    relative, digest = prompt_hash(ledger_root, focus)
    if focus not in FOCUS_FILES:
        focus = "full"
    rows = _source_rows(dossier)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    kind_counts: dict[str, int] = {}
    for row in rows:
        kind_counts[row["type"]] = kind_counts.get(row["type"], 0) + 1
    mix = ", ".join(f"{count} {kind}" for kind, count in sorted(kind_counts.items())) or "none"
    sentiment = "\n\n".join(
        part
        for part in (
            opener,
            facts,
            model_read,
            closer,
            f"Sentiment score: {score_text}",
        )
        if part
    )
    theme_block = "\n".join(f"- {clean_text(item)}" for item in themes)
    event_block = "\n".join(f"- {clean_text(item)}" for item in events)
    ratio = _volume_ratio(dossier)
    tape = dossier.tape
    report = f"""---
ticker: {dossier.ticker}
date: {session}
github_username: {persona.github_username}
contributor_hash: {persona.hash12}
daily_role: research
focus: {focus}
prompt_hash: {digest}
prompt_file: {relative}
sentiment_score: {score_text}
confidence: {_fmt(min(0.8, 0.35 + 0.08 * len(rows)))}
detail_level: standard
---

# Sentiment

{sentiment}

# Key Themes

{theme_block}

# Sources

This pass stored {len(rows)} sources ({mix}). {persona.pr_line}

# Price Snapshot

| Field | Value |
| --- | --- |
| Price | {_fmt(tape.last_close if tape else None)} |
| Change % | {_fmt(tape.change_pct if tape else None)} |
| Volume vs 20d | {_fmt(ratio) if ratio is not None else "N/A"} |
| 20d return % | {_fmt(tape.return_20d_pct if tape else None)} |
| 20d daily vol | {_fmt(tape.vol_20d if tape else None, 4)} |
| 52w position | {_fmt(tape.position_52w if tape else None)} |

# Notable Events

{event_block}
"""
    report = clean_text_block(report)
    blocked = simulated_block_reason(persona.github_username, report)
    sources = {
        "ticker": dossier.ticker,
        "date": session,
        "github_username": persona.github_username,
        "focus": focus,
        "collected_at": now,
        "sources": rows,
    }
    slug = persona.github_username
    report_name = f"report.{slug}.md"
    sources_name = f"sources.{slug}.json"
    rel_dir = f"data/{session}/{dossier.ticker}"
    return {
        "report": report,
        "sources": sources,
        "sources_text": json.dumps(sources, indent=2) + "\n",
        "report_name": report_name,
        "sources_name": sources_name,
        "rel_dir": rel_dir,
        "rel_report": f"{rel_dir}/{report_name}",
        "rel_sources": f"{rel_dir}/{sources_name}",
        "branch": branch_name(session, dossier.ticker, persona.hash8),
        "score": score_text,
        "score_detail": score_detail,
        "writer": writer,
        "fell_back": fell_back,
        "llm_note": llm_note,
        "prompt_file": relative,
        "prompt_hash": digest,
        "focus": focus,
        "blocked": blocked,
        "source_rows": rows,
    }


def clean_text_block(text: str) -> str:
    """Drop dash characters without collapsing markdown newlines."""
    return (
        text.replace("\u2014", ", ")
        .replace("\u2013", "-")
        .replace("\u2018", "'")
        .replace("\u2019", "'")
        .replace("\u201c", '"')
        .replace("\u201d", '"')
    )


def write_pair(root: Path, rendered: dict) -> Path:
    folder = root / rendered["rel_dir"]
    folder.mkdir(parents=True, exist_ok=True)
    report_path = folder / rendered["report_name"]
    sources_path = folder / rendered["sources_name"]
    report_path.write_text(rendered["report"], encoding="utf-8")
    sources_path.write_text(rendered["sources_text"], encoding="utf-8")
    return report_path
