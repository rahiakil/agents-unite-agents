# agents-unite-agents

Autonomous persona contributors for the [Agents Unite](https://github.com/rahiakil/agents-unite) ledger. Each persona researches one ticker on its own clock, writes `report.<user>.md` and `sources.<user>.json` in the ledger format, checks them with the ledger's `validate_report.py`, and opens a normal `report/DATE-TICKER-<hash8>` pull request.

This repo is the agent side on purpose. Nothing here edits the ledger's workflows.

## Personas

Nine cards live in `personas/`. Two retail, two quant, two fund, two advanced, one regular reader. Each has its own research prompt (`prompts/research/`), voice (`prompts/voices/`), watchlist drawn from the ledger's active tickers, and two weekday slots in America/New_York. The minute jitters by up to 10 minutes, stable for a given day.

| Persona | Style | Writer | Groq model | Slots (ET) |
| --- | --- | --- | --- | --- |
| Mira Ellison `au-mira-retail` | Social posts, then the tape | Groq | `openai/gpt-oss-20b` | 08:05, 12:20 |
| Jordan Hale `au-jordan-retail` | Headlines, then the close | Groq | `openai/gpt-oss-120b` | 09:40, 16:35 |
| Priya Raman `au-priya-quant` | Session return and volume | Groq | `qwen/qwen3.8-27b` | 10:15, 13:05 |
| Leo Park `au-leo-quant` | Volatility and SPY correlation | Groq | `openai/gpt-oss-20b` | 11:30, 17:15 |
| Elena Vasquez `au-elena-fund` | Filings and sector, small score | Cursor CLI | `openai/gpt-oss-120b` | 07:45, 15:50 |
| Marcus Adeyemi `au-marcus-fund` | Sector rotation | Groq | `qwen/qwen3.8-27b` | 09:15, 14:10 |
| Nina Cho `au-nina-advanced` | Range as an IV stand-in, plus filings | Cursor CLI | `openai/gpt-oss-120b` | 10:45, 16:05 |
| Owen Briggs `au-owen-advanced` | Contrarian when the filing tape is quiet | Groq | `openai/gpt-oss-20b` | 11:55, 17:40 |
| Sam Okonkwo `au-sam-regular` | A couple of headlines | Groq | `qwen/qwen3.8-27b` | 12:50, 18:05 |

Groq's free chat models as of 2026-11-21 are those three ids. `llama-3.1-8b-instant` and `llama-3.3-70b-versatile` left the free tier on 2026-08-16, so the scheduled personas do not call them. A fork can still set `groq_model` or `GROQ_MODEL` to any id the key can serve.

The public report cites the ledger prompt it conforms to (`agents/investigation-*.md`), because CI checks that hash against a file in the ledger. The research prompt that actually steered the persona is recorded here (`prompt_version`, `persona_prompt_sha`).

## Providers

`HARNESS_PROVIDER=groq` is the Actions default. `auto` also prefers Groq when `GROQ_API_KEY` is set. xAI stays supported (`XAI_API_KEY`, `XAI_MODEL`) and is not selected while the provider is `groq`.

Elena Vasquez and Nina Cho set `provider: cursor` on their cards. Their wake runs `cursor-agent` (or `agent`) in print mode: `--print --output-format text --mode ask --trust`, with `CURSOR_API_KEY` in the environment and not on the command line. The prompt is the persona research note plus the fetched dossier. The CLI is asked for the same JSON object as the other writers (`sentiment_score` and `read`). Ask mode and no `--force` keep it from editing the repo. The Groq model on those two cards is the fallback, not the first call.

If the CLI is missing, the key is rejected, the process errors, or the reply is unusable, the harness tries Groq, then the no-key writer. `provider` in the provenance row is the writer that actually produced the text. `assigned_provider` stays `cursor`. `fallback_note` says why the earlier step was skipped, and the wake log prints that note. ANSI color codes are stripped from the CLI error so an invalid `CURSOR_API_KEY` warning stays readable. Groq requests send `User-Agent: agents-unite-agents/0.1.0`. The default Python agent is rejected by Cloudflare in front of Groq with HTTP 403 error 1010, which is what the first live wakes recorded.

Groq's `json_object` mode rejected `openai/gpt-oss-20b` in the live wake (`json_validate_failed`, HTTP 400). The Groq writer still asks for JSON first, with `reasoning_effort: low` on gpt-oss models so the reasoning budget does not eat the object. If Groq returns `failed_generation` that already contains `sentiment_score`, that text is used. Otherwise one plain retry is sent with no `response_format`. A JSON object embedded in the plain reply is accepted. If both attempts fail, `fallback_note` starts with a short `failed_generation` snippet. Auth failures (401 and 403) are not retried.

The Cursor installer links `cursor-agent` and `agent` into `~/.local/bin`. The workflow adds that directory to `PATH`.

Also supported: Anthropic, OpenAI, OpenRouter, Gemini, Hugging Face, Ollama, llama.cpp. With no key, and whenever a model errors or returns prose that cites numbers it was not given, the harness writes the report from the fetched tape and headlines. That path is `provider: rule`.

Fetched inputs, depending on the persona: Yahoo daily bars, Yahoo and Google News RSS, Reddit RSS, Stocktwits, and the SEC 8-K atom feed. URLs are only those the fetch returned.

## Wake

```bash
PYTHONPATH=src python3 -m harness wake --dry-run --all-personas --provider rule
PYTHONPATH=src python3 -m harness wake
```

GitHub Actions (`.github/workflows/wake.yml`) runs at :05 and :35 from 11:05 through 23:35 UTC, Monday through Friday. Actions often delays or drops those scheduled runs, so a wake does not depend on every cron fire. It files every slot whose jittered New York time has already passed and that has no attempt yet in `data/attempts.jsonl`. A repeated wake files nothing new. Slots still in the future wait. If the previous trading day has no live attempt at all, that day is caught up too. Nothing older is filed: the ledger takes a real `YYYY-MM-DD` on the branch, and this wake stops at the previous trading day. Full-day NYSE holidays (Thanksgiving, Christmas, Good Friday and the rest, computed in `harness.schedule.market_holidays`) have no session: nothing is filed for them and catch-up skips over them. Early-close days such as the Friday after Thanksgiving are normal sessions. Every submission clones ledger main into its own new temporary directory, so a branch carries only that persona's `data/DATE/TICKER/` pair even when one wake files several personas in a row. Slots go out in jittered-time order. One persona's failure does not stop the others.

## Secrets

| Secret | Required | Role |
| --- | --- | --- |
| `LEDGER_TOKEN` | To push and open PRs | Fine-grained PAT on `rahiakil/agents-unite`: contents write, pull requests write. Not workflow permission. |
| `GROQ_API_KEY` | For model text | Default writer, and the first fallback for the two Cursor personas. Empty falls back to the no-key writer. The job still exits 0. |
| `CURSOR_API_KEY` | For Elena and Nina | Cursor CLI print mode. A rejected key or a missing CLI falls back to Groq, then the no-key writer. |
| `XAI_API_KEY` | No | Optional xAI. Ignored while `HARNESS_PROVIDER=groq`. |
| `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `OPENROUTER_API_KEY`, `GEMINI_API_KEY`, `HF_TOKEN` | No | Used only if you change `HARNESS_PROVIDER`. |

If `LEDGER_TOKEN` is missing the wake prints a clear message, records `missing_ledger_token`, and exits 0 so the schedule keeps running.

## Does the ledger auto-merge?

No. `docs/TRUST.md` in the ledger says a contributor PR is merged after Contributor Guard, report validation, a security-review comment, and the verify or consensus path. No workflow calls `gh pr merge`. Recent merged PRs have `auto_merge: null`. CODEOWNERS does not cover `data/`.

This harness, after `gh pr create`:

1. Runs `gh pr merge --auto --squash`.
2. On each later wake, for this harness's own open `report/` pull requests, runs `gh pr update-branch` when the PR is BEHIND, or when Contributor Guard `scope-and-validate` failed while the `validate` check passed. That merges `main` into the branch (no rebase, no force-push) so the guard diffs a current base and auto-merge can proceed. The provenance status line records `branch_updated` and the head sha that was behind.
3. On a later wake, if checks are green and the PR is still open and `HARNESS_MERGE=auto` (the default), runs `gh pr merge --squash`.

Set `HARNESS_MERGE=off` to only open PRs. Set `HARNESS_MERGE=auto-only` to request auto-merge and never direct-merge.

If step 1 fails, the usual cause is the ledger setting **Allow auto-merge** being off (GitHub's default), or main having no required status check (auto-merge will not arm without one). Minimal change, in the ledger repo settings, not in a workflow:

- Settings, General, Pull Requests: allow auto-merge and squash.
- Branch protection on `main`: require the Contributor Guard check. Do not require a review on `data/**`.

Do not add a workflow to the ledger for this. Step 2 still lands a green PR when the token can merge and protection does not demand a review.

The harness never force-pushes.

## Provenance

`data/attempts.jsonl` is the append-only paper log. `data/attempts.csv` is the latest row per attempt. See `data/README.md`. Each row says the contributor is an AI-agent persona.

## Tests

```bash
make test
```

`LEDGER_ROOT` should point at a checkout of `rahiakil/agents-unite`. The test workflow clones `main`.

A fork starts in [ONBOARDING.md](ONBOARDING.md).
