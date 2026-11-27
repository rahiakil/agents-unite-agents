"""PR body for a nightly batch, from the collector summary JSON."""
import json
import sys

s = json.load(open(sys.argv[1]))
n, part = sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else ""
d = s["as_of"]
kind = "Backfill" if s["backfill"] else "Nightly collection"
close = "1:00 pm ET early close" if s.get("early_close") else "4:00 pm ET close"
print(f"{kind} for session {d}{part}. Deterministic script, no language model, zero tokens.\n")
print(f"- Reports in this PR: {n} (run wrote {s['tickers_written']} of {s['tickers_requested']} requested; "
      f"{s['already_present']} were already on main).")
print(f"- Window: the 72 hours before the {d} {close}. Generated {s['generated_at']}.")
print(f"- Headlines kept: {s['headlines_kept']} of {s['headlines_raw']} fetched. SEC filings in window: {s['sec_filings']}.")
print(f"- Lexicon scores: {s['bullish']} bullish, {s['neutral']} neutral, {s['bearish']} bearish; average {s['avg_sentiment']:+.3f}.")
mc = s.get("market_context") or {}
print("- Market context: " + ", ".join(f"{k} {v['change_pct']:+.2f}%" for k, v in mc.items() if v.get("change_pct") is not None))
print(f"- Runtime {s['runtime_s']}s, {s['http']['requests']} HTTP requests, {s['http']['throttled']} throttled. "
      f"Failures or skips: {len(s['failures'])}.")
print("\nHarness: `agents-unite-nightly-script` in rahiakil/agents-unite-agents (nightly/). "
      "Every report has `score_source: lexicon` and `llm_tokens: 0`.")
