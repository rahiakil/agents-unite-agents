import io
import json
import os
import stat
import subprocess
import tempfile
import unittest
import urllib.error
import urllib.request
from email.message import Message
from pathlib import Path

import support  # noqa: F401

from harness.providers import (
    GROQ_FREE_MODELS,
    USER_AGENT,
    Completion,
    CursorProvider,
    choose_completion,
    cursor_binary,
    select_provider,
)


class ProviderTests(unittest.TestCase):
    def setUp(self):
        self._saved = {
            key: os.environ.get(key)
            for key in (
                "GROQ_API_KEY",
                "GROQ_MODEL",
                "XAI_API_KEY",
                "OPENAI_API_KEY",
                "ANTHROPIC_API_KEY",
                "OPENROUTER_API_KEY",
                "GEMINI_API_KEY",
                "GOOGLE_API_KEY",
                "HF_TOKEN",
                "HUGGINGFACE_API_KEY",
                "HARNESS_ENABLE_OLLAMA",
                "SIM_ENABLE_OLLAMA",
                "OLLAMA_HOST",
                "LLAMA_CPP_MODEL",
                "CURSOR_API_KEY",
                "CURSOR_AGENT_BIN",
                "CURSOR_MODEL",
            )
        }
        for key in self._saved:
            os.environ.pop(key, None)

    def tearDown(self):
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_no_key_falls_back_to_rule(self):
        self.assertEqual(select_provider("auto").name, "rule")
        self.assertEqual(select_provider("groq").name, "rule")
        self.assertEqual(select_provider("xai").name, "rule")

    def test_groq_beats_xai_when_both_keys_exist(self):
        os.environ["GROQ_API_KEY"] = "test-groq"
        os.environ["XAI_API_KEY"] = "test-xai"
        self.assertEqual(select_provider("auto").name, "groq")
        self.assertEqual(select_provider("xai").name, "xai")

    def test_persona_model_override(self):
        os.environ["GROQ_API_KEY"] = "test-groq"
        os.environ["GROQ_MODEL"] = "openai/gpt-oss-20b"
        provider = select_provider("groq")
        provider._model_override = "qwen/qwen3.8-27b"
        self.assertEqual(provider.model_name(), "qwen/qwen3.8-27b")

    def test_free_model_ids(self):
        self.assertEqual(
            set(GROQ_FREE_MODELS),
            {"openai/gpt-oss-20b", "openai/gpt-oss-120b", "qwen/qwen3.8-27b"},
        )

    def test_unknown_provider(self):
        with self.assertRaises(ValueError):
            select_provider("not-a-provider")

    def test_groq_request_names_its_user_agent(self):
        os.environ["GROQ_API_KEY"] = "  test-groq  "
        seen = {"calls": 0}

        def fake_open(request, timeout=60):
            seen["calls"] += 1
            seen["ua"] = request.get_header("User-agent")
            seen["auth"] = request.get_header("Authorization")
            raise urllib.error.HTTPError(
                request.full_url,
                401,
                "no",
                Message(),
                io.BytesIO(b'{"error":"invalid"}'),
            )

        original = urllib.request.urlopen
        urllib.request.urlopen = fake_open
        try:
            result = select_provider("groq").complete("system", "user")
        finally:
            urllib.request.urlopen = original
        self.assertEqual(seen["calls"], 1)
        self.assertEqual(seen["ua"], USER_AGENT)
        self.assertEqual(seen["auth"], "Bearer test-groq")
        self.assertIn("401", result.error)
        self.assertTrue(result.fell_back)

    def _groq_script(self, responses):
        """Return a urlopen stand-in that records JSON payloads and pops responses."""
        seen = {"calls": 0, "payloads": []}

        def fake_open(request, timeout=60):
            seen["calls"] += 1
            seen["payloads"].append(json.loads(request.data.decode("utf-8")))
            status, body = responses.pop(0)
            if status != 200:
                raise urllib.error.HTTPError(
                    request.full_url,
                    status,
                    "no",
                    Message(),
                    io.BytesIO(body.encode("utf-8")),
                )
            return io.BytesIO(body.encode("utf-8"))

        return seen, fake_open

    def test_groq_recovers_failed_generation_without_a_second_call(self):
        os.environ["GROQ_API_KEY"] = "test-groq"
        os.environ["GROQ_MODEL"] = "openai/gpt-oss-20b"
        generation = '{"sentiment_score": 0.2, "read": "NVDA up on the session"}'
        error = json.dumps(
            {
                "error": {
                    "message": "Failed to validate JSON. Please adjust your prompt.",
                    "type": "invalid_request_error",
                    "code": "json_validate_failed",
                    "failed_generation": generation,
                }
            }
        )
        seen, fake_open = self._groq_script([(400, error)])
        original = urllib.request.urlopen
        urllib.request.urlopen = fake_open
        try:
            result = select_provider("groq").complete("system", "user")
        finally:
            urllib.request.urlopen = original
        self.assertEqual(seen["calls"], 1)
        self.assertEqual(seen["payloads"][0]["response_format"], {"type": "json_object"})
        self.assertEqual(seen["payloads"][0]["reasoning_effort"], "low")
        self.assertEqual(seen["payloads"][0]["max_tokens"], 1500)
        self.assertIn("sentiment_score", result.text)
        self.assertEqual(result.error, "")
        self.assertFalse(result.fell_back)

    def test_groq_retries_plain_text_when_failed_generation_is_unusable(self):
        os.environ["GROQ_API_KEY"] = "test-groq"
        os.environ["GROQ_MODEL"] = "openai/gpt-oss-120b"
        error = json.dumps(
            {
                "error": {
                    "message": "Failed to validate JSON.",
                    "code": "json_validate_failed",
                    "failed_generation": "not json at all",
                }
            }
        )
        reply = json.dumps(
            {
                "choices": [
                    {
                        "message": {
                            "content": 'Here is the object:\n{"sentiment_score": -0.4, "read": "soft tape"}'
                        }
                    }
                ]
            }
        )
        seen, fake_open = self._groq_script([(400, error), (200, reply)])
        original = urllib.request.urlopen
        urllib.request.urlopen = fake_open
        try:
            result = select_provider("groq").complete("system", "user")
        finally:
            urllib.request.urlopen = original
        self.assertEqual(seen["calls"], 2)
        self.assertNotIn("response_format", seen["payloads"][1])
        self.assertEqual(seen["payloads"][1]["reasoning_effort"], "low")
        self.assertIn("soft tape", result.text)
        self.assertEqual(result.error, "")
        self.assertFalse(result.fell_back)

    def test_groq_logs_failed_generation_when_both_attempts_fail(self):
        os.environ["GROQ_API_KEY"] = "test-groq"
        os.environ["GROQ_MODEL"] = "openai/gpt-oss-20b"
        snippet = "score was maybe high but the braces never closed"
        error = json.dumps(
            {
                "error": {
                    "message": "Failed to validate JSON.",
                    "code": "json_validate_failed",
                    "failed_generation": snippet,
                }
            }
        )
        seen, fake_open = self._groq_script([(400, error), (400, error)])
        original = urllib.request.urlopen
        urllib.request.urlopen = fake_open
        try:
            result = select_provider("groq").complete("system", "user")
        finally:
            urllib.request.urlopen = original
        self.assertEqual(seen["calls"], 2)
        self.assertTrue(result.fell_back)
        self.assertEqual(result.text, "")
        self.assertTrue(result.error.startswith("failed_generation: " + snippet))

    def test_qwen_payload_omits_reasoning_effort(self):
        os.environ["GROQ_API_KEY"] = "test-groq"
        os.environ["GROQ_MODEL"] = "qwen/qwen3.8-27b"
        reply = json.dumps(
            {"choices": [{"message": {"content": '{"sentiment_score": 0.0, "read": "unchanged"}'}}]}
        )
        seen, fake_open = self._groq_script([(200, reply)])
        original = urllib.request.urlopen
        urllib.request.urlopen = fake_open
        try:
            result = select_provider("groq").complete("system", "user")
        finally:
            urllib.request.urlopen = original
        self.assertEqual(seen["calls"], 1)
        self.assertNotIn("reasoning_effort", seen["payloads"][0])
        self.assertIn("unchanged", result.text)
        self.assertFalse(result.fell_back)

    def test_cursor_binary_finds_local_bin(self):
        with tempfile.TemporaryDirectory() as raw:
            home = Path(raw)
            binary = home / ".local" / "bin" / "cursor-agent"
            binary.parent.mkdir(parents=True)
            binary.write_text("#!/bin/sh\n", encoding="utf-8")
            self.assertEqual(cursor_binary(home), str(binary))

    def test_auto_does_not_select_cursor(self):
        os.environ["CURSOR_API_KEY"] = "test-cursor"
        self.assertEqual(select_provider("auto").name, "rule")
        self.assertEqual(select_provider("cursor").name, "cursor")
        os.environ["GROQ_API_KEY"] = "test-groq"
        self.assertEqual(select_provider("auto").name, "groq")

    def test_cursor_print_mode_keeps_the_key_off_argv(self):
        os.environ["CURSOR_API_KEY"] = "secret-cursor-key"
        captured = {}

        def runner(argv, env, timeout):
            captured["argv"] = argv
            captured["key"] = env.get("CURSOR_API_KEY")
            return subprocess.CompletedProcess(
                argv,
                0,
                stdout='{"sentiment_score": 0.1, "read": "flat session"}',
                stderr="",
            )

        provider = CursorProvider()
        provider.runner = runner
        result = provider.complete("system", "user payload")
        self.assertEqual(result.provider, "cursor")
        self.assertIn("flat session", result.text)
        self.assertEqual(result.error, "")
        self.assertIn("--print", captured["argv"])
        self.assertIn("--mode", captured["argv"])
        self.assertIn("ask", captured["argv"])
        self.assertNotIn("--force", captured["argv"])
        self.assertNotIn("--api-key", captured["argv"])
        self.assertNotIn("secret-cursor-key", captured["argv"])
        self.assertEqual(captured["key"], "secret-cursor-key")

    def test_cursor_failure_is_redacted_and_marked_fallback(self):
        os.environ["CURSOR_API_KEY"] = "secret-cursor-key"
        provider = CursorProvider()
        provider.runner = lambda argv, env, timeout: subprocess.CompletedProcess(
            argv,
            1,
            stdout="",
            stderr="rejected secret-cursor-key",
        )
        result = provider.complete("system", "user")
        self.assertTrue(result.fell_back)
        self.assertEqual(result.text, "")
        self.assertIn("rejected", result.error)
        self.assertNotIn("secret-cursor-key", result.error)

    def test_cursor_invalid_key_strips_ansi(self):
        os.environ["CURSOR_API_KEY"] = "secret-cursor-key"
        warning = (
            "\x1b[33mWarning: The provided API key is invalid. "
            "The API key was loaded from the CURSOR_API_KEY environment variable.\x1b[0m"
        )
        provider = CursorProvider()
        provider.runner = lambda argv, env, timeout: subprocess.CompletedProcess(
            argv,
            1,
            stdout="",
            stderr=warning,
        )
        result = provider.complete("system", "user")
        self.assertTrue(result.fell_back)
        self.assertNotIn("\x1b", result.error)
        self.assertIn("API key is invalid", result.error)
        self.assertIn("rejected CURSOR_API_KEY", result.error)

    def test_cursor_shell_binary_returns_text(self):
        with tempfile.TemporaryDirectory() as raw:
            binary = Path(raw) / "cursor-agent"
            binary.write_text(
                "#!/bin/sh\n"
                "for arg in \"$@\"; do\n"
                "  case \"$arg\" in\n"
                "    *secret-cursor-key*) echo leaked >&2; exit 3 ;;\n"
                "  esac\n"
                "done\n"
                "printf '%s' '{\"sentiment_score\": 0.2, \"read\": \"from cli\"}'\n",
                encoding="utf-8",
            )
            binary.chmod(binary.stat().st_mode | stat.S_IEXEC)
            os.environ["CURSOR_AGENT_BIN"] = str(binary)
            os.environ["CURSOR_API_KEY"] = "secret-cursor-key"
            result = CursorProvider().complete("system", "user")
        self.assertEqual(result.error, "")
        self.assertIn("from cli", result.text)
        self.assertEqual(result.provider, "cursor")

    def test_cursor_chain_falls_back_to_groq_then_rule(self):
        def fake(name, text="", error="", available=True):
            class _Provider:
                pass

            item = _Provider()
            item.name = name
            item.model = name
            item._available = available
            item._text = text
            item._error = error

            def available_fn() -> bool:
                return item._available

            def complete(system: str, user: str) -> Completion:
                return Completion(
                    provider=name,
                    model=name,
                    text=item._text,
                    prompt_tokens=None,
                    completion_tokens=None,
                    latency_ms=0.0,
                    cost_usd=None,
                    error=item._error,
                    fell_back=bool(item._error or not item._text),
                )

            item.available = available_fn
            item.complete = complete
            return item

        calls = []

        def call(provider):
            calls.append(provider.name)
            return provider.complete("", "")

        def accept(completion):
            if completion.error or not completion.text:
                return False, completion.error or "empty model reply"
            return True, ""

        cursor = fake("cursor", error="HTTP 401: unauthorized")
        groq = fake("groq", text='{"sentiment_score": 0.2, "read": "ok"}')
        rule = fake("rule", text="")
        chosen, note = choose_completion([cursor, groq, rule], call, accept)
        self.assertEqual(chosen.provider, "groq")
        self.assertEqual(calls, ["cursor", "groq"])
        self.assertIn("unauthorized", note)

        calls.clear()
        groq_down = fake("groq", error="HTTP 429: slow")
        chosen, note = choose_completion([cursor, groq_down, rule], call, accept)
        self.assertEqual(chosen.provider, "rule")
        self.assertEqual(calls, ["cursor", "groq", "rule"])
        self.assertIn("429", note)

        calls.clear()
        missing = fake("cursor", available=False)
        chosen, note = choose_completion([missing, groq, rule], call, accept)
        self.assertEqual(chosen.provider, "groq")
        self.assertEqual(calls, ["groq"])
        self.assertIn("cursor: unavailable", note)


if __name__ == "__main__":
    unittest.main()
