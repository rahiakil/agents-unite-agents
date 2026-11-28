# Cost and token burn-down

Rebuilt by `python -m harness.costs rebuild` after every collector run. Raw rows: [cost_log.csv](cost_log.csv) and [cost_log.jsonl](cost_log.jsonl). Dry runs are excluded below.

`est cost` is what was actually spent (0 for the lexicon script and free tiers). `list price` prices the same tokens at the provider's published rate. Rows with `tokens_source: estimated` used 4 characters per token.

All time: 65 runs, 12,823 stock reports written, 2h 0m recorded runtime, 13,964 recorded LLM tokens, est cost $0.0000, $0.0000 per 50 stocks. A zero token or dollar total is expected for the lexicon script and free tiers; it does not mean the collector did nothing.

## By harness

| Harness | Runs | Stocks written | Tokens | Tokens/stock | Est cost | List price | Cost/50 stocks | Avg runtime s |
|---|---|---|---|---|---|---|---|---|
| grokbot-backfill | 21 | 2,092 | 0 | 0 | $0.0000 | $0.0000 | $0.0000 | 78 |
| grokbot-routine | 2 | 200 | 0 | 0 | $0.0000 +unpriced | $0.0000 | $0.0000 | 33 |
| nightly-backfill | 25 | 9,006 | 0 | 0 | $0.0000 | $0.0000 | $0.0000 | 178 |
| nightly-script | 3 | 1,467 | 0 | 0 | $0.0000 | $0.0000 | $0.0000 | 256 |
| persona-wake | 14 | 58 | 13,964 | 241 | $0.0000 | $0.0011 | $0.0000 | 20 |

## By day and harness

