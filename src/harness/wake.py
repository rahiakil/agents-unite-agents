"""One wake: slots that have already passed, one ticker each, validate, then the contributor lane."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import traceback
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

from harness.ledger import validate_report_dir, validate_scope
from harness.personas import Persona, Slot, load_personas
from harness.provenance import Provenance, now_stamp
from harness.providers import CursorProvider, GroqProvider, RuleProvider, choose_completion, cursor_binary, select_provider
from harness.report import DISCLOSURE, model_reply_reason, render_report, write_pair
from harness.research import Dossier, gather, has_sources
from harness.schedule import (
    is_weekday,
    ledger_accepts_report_date,
    passed_slots,
    previous_trading_day,
    session_date,
)
from harness.submit import (
    SubmitResult,
    advance_open_pull_request,
    branch_update_sha,
    clone_ledger,
    commit_and_push,
    fresh_submit_dir,
    open_pr,
    request_auto_merge,
)
from harness.universe import Ticker, load_universe

ROOT = Path(__file__).resolve().parents[2]
MISSING_TOKEN = (
    "LEDGER_TOKEN is not set, so this wake will not push or open ledger pull requests. "
    "Add a repository secret named LEDGER_TOKEN with contents write and pull request write "
    "on the ledger repo. Optional model keys are listed in ONBOARDING.md. "
    "The wake exits 0 so the schedule keeps running."
)


@dataclass
class WakeConfig:
    now: datetime
    dry_run: bool
    all_personas: bool
    persona_id: str
    session: date | None
    # Parsed for older commands. Slot selection uses passed slots, not this window.
    lookback_minutes: int
    output_dir: Path
    provenance_path: Path
    ledger_root: Path | None
    personas_dir: Path | None
    ledger_repo: str
    ledger_ref: str
    provider_name: str
    merge_mode: str
    token: str
    force: bool
    backfill: bool


def default_provider_name() -> str:
    """Groq when a key is present, unless HARNESS_PROVIDER says otherwise."""
    chosen = os.environ.get("HARNESS_PROVIDER", "").strip()
    if chosen:
        return chosen
    if os.environ.get("GROQ_API_KEY"):
        return "groq"
    return "auto"


def provider_for(requested: str, persona: Persona):
    provider = select_provider(requested)
    if provider.name == "groq" and persona.groq_model:
        provider._model_override = persona.groq_model
    return provider


def assigned_provider_name(persona: Persona, requested: str) -> str:
    """Persona card wins over the Groq default. An explicit other provider does not.

    `--provider rule` stays on the no-key writer so an offline dry run never
    calls Cursor or Groq. `HARNESS_PROVIDER=groq` still lets a card marked
    `provider: cursor` use the CLI.
    """
    if requested in {"groq", "auto", ""} and persona.provider:
        return persona.provider
    return requested or "auto"


def provider_chain(persona: Persona, requested: str) -> list:
    assigned = assigned_provider_name(persona, requested)
    if assigned != "cursor":
        return [provider_for(assigned, persona)]
    cursor = CursorProvider()
    if persona.cursor_model:
        cursor._model_override = persona.cursor_model
    groq = GroqProvider()
    if persona.groq_model:
        groq._model_override = persona.groq_model
    return [cursor, groq, RuleProvider()]


def load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _candidates(persona: Persona, universe: dict[str, Ticker], session: str, slot_id: str, avoid: set[str]) -> list[str]:
    active = {symbol for symbol, item in universe.items() if item.active}
    preferred = [symbol for symbol in persona.watchlist if symbol in active and symbol not in avoid]
    pool = preferred or [symbol for symbol in sorted(active) if symbol not in avoid] or sorted(active)
    if not pool:
        return []
    digest = hashlib.sha256(f"{persona.id}|{session}|{slot_id}".encode("utf-8")).hexdigest()
    start = int(digest[:8], 16) % len(pool)
    ordered = pool[start:] + pool[:start]
    return ordered[:3]


def _focus(persona: Persona, session: str, slot_id: str) -> str:
    digest = hashlib.sha256(f"{persona.id}|{session}|{slot_id}|focus".encode("utf-8")).hexdigest()
    return persona.focus_modes[int(digest[:8], 16) % len(persona.focus_modes)]


def _tape_summary(dossier: Dossier) -> dict:
    tape = dossier.tape
    if tape is None:
        return {}
    ratio = None
    if tape.volume and tape.avg_volume_20:
        ratio = round(tape.volume / tape.avg_volume_20, 4)
    return {
        "last_close": tape.last_close,
        "change_pct": None if tape.change_pct is None else round(tape.change_pct, 4),
        "return_20d_pct": None if tape.return_20d_pct is None else round(tape.return_20d_pct, 4),
        "vol_20d": None if tape.vol_20d is None else round(tape.vol_20d, 6),
        "volume_ratio": ratio,
        "position_52w": None if tape.position_52w is None else round(tape.position_52w, 4),
        "spy_corr_20d": None if tape.spy_corr_20d is None else round(tape.spy_corr_20d, 4),
        "as_of": tape.as_of,
        "bar_count": tape.bar_count,
    }


def _llm_brief(persona: Persona, dossier: Dossier, provider) -> object:
    if provider.name == "rule":
        return provider.complete("", "")
    payload = {
        "ticker": dossier.ticker,
        "name": dossier.name,
        "sector": dossier.sector,
        "tape": _tape_summary(dossier),
        "headlines": [item.title for item in dossier.headlines[:6]],
        "filings": [item.title for item in dossier.filings[:3]],
        "social_count": len(dossier.social),
    }
    system = (
        persona.research_prompt
        + "\n\nVoice:\n"
        + persona.voice.notes
        + "\n\nReturn one JSON object with keys sentiment_score (number from -1 to 1) "
        "and read (a short paragraph). Use only numbers present in the user payload. "
        "Do not invent URLs, filings, or prices. Do not give trading instructions. "
        "Do not use an em dash."
    )
    user = json.dumps(payload)
    completion = provider.complete(system, user)
    completion.prompt_chars = len(system) + len(user)
    return completion


def _token_fields(completion) -> dict:
    """Provider-reported tokens when present, else a 4 chars per token estimate."""
    if completion is None or getattr(completion, "provider", "rule") == "rule":
        return {"prompt_tokens": 0, "completion_tokens": 0, "tokens_source": "none"}
    pt, ct = completion.prompt_tokens, completion.completion_tokens
    if pt is not None or ct is not None:
        return {"prompt_tokens": int(pt or 0), "completion_tokens": int(ct or 0), "tokens_source": "provider",
                "cost_usd": completion.cost_usd}
    if not completion.text and completion.error:
        return {"prompt_tokens": 0, "completion_tokens": 0, "tokens_source": "none"}
    return {"prompt_tokens": int(getattr(completion, "prompt_chars", 0) / 4),
            "completion_tokens": int(len(completion.text or "") / 4), "tokens_source": "estimated"}


def _base_record(persona: Persona, session: str, slot: Slot, scheduled_for: str, provider, requested: str) -> dict:
    fell_back = requested not in {provider.name, "auto"} and provider.name == "rule"
    if requested == "auto":
        fell_back = False
    return {
        "record_type": "attempt",
        "attempt_id": f"{persona.id}|{session}|{slot.id}",
        "timestamp": now_stamp(),
        "scheduled_for": scheduled_for,
        "persona_id": persona.id,
        "archetype": persona.archetype,
        "display_name": persona.display_name,
        "github_username": persona.github_username,
        "is_ai_agent": True,
        "contributor_kind": "ai_agent_persona",
        "assigned_provider": assigned_provider_name(persona, requested),
        "provider": provider.name,
        "model": _model_name(provider),
        "assigned_model": persona.groq_model,
        "requested_provider": requested,
        "fallback_note": "",
        "fell_back": fell_back,
        "writer": "rule",
        "prompt_version": persona.prompt_version,
        "persona_prompt_sha": persona.prompt_sha256,
        "voice_sha": persona.voice.sha256,
        "research_prompt": str(persona.research_prompt_path),
        "ticker": "",
        "session_date": session,
        "slot": slot.id,
        "focus": "",
        "source_count": 0,
        "sources_json": "[]",
        "tape_json": "{}",
        "local_validation": "fail",
        "branch": "",
        "pr_url": "",
        "pr_number": "",
        "ci_outcome": "not_run",
        "merge_outcome": "not_submitted",
        "failure_reason": "",
        "dry_run": False,
        "disclosure": DISCLOSURE,
        "fetch_errors": [],
    }


def _model_name(provider) -> str:
    if hasattr(provider, "model_name"):
        try:
            return str(provider.model_name())
        except Exception:
            return provider.name
    return str(getattr(provider, "model", provider.name))


def slot_status(record: dict) -> str:
    """A slot passes only when the report is valid and, live, a PR exists.

    Local validation without a pull request is a failed submit. A dry run
    passes when validation passes. A missing ledger token stays a soft miss
    for the process exit code, but the row is still ``fail``.
    """
    if record.get("dry_run") in {True, "true", "True", "1", 1}:
        return "pass" if record.get("local_validation") == "pass" else "fail"
    if record.get("local_validation") != "pass":
        return "fail"
    if record.get("pr_url"):
        return "pass"
    if record.get("merge_outcome") in {"merged", "auto_merge_requested", "checks_pending"}:
        return "pass"
    return "fail"


def split_results(results: list[dict]) -> tuple[list[dict], list[dict]]:
    failed = [row for row in results if row.get("slot_status") == "fail"]
    passed = [row for row in results if row not in failed]
    return passed, failed


def exit_code(config: WakeConfig, results: list[dict]) -> int:
    _passed, failed = split_results(results)
    if not failed:
        return 0
    if config.dry_run:
        return 1
    if all(row.get("merge_outcome") == "missing_token" for row in failed):
        return 0
    return 1


def _finish(provenance: Provenance, record: dict, *, dry_run: bool) -> dict:
    record["dry_run"] = dry_run
    record["model"] = record.get("model") or ""
    record["slot_status"] = slot_status(record)
    provenance.append(record)
    label = record["local_validation"]
    reason = record.get("failure_reason") or ""
    note = " ".join(str(record.get("fallback_note") or "").split())
    if len(note) > 180:
        note = note[:177] + "..."
    assigned = record.get("assigned_provider") or ""
    provider = record.get("provider") or "-"
    assigned_bit = f"assigned={assigned} " if assigned and assigned != provider else ""
    fallback_bit = f" fallback={note}" if note else ""
    print(
        f"{record['persona_id']} {record['slot']} {record.get('ticker') or '-'} "
        f"{assigned_bit}provider={provider} model={record.get('model') or '-'} "
        f"validation={label} slot={record['slot_status']} "
        f"merge={record.get('merge_outcome') or '-'}{fallback_bit} {reason}".rstrip()
    )
    return record


def _complete_for(persona: Persona, dossier: Dossier, requested: str, session: str, record: dict):
    """Call the assigned provider. Cursor falls through to Groq, then rule."""
    chain = provider_chain(persona, requested)
    if record.get("assigned_provider") != "cursor":
        completion = _llm_brief(persona, dossier, chain[0])
        record["provider"] = completion.provider
        record["model"] = completion.model or _model_name(chain[0])
        return completion

    def call(provider):
        return _llm_brief(persona, dossier, provider)

    def accept(completion) -> tuple[bool, str]:
        score, _read, reason = model_reply_reason(completion, dossier, session)
        if score is None:
            return False, reason or "unusable reply"
        return True, ""

    completion, note = choose_completion(chain, call, accept)
    record["provider"] = completion.provider
    record["model"] = completion.model or completion.provider
    record["fallback_note"] = note
    record["fell_back"] = completion.provider != "cursor"
    return completion


def run_slot(
    *,
    config: WakeConfig,
    persona: Persona,
    slot: Slot,
    scheduled_for: datetime,
    session: str,
    universe: dict[str, Ticker],
    ledger_root: Path,
    provenance: Provenance,
    provider,
    requested: str,
    gather_fn,
    avoid: set[str],
) -> dict:
    provider = provider_chain(persona, requested)[0]
    record = _base_record(persona, session, slot, scheduled_for.strftime("%Y-%m-%dT%H:%M:%SZ"), provider, requested)
    record["dry_run"] = config.dry_run
    focus = _focus(persona, session, slot.id)
    record["focus"] = focus
    dossier: Dossier | None = None
    ticker = ""
    errors: list[str] = []
    for candidate in _candidates(persona, universe, session, slot.id, avoid):
        meta = universe.get(candidate)
        try:
            found = gather_fn(
                candidate,
                persona.search,
                name=meta.name if meta else candidate,
                sector=meta.sector if meta else "",
            )
        except Exception as exc:
            errors.append(f"{candidate}: {type(exc).__name__}: {exc}")
            continue
        if has_sources(found):
            dossier = found
            ticker = candidate
            errors.extend(found.errors)
            break
        errors.extend(found.errors or [f"{candidate}: no grounded sources"])
    record["fetch_errors"] = errors
    if dossier is None or not ticker:
        record["failure_reason"] = "no_grounded_sources"
        record["merge_outcome"] = "not_submitted"
        return _finish(provenance, record, dry_run=config.dry_run)

    avoid.add(ticker)
    record["ticker"] = ticker
    try:
        completion = _complete_for(persona, dossier, requested, session, record)
        record.update(_token_fields(completion))
        rendered = render_report(
            persona,
            dossier,
            session=session,
            slot_id=slot.id,
            focus=focus,
            ledger_root=ledger_root,
            completion=completion,
        )
    except Exception as exc:
        record["failure_reason"] = f"render: {type(exc).__name__}: {exc}"[:500]
        return _finish(provenance, record, dry_run=config.dry_run)

    record["writer"] = rendered["writer"]
    if rendered["llm_note"] and not record.get("fallback_note"):
        record["fallback_note"] = rendered["llm_note"]
    if rendered["fell_back"] and rendered["writer"] == "rule":
        record["provider"] = "rule"
        record["model"] = "rule"
        record["fell_back"] = record.get("assigned_provider") not in {"rule", "auto"}
    else:
        record["fell_back"] = bool(record["fell_back"] or rendered["fell_back"])
    record["ledger_prompt_file"] = rendered["prompt_file"]
    record["ledger_prompt_hash"] = rendered["prompt_hash"]
    record["branch"] = rendered["branch"]
    record["source_count"] = len(rendered["source_rows"])
    record["sources_json"] = json.dumps(
        [{"type": row["type"], "url": row["url"], "title": row["title"]} for row in rendered["source_rows"]]
    )
    record["tape_json"] = json.dumps(_tape_summary(dossier))
    if rendered["llm_note"]:
        record["llm_note"] = rendered["llm_note"]
    if rendered["blocked"]:
        record["failure_reason"] = rendered["blocked"]
        return _finish(provenance, record, dry_run=config.dry_run)

    write_pair(config.output_dir, rendered)
    problems = validate_report_dir(ledger_root, config.output_dir / rendered["rel_dir"])
    problems.extend(
        validate_scope(
            ledger_root,
            config.output_dir,
            rendered["branch"],
            [rendered["rel_report"], rendered["rel_sources"]],
        )
    )
    if problems:
        record["local_validation"] = "fail"
        record["failure_reason"] = "; ".join(problems)[:500]
        return _finish(provenance, record, dry_run=config.dry_run)

    record["local_validation"] = "pass"
    if config.dry_run:
        record["ci_outcome"] = "not_run"
        record["merge_outcome"] = "dry_run"
        return _finish(provenance, record, dry_run=True)

    if not config.token:
        record["ci_outcome"] = "not_submitted"
        record["merge_outcome"] = "missing_token"
        record["failure_reason"] = "missing_ledger_token"
        return _finish(provenance, record, dry_run=False)

    try:
        submitted = _submit(config, persona, rendered)
    except Exception as exc:
        record["failure_reason"] = f"submit: {type(exc).__name__}: {exc}"[:500]
        record["merge_outcome"] = "not_submitted"
        return _finish(provenance, record, dry_run=False)
    record["pr_url"] = submitted.pr_url
    record["pr_number"] = "" if submitted.pr_number is None else str(submitted.pr_number)
    record["ci_outcome"] = "pending" if submitted.pr_url else "not_submitted"
    record["merge_outcome"] = submitted.merge_outcome
    if submitted.detail and submitted.merge_outcome not in {"auto_merge_requested", "merged"}:
        record["failure_reason"] = submitted.detail[:500]
    return _finish(provenance, record, dry_run=False)


def _submit(config: WakeConfig, persona: Persona, rendered: dict) -> SubmitResult:
    scratch = fresh_submit_dir()
    clone_ledger(scratch, config.ledger_repo, config.ledger_ref, config.token)
    target = scratch / rendered["rel_dir"]
    target.mkdir(parents=True, exist_ok=True)
    (target / rendered["report_name"]).write_text(rendered["report"], encoding="utf-8")
    (target / rendered["sources_name"]).write_text(rendered["sources_text"], encoding="utf-8")
    problems = validate_report_dir(scratch, target)
    if problems:
        return SubmitResult("", None, "not_submitted", "; ".join(problems)[:400])
    message = f"sentiment: {rendered['sources']['ticker']} {rendered['sources']['date']} ({persona.github_username})"
    pushed = commit_and_push(
        scratch,
        repo=config.ledger_repo,
        branch=rendered["branch"],
        message=message,
        author_name=persona.author_name,
        author_email=persona.author_email,
        token=config.token,
        paths=[rendered["rel_report"], rendered["rel_sources"]],
    )
    if pushed and pushed != "branch-exists":
        return SubmitResult("", None, "not_submitted", pushed)
    title = f"sentiment: {rendered['sources']['ticker']} {rendered['sources']['date']}"
    body = (
        f"{persona.pr_line}\n\n"
        f"Contributor: {persona.github_username}\n"
        f"Focus: {rendered['focus']}\n"
        f"Branch: {rendered['branch']}\n"
    )
    number, url, error = open_pr(config.ledger_repo, rendered["branch"], title, body, config.token)
    if not number:
        return SubmitResult(url, None, "not_submitted", error or pushed)
    outcome, detail = request_auto_merge(config.ledger_repo, number, config.token, config.merge_mode)
    return SubmitResult(url, number, outcome, detail)


def _slots_for(persona: Persona, config: WakeConfig, session: str) -> list[tuple[Slot, datetime]]:
    if config.all_personas or config.force:
        slot = persona.slots[0]
        target = datetime.fromisoformat(f"{session}T{slot.et}:00").replace(tzinfo=timezone.utc)
        return [(slot, target)]
    day = date.fromisoformat(session)
    return passed_slots(persona, config.now, day)


def _is_dry(value: object) -> bool:
    return value in {True, "true", "True", "1", 1}


def _skip(
    provenance: Provenance,
    persona_id: str,
    session: str,
    slot_id: str,
    force: bool,
    *,
    dry_run: bool = False,
) -> bool:
    """Skip a slot that already has an attempt for this day and slot.

    A repeated or overlapping wake must not file the same slot twice.
    A dry-run row does not block the later live wake. ``--force`` files anyway.
    """
    if force:
        return False
    row = provenance.latest().get(f"{persona_id}|{session}|{slot_id}")
    if not row:
        return False
    if _is_dry(row.get("dry_run")) and not dry_run:
        return False
    return True


def _day_fully_missed(provenance: Provenance, day: date) -> bool:
    """True when the log has no live attempt for this session date."""
    key = day.isoformat()
    for row in provenance.latest().values():
        if str(row.get("session_date") or "") != key:
            continue
        if _is_dry(row.get("dry_run")):
            continue
        return False
    return True


def _sessions_for(config: WakeConfig, provenance: Provenance) -> list[date]:
    """Trading days this wake may file.

    The current weekday, plus the previous trading day when that day has no
    live attempt. Dates older than that are not filed. The ledger accepts a
    real ``YYYY-MM-DD`` on the branch, and this list never goes past the
    previous weekday.
    """
    today = session_date(config.now)
    if config.session is not None:
        if ledger_accepts_report_date(config.session, today=today):
            return [config.session]
        print(f"session {config.session.isoformat()} is not a trading day this wake can file")
        return []
    if config.all_personas or config.force:
        return [today] if is_weekday(today) else []
    sessions: list[date] = []
    if is_weekday(today):
        sessions.append(today)
    previous = previous_trading_day(today)
    if (
        previous not in sessions
        and _day_fully_missed(provenance, previous)
        and ledger_accepts_report_date(previous, today=today)
    ):
        sessions.append(previous)
        print(f"catch-up: {previous.isoformat()} has no attempts, filing slots that have already passed")
    return sessions


def backfill(config: WakeConfig, provenance: Provenance) -> None:
    if config.dry_run or not config.token or not config.backfill:
        return
    for row in provenance.open_for_backfill():
        number = row.get("pr_number")
        if not number and row.get("pr_url"):
            tail = str(row["pr_url"]).rstrip("/").rsplit("/", 1)[-1]
            number = tail if tail.isdigit() else ""
        if not number:
            continue
        outcome, detail = advance_open_pull_request(
            config.ledger_repo,
            int(number),
            config.token,
            config.merge_mode,
            prior_outcome=str(row.get("merge_outcome") or ""),
        )
        if outcome == row.get("merge_outcome") and (not detail or detail == row.get("failure_reason")):
            continue
        ci = "pending"
        if outcome in {"merged", "closed_unmerged"}:
            ci = "success" if outcome == "merged" else row.get("ci_outcome") or "unknown"
        elif outcome == "checks_failed":
            ci = "failure"
        elif outcome in {"checks_pending", "branch_updated"}:
            ci = "pending"
        elif outcome == "merged":
            ci = "success"
        updated_sha = branch_update_sha(detail) if outcome == "branch_updated" else ""
        status = {
            "record_type": "status",
            "attempt_id": row["attempt_id"],
            "timestamp": now_stamp(),
            "pr_number": str(number),
            "pr_url": row.get("pr_url") or "",
            "ci_outcome": ci,
            "merge_outcome": outcome,
            "failure_reason": "" if outcome in {"merged", "checks_pending", "auto_merge_requested"} else detail[:500],
        }
        if updated_sha:
            status["branch_update_sha"] = updated_sha
        provenance.append(status)
        print(f"backfill {row['attempt_id']} ci={ci} merge={outcome}")


def run_wake(config: WakeConfig, *, gather_fn=gather) -> list[dict]:
    load_dotenv(ROOT / ".env")
    personas = load_personas(config.personas_dir)
    if config.persona_id:
        personas = [item for item in personas if item.id == config.persona_id or item.github_username == config.persona_id]
        if not personas:
            raise SystemExit(f"unknown persona {config.persona_id}")
    if config.ledger_root is None:
        cache = ROOT / ".cache" / "ledger"
        if not (cache / "scripts" / "validate_report.py").is_file():
            clone_public(cache, config.ledger_ref)
        ledger_root = cache
    else:
        ledger_root = config.ledger_root
    universe = load_universe(ledger_root)
    provenance = Provenance(config.provenance_path)
    if not config.token and not config.dry_run:
        print(MISSING_TOKEN)
    backfill(config, provenance)
    requested = config.provider_name
    groq_key = "set" if os.environ.get("GROQ_API_KEY", "").strip() else "missing"
    cursor_key = "set" if os.environ.get("CURSOR_API_KEY", "").strip() else "missing"
    print(f"providers: requested={requested} groq_key={groq_key} cursor_key={cursor_key} cursor_bin={cursor_binary() or 'missing'}")
    if requested == "groq" and select_provider("groq").name == "rule":
        print("GROQ_API_KEY is not set. Reports will use the no-key writer on fetched sources.")
    results = []
    planned: list[tuple[Persona, Slot, datetime, str]] = []
    try:
        for session_day in _sessions_for(config, provenance):
            session = session_day.isoformat()
            for persona in personas:
                try:
                    slots = _slots_for(persona, config, session)
                except Exception as exc:
                    print(f"{persona.id} schedule failed: {exc}", file=sys.stderr)
                    continue
                for slot, target in slots:
                    if _skip(provenance, persona.id, session, slot.id, config.force, dry_run=config.dry_run):
                        print(f"{persona.id} {slot.id} already recorded for {session}")
                        continue
                    planned.append((persona, slot, target, session))
        # Earlier jittered times go out first, so a catch-up keeps the day's order.
        planned.sort(key=lambda item: (item[2], item[0].id, item[1].id))
        avoids: dict[tuple[str, str], set[str]] = {}
        for persona, slot, target, session in planned:
            avoid = avoids.setdefault((persona.id, session), set())
            try:
                results.append(
                    run_slot(
                        config=config,
                        persona=persona,
                        slot=slot,
                        scheduled_for=target,
                        session=session,
                        universe=universe,
                        ledger_root=ledger_root,
                        provenance=provenance,
                        provider=None,
                        requested=requested,
                        gather_fn=gather_fn,
                        avoid=avoid,
                    )
                )
            except Exception as exc:
                traceback.print_exc()
                record = {
                    "record_type": "attempt",
                    "attempt_id": f"{persona.id}|{session}|{slot.id}",
                    "timestamp": now_stamp(),
                    "persona_id": persona.id,
                    "archetype": persona.archetype,
                    "is_ai_agent": True,
                    "contributor_kind": "ai_agent_persona",
                    "slot": slot.id,
                    "session_date": session,
                    "local_validation": "fail",
                    "failure_reason": f"{type(exc).__name__}: {exc}"[:500],
                    "merge_outcome": "not_submitted",
                    "ci_outcome": "not_run",
                    "dry_run": config.dry_run,
                    "slot_status": "fail",
                    "disclosure": DISCLOSURE,
                    "assigned_provider": assigned_provider_name(persona, requested),
                    "provider": assigned_provider_name(persona, requested),
                    "model": persona.groq_model,
                    "assigned_model": persona.groq_model,
                    "prompt_version": persona.prompt_version,
                }
                provenance.append(record)
                results.append(record)
                print(f"{persona.id} {slot.id} failed and the wake continued: {exc}")
    finally:
        backfill(config, provenance)
    return results


def clone_public(dest: Path, ref: str) -> None:
    import subprocess

    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        return
    result = subprocess.run(
        ["git", "clone", "--depth", "1", "--branch", ref, "https://github.com/rahiakil/agents-unite.git", str(dest)],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr[-400:] or "ledger clone failed")


def build_config(args: argparse.Namespace) -> WakeConfig:
    now = datetime.now(timezone.utc)
    if args.now:
        text = args.now.replace("Z", "+00:00")
        now = datetime.fromisoformat(text)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
    session = date.fromisoformat(args.date) if args.date else None
    ledger_root = Path(args.ledger_root).resolve() if args.ledger_root else None
    if ledger_root is None and os.environ.get("LEDGER_ROOT"):
        ledger_root = Path(os.environ["LEDGER_ROOT"]).resolve()
    return WakeConfig(
        now=now,
        dry_run=bool(args.dry_run),
        all_personas=bool(args.all_personas),
        persona_id=args.persona or "",
        session=session,
        lookback_minutes=int(args.lookback),
        output_dir=Path(args.output).resolve(),
        provenance_path=Path(args.provenance).resolve(),
        ledger_root=ledger_root,
        personas_dir=Path(args.personas).resolve() if args.personas else None,
        ledger_repo=os.environ.get("LEDGER_REPO", "rahiakil/agents-unite"),
        ledger_ref=os.environ.get("LEDGER_REF", "main"),
        provider_name=(args.provider or default_provider_name()),
        merge_mode=os.environ.get("HARNESS_MERGE", "auto"),
        token=os.environ.get("LEDGER_TOKEN", ""),
        force=bool(args.force),
        backfill=not bool(args.no_backfill),
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run due persona contributors against the Agents Unite ledger.")
    parser.add_argument("--dry-run", action="store_true", help="Fetch, render, and validate. Do not push.")
    parser.add_argument("--all-personas", action="store_true", help="Run the first slot of every persona.")
    parser.add_argument("--persona", default="", help="Limit the wake to one persona id.")
    parser.add_argument("--date", default="", help="Session date YYYY-MM-DD. Defaults to the New York date.")
    parser.add_argument("--now", default="", help="Override the clock, ISO-8601 UTC.")
    parser.add_argument(
        "--lookback",
        default="40",
        help="Kept so older commands still parse. A wake files every passed slot and does not use this window.",
    )
    parser.add_argument("--output", default="out/wake", help="Where rendered reports are written.")
    parser.add_argument("--provenance", default="data/attempts.jsonl")
    parser.add_argument("--ledger-root", default="", help="Existing agents-unite checkout. Skips cloning.")
    parser.add_argument("--personas", default="")
    parser.add_argument("--provider", default="", help="Provider name. Default: groq if GROQ_API_KEY is set, else auto.")
    parser.add_argument("--force", action="store_true", help="Run even if this slot already passed validation.")
    parser.add_argument("--no-backfill", action="store_true")
    if argv is None:
        argv = sys.argv[1:]
    if argv and argv[0] == "wake":
        argv = argv[1:]
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    load_dotenv(ROOT / ".env")
    args = parse_args(argv)
    config = build_config(args)
    started = time.time()
    try:
        results = run_wake(config)
    except Exception as exc:
        print(f"wake failed: {exc}", file=sys.stderr)
        return 1
    try:
        from harness import costs

        rows = costs.persona_rows(results, run_id=costs.default_run_id("persona-wake"),
                                  runtime_s=time.time() - started, dry_run=config.dry_run)
        print(f"cost log: {costs.write_run(rows)}")
    except Exception as exc:  # the cost log must never fail a wake
        print(f"cost log failed: {exc}", file=sys.stderr)
    if not results:
        print("No personas due.")
        return 0
    passed, failed = split_results(results)
    print(f"Wake finished: {len(passed)} passed, {len(failed)} failed.")
    return exit_code(config, results)


if __name__ == "__main__":
    raise SystemExit(main())
