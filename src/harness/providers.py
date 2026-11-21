"""Model providers.

Ported from agents-unite `sim/providers.py` (PR 37) and extended with
Anthropic (Claude), xAI (Grok), OpenAI, and Cursor CLI print mode. `rule`
needs no key. `auto` picks the first configured provider, then `rule`.

Cursor is not in the auto order. Two persona cards request it. If the CLI
is missing, the key is rejected, or the reply cannot be used, the caller
tries Groq and then `rule`.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Completion:
    provider: str
    model: str
    text: str
    prompt_tokens: int | None
    completion_tokens: int | None
    latency_ms: float
    cost_usd: float | None
    error: str
    fell_back: bool


class RuleProvider:
    name = "rule"
    model = "rule"

    def available(self) -> bool:
        return True

    def complete(self, system: str, user: str) -> Completion:
        return Completion(
            provider=self.name,
            model=self.model,
            text="",
            prompt_tokens=0,
            completion_tokens=0,
            latency_ms=0.0,
            cost_usd=0.0,
            error="",
            fell_back=False,
        )


def _env_first(*names: str, default: str = "") -> str:
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return default


def _cost(model: str, prompt_tokens: int | None, completion_tokens: int | None) -> float | None:
    """Only free models get a numeric zero. Other prices stay unknown."""
    if model in {"rule", "openrouter/free"} or model.endswith(":free"):
        return 0.0
    return None


def _error_detail(exc: Exception) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        try:
            body = exc.read().decode("utf-8", errors="replace")[:300]
        except Exception:
            body = ""
        return f"HTTP {exc.code}: {body or exc.reason}"
    return f"{type(exc).__name__}: {exc}"


def _failed(provider: str, model: str, started: float, exc: Exception) -> Completion:
    return Completion(
        provider=provider,
        model=model,
        text="",
        prompt_tokens=None,
        completion_tokens=None,
        latency_ms=(time.perf_counter() - started) * 1000.0,
        cost_usd=None,
        error=_error_detail(exc),
        fell_back=True,
    )


def _post_json(url: str, payload: dict, headers: dict[str, str], timeout: float = 60.0) -> tuple[dict, float]:
    started = time.perf_counter()
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = json.loads(response.read().decode("utf-8"))
    return body, (time.perf_counter() - started) * 1000.0


class _OpenAIChat:
    name = ""
    default_model = ""
    env_key = ""
    model_envs: tuple[str, ...] = ()
    url = ""

    def model_name(self) -> str:
        override = getattr(self, "_model_override", "")
        if override:
            return str(override)
        return _env_first(*self.model_envs, default=self.default_model)

    def extra_payload(self) -> dict:
        return {}

    def available(self) -> bool:
        return bool(os.environ.get(self.env_key))

    def complete(self, system: str, user: str) -> Completion:
        model = self.model_name()
        started = time.perf_counter()
        payload = {
            "model": model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        payload.update(self.extra_payload())
        try:
            body, latency = _post_json(
                self.url,
                payload,
                {"Authorization": f"Bearer {os.environ[self.env_key]}"},
            )
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, KeyError) as exc:
            return _failed(self.name, model, started, exc)
        message = ((body.get("choices") or [{}])[0].get("message") or {})
        choice = _message_text(message)
        usage = body.get("usage") or {}
        prompt_tokens = usage.get("prompt_tokens")
        completion_tokens = usage.get("completion_tokens")
        return Completion(
            provider=self.name,
            model=model,
            text=str(choice),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            latency_ms=latency,
            cost_usd=_cost(model, prompt_tokens, completion_tokens),
            error="",
            fell_back=False,
        )


class OpenAIProvider(_OpenAIChat):
    name = "openai"
    default_model = "gpt-4o-mini"
    env_key = "OPENAI_API_KEY"
    model_envs = ("OPENAI_MODEL", "HARNESS_OPENAI_MODEL")
    url = "https://api.openai.com/v1/chat/completions"


class XAIProvider(_OpenAIChat):
    name = "xai"
    default_model = "grok-3-mini"
    env_key = "XAI_API_KEY"
    model_envs = ("XAI_MODEL", "HARNESS_XAI_MODEL")
    url = "https://api.x.ai/v1/chat/completions"


class OpenRouterProvider(_OpenAIChat):
    name = "openrouter"
    default_model = "openrouter/free"
    env_key = "OPENROUTER_API_KEY"
    model_envs = ("OPENROUTER_MODEL", "HARNESS_OPENROUTER_MODEL", "SIM_OPENROUTER_MODEL")
    url = "https://openrouter.ai/api/v1/chat/completions"


# Free chat models on Groq as of 2026-11-21 (rate-limit card).
# llama-3.1-8b-instant and llama-3.3-70b-versatile left the free tier on
# 2026-08-16. A persona or GROQ_MODEL can still name them for a plan that
# serves them. The shipped personas use the three ids below.
GROQ_FREE_MODELS = (
    "openai/gpt-oss-20b",
    "openai/gpt-oss-120b",
    "qwen/qwen3.8-27b",
)


def _message_text(message: dict) -> str:
    content = message.get("content") if isinstance(message, dict) else ""
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict):
                parts.append(str(part.get("text") or ""))
            else:
                parts.append(str(part))
        content = "".join(parts)
    return str(content or "")


class GroqProvider(_OpenAIChat):
    name = "groq"
    default_model = "openai/gpt-oss-20b"
    env_key = "GROQ_API_KEY"
    model_envs = ("GROQ_MODEL", "HARNESS_GROQ_MODEL", "SIM_GROQ_MODEL")
    url = "https://api.groq.com/openai/v1/chat/completions"

    def extra_payload(self) -> dict:
        # Ask for JSON so the report parser can read sentiment_score and read.
        return {"response_format": {"type": "json_object"}, "max_tokens": 700}


class HuggingFaceProvider(_OpenAIChat):
    name = "huggingface"
    default_model = "Qwen/Qwen2.5-7B-Instruct"
    env_key = "HF_TOKEN"
    model_envs = ("HF_MODEL", "HARNESS_HF_MODEL", "SIM_HF_MODEL")
    url = "https://router.huggingface.co/v1/chat/completions"

    def available(self) -> bool:
        return bool(os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_API_KEY"))

    def complete(self, system: str, user: str) -> Completion:
        token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_API_KEY") or ""
        os.environ["HF_TOKEN"] = token
        return super().complete(system, user)


class AnthropicProvider:
    name = "anthropic"
    default_model = "claude-3-5-haiku-latest"

    def model_name(self) -> str:
        return _env_first("ANTHROPIC_MODEL", "HARNESS_ANTHROPIC_MODEL", default=self.default_model)

    def available(self) -> bool:
        return bool(os.environ.get("ANTHROPIC_API_KEY"))

    def complete(self, system: str, user: str) -> Completion:
        model = self.model_name()
        started = time.perf_counter()
        try:
            body, latency = _post_json(
                "https://api.anthropic.com/v1/messages",
                {
                    "model": model,
                    "max_tokens": 1200,
                    "temperature": 0,
                    "system": system,
                    "messages": [{"role": "user", "content": user}],
                },
                {
                    "x-api-key": os.environ["ANTHROPIC_API_KEY"],
                    "anthropic-version": "2023-06-01",
                },
            )
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, KeyError) as exc:
            return _failed(self.name, model, started, exc)
        parts = body.get("content") or [{}]
        text = str(parts[0].get("text") or "") if parts else ""
        usage = body.get("usage") or {}
        return Completion(
            provider=self.name,
            model=model,
            text=text,
            prompt_tokens=usage.get("input_tokens"),
            completion_tokens=usage.get("output_tokens"),
            latency_ms=latency,
            cost_usd=_cost(model, None, None),
            error="",
            fell_back=False,
        )


class GeminiProvider:
    name = "gemini"
    default_model = "gemini-2.0-flash"

    def model_name(self) -> str:
        return _env_first("GEMINI_MODEL", "HARNESS_GEMINI_MODEL", "SIM_GEMINI_MODEL", default=self.default_model)

    def available(self) -> bool:
        return bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))

    def complete(self, system: str, user: str) -> Completion:
        model = self.model_name()
        key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or ""
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
        started = time.perf_counter()
        try:
            body, latency = _post_json(
                url,
                {
                    "contents": [{"parts": [{"text": system + "\n\n" + user}]}],
                    "generationConfig": {"temperature": 0},
                },
                {},
            )
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            return _failed(self.name, model, started, exc)
        parts = (((body.get("candidates") or [{}])[0].get("content") or {}).get("parts") or [{}])
        text = str(parts[0].get("text") or "") if parts else ""
        usage = body.get("usageMetadata") or {}
        return Completion(
            provider=self.name,
            model=model,
            text=text,
            prompt_tokens=usage.get("promptTokenCount"),
            completion_tokens=usage.get("candidatesTokenCount"),
            latency_ms=latency,
            cost_usd=_cost(model, None, None),
            error="",
            fell_back=False,
        )


class OllamaProvider:
    name = "ollama"

    def model_name(self) -> str:
        return _env_first("OLLAMA_MODEL", "HARNESS_OLLAMA_MODEL", "SIM_OLLAMA_MODEL", default="llama3.2")

    def host(self) -> str:
        return os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")

    def enabled(self) -> bool:
        if os.environ.get("HARNESS_ENABLE_OLLAMA") == "1":
            return True
        if os.environ.get("SIM_ENABLE_OLLAMA") == "1":
            return True
        return bool(os.environ.get("OLLAMA_HOST"))

    def available(self) -> bool:
        if not self.enabled():
            return False
        try:
            with urllib.request.urlopen(self.host() + "/api/tags", timeout=2) as response:
                return response.status == 200
        except (urllib.error.URLError, TimeoutError):
            return False

    def complete(self, system: str, user: str) -> Completion:
        model = self.model_name()
        started = time.perf_counter()
        try:
            body, latency = _post_json(
                self.host() + "/api/chat",
                {
                    "model": model,
                    "stream": False,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                },
                {},
            )
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            failed = _failed(self.name, model, started, exc)
            failed.cost_usd = 0.0
            return failed
        text = str((body.get("message") or {}).get("content") or "")
        return Completion(
            provider=self.name,
            model=model,
            text=text,
            prompt_tokens=None,
            completion_tokens=None,
            latency_ms=latency,
            cost_usd=0.0,
            error="",
            fell_back=False,
        )


def cursor_binary() -> str | None:
    """Resolve cursor-agent, then the newer `agent` binary."""
    override = os.environ.get("CURSOR_AGENT_BIN", "").strip()
    if override:
        path = Path(override)
        if path.is_file():
            return str(path)
        return shutil.which(override)
    for name in ("cursor-agent", "agent"):
        found = shutil.which(name)
        if found:
            return found
    home_bin = Path.home() / ".cursor" / "bin"
    for name in ("cursor-agent", "agent"):
        candidate = home_bin / name
        if candidate.is_file():
            return str(candidate)
    return None


def _redact(text: str) -> str:
    secret = os.environ.get("CURSOR_API_KEY") or ""
    if secret:
        text = text.replace(secret, "[redacted]")
    return text


class CursorProvider:
    """Headless Cursor CLI. Print mode, ask mode, no --force, key only via env."""

    name = "cursor"

    def __init__(self) -> None:
        self._model_override = ""
        self.runner = None

    def model_name(self) -> str:
        override = getattr(self, "_model_override", "")
        if override:
            return str(override)
        return _env_first("CURSOR_MODEL", "HARNESS_CURSOR_MODEL", default="")

    def available(self) -> bool:
        return bool(os.environ.get("CURSOR_API_KEY"))

    def complete(self, system: str, user: str) -> Completion:
        model = self.model_name()
        logged = model or "default"
        started = time.perf_counter()
        if not os.environ.get("CURSOR_API_KEY"):
            return Completion(
                provider=self.name,
                model=logged,
                text="",
                prompt_tokens=None,
                completion_tokens=None,
                latency_ms=0.0,
                cost_usd=None,
                error="CURSOR_API_KEY is not set",
                fell_back=True,
            )
        binary = cursor_binary()
        if self.runner is None and not binary:
            return Completion(
                provider=self.name,
                model=logged,
                text="",
                prompt_tokens=None,
                completion_tokens=None,
                latency_ms=0.0,
                cost_usd=None,
                error="cursor-agent is not installed",
                fell_back=True,
            )
        prompt = system + "\n\n" + user
        workspace = tempfile.mkdtemp(prefix="cursor-persona-")
        command = [
            binary or "cursor-agent",
            "--print",
            "--output-format",
            "text",
            "--mode",
            "ask",
            "--trust",
            "--workspace",
            workspace,
        ]
        if model:
            command.extend(["--model", model])
        command.append(prompt)
        timeout = _cursor_timeout()
        try:
            if self.runner is not None:
                result = self.runner(command, os.environ.copy(), timeout)
            else:
                result = subprocess.run(
                    command,
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    env=os.environ.copy(),
                )
        except (OSError, subprocess.TimeoutExpired) as exc:
            failed = _failed(self.name, logged, started, exc)
            failed.error = _redact(failed.error)
            return failed
        finally:
            shutil.rmtree(workspace, ignore_errors=True)
        latency = (time.perf_counter() - started) * 1000.0
        if result.returncode != 0:
            detail = _redact((result.stderr or result.stdout or "cursor-agent failed").strip())
            return Completion(
                provider=self.name,
                model=logged,
                text="",
                prompt_tokens=None,
                completion_tokens=None,
                latency_ms=latency,
                cost_usd=None,
                error=detail[:500],
                fell_back=True,
            )
        return Completion(
            provider=self.name,
            model=logged,
            text=result.stdout or "",
            prompt_tokens=None,
            completion_tokens=None,
            latency_ms=latency,
            cost_usd=None,
            error="",
            fell_back=False,
        )


def _cursor_timeout() -> float:
    raw = os.environ.get("CURSOR_AGENT_TIMEOUT", "").strip()
    if not raw:
        return 180.0
    try:
        return max(1.0, float(raw))
    except ValueError:
        return 180.0


class LlamaCppProvider:
    name = "llamacpp"

    def available(self) -> bool:
        model = os.environ.get("LLAMA_CPP_MODEL")
        binary = os.environ.get("LLAMA_CPP_BIN", "llama-cli")
        return bool(model and Path_exists(model) and shutil.which(binary))

    def complete(self, system: str, user: str) -> Completion:
        model = os.environ.get("LLAMA_CPP_MODEL", "")
        binary = os.environ.get("LLAMA_CPP_BIN", "llama-cli")
        started = time.perf_counter()
        try:
            result = subprocess.run(
                [binary, "-m", model, "-p", system + "\n\n" + user, "-n", "256", "--no-display-prompt"],
                check=False,
                capture_output=True,
                text=True,
                timeout=120,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            failed = _failed(self.name, model, started, exc)
            failed.cost_usd = 0.0
            return failed
        if result.returncode != 0:
            return Completion(
                provider=self.name,
                model=model,
                text="",
                prompt_tokens=None,
                completion_tokens=None,
                latency_ms=(time.perf_counter() - started) * 1000.0,
                cost_usd=0.0,
                error=result.stderr[-400:],
                fell_back=True,
            )
        return Completion(
            provider=self.name,
            model=model,
            text=result.stdout,
            prompt_tokens=None,
            completion_tokens=None,
            latency_ms=(time.perf_counter() - started) * 1000.0,
            cost_usd=0.0,
            error="",
            fell_back=False,
        )


def Path_exists(path: str) -> bool:
    from pathlib import Path

    return Path(path).is_file()


PROVIDERS = {
    "rule": RuleProvider,
    "cursor": CursorProvider,
    "anthropic": AnthropicProvider,
    "xai": XAIProvider,
    "openai": OpenAIProvider,
    "openrouter": OpenRouterProvider,
    "groq": GroqProvider,
    "huggingface": HuggingFaceProvider,
    "gemini": GeminiProvider,
    "ollama": OllamaProvider,
    "llamacpp": LlamaCppProvider,
}
# Groq is the default when its key is set. xAI stays available, after the
# other keyed providers, so a stray XAI_API_KEY does not take the wake.
AUTO_ORDER = (
    GroqProvider,
    AnthropicProvider,
    OpenAIProvider,
    OpenRouterProvider,
    GeminiProvider,
    HuggingFaceProvider,
    XAIProvider,
    OllamaProvider,
    LlamaCppProvider,
)


def select_provider(name: str):
    if name == "auto":
        for cls in AUTO_ORDER:
            provider = cls()
            if provider.available():
                return provider
        return RuleProvider()
    cls = PROVIDERS.get(name)
    if cls is None:
        raise ValueError(f"unknown provider {name!r}")
    provider = cls()
    if name != "rule" and not provider.available():
        return RuleProvider()
    return provider


def choose_completion(providers, call, accept) -> tuple[Completion, str]:
    """Use the first provider whose reply `accept` allows.

    A provider named `rule` is the terminal writer and is not judged.
    Notes list why earlier providers were skipped. The caller logs
    `completion.provider`, which is the one that actually answered.
    """
    notes: list[str] = []
    for provider in providers:
        if provider.name == "rule":
            return call(provider), "; ".join(notes)
        if not provider.available():
            notes.append(f"{provider.name}: unavailable")
            continue
        completion = call(provider)
        ok, reason = accept(completion)
        if ok:
            return completion, "; ".join(notes)
        notes.append(f"{provider.name}: {reason or 'unusable reply'}")
    return RuleProvider().complete("", ""), "; ".join(notes)


def parse_model_score(text: str) -> float | None:
    """Pull a sentiment_score from a model reply. None when the reply is unusable."""
    payload = parse_model_json(text)
    if not isinstance(payload, dict) or "sentiment_score" not in payload:
        return None
    try:
        score = float(payload["sentiment_score"])
    except (TypeError, ValueError):
        return None
    if -1.0 <= score <= 1.0:
        return score
    return None


def parse_model_json(text: str) -> dict | None:
    if not text:
        return None
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        payload = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None
