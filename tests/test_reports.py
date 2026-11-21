import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import support
from support import LEDGER, fixture_gather

from harness.ledger import MAIN_SCORE_RE
from harness.personas import load_personas
from harness.provenance import Provenance
from harness.textutil import EM_DASH
from harness.universe import load_universe
from harness.wake import WakeConfig, _candidates, _skip, exit_code, run_wake, slot_status


def _config(tmp: Path, provenance: Path) -> WakeConfig:
    return WakeConfig(
        now=datetime(2026, 11, 23, 16, 0, tzinfo=timezone.utc),
        dry_run=True,
        all_personas=True,
        persona_id="",
        session=None,
        lookback_minutes=40,
        output_dir=tmp / "out",
        provenance_path=provenance,
        ledger_root=LEDGER,
        personas_dir=None,
        ledger_repo="rahiakil/agents-unite",
        ledger_ref="main",
        provider_name="rule",
        merge_mode="off",
        token="",
        force=True,
        backfill=False,
    )


class ReportTests(unittest.TestCase):
    def setUp(self):
        if not (LEDGER / "scripts" / "validate_report.py").is_file():
            self.skipTest(f"ledger checkout missing at {LEDGER}")

    def test_all_nine_pass_ledger_validator(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            provenance = Provenance(tmp / "attempts.jsonl")
            results = run_wake(_config(tmp, provenance.jsonl_path), gather_fn=fixture_gather)
            self.assertEqual(len(results), 9)
            for row in results:
                self.assertEqual(row["local_validation"], "pass", msg=row.get("failure_reason"))
                report = next((tmp / "out").glob(f"data/*/*/report.{row['github_username']}.md"))
                text = report.read_text(encoding="utf-8")
                self.assertGreaterEqual(len(text), 400)
                self.assertRegex(text, MAIN_SCORE_RE)
                self.assertNotIn(EM_DASH, text)
                self.assertNotIn("simulated:", text.lower())
            texts = []
            for path in (tmp / "out").glob("data/*/*/report.*.md"):
                texts.append(path.read_text(encoding="utf-8"))
            self.assertEqual(len(set(texts)), 9)
            self.assertTrue(all(row["slot_status"] == "pass" for row in results))

    def test_one_persona_failure_does_not_stop_the_others(self):
        personas = load_personas()
        mira = next(item for item in personas if item.id == "au-mira-retail")
        universe = load_universe(LEDGER)
        bad = set(_candidates(mira, universe, "2026-11-23", mira.slots[0].id, set()))

        def flaky(ticker, search, name="", sector="", getter=None):
            if ticker in bad:
                raise RuntimeError("injected failure")
            return fixture_gather(ticker, search, name=name, sector=sector)

        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            results = run_wake(_config(tmp, tmp / "attempts.jsonl"), gather_fn=flaky)
            by_id = {row["persona_id"]: row for row in results}
            self.assertEqual(len(by_id), 9)
            self.assertEqual(by_id["au-mira-retail"]["local_validation"], "fail")
            others = [row for key, row in by_id.items() if key != "au-mira-retail"]
            self.assertTrue(all(row["local_validation"] == "pass" for row in others))

    def test_submit_failure_counts_as_a_failed_slot(self):
        import harness.wake as wake

        def boom(*_args, **_kwargs):
            raise RuntimeError("refusing to reuse submit directory /tmp/au-ledger-test")

        original = wake._submit
        wake._submit = boom
        try:
            with tempfile.TemporaryDirectory() as raw:
                tmp = Path(raw)
                config = replace(
                    _config(tmp, tmp / "attempts.jsonl"),
                    dry_run=False,
                    token="test-token",
                    persona_id="au-mira-retail",
                    backfill=False,
                )
                results = wake.run_wake(config, gather_fn=fixture_gather)
        finally:
            wake._submit = original
        self.assertEqual(len(results), 1)
        row = results[0]
        self.assertEqual(row["local_validation"], "pass")
        self.assertEqual(row["slot_status"], "fail")
        self.assertEqual(row["merge_outcome"], "not_submitted")
        self.assertIn("refusing to reuse submit directory", row["failure_reason"])
        self.assertEqual(exit_code(config, results), 1)
        self.assertEqual(slot_status(row), "fail")

    def test_skip_requires_a_pull_request(self):
        with tempfile.TemporaryDirectory() as raw:
            log = Provenance(Path(raw) / "attempts.jsonl")
            base = {
                "attempt_id": "au-mira-retail|2026-11-21|premarket",
                "persona_id": "au-mira-retail",
                "session_date": "2026-11-21",
                "slot": "premarket",
                "local_validation": "pass",
                "dry_run": False,
                "merge_outcome": "not_submitted",
            }
            log.append(base)
            self.assertFalse(_skip(log, "au-mira-retail", "2026-11-21", "premarket", False))
            dry = dict(base)
            dry["attempt_id"] = "au-jordan-retail|2026-11-21|open"
            dry["persona_id"] = "au-jordan-retail"
            dry["slot"] = "open"
            dry["dry_run"] = True
            log.append(dry)
            self.assertFalse(_skip(log, "au-jordan-retail", "2026-11-21", "open", False))
            sent = dict(base)
            sent["pr_url"] = "https://github.com/rahiakil/agents-unite/pull/9"
            sent["merge_outcome"] = "auto_merge_requested"
            log.append(sent)
            self.assertTrue(_skip(log, "au-mira-retail", "2026-11-21", "premarket", False))

    def test_missing_ledger_token_message_is_soft(self):
        from harness.wake import MISSING_TOKEN

        self.assertIn("LEDGER_TOKEN", MISSING_TOKEN)
        self.assertIn("exits 0", MISSING_TOKEN)


if __name__ == "__main__":
    unittest.main()
