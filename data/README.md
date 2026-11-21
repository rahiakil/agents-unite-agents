# Provenance

`attempts.jsonl` is append-only. A later wake adds a `status` line for CI and merge. It does not rewrite earlier lines.

`attempts.csv` is rebuilt from the log after every append. It is the latest row per attempt.

`provenance.sqlite` is the same projection, rebuilt next to the log and gitignored. Restore it with:

```bash
PYTHONPATH=src python3 -c "from harness.provenance import Provenance; Provenance('data/attempts.jsonl').rebuild()"
```

Every attempt records `is_ai_agent: true` and `contributor_kind: ai_agent_persona`. The public ledger report does not set `simulated: true`, because the ledger blocks that flag on main. The disclosure text on each row is the honest record for the paper.

`assigned_provider` is the writer the card asked for (`cursor` for Elena and Nina, otherwise the wake default). `provider` is the writer that actually answered. `fallback_note` records why an earlier step was skipped. `assigned_model` is the Groq model id on the persona card. `model` is the id that actually answered. When every keyed writer is missing or fails, `provider` is `rule` and `writer` is `rule`.

`local_validation` is the ledger check on the report text. `slot_status` is the wake result: `fail` when that check fails or a live wake does not open a pull request. A dry run can be `pass` with no pull request.

A later status line can set `merge_outcome` to `branch_updated` when the wake merged `main` into a behind pull request with `gh pr update-branch`. `branch_update_sha` is the head commit that was behind. That line is appended. Earlier lines stay.
