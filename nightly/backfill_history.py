#!/usr/bin/env python3
"""Resumable zero-token history backfill for the agents-unite ledger.

Walks NYSE sessions backward from the newest one, newest first, and lands one
batch PR per session through nightly/publish.sh (lexicon scores, no LLM). The
cursor lives in data/backfill/progress.json and is committed after every run,
so each scheduled run picks up where the last one stopped.

A run stops cleanly when:
  * its time budget is used up (BACKFILL_BUDGET_MIN, default 50),
  * Google News throttles (publish.sh exit 3); the same day is retried next run,
  * a day fails 3 runs in a row (it is recorded as skipped and the cursor moves on).

The whole backfill stops (status finished or paused) when:
  * Google News returns no dated headline for any ticker on 10 sessions in a row
    (the archive does not go further back), or
  * the cursor passes BACKFILL_FLOOR (optional), or
  * the ledger repository is larger than BACKFILL_MAX_REPO_KB (default 2.5 GB),
    to stay well under GitHub's repository size guidance.

Every day gets reports for all tickers with a price bar: tickers with no dated
news get a preliminary report (preliminary: true) that a later run can update.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import collect_nightly as C  # noqa: E402

PROGRESS = ROOT / "data" / "backfill" / "progress.json"
EMPTY_STREAK_STOP = 10
MAX_DAY_FAILURES = 3


def now_z() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def log(msg: str) -> None:
    print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%SZ')}] {msg}", flush=True)


def load() -> dict:
    if PROGRESS.is_file():
        return json.loads(PROGRESS.read_text())
    start = C.auto_session() or date.fromisoformat(os.environ.get("BACKFILL_START", "2026-11-27"))
    newest = date.fromisoformat(os.environ.get("BACKFILL_START", start.isoformat()))
    return {"started_at": now_z(), "newest": newest.isoformat(), "cursor": newest.isoformat(),
            "year_target": (newest - timedelta(days=365)).isoformat(), "status": "running", "stop_reason": "",
            "empty_news_streak": 0, "runs": 0, "days": {}}


def save(p: dict) -> None:
    PROGRESS.parent.mkdir(parents=True, exist_ok=True)
    done = {k: v for k, v in p["days"].items() if v.get("state") == "merged"}
    p["days_merged"] = len(done)
    p["reports_written"] = sum(v.get("reports", 0) for v in done.values())
    p["preliminary_reports"] = sum(v.get("preliminary", 0) for v in done.values())
    p["earliest_merged"] = min(done) if done else None
    p["prs"] = sorted({v["pr"] for v in done.values() if v.get("pr")})[-20:]
    if p["earliest_merged"]:
        left = 0
        d = date.fromisoformat(p["earliest_merged"]) - timedelta(days=1)
        while d.isoformat() >= p["year_target"]:
            left += C.is_trading_day(d)
            d -= timedelta(days=1)
        p["year_sessions_remaining"] = left
    p["updated_at"] = now_z()
    PROGRESS.write_text(json.dumps(p, indent=1, sort_keys=True) + "\n")


def repo_kb() -> int | None:
    try:
        out = subprocess.run(["gh", "api", f"repos/{os.environ.get('LEDGER_REPO', 'rahiakil/agents-unite')}", "--jq", ".size"],
                             capture_output=True, text=True, timeout=60)
        return int(out.stdout.strip())
    except Exception:  # noqa: BLE001
        return None


def prev_day(d: date) -> date:
    d -= timedelta(days=1)
    while not C.is_trading_day(d):
        d -= timedelta(days=1)
    return d


def run_day(d: date) -> tuple[int, dict | None, list[str]]:
    work = Path(tempfile.mkdtemp(prefix=f"bf-{d}-"))
    env = {**os.environ, "SESSIONS": d.isoformat(), "BACKFILL": "true", "PRELIMINARY": "true",
           "COST_HARNESS": "nightly-backfill", "SUMMARY_DIR": "backfill", "NIGHTLY_WORK": str(work)}
    rc = subprocess.run(["bash", str(HERE / "publish.sh")], env=env).returncode
    s = work / f"summary-{d}.json"
    summary = json.loads(s.read_text()) if s.is_file() else None
    prs = (work / "prs.txt").read_text().split("\n") if (work / "prs.txt").is_file() else []
    return rc, summary, [x for x in prs if x.strip()]


def main() -> int:
    p = load()
    if p["status"] in ("finished", "paused_size") and os.environ.get("BACKFILL_FORCE") != "1":
        log(f"backfill is {p['status']}: {p['stop_reason']}")
        return 0
    p["status"] = "running"
    p["runs"] += 1
    budget = float(os.environ.get("BACKFILL_BUDGET_MIN", "50")) * 60
    floor = os.environ.get("BACKFILL_FLOOR", "")
    max_kb = int(os.environ.get("BACKFILL_MAX_REPO_KB", "2500000"))
    t0 = time.time()
    d = date.fromisoformat(p["cursor"])
    while C.is_trading_day(d) is False:
        d -= timedelta(days=1)
    while time.time() - t0 < budget:
        if floor and d.isoformat() < floor:
            p.update(status="finished", stop_reason=f"reached BACKFILL_FLOOR {floor}")
            break
        kb = repo_kb()
        if kb and kb > max_kb:
            p.update(status="paused_size", stop_reason=f"ledger repo is {kb:,} KB, over the {max_kb:,} KB guard")
            break
        log(f"session {d}")
        rc, summ, prs = run_day(d)
        rec = p["days"].get(d.isoformat(), {})
        if rc == 3:
            p.update(status="throttled", stop_reason=f"Google News throttled on {d} at {now_z()}; retry next run")
            rec.update(state="throttled", last_try=now_z())
            p["days"][d.isoformat()] = rec
            break
        if rc == 0 and summ is None:
            rec.update(state="no_session", last_try=now_z())
            p["days"][d.isoformat()] = rec
            d = prev_day(d)
            p["cursor"] = d.isoformat()
            save(p)
            continue
        merged = [x for x in prs if " MERGED" in x]
        if rc == 0 and summ and (merged or summ.get("tickers_written", 0) == 0 or not prs):
            rec = {"state": "merged", "reports": summ.get("tickers_written", 0), "preliminary": summ.get("preliminary", 0),
                   "headlines": summ.get("headlines_kept", 0), "runtime_s": summ.get("runtime_s"),
                   "already_present": summ.get("already_present", 0),
                   "pr": merged[0].split()[0] if merged else "", "done_at": now_z()}
            p["days"][d.isoformat()] = rec
            empty = summ.get("tickers_written", 0) > 0 and summ.get("headlines_kept", 0) == 0
            p["empty_news_streak"] = p["empty_news_streak"] + 1 if empty else 0
            if p["empty_news_streak"] >= EMPTY_STREAK_STOP:
                p.update(status="finished", stop_reason=f"Google News returned no dated headline for {EMPTY_STREAK_STOP} "
                                                        f"sessions in a row, ending at {d}")
                p["cursor"] = prev_day(d).isoformat()
                break
            d = prev_day(d)
            p["cursor"] = d.isoformat()
            save(p)
            continue
        fails = rec.get("failures", 0) + 1
        rec.update(state="failed", failures=fails, last_try=now_z(), rc=rc, prs=prs[-2:])
        p["days"][d.isoformat()] = rec
        if fails >= MAX_DAY_FAILURES:
            rec["state"] = "skipped"
            d = prev_day(d)
            p["cursor"] = d.isoformat()
        p["stop_reason"] = f"{d} failed (exit {rc}); retry next run"
        break
    else:
        p["stop_reason"] = "time budget used; continues next run"
    save(p)
    log(f"status {p['status']}: {p['stop_reason']}; cursor {p['cursor']}; merged days {p['days_merged']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
