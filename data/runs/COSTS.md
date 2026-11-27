# Cost and token burn-down

Rebuilt by `python -m harness.costs rebuild` after every collector run. Raw rows: [cost_log.csv](cost_log.csv) and [cost_log.jsonl](cost_log.jsonl). Dry runs are excluded below.

`est cost` is what was actually spent (0 for the lexicon script and free tiers). `list price` prices the same tokens at the provider's published rate. Rows with `tokens_source: estimated` used 4 characters per token.

All time: 23 runs, 2,292 stock reports, 0 tokens, est cost $0.0000, 0 tokens per stock, $0.0000 per 50 stocks.

## By harness

| Harness | Runs | Stocks written | Tokens | Tokens/stock | Est cost | List price | Cost/50 stocks | Avg runtime s |
|---|---|---|---|---|---|---|---|---|
| grokbot-backfill | 21 | 2,092 | 0 | 0 | $0.0000 | $0.0000 | $0.0000 | 78 |
| grokbot-routine | 2 | 200 | 0 | 0 | $0.0000 +unpriced | $0.0000 | $0.0000 | 33 |

## By day and harness

| Day | Harness | Runs | Stocks written | Tokens | Est cost | List price | Cost/50 stocks |
|---|---|---|---|---|---|---|---|
| 2026-11-25 | grokbot-routine | 1 | 100 | 0 | $0.0000 +unpriced | $0.0000 | $0.0000 |
| 2026-11-20 | grokbot-routine | 1 | 100 | 0 | $0.0000 +unpriced | $0.0000 | $0.0000 |
| 2026-11-19 | grokbot-backfill | 1 | 100 | 0 | $0.0000 | $0.0000 | $0.0000 |
| 2026-11-18 | grokbot-backfill | 1 | 100 | 0 | $0.0000 | $0.0000 | $0.0000 |
| 2026-11-17 | grokbot-backfill | 1 | 100 | 0 | $0.0000 | $0.0000 | $0.0000 |
| 2026-11-16 | grokbot-backfill | 1 | 100 | 0 | $0.0000 | $0.0000 | $0.0000 |
| 2026-11-13 | grokbot-backfill | 1 | 100 | 0 | $0.0000 | $0.0000 | $0.0000 |
| 2026-11-12 | grokbot-backfill | 1 | 99 | 0 | $0.0000 | $0.0000 | $0.0000 |
| 2026-11-11 | grokbot-backfill | 1 | 100 | 0 | $0.0000 | $0.0000 | $0.0000 |
| 2026-11-10 | grokbot-backfill | 1 | 100 | 0 | $0.0000 | $0.0000 | $0.0000 |
| 2026-11-09 | grokbot-backfill | 1 | 100 | 0 | $0.0000 | $0.0000 | $0.0000 |
| 2026-11-06 | grokbot-backfill | 1 | 100 | 0 | $0.0000 | $0.0000 | $0.0000 |
| 2026-11-05 | grokbot-backfill | 1 | 100 | 0 | $0.0000 | $0.0000 | $0.0000 |
| 2026-11-04 | grokbot-backfill | 1 | 100 | 0 | $0.0000 | $0.0000 | $0.0000 |
| 2026-11-03 | grokbot-backfill | 1 | 100 | 0 | $0.0000 | $0.0000 | $0.0000 |
| 2026-11-02 | grokbot-backfill | 1 | 99 | 0 | $0.0000 | $0.0000 | $0.0000 |
| 2026-10-30 | grokbot-backfill | 1 | 100 | 0 | $0.0000 | $0.0000 | $0.0000 |
| 2026-10-29 | grokbot-backfill | 1 | 100 | 0 | $0.0000 | $0.0000 | $0.0000 |
| 2026-10-28 | grokbot-backfill | 1 | 100 | 0 | $0.0000 | $0.0000 | $0.0000 |
| 2026-10-27 | grokbot-backfill | 1 | 97 | 0 | $0.0000 | $0.0000 | $0.0000 |
| 2026-10-26 | grokbot-backfill | 1 | 97 | 0 | $0.0000 | $0.0000 | $0.0000 |
| 2026-10-23 | grokbot-backfill | 1 | 100 | 0 | $0.0000 | $0.0000 | $0.0000 |
| 2026-10-22 | grokbot-backfill | 1 | 100 | 0 | $0.0000 | $0.0000 | $0.0000 |

## By model

| Provider | Model | Calls | Tokens | Est cost | List price |
|---|---|---|---|---|---|
| grokbot | grokbot | 2 | 0 | $0.0000 | $0.0000 |
| groq | mixed:openai/gpt-oss-120b+openai/gpt-oss-20b | 300 | 0 | $0.0000 | $0.0000 |
| groq | mixed:openai/gpt-oss-120b+openai/gpt-oss-20b+qwen/qwen3.8-27b | 100 | 0 | $0.0000 | $0.0000 |
| groq | openai/gpt-oss-120b | 991 | 0 | $0.0000 | $0.0000 |
| groq | openai/gpt-oss-20b | 698 | 0 | $0.0000 | $0.0000 |
