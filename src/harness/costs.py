"""Per-run cost and token burn-down log for every Agents Unite collector path.

Each run writes one small JSON file under data/runs/cost/ (one file per run, so
concurrent jobs never conflict on rebase). The combined views are rebuilt from
those files every time:

  data/runs/cost_log.jsonl   one row per (run, provider, model)
  data/runs/cost_log.csv     the same rows as CSV
  data/runs/COSTS.md         totals by day and by harness

est_cost_usd is what the run actually cost: 0 for the lexicon script, the
no-key writer and Groq's free tier. list_price_usd is the same tokens priced at
the provider's published pay-as-you-go rate, so the free tier still shows what
it would cost. Token counts come from the provider response when it returns
them. Otherwise they are estimated at 4 characters per token and the row says
tokens_source: estimated.

CLI (used by workflows and by the Grok Bot routine on the box):
  python -m harness.costs log --harness grokbot-routine --tickers-requested 100 \
      --tickers-written 100 --provider grokbot --model grokbot --total-tokens 250000 \
      --est-cost-usd 1.20 --runtime-s 900
  python -m harness.costs rebuild
  python -m harness.costs html site/index.html   (the GitHub Pages page)
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNS_DIR = ROOT / "data" / "runs"

# USD per 1M tokens (input, output) at the providers' last known published
# pay-as-you-go rates (Groq, xAI, OpenAI pricing pages). Edit here when they change.
# qwen/qwen3.8-27b has no published Groq price yet; the qwen3-32b rate is used.
PRICES = {
    "openai/gpt-oss-20b": (0.075, 0.30),
    "openai/gpt-oss-120b": (0.15, 0.60),
    "qwen/qwen3.8-27b": (0.29, 0.59),
    "qwen/qwen3-32b": (0.29, 0.59),
    "llama-3.1-8b-instant": (0.05, 0.08),
    "llama-3.3-70b-versatile": (0.59, 0.79),
    "grok-3-mini": (0.30, 0.50),
    "grok-4-fast": (0.20, 0.50),
    "gpt-4o-mini": (0.15, 0.60),
}
# Providers billed at zero for this project (free tier or no model at all).
FREE_PROVIDERS = {"none", "lexicon", "rule", "groq"}

COLUMNS = [
    "run_id", "date", "timestamp", "harness", "tickers_requested", "tickers_written",
    "llm_provider", "model", "prompt_tokens", "completion_tokens", "total_tokens",
    "tokens_source", "llm_calls", "est_cost_usd", "list_price_usd", "runtime_s",
    "tokens_per_stock", "cost_per_stock", "cost_per_50_stocks", "list_cost_per_50_stocks",
    "dry_run", "notes",
]


def price_usd(model: str, prompt_tokens: int, completion_tokens: int) -> float | None:
    rate = PRICES.get(model)
    if rate is None:
        return None
    return round(prompt_tokens / 1e6 * rate[0] + completion_tokens / 1e6 * rate[1], 6)


def make_row(*, run_id: str, harness: str, date: str, tickers_requested: int, tickers_written: int,
             llm_provider: str = "none", model: str = "none", prompt_tokens: int = 0, completion_tokens: int = 0,
             total_tokens: int | None = None, tokens_source: str = "none", llm_calls: int = 0,
             est_cost_usd: float | None = None, runtime_s: float = 0.0, dry_run: bool = False,
             notes: str = "", timestamp: str | None = None) -> dict:
    total = int(total_tokens if total_tokens is not None else prompt_tokens + completion_tokens)
    listed = price_usd(model, prompt_tokens, completion_tokens)
    if listed is None and total and not (prompt_tokens or completion_tokens):
        rate = PRICES.get(model)
        listed = round(total / 1e6 * rate[1], 6) if rate else None
    if est_cost_usd is None:
        est_cost_usd = 0.0 if llm_provider in FREE_PROVIDERS else listed
    per = max(1, tickers_written)
    cost = est_cost_usd
    return {
        "run_id": run_id, "date": date,
        "timestamp": timestamp or datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "harness": harness, "tickers_requested": int(tickers_requested), "tickers_written": int(tickers_written),
        "llm_provider": llm_provider, "model": model, "prompt_tokens": int(prompt_tokens),
        "completion_tokens": int(completion_tokens), "total_tokens": total, "tokens_source": tokens_source,
        "llm_calls": int(llm_calls), "est_cost_usd": None if cost is None else round(cost, 6),
        "list_price_usd": listed, "runtime_s": round(float(runtime_s), 1),
        "tokens_per_stock": round(total / per, 1),
        "cost_per_stock": None if cost is None else round(cost / per, 6),
        "cost_per_50_stocks": None if cost is None else round(cost / per * 50, 4),
        "list_cost_per_50_stocks": None if listed is None else round(listed / per * 50, 4),
        "dry_run": bool(dry_run), "notes": notes,
    }


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name)[:120]


def write_run(rows: list[dict], runs_dir: Path = RUNS_DIR) -> Path:
    """Write one run's rows to data/runs/cost/<run_id>.json and rebuild the views."""
    if not rows:
        raise ValueError("no rows")
    d = runs_dir / "cost"
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{_safe(rows[0]['run_id'])}.json"
    path.write_text(json.dumps({"rows": rows}, indent=2) + "\n", encoding="utf-8")
    rebuild(runs_dir)
    return path