| Day | Harness | Runs | Stocks written | Tokens | Est cost | List price | Cost/50 stocks |
|---|---|---|---|---|---|---|---|
| 2026-11-27 | persona-wake | 5 | 36 | 13,964 | $0.0000 | $0.0011 | $0.0000 |
| 2026-11-27 | nightly-script | 1 | 501 | 0 | $0.0000 | $0.0000 | $0.0000 |
| 2026-11-27 | nightly-backfill | 1 | 0 | 0 | $0.0000 | $0.0000 | $0.0000 |
| 2026-11-26 | persona-wake | 4 | 18 | 0 | $0.0000 | $0.0000 | $0.0000 |
| 2026-11-25 | nightly-backfill | 1 | 402 | 0 | $0.0000 | $0.0000 | $0.0000 |
| 2026-11-25 | grokbot-routine | 1 | 100 | 0 | $0.0000 +unpriced | $0.0000 | $0.0000 |
| 2026-11-24 | persona-wake | 1 | 1 | 0 | $0.0000 | $0.0000 | $0.0000 |
| 2026-11-24 | nightly-script | 1 | 484 | 0 | $0.0000 | $0.0000 | $0.0000 |
| 2026-11-24 | nightly-backfill | 1 | 18 | 0 | $0.0000 | $0.0000 | $0.0000 |
| 2026-11-23 | persona-wake | 1 | 1 | 0 | $0.0000 | $0.0000 | $0.0000 |
| 2026-11-23 | nightly-script | 1 | 482 | 0 | $0.0000 | $0.0000 | $0.0000 |
| 2026-11-23 | nightly-backfill | 1 | 20 | 0 | $0.0000 | $0.0000 | $0.0000 |
| 2026-11-21 | persona-wake | 3 | 2 | 0 | $0.0000 | $0.0000 | $0.0000 |
| 2026-11-20 | nightly-backfill | 1 | 402 | 0 | $0.0000 | $0.0000 | $0.0000 |
| 2026-11-20 | grokbot-routine | 1 | 100 | 0 | $0.0000 +unpriced | $0.0000 | $0.0000 |
| 2026-11-19 | nightly-backfill | 1 | 403 | 0 | $0.0000 | $0.0000 | $0.0000 |
| 2026-11-19 | grokbot-backfill | 1 | 100 | 0 | $0.0000 | $0.0000 | $0.0000 |
| 2026-11-18 | nightly-backfill | 1 | 403 | 0 | $0.0000 | $0.0000 | $0.0000 |
| 2026-11-18 | grokbot-backfill | 1 | 100 | 0 | $0.0000 | $0.0000 | $0.0000 |
| 2026-11-17 | nightly-backfill | 1 | 403 | 0 | $0.0000 | $0.0000 | $0.0000 |
| 2026-11-17 | grokbot-backfill | 1 | 100 | 0 | $0.0000 | $0.0000 | $0.0000 |
| 2026-11-16 | nightly-backfill | 1 | 403 | 0 | $0.0000 | $0.0000 | $0.0000 |
| 2026-11-16 | grokbot-backfill | 1 | 100 | 0 | $0.0000 | $0.0000 | $0.0000 |
| 2026-11-13 | nightly-backfill | 1 | 403 | 0 | $0.0000 | $0.0000 | $0.0000 |
| 2026-11-13 | grokbot-backfill | 1 | 100 | 0 | $0.0000 | $0.0000 | $0.0000 |
| 2026-11-12 | nightly-backfill | 1 | 404 | 0 | $0.0000 | $0.0000 | $0.0000 |
| 2026-11-12 | grokbot-backfill | 1 | 99 | 0 | $0.0000 | $0.0000 | $0.0000 |
| 2026-11-11 | nightly-backfill | 1 | 403 | 0 | $0.0000 | $0.0000 | $0.0000 |
| 2026-11-11 | grokbot-backfill | 1 | 100 | 0 | $0.0000 | $0.0000 | $0.0000 |
| 2026-11-10 | nightly-backfill | 1 | 403 | 0 | $0.0000 | $0.0000 | $0.0000 |
| 2026-11-10 | grokbot-backfill | 1 | 100 | 0 | $0.0000 | $0.0000 | $0.0000 |
| 2026-11-09 | nightly-backfill | 1 | 403 | 0 | $0.0000 | $0.0000 | $0.0000 |
| 2026-11-09 | grokbot-backfill | 1 | 100 | 0 | $0.0000 | $0.0000 | $0.0000 |
| 2026-11-06 | nightly-backfill | 1 | 403 | 0 | $0.0000 | $0.0000 | $0.0000 |
| 2026-11-06 | grokbot-backfill | 1 | 100 | 0 | $0.0000 | $0.0000 | $0.0000 |
| 2026-11-05 | nightly-backfill | 1 | 403 | 0 | $0.0000 | $0.0000 | $0.0000 |
| 2026-11-05 | grokbot-backfill | 1 | 100 | 0 | $0.0000 | $0.0000 | $0.0000 |
| 2026-11-04 | nightly-backfill | 1 | 403 | 0 | $0.0000 | $0.0000 | $0.0000 |
| 2026-11-04 | grokbot-backfill | 1 | 100 | 0 | $0.0000 | $0.0000 | $0.0000 |
| 2026-11-03 | nightly-backfill | 1 | 403 | 0 | $0.0000 | $0.0000 | $0.0000 |
| 2026-11-03 | grokbot-backfill | 1 | 100 | 0 | $0.0000 | $0.0000 | $0.0000 |
| 2026-11-02 | nightly-backfill | 1 | 404 | 0 | $0.0000 | $0.0000 | $0.0000 |
| 2026-11-02 | grokbot-backfill | 1 | 99 | 0 | $0.0000 | $0.0000 | $0.0000 |
| 2026-10-30 | nightly-backfill | 1 | 403 | 0 | $0.0000 | $0.0000 | $0.0000 |
| 2026-10-30 | grokbot-backfill | 1 | 100 | 0 | $0.0000 | $0.0000 | $0.0000 |
| 2026-10-29 | nightly-backfill | 1 | 403 | 0 | $0.0000 | $0.0000 | $0.0000 |
| 2026-10-29 | grokbot-backfill | 1 | 100 | 0 | $0.0000 | $0.0000 | $0.0000 |
| 2026-10-28 | nightly-backfill | 1 | 403 | 0 | $0.0000 | $0.0000 | $0.0000 |
| 2026-10-28 | grokbot-backfill | 1 | 100 | 0 | $0.0000 | $0.0000 | $0.0000 |
| 2026-10-27 | nightly-backfill | 1 | 406 | 0 | $0.0000 | $0.0000 | $0.0000 |
| 2026-10-27 | grokbot-backfill | 1 | 97 | 0 | $0.0000 | $0.0000 | $0.0000 |
| 2026-10-26 | nightly-backfill | 1 | 406 | 0 | $0.0000 | $0.0000 | $0.0000 |
| 2026-10-26 | grokbot-backfill | 1 | 97 | 0 | $0.0000 | $0.0000 | $0.0000 |
| 2026-10-23 | grokbot-backfill | 1 | 100 | 0 | $0.0000 | $0.0000 | $0.0000 |
| 2026-10-22 | grokbot-backfill | 1 | 100 | 0 | $0.0000 | $0.0000 | $0.0000 |
| 2025-08-05 | nightly-backfill | 1 | 499 | 0 | $0.0000 | $0.0000 | $0.0000 |

## By model

| Provider | Model | Calls | Tokens | Est cost | List price |
|---|---|---|---|---|---|
| grokbot | grokbot | 2 | 0 | $0.0000 | $0.0000 |
| groq | mixed:openai/gpt-oss-120b+openai/gpt-oss-20b | 300 | 0 | $0.0000 | $0.0000 |
| groq | mixed:openai/gpt-oss-120b+openai/gpt-oss-20b+qwen/qwen3.8-27b | 100 | 0 | $0.0000 | $0.0000 |
| groq | openai/gpt-oss-120b | 998 | 2,180 | $0.0000 | $0.0005 |
| groq | openai/gpt-oss-20b | 703 | 1,658 | $0.0000 | $0.0002 |
| groq | qwen/qwen3.8-27b | 6 | 1,202 | $0.0000 | $0.0004 |
| lexicon | lexicon-vader-fin-v1 | 0 | 0 | $0.0000 | $0.0000 |
| rule | rule | 11 | 8,924 | $0.0000 | $0.0000 |
