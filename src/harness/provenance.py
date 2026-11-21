"""Append-only attempt log plus a CSV and SQLite projection.

JSONL is never rewritten. Status updates are new lines. The CSV and SQLite
files are rebuilt from the log so a later wake can fill in CI and merge.
"""

from __future__ import annotations

import csv
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

COLUMNS = [
    "attempt_id",
    "timestamp",
    "scheduled_for",
    "persona_id",
    "archetype",
    "display_name",
    "github_username",
    "is_ai_agent",
    "contributor_kind",
    "assigned_provider",
    "provider",
    "model",
    "assigned_model",
    "fell_back",
    "fallback_note",
    "writer",
    "prompt_version",
    "persona_prompt_sha",
    "voice_sha",
    "ledger_prompt_file",
    "ledger_prompt_hash",
    "ticker",
    "session_date",
    "slot",
    "focus",
    "source_count",
    "sources_json",
    "tape_json",
    "local_validation",
    "slot_status",
    "branch",
    "pr_url",
    "pr_number",
    "ci_outcome",
    "merge_outcome",
    "failure_reason",
    "dry_run",
    "disclosure",
]

TERMINAL_MERGE = {"merged", "closed_unmerged"}


def now_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def repair_rebase_jsonl(path: str | Path = "data/attempts.jsonl") -> None:
    """Replace a conflicted JSONL with the union of the two rebase stages."""
    import subprocess

    def stage(number: int) -> str:
        result = subprocess.run(
            ["git", "show", f":{number}:{path}"],
            check=False,
            capture_output=True,
            text=True,
        )
        return result.stdout if result.returncode == 0 else ""

    target = Path(path)
    left = stage(2)
    right = stage(3)
    if left and right:
        text = union_jsonl(left, right)
    elif left or right:
        text = left or right
        if text and not text.endswith("\n"):
            text += "\n"
    else:
        return
    target.write_text(text, encoding="utf-8")
    Provenance(target).rebuild()


def union_jsonl(left: str, right: str) -> str:
    """Keep every unique JSON line from two logs. Drop conflict markers."""
    seen: set[str] = set()
    lines: list[str] = []
    for source in (left, right):
        for line in source.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith(("<<<<<<<", "=======", ">>>>>>>")):
                continue
            if stripped in seen:
                continue
            seen.add(stripped)
            lines.append(stripped)
    if not lines:
        return ""
    return "\n".join(lines) + "\n"


class Provenance:
    def __init__(self, jsonl_path: Path | str):
        self.jsonl_path = Path(jsonl_path)
        self.csv_path = self.jsonl_path.with_suffix(".csv")
        self.sqlite_path = self.jsonl_path.with_name("provenance.sqlite")
        self.jsonl_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.jsonl_path.exists():
            self.jsonl_path.write_text("", encoding="utf-8")

    def append(self, record: dict) -> None:
        payload = dict(record)
        payload.setdefault("timestamp", now_stamp())
        payload.setdefault("record_type", "attempt")
        with self.jsonl_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, sort_keys=True) + "\n")
        self.rebuild()

    def has_attempt(self, persona_id: str, session: str, slot_id: str) -> bool:
        attempt_id = f"{persona_id}|{session}|{slot_id}"
        return attempt_id in self.latest()

    def latest(self) -> dict[str, dict]:
        rows: dict[str, dict] = {}
        if not self.jsonl_path.is_file():
            return rows
        for line in self.jsonl_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            item = json.loads(line)
            if item.get("record_type") == "status":
                current = rows.get(item.get("attempt_id") or "")
                if current is None:
                    continue
                for key in ("ci_outcome", "merge_outcome", "pr_url", "pr_number", "failure_reason"):
                    if item.get(key) not in (None, ""):
                        current[key] = item[key]
                continue
            attempt_id = item.get("attempt_id")
            if attempt_id:
                rows[attempt_id] = item
        return rows

    def open_for_backfill(self, *, max_age_days: int = 14) -> list[dict]:
        cutoff = datetime.now(timezone.utc).timestamp() - max_age_days * 86400
        pending = []
        for row in self.latest().values():
            if row.get("dry_run") in {True, "true", "1"}:
                continue
            if not row.get("pr_number") and not row.get("pr_url"):
                continue
            if str(row.get("merge_outcome") or "") in TERMINAL_MERGE:
                continue
            stamp = str(row.get("timestamp") or "")
            try:
                seen = datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
            except ValueError:
                seen = cutoff
            if seen >= cutoff:
                pending.append(row)
        return pending

    def rebuild(self) -> None:
        rows = list(self.latest().values())
        with self.csv_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=COLUMNS, extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                writer.writerow({column: row.get(column, "") for column in COLUMNS})
        if self.sqlite_path.exists():
            self.sqlite_path.unlink()
        connection = sqlite3.connect(self.sqlite_path)
        try:
            definitions = ", ".join(
                f"{column} TEXT PRIMARY KEY" if column == "attempt_id" else f"{column} TEXT"
                for column in COLUMNS
            )
            connection.execute(f"CREATE TABLE attempts ({definitions})")
            for row in rows:
                values = ["" if row.get(column) is None else str(row.get(column)) for column in COLUMNS]
                placeholders = ", ".join("?" for _ in COLUMNS)
                connection.execute(
                    f"INSERT INTO attempts ({', '.join(COLUMNS)}) VALUES ({placeholders})",
                    values,
                )
            connection.commit()
        finally:
            connection.close()
