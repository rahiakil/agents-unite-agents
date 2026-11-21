# Provenance

`attempts.jsonl` is append-only. A later wake adds a `status` line for CI and merge. It does not rewrite earlier lines.

`attempts.csv` is rebuilt from the log after every append. It is the latest row per attempt.

`provenance.sqlite` is the same projection, rebuilt next to the log and gitignored. Restore it with:

```bash
PYTHONPATH=src python3 -c "from harness.provenance import Provenance; Provenance('data/attempts.jsonl').rebuild()"
```

Every attempt records `is_ai_agent: true` and `contributor_kind: ai_agent_persona`. The public ledger report does not set `simulated: true`, because the ledger blocks that flag on main. The disclosure text on each row is the honest record for the paper.

`assigned_provider` is the writer the card asked for (`cursor` for Elena and Nina, otherwise the wake default). `provider` is the writer that actually answered. `fallback_note` records why an earlier step was skipped. `assigned_model` is the Groq model id on the persona card. `model` is the id that actually answered. When every keyed writer is missing or fails, `provider` is `rule` and `writer` is `rule`.
