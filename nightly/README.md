# Zero-token nightly collector

`collect_nightly.py` is a deterministic Python script. It makes no LLM call, so a
nightly run costs nothing beyond free GitHub Actions minutes on this public repo.

What each report contains, per S&P 500 ticker and session:

- Up to 10 dated headlines from a Google News RSS search, each with its link and
  publish date, filtered to the 72 hours before the close (1:00 pm ET on early
  close days). Date-only items dated on the session day are dropped because they
  may have appeared after the close.
- Close, change, volume and 52-week range from Yahoo Finance daily bars.
- SEC EDGAR 8-K, 10-Q and 10-K filings dated in the window. The User-Agent is a
  name plus email (default `Parag Paul parag@rigai.co`). Override with the
  `SEC_CONTACT` secret, which should be written as `Name email@domain`.
- A word-list score (VADER plus a finance lexicon). Front matter says
  `harness: agents-unite-nightly-script`, `score_source: lexicon`, `llm_tokens: 0`.

It does not scrape google.com search pages or AI Overviews.

## Coverage

`sp500.csv` is the S&P 500 constituent list (503 share classes) taken from
Wikipedia on 2026-11-27 and committed so every run uses the same list. Edit
the file to change coverage. `aliases.json` holds the search names used in
the Google News query and the relevance filter.

A full run measured 4.5 minutes for 503 tickers at 4 workers (about 1,400 HTTP
requests, 14 Google 503s retried with backoff, 0 failures). Google News is
spaced at roughly 2 requests a second with jitter.

## Schedule

`.github/workflows/nightly.yml` runs at 01:30 UTC Tuesday to Saturday (5:30 pm
PT in winter), with catch-up runs at 03:45 and 13:15 UTC because Actions drops
some scheduled runs. Each run collects the most recent completed sessions
(up to three) that have no batch from this contributor on the ledger yet, skips
NYSE holidays, and lands one `batch/DATE-5cd8be5d` PR per session with
auto-merge. Run it by hand from the Actions tab with `sessions` set to dates.

## Cost log

Every run appends a row to `data/runs/` (see `src/harness/costs.py`), and the
Cost page workflow publishes it to GitHub Pages.
