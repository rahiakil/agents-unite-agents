"""Load persona cards."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from harness.yamlio import parse_yaml

ROOT = Path(__file__).resolve().parents[2]
PERSONAS_DIR = ROOT / "personas"
SLUG_RE = re.compile(r"^[a-zA-Z0-9_-]+$")
KNOWN_PROVIDERS = {
    "",
    "cursor",
    "groq",
    "rule",
    "xai",
    "openai",
    "anthropic",
    "openrouter",
    "gemini",
    "huggingface",
    "ollama",
    "llamacpp",
    "auto",
}


@dataclass(frozen=True)
class Slot:
    id: str
    et: str
    jitter_minutes: int


@dataclass(frozen=True)
class Voice:
    notes: str
    openers: tuple[str, ...]
    reads: tuple[str, ...]
    closers: tuple[str, ...]
    sha256: str


@dataclass(frozen=True)
class Persona:
    id: str
    archetype: str
    display_name: str
    author_name: str
    author_email: str
    github_username: str
    background: str
    style: str
    risk_tolerance: str
    score_style: str
    preferred_sectors: tuple[str, ...]
    watchlist: tuple[str, ...]
    focus_modes: tuple[str, ...]
    search: tuple[str, ...]
    slots: tuple[Slot, ...]
    pr_line: str
    research_prompt_path: Path
    research_prompt: str
    prompt_version: str
    prompt_sha256: str
    voice: Voice
    groq_model: str
    provider: str
    cursor_model: str

    @property
    def hash8(self) -> str:
        return hashlib.sha256(self.github_username.lower().encode("utf-8")).hexdigest()[:8]

    @property
    def hash12(self) -> str:
        return hashlib.sha256(self.github_username.lower().encode("utf-8")).hexdigest()[:12]


def _voice(path: Path) -> Voice:
    text = path.read_text(encoding="utf-8")
    sections: dict[str, list[str]] = {}
    current = "voice"
    for line in text.splitlines():
        if line.startswith("# "):
            current = line[2:].strip().lower()
            sections.setdefault(current, [])
            continue
        sections.setdefault(current, []).append(line)

    def lines(name: str) -> tuple[str, ...]:
        return tuple(item.strip() for item in sections.get(name, []) if item.strip())

    notes = "\n".join(sections.get("voice", [])).strip()
    return Voice(
        notes=notes,
        openers=lines("openers"),
        reads=lines("reads"),
        closers=lines("closers"),
        sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
    )


def _prompt_meta(path: Path) -> tuple[str, str, str]:
    text = path.read_text(encoding="utf-8")
    version = "unknown"
    for line in text.splitlines():
        if line.lower().startswith("prompt-version:"):
            version = line.split(":", 1)[1].strip()
            break
    return text, version, hashlib.sha256(text.encode("utf-8")).hexdigest()


def _as_tuple(value, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{field} must be a non-empty list")
    return tuple(str(item) for item in value)


def load_persona(path: Path) -> Persona:
    raw = parse_yaml(path.read_text(encoding="utf-8"))
    username = str(raw.get("github_username") or raw.get("id") or "").strip()
    if not SLUG_RE.match(username):
        raise ValueError(f"{path.name}: github_username must match [A-Za-z0-9_-]+, got {username!r}")
    if username.lower().startswith("sim-"):
        raise ValueError(
            f"{path.name}: github_username {username!r} starts with sim-. "
            "The ledger blocks that prefix on main."
        )
    slots = []
    for item in raw.get("slots") or []:
        if not isinstance(item, dict):
            raise ValueError(f"{path.name}: slot must be a map")
        hour, _, minute = str(item["et"]).partition(":")
        if not (hour.isdigit() and minute.isdigit()):
            raise ValueError(f"{path.name}: bad slot time {item.get('et')!r}")
        slots.append(
            Slot(
                id=str(item["id"]),
                et=f"{int(hour):02d}:{int(minute):02d}",
                jitter_minutes=int(item.get("jitter_minutes") or 10),
            )
        )
    if not slots:
        raise ValueError(f"{path.name}: at least one slot is required")
    provider = str(raw.get("provider") or "").strip()
    if provider not in KNOWN_PROVIDERS:
        raise ValueError(f"{path.name}: unknown provider {provider!r}")
    research_path = (path.parent.parent / str(raw["research_prompt"])).resolve()
    voice_path = (path.parent.parent / str(raw["voice_file"])).resolve()
    prompt_text, version, prompt_sha = _prompt_meta(research_path)
    return Persona(
        id=str(raw["id"]),
        archetype=str(raw["archetype"]),
        display_name=str(raw["display_name"]),
        author_name=str(raw.get("author_name") or raw["display_name"]),
        author_email=str(raw["author_email"]),
        github_username=username,
        background=str(raw.get("background") or ""),
        style=str(raw.get("style") or ""),
        risk_tolerance=str(raw.get("risk_tolerance") or "medium"),
        score_style=str(raw.get("score_style") or "dampened"),
        preferred_sectors=_as_tuple(raw.get("preferred_sectors") or ["Technology"], "preferred_sectors"),
        watchlist=_as_tuple(raw.get("watchlist"), "watchlist"),
        focus_modes=_as_tuple(raw.get("focus_modes"), "focus_modes"),
        search=_as_tuple(raw.get("search"), "search"),
        slots=tuple(slots),
        pr_line=str(raw.get("pr_line") or "Daily research note."),
        research_prompt_path=research_path,
        research_prompt=prompt_text,
        prompt_version=version,
        prompt_sha256=prompt_sha,
        voice=_voice(voice_path),
        groq_model=str(raw.get("groq_model") or ""),
        provider=provider,
        cursor_model=str(raw.get("cursor_model") or ""),
    )


def load_personas(directory: Path | None = None) -> list[Persona]:
    folder = directory or PERSONAS_DIR
    personas = []
    for path in sorted(folder.glob("*.yaml")):
        if path.name.startswith("_") or path.name.lower().startswith("template"):
            continue
        personas.append(load_persona(path))
    if not personas:
        raise ValueError(f"no personas in {folder}")
    return personas
