import tempfile
import unittest
from pathlib import Path

import support  # noqa: F401

from harness.provenance import Provenance


class ProvenanceTests(unittest.TestCase):
    def test_append_only_status_updates_projection(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "attempts.jsonl"
            log = Provenance(path)
            log.append(
                {
                    "attempt_id": "au-mira-retail|2026-11-23|premarket",
                    "persona_id": "au-mira-retail",
                    "is_ai_agent": True,
                    "contributor_kind": "ai_agent_persona",
                    "provider": "groq",
                    "model": "openai/gpt-oss-20b",
                    "assigned_model": "openai/gpt-oss-20b",
                    "prompt_version": "2026-11-21.1",
                    "ticker": "NVDA",
                    "session_date": "2026-11-23",
                    "slot": "premarket",
                    "local_validation": "pass",
                    "pr_url": "https://github.com/rahiakil/agents-unite/pull/1",
                    "pr_number": "1",
                    "ci_outcome": "pending",
                    "merge_outcome": "auto_merge_requested",
                    "dry_run": False,
                    "disclosure": "ai agent persona",
                }
            )
            before = path.read_text(encoding="utf-8")
            log.append(
                {
                    "record_type": "status",
                    "attempt_id": "au-mira-retail|2026-11-23|premarket",
                    "ci_outcome": "success",
                    "merge_outcome": "merged",
                    "pr_url": "https://github.com/rahiakil/agents-unite/pull/1",
                    "pr_number": "1",
                }
            )
            after = path.read_text(encoding="utf-8")
            self.assertTrue(after.startswith(before))
            self.assertEqual(after.count("\n"), 2)
            latest = log.latest()["au-mira-retail|2026-11-23|premarket"]
            self.assertEqual(latest["merge_outcome"], "merged")
            self.assertEqual(latest["ci_outcome"], "success")
            self.assertIn("merged", log.csv_path.read_text(encoding="utf-8"))
            self.assertTrue(log.sqlite_path.is_file())


if __name__ == "__main__":
    unittest.main()
