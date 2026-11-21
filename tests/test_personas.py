import unittest
from collections import Counter
from pathlib import Path

import support  # noqa: F401

from harness.personas import load_personas
from harness.providers import GROQ_FREE_MODELS

ROOT = Path(__file__).resolve().parents[1]


class PersonaTests(unittest.TestCase):
    def test_nine_personas_and_groq_models(self):
        personas = load_personas(ROOT / "personas")
        self.assertEqual(len(personas), 9)
        counts = Counter(item.archetype for item in personas)
        self.assertEqual(counts["retail_trader"], 2)
        self.assertEqual(counts["quant_trader"], 2)
        self.assertEqual(counts["fund_manager"], 2)
        self.assertEqual(counts["advanced_investor"], 2)
        self.assertEqual(counts["regular_user"], 1)
        models = [item.groq_model for item in personas]
        self.assertEqual(set(models), set(GROQ_FREE_MODELS))
        cursor = [item for item in personas if item.provider == "cursor"]
        self.assertEqual(sorted(item.id for item in cursor), ["au-elena-fund", "au-nina-advanced"])
        self.assertEqual(sorted(item.archetype for item in cursor), ["advanced_investor", "fund_manager"])
        self.assertEqual(sum(1 for item in personas if item.provider), 2)
        for item in personas:
            self.assertGreaterEqual(len(item.slots), 2)
            self.assertFalse(item.github_username.lower().startswith("sim-"))
            self.assertTrue(item.prompt_version)
            self.assertTrue(item.voice.openers)

    def test_cursor_cards_override_groq_default_only(self):
        from harness.wake import assigned_provider_name, provider_chain

        personas = {item.id: item for item in load_personas(ROOT / "personas")}
        elena = personas["au-elena-fund"]
        mira = personas["au-mira-retail"]
        self.assertEqual(assigned_provider_name(elena, "groq"), "cursor")
        self.assertEqual(assigned_provider_name(elena, "rule"), "rule")
        self.assertEqual(assigned_provider_name(mira, "groq"), "groq")
        self.assertEqual([item.name for item in provider_chain(elena, "groq")], ["cursor", "groq", "rule"])
        self.assertEqual([item.name for item in provider_chain(elena, "rule")], ["rule"])

    def test_wake_token_is_optional(self):
        from harness.wake import parse_args

        self.assertTrue(parse_args(["wake", "--dry-run"]).dry_run)
        self.assertTrue(parse_args(["--dry-run"]).dry_run)

    def test_workflow_defaults_to_groq(self):
        text = (ROOT / ".github" / "workflows" / "wake.yml").read_text(encoding="utf-8")
        self.assertIn("HARNESS_PROVIDER: groq", text)
        self.assertIn("secrets.GROQ_API_KEY", text)
        self.assertIn("secrets.LEDGER_TOKEN", text)
        self.assertIn("secrets.XAI_API_KEY", text)
        self.assertIn("secrets.CURSOR_API_KEY", text)
        self.assertIn("https://cursor.com/install", text)
        self.assertNotIn("llama-3.1-8b-instant", text)
        self.assertNotIn("HARNESS_PROVIDER: cursor", text)

    def test_prompt_version_stamp(self):
        version = (ROOT / "prompts" / "VERSION").read_text(encoding="utf-8").strip()
        for path in (ROOT / "prompts" / "research").glob("*.md"):
            first = path.read_text(encoding="utf-8").splitlines()[0]
            self.assertEqual(first, f"Prompt-Version: {version}")


if __name__ == "__main__":
    unittest.main()