def load_rows(runs_dir: Path = RUNS_DIR) -> list[dict]:
    rows = []
    for f in sorted((runs_dir / "cost").glob("*.json")):
        try:
            rows.extend(json.loads(f.read_text(encoding="utf-8"))["rows"])
        except (ValueError, KeyError):
            continue
    rows.sort(key=lambda r: (r.get("timestamp", ""), r.get("run_id", ""), r.get("model", "")))
    return rows


def _money(v: float | None) -> str:
    return "n/a" if v is None else f"${v:,.4f}"


def rebuild(runs_dir: Path = RUNS_DIR) -> None:
    rows = load_rows(runs_dir)
    runs_dir.mkdir(parents=True, exist_ok=True)
    with (runs_dir / "cost_log.jsonl").open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps({k: r.get(k) for k in COLUMNS}) + "\n")
    with (runs_dir / "cost_log.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k) for k in COLUMNS})
    (runs_dir / "COSTS.md").write_text(summary_md(rows), encoding="utf-8")


def _agg(rows: list[dict], key) -> list[tuple[str, dict]]:
    out: dict[str, dict] = defaultdict(lambda: {"runs": set(), "written": 0, "tokens": 0, "cost": 0.0, "list": 0.0,
                                                "unpriced": 0, "runtime": 0.0})
    for r in rows:
        a = out[key(r)]
        a["runs"].add(r["run_id"])
        a["written"] += r.get("tickers_written") or 0
        a["tokens"] += r.get("total_tokens") or 0
        a["runtime"] += r.get("runtime_s") or 0.0
        if r.get("est_cost_usd") is None:
            a["unpriced"] += 1
        else:
            a["cost"] += r["est_cost_usd"]
        a["list"] += r.get("list_price_usd") or 0.0
    return sorted(out.items())


def summary_md(rows: list[dict]) -> str:
    live = [r for r in rows if not r.get("dry_run")]
    tot_cost = sum(r.get("est_cost_usd") or 0 for r in live)
    tot_tok = sum(r.get("total_tokens") or 0 for r in live)
    tot_w = sum(r.get("tickers_written") or 0 for r in live)
    lines = [
        "# Cost and token burn-down", "",
        "Rebuilt by `python -m harness.costs rebuild` after every collector run. Raw rows: "
        "[cost_log.csv](cost_log.csv) and [cost_log.jsonl](cost_log.jsonl). Dry runs are excluded below.", "",
        "`est cost` is what was actually spent (0 for the lexicon script and free tiers). "
        "`list price` prices the same tokens at the provider's published rate. "
        "Rows with `tokens_source: estimated` used 4 characters per token.", "",
        f"All time: {len({r['run_id'] for r in live})} runs, {tot_w:,} stock reports written, "
        f"{_fmt_runtime(sum(r.get('runtime_s') or 0 for r in live))} recorded runtime, "
        f"{tot_tok:,} recorded LLM tokens, est cost {_money(tot_cost)}, "
        f"{_money(tot_cost / max(1, tot_w) * 50)} per 50 stocks. "
        "A zero token or dollar total is expected for the lexicon script and free tiers; "
        "it does not mean the collector did nothing.", "",
        "## By harness", "",
        "| Harness | Runs | Stocks written | Tokens | Tokens/stock | Est cost | List price | Cost/50 stocks | Avg runtime s |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for k, a in _agg(live, lambda r: r["harness"]):
        n = max(1, a["written"])
        lines.append(f"| {k} | {len(a['runs'])} | {a['written']:,} | {a['tokens']:,} | {a['tokens'] / n:,.0f} | "
                     f"{_money(a['cost'])}{' +unpriced' if a['unpriced'] else ''} | {_money(a['list'])} | "
                     f"{_money(a['cost'] / n * 50)} | {a['runtime'] / max(1, len(a['runs'])):,.0f} |")
    lines += ["", "## By day and harness", "",
              "| Day | Harness | Runs | Stocks written | Tokens | Est cost | List price | Cost/50 stocks |",
              "|---|---|---|---|---|---|---|---|"]
    for k, a in reversed(_agg(live, lambda r: f"{r['date']}|{r['harness']}")):
        day, h = k.split("|", 1)
        n = max(1, a["written"])
        lines.append(f"| {day} | {h} | {len(a['runs'])} | {a['written']:,} | {a['tokens']:,} | "
                     f"{_money(a['cost'])}{' +unpriced' if a['unpriced'] else ''} | {_money(a['list'])} | "
                     f"{_money(a['cost'] / n * 50)} |")
    lines += ["", "## By model", "", "| Provider | Model | Calls | Tokens | Est cost | List price |", "|---|---|---|---|---|---|"]
    for k, a in _agg(live, lambda r: f"{r['llm_provider']}|{r['model']}"):
        p, m = k.split("|", 1)
        calls = sum(r.get("llm_calls") or 0 for r in live if r["llm_provider"] == p and r["model"] == m)
        lines.append(f"| {p} | {m} | {calls:,} | {a['tokens']:,} | {_money(a['cost'])} | {_money(a['list'])} |")
    lines.append("")
    return "\n".join(lines)


def _esc(v) -> str:
    return str(v).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _fmt_runtime(seconds: float) -> str:
    seconds = float(seconds or 0)
    if seconds <= 0:
        return "n/a"
    if seconds < 60:
        return f"{seconds:.0f}s"
    minutes, rem = divmod(int(round(seconds)), 60)
    if minutes < 60:
        return f"{minutes}m {rem}s" if rem else f"{minutes}m"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m"


def summary_html(rows: list[dict]) -> str:
    """Mobile-friendly static page. Leads with activity (runs, stocks, runtime)
    so a zero-token or free-tier day does not look like nothing happened."""
    live = [r for r in rows if not r.get("dry_run")]
    tot_cost = sum(r.get("est_cost_usd") or 0 for r in live)
    tot_list = sum(r.get("list_price_usd") or 0 for r in live)
    tot_tok = sum(r.get("total_tokens") or 0 for r in live)
    tot_w = sum(r.get("tickers_written") or 0 for r in live)
    tot_req = sum(r.get("tickers_requested") or 0 for r in live)
    tot_run = sum(r.get("runtime_s") or 0 for r in live)
    n_runs = len({r["run_id"] for r in live})
    unrecorded = sum(1 for r in live if (r.get("tokens_source") or "") in {"unrecorded", "none"} and (r.get("llm_provider") or "none") not in {"none", "lexicon", "rule"})
    lexicon_or_free = sum(1 for r in live if (r.get("llm_provider") or "") in {"none", "lexicon", "rule", "groq"})
    harnesses = sorted({r["harness"] for r in live})
    palette = ["#2563eb", "#16a34a", "#d97706", "#9333ea", "#dc2626", "#0891b2"]
    color = {h: palette[i % len(palette)] for i, h in enumerate(harnesses)}
    days = sorted({r["date"] for r in live})[-30:]
    per = {(d, h): 0 for d in days for h in harnesses}
    day_tokens = {d: 0 for d in days}
    for r in live:
        if r["date"] in day_tokens:
            per[(r["date"], r["harness"])] += r.get("tickers_written") or 0
            day_tokens[r["date"]] += r.get("total_tokens") or 0
    top = max([sum(per[(d, h)] for h in harnesses) for d in days] + [1])
    w, h_px, bw = 340, 150, max(4, int(320 / max(1, len(days))) - 2)
    bars = []
    for i, d in enumerate(days):
        x, y = 10 + i * (bw + 2), h_px
        day_total = sum(per[(d, hn)] for hn in harnesses)
        for hn in harnesses:
            v = per[(d, hn)]
            if not v:
                continue
            bh = max(1, int(v / top * (h_px - 20)))
            y -= bh
            bars.append(
                f'<rect x="{x}" y="{y}" width="{bw}" height="{bh}" fill="{color[hn]}">'
                f"<title>{d} {hn}: {v:,} reports, {day_tokens[d]:,} tokens</title></rect>"
            )
        if day_total == 0:
            bars.append(
                f'<rect x="{x}" y="{h_px - 2}" width="{bw}" height="2" fill="#94a3b8">'
                f"<title>{d}: no reports</title></rect>"
            )
    legend = " ".join(
        f'<span class="lg"><i style="background:{color[hn]}"></i>{_esc(hn)}</span>' for hn in harnesses
    )

    def table(head, body_rows):
        th = "".join(f"<th>{_esc(c)}</th>" for c in head)
        trs = "".join("<tr>" + "".join(f"<td>{_esc(c)}</td>" for c in r) + "</tr>" for r in body_rows)
        return f'<div class="tw"><table><thead><tr>{th}</tr></thead><tbody>{trs}</tbody></table></div>'

    by_h = []
    for k, a in _agg(live, lambda r: r["harness"]):
        n = max(1, a["written"])
        by_h.append([
            k, len(a["runs"]), f"{a['written']:,}", _fmt_runtime(a["runtime"]),
            f"{a['tokens']:,}", f"{a['tokens'] / n:,.0f}",
            _money(a["cost"]) + (" +unpriced" if a["unpriced"] else ""),
            _money(a["list"]), _money(a["cost"] / n * 50), _money(a["list"] / n * 50),
        ])
    by_d = []
    for k, a in reversed(_agg(live, lambda r: f"{r['date']}|{r['harness']}")):
        d, hn = k.split("|", 1)
        n = max(1, a["written"])
        by_d.append([
            d, hn, len(a["runs"]), f"{a['written']:,}", _fmt_runtime(a["runtime"]),
            f"{a['tokens']:,}",
            _money(a["cost"]) + (" +unpriced" if a["unpriced"] else ""),
            _money(a["cost"] / n * 50), _money(a["list"] / n * 50),
        ])
    recent = [[
        (r["timestamp"][:16]).replace("T", " "), r["harness"], r["model"],
        f"{r.get('tickers_written') or 0}/{r.get('tickers_requested') or 0}",
        f"{r.get('total_tokens') or 0:,}", r.get("tokens_source") or "none",
        _money(r.get("est_cost_usd")), _fmt_runtime(r.get("runtime_s") or 0),
    ] for r in reversed(live[-25:])]
    updated = max((r["timestamp"] for r in rows), default="never")
    zero_note = (
        "Zero dollars and zero tokens here usually mean a run used the lexicon script, "
        "the no-key writer, or Groq's free tier, not that the collector failed. "
        "Stock counts and runtimes are the activity signal. "
        "Rows marked unrecorded had no token counts saved at the time; those numbers were not estimated."
        if tot_tok == 0 or lexicon_or_free
        else "Spent is what was billed. List price prices the same tokens at the provider's published rate."
    )
    if unrecorded:
        zero_note += f" {unrecorded} paid-looking row(s) have unrecorded token counts."
    token_label = "0 recorded" if tot_tok == 0 else f"{tot_tok:,}"
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Agents Unite cost burn-down</title>
<style>
body{{font:15px/1.45 -apple-system,system-ui,Segoe UI,Roboto,sans-serif;margin:0;padding:16px;color:#0f172a;background:#f8fafc;max-width:900px;margin:auto}}
h1{{font-size:20px;margin:4px 0 2px}}h2{{font-size:16px;margin:22px 0 8px}}.sub{{color:#64748b;font-size:13px}}
.note{{background:#fffbeb;border:1px solid #fde68a;border-radius:10px;padding:10px 12px;margin:12px 0;font-size:13px}}
.cards{{display:grid;grid-template-columns:repeat(2,1fr);gap:8px;margin:14px 0}}@media(min-width:640px){{.cards{{grid-template-columns:repeat(3,1fr)}}}}
.card{{background:#fff;border:1px solid #e2e8f0;border-radius:10px;padding:10px}}.card b{{display:block;font-size:19px}}.card span{{color:#64748b;font-size:12px}}
.tw{{overflow-x:auto;background:#fff;border:1px solid #e2e8f0;border-radius:10px}}table{{border-collapse:collapse;width:100%;font-size:13px}}
th,td{{padding:6px 8px;border-bottom:1px solid #eef2f7;text-align:left;white-space:nowrap}}th{{background:#f1f5f9;font-weight:600}}
svg{{width:100%;height:auto;background:#fff;border:1px solid #e2e8f0;border-radius:10px}}.lg{{font-size:12px;margin-right:10px}}.lg i{{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:4px}}
a{{color:#2563eb}}
</style></head><body>
<h1>Agents Unite collector activity and cost</h1>
<div class="sub">Updated {_esc(updated)} UTC. Every collector run adds a row. Dry runs excluded.</div>
<div class="cards">
<div class="card"><b>{n_runs}</b><span>collector runs</span></div>
<div class="card"><b>{tot_w:,}</b><span>stock reports written</span></div>
<div class="card"><b>{tot_req:,}</b><span>tickers requested</span></div>
<div class="card"><b>{_esc(_fmt_runtime(tot_run))}</b><span>total recorded runtime</span></div>
<div class="card"><b>{_esc(_money(tot_cost))}</b><span>spent, all time ({_esc(_money(tot_cost / max(1, tot_w) * 50))} per 50 stocks)</span></div>
<div class="card"><b>{_esc(token_label)}</b><span>LLM tokens ({_esc(_money(tot_list))} at list price; {_esc(_money(tot_list / max(1, tot_w) * 50))} per 50 stocks)</span></div>
</div>
<div class="note">{_esc(zero_note)}</div>
<h2>Stock reports written per day (last 30 days)</h2>
<svg viewBox="0 0 {w} {h_px + 4}" role="img" aria-label="stock reports written per day">{''.join(bars)}</svg>
<div>{legend}</div>
<h2>By harness</h2>
{table(["Harness", "Runs", "Stocks", "Runtime", "Tokens", "Tokens/stock", "Spent", "List price", "Spent/50", "List/50"], by_h)}
<h2>By day</h2>
{table(["Day", "Harness", "Runs", "Stocks", "Runtime", "Tokens", "Spent", "Spent/50", "List/50"], by_d)}
<h2>Latest runs</h2>
{table(["When (UTC)", "Harness", "Model", "Written/requested", "Tokens", "Token count", "Spent", "Runtime"], recent)}
<p class="sub">Spent is 0 for the lexicon script and free tiers. List price prices the same tokens at the provider's published rate when a token count was recorded.
Raw data: <a href="https://github.com/rahiakil/agents-unite-agents/blob/main/data/runs/cost_log.csv">cost_log.csv</a>,
<a href="https://github.com/rahiakil/agents-unite-agents/blob/main/data/runs/COSTS.md">COSTS.md</a>.</p>
</body></html>
"""


def import_persona_attempts(path: Path, runs_dir: Path = RUNS_DIR, *, write: bool = True) -> list[dict]:
    """Rebuild persona-wake cost rows from data/attempts.jsonl.

    Token fields are used only when the attempt recorded them. Older attempts
    have no token counts; those rows are labeled unrecorded and left at 0.
    Numbers are never invented.
    """
    from collections import defaultdict

    attempts = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        if rec.get("record_type", "attempt") == "attempt":
            attempts.append(rec)
    # One reconstructed run per session date, split by provider and model.
    groups: dict[tuple[str, str, str], list] = defaultdict(list)
    for rec in attempts:
        day = rec.get("session_date") or (rec.get("timestamp") or "")[:10]
        if not day:
            continue
        groups[(day, rec.get("provider") or "rule", rec.get("model") or "rule")].append(rec)
    existing = {r["run_id"] for r in load_rows(runs_dir)} if (runs_dir / "cost").exists() else set()
    written = []
    for (day, prov, model), recs in sorted(groups.items()):
        run_id = f"persona-wake-reconstructed-{day}-{_safe(prov)}-{_safe(model)}"
        if run_id in existing:
            continue
        sources = {r.get("tokens_source") or "" for r in recs}
        has_tokens = any((r.get("prompt_tokens") is not None) or (r.get("completion_tokens") is not None) for r in recs)
        if has_tokens:
            pt = sum(int(r.get("prompt_tokens") or 0) for r in recs)
            ct = sum(int(r.get("completion_tokens") or 0) for r in recs)
            src = "mixed" if len(sources - {"", "none"}) > 1 else next(iter(sources - {"", "none"}), "provider")
            notes = f"Rebuilt from attempts.jsonl ({len(recs)} attempts). Token counts as recorded."
        else:
            pt = ct = 0
            src = "unrecorded"
            notes = (f"Rebuilt from attempts.jsonl ({len(recs)} attempts). "
                     "Token counts were not saved on these attempts and were not estimated.")
        written_n = sum(
            1 for r in recs
            if r.get("merge_outcome") in {"merged", "auto_merge_requested", "dry_run"}
            or (r.get("pr_url") and r.get("local_validation") == "pass")
        )
        ts = max((r.get("timestamp") or "") for r in recs) or None
        row = make_row(
            run_id=run_id, harness="persona-wake", date=day,
            tickers_requested=len(recs), tickers_written=written_n,
            llm_provider=prov, model=model, prompt_tokens=pt, completion_tokens=ct,
            tokens_source=src, llm_calls=sum(1 for r in recs if r.get("provider") not in {None, "", "rule"}),
            runtime_s=0.0, notes=notes, timestamp=ts,
        )
        if write:
            write_run([row], runs_dir)
        written.append(row)
    return written


def persona_rows(results: list[dict], *, run_id: str, runtime_s: float, dry_run: bool) -> list[dict]:
    """One row per (provider, model) for a persona wake, from its attempt records."""
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in results:
        groups[(r.get("provider") or "rule", r.get("model") or "rule")].append(r)
    if not groups:
        groups[("none", "none")] = []
    dates = sorted({r.get("session_date") or "" for r in results} - {""})
    day = dates[-1] if dates else datetime.now(timezone.utc).date().isoformat()
    rows = []
    for (prov, model), recs in sorted(groups.items()):
        pt = sum(int(r.get("prompt_tokens") or 0) for r in recs)
        ct = sum(int(r.get("completion_tokens") or 0) for r in recs)
        srcs = {r.get("tokens_source") or "none" for r in recs}
        calls = sum(1 for r in recs if r.get("tokens_source") not in (None, "", "none"))
        written = sum(1 for r in recs if r.get("merge_outcome") in {"merged", "auto_merge_requested", "dry_run"}
                      or (r.get("pr_url") and r.get("local_validation") == "pass"))
        rows.append(make_row(run_id=run_id, harness="persona-wake", date=day, tickers_requested=len(recs),
                             tickers_written=written, llm_provider=prov, model=model, prompt_tokens=pt,
                             completion_tokens=ct, llm_calls=calls,
                             tokens_source="mixed" if len(srcs) > 1 else next(iter(srcs)),
                             runtime_s=runtime_s / max(1, len(groups)), dry_run=dry_run,
                             notes=f"sessions {','.join(dates)}" if dates else ""))
    return rows


def default_run_id(harness: str) -> str:
    gh = os.environ.get("GITHUB_RUN_ID")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{harness}-{stamp}" + (f"-gh{gh}-{os.environ.get('GITHUB_RUN_ATTEMPT', '1')}" if gh else "")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m harness.costs")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("rebuild")
    hp = sub.add_parser("html")
    hp.add_argument("out")
    imp = sub.add_parser("import-attempts")
    imp.add_argument("attempts", nargs="?", default=str(ROOT / "data" / "attempts.jsonl"))
    lg = sub.add_parser("log")
    lg.add_argument("--harness", required=True)
    lg.add_argument("--run-id")
    lg.add_argument("--date", default=datetime.now(timezone.utc).date().isoformat())
    lg.add_argument("--tickers-requested", type=int, default=0)
    lg.add_argument("--tickers-written", type=int, default=0)
    lg.add_argument("--provider", default="none")
    lg.add_argument("--model", default="none")
    lg.add_argument("--prompt-tokens", type=int, default=0)
    lg.add_argument("--completion-tokens", type=int, default=0)
    lg.add_argument("--total-tokens", type=int)
    lg.add_argument("--tokens-source", default="none")
    lg.add_argument("--llm-calls", type=int, default=0)
    lg.add_argument("--est-cost-usd", type=float)
    lg.add_argument("--runtime-s", type=float, default=0.0)
    lg.add_argument("--dry-run", action="store_true")
    lg.add_argument("--notes", default="")
    lg.add_argument("--from-summary", help="nightly collector summary JSON (fills counts and runtime)")
    a = ap.parse_args(argv)
    if a.cmd == "rebuild":
        rebuild()
        return 0
    if a.cmd == "html":
        out = Path(a.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(summary_html(load_rows()), encoding="utf-8")
        print(out)
        return 0
    if a.cmd == "import-attempts":
        rows = import_persona_attempts(Path(a.attempts))
        print(f"imported {len(rows)} persona rows from {a.attempts}")
        return 0
    if a.from_summary:
        s = json.loads(Path(a.from_summary).read_text())
        if s.get("status") != "ok":
            print(f"summary status {s.get('status')}; nothing logged")
            return 0
        a.date = s["as_of"]
        a.tickers_requested = s["tickers_requested"]
        a.tickers_written = s["tickers_written"]
        a.runtime_s = s["runtime_s"]
        a.notes = a.notes or (f"backfill={s['backfill']} headlines={s['headlines_kept']} "
                              f"http_requests={s['http']['requests']} throttled={s['http']['throttled']}")
    row = make_row(run_id=a.run_id or default_run_id(a.harness), harness=a.harness, date=a.date,
                   tickers_requested=a.tickers_requested, tickers_written=a.tickers_written,
                   llm_provider=a.provider, model=a.model, prompt_tokens=a.prompt_tokens,
                   completion_tokens=a.completion_tokens, total_tokens=a.total_tokens,
                   tokens_source=a.tokens_source, llm_calls=a.llm_calls, est_cost_usd=a.est_cost_usd,
                   runtime_s=a.runtime_s, dry_run=a.dry_run, notes=a.notes)
    print(write_run([row]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
