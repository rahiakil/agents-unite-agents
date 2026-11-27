import tempfile
import unittest
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path

import support
from support import LEDGER, fixture_gather

from harness.ledger import MAIN_SCORE_RE
from harness.personas import load_personas
from harness.provenance import Provenance
from harness.schedule import slot_target
from harness.textutil import EM_DASH
from harness.universe import load_universe
from harness.wake import (
    WakeConfig,
    _candidates,
    _sessions_for,
    _skip,
    backfill,
    exit_code,
    run_wake,
    slot_status,
)


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

    def test_recorded_attempt_is_not_filed_again(self):
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
            self.assertTrue(_skip(log, "au-mira-retail", "2026-11-21", "premarket", False))
            self.assertFalse(_skip(log, "au-mira-retail", "2026-11-21", "premarket", True))
            dry = dict(base)
            dry["attempt_id"] = "au-jordan-retail|2026-11-21|open"
            dry["persona_id"] = "au-jordan-retail"
            dry["slot"] = "open"
            dry["dry_run"] = True
            log.append(dry)
            self.assertFalse(_skip(log, "au-jordan-retail", "2026-11-21", "open", False))
            self.assertTrue(_skip(log, "au-jordan-retail", "2026-11-21", "open", False, dry_run=True))
            sent = dict(base)
            sent["pr_url"] = "https://github.com/rahiakil/agents-unite/pull/9"
            sent["merge_outcome"] = "auto_merge_requested"
            log.append(sent)
            self.assertTrue(_skip(log, "au-mira-retail", "2026-11-21", "premarket", False))

    def test_backfill_records_a_branch_update(self):
        import harness.wake as wake

        def fake(repo, number, token, mode, prior_outcome=""):
            self.assertEqual(number, 40)
            self.assertEqual(prior_outcome, "auto_merge_requested")
            return "branch_updated", "update-branch (behind) sha=4a93befec1941e14cc2134b20f5a34f937f6e2ad"

        original = wake.advance_open_pull_request
        wake.advance_open_pull_request = fake
        try:
            with tempfile.TemporaryDirectory() as raw:
                log = Provenance(Path(raw) / "attempts.jsonl")
                log.append(
                    {
                        "attempt_id": "au-elena-fund|2026-11-21|premarket",
                        "persona_id": "au-elena-fund",
                        "github_username": "au-elena-fund",
                        "branch": "report/2026-11-21-ABBV-f570eafc",
                        "session_date": "2026-11-21",
                        "slot": "premarket",
                        "local_validation": "pass",
                        "dry_run": False,
                        "pr_number": "40",
                        "pr_url": "https://github.com/rahiakil/agents-unite/pull/40",
                        "merge_outcome": "auto_merge_requested",
                        "ci_outcome": "pending",
                    }
                )
                before = log.jsonl_path.read_text(encoding="utf-8")
                config = replace(_config(Path(raw), log.jsonl_path), dry_run=False, token="test-token", backfill=True)
                backfill(config, log)
                after = log.jsonl_path.read_text(encoding="utf-8")
                latest = log.latest()["au-elena-fund|2026-11-21|premarket"]
        finally:
            wake.advance_open_pull_request = original
        self.assertTrue(after.startswith(before))
        self.assertEqual(latest["merge_outcome"], "branch_updated")
        self.assertEqual(latest["ci_outcome"], "pending")
        self.assertEqual(latest["branch_update_sha"], "4a93befec1941e14cc2134b20f5a34f937f6e2ad")
        self.assertIn("behind", latest["failure_reason"])

    def test_wake_after_missed_slots_files_each_due_slot_once(self):
        # 15:00 UTC is 10:00 ET. Four morning slots have passed. Later slots,
        # and every persona whose first slot is still ahead, wait.
        moment = datetime(2026, 11, 23, 15, 0, tzinfo=timezone.utc)
        day = date(2026, 11, 23)
        expected = []
        future = []
        for persona in load_personas():
            for slot in persona.slots:
                target = slot_target(persona.id, day, slot)
                attempt_id = f"{persona.id}|{day.isoformat()}|{slot.id}"
                if target <= moment:
                    expected.append((target, persona.id, slot.id, attempt_id))
                else:
                    future.append(attempt_id)
        expected.sort(key=lambda item: (item[0], item[1], item[2]))
        self.assertGreaterEqual(len(expected), 4)
        self.assertGreater(len(future), 0)
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            log = Provenance(tmp / "attempts.jsonl")
            log.append(
                {
                    "record_type": "attempt",
                    "attempt_id": "au-leo-quant|2026-11-20|midday",
                    "persona_id": "au-leo-quant",
                    "session_date": "2026-11-20",
                    "slot": "midday",
                    "dry_run": False,
                    "local_validation": "pass",
                    "pr_url": "https://github.com/rahiakil/agents-unite/pull/1",
                    "merge_outcome": "auto_merge_requested",
                }
            )
            config = replace(
                _config(tmp, log.jsonl_path),
                now=moment,
                all_personas=False,
                force=False,
                dry_run=True,
                backfill=False,
            )
            first = run_wake(config, gather_fn=fixture_gather)
            ids = [row["attempt_id"] for row in first]
            self.assertEqual(ids, [item[3] for item in expected])
            self.assertEqual(len(ids), len(set(ids)))
            for row in first:
                self.assertEqual(row["local_validation"], "pass", msg=row.get("failure_reason"))
                self.assertEqual(row["slot_status"], "pass")
            self.assertTrue(set(ids).isdisjoint(future))
            self.assertTrue(all("2026-11-20" not in row["attempt_id"] for row in first))
            recorded = log.jsonl_path.read_text(encoding="utf-8")
            second = run_wake(config, gather_fn=fixture_gather)
            self.assertEqual(second, [])
            self.assertEqual(log.jsonl_path.read_text(encoding="utf-8"), recorded)

    def test_previous_trading_day_catchup_when_fully_missed(self):
        # Monday 07:00 ET. No Monday slot has started. Friday was not filed.
        moment = datetime(2026, 11, 23, 12, 0, tzinfo=timezone.utc)
        friday = date(2026, 11, 20)
        persona = next(item for item in load_personas() if item.id == "au-mira-retail")
        expected = []
        for slot in persona.slots:
            target = slot_target(persona.id, friday, slot)
            self.assertLessEqual(target, moment)
            expected.append((target, slot.id, f"{persona.id}|{friday.isoformat()}|{slot.id}"))
        expected.sort(key=lambda item: (item[0], item[1]))
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            config = replace(
                _config(tmp, tmp / "attempts.jsonl"),
                now=moment,
                all_personas=False,
                force=False,
                dry_run=True,
                backfill=False,
                persona_id=persona.id,
            )
            results = run_wake(config, gather_fn=fixture_gather)
            self.assertEqual([row["attempt_id"] for row in results], [item[2] for item in expected])
            for row in results:
                self.assertEqual(row["session_date"], "2026-11-20")
                self.assertEqual(row["local_validation"], "pass", msg=row.get("failure_reason"))
            self.assertTrue(all(row["session_date"] != "2026-11-23" for row in results))

    def test_previous_trading_day_is_left_alone_when_any_attempt_exists(self):
        moment = datetime(2026, 11, 23, 12, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            log = Provenance(tmp / "attempts.jsonl")
            log.append(
                {
                    "record_type": "attempt",
                    "attempt_id": "au-leo-quant|2026-11-20|midday",
                    "persona_id": "au-leo-quant",
                    "session_date": "2026-11-20",
                    "slot": "midday",
                    "dry_run": False,
                }
            )
            before = log.jsonl_path.read_text(encoding="utf-8")
            config = replace(
                _config(tmp, log.jsonl_path),
                now=moment,
                all_personas=False,
                force=False,
                dry_run=True,
                backfill=False,
                persona_id="au-mira-retail",
            )
            results = run_wake(config, gather_fn=fixture_gather)
            self.assertEqual(results, [])
            self.assertEqual(log.jsonl_path.read_text(encoding="utf-8"), before)

    def test_thanksgiving_is_never_filed(self):
        # Thanksgiving evening: no session. Friday morning catch-up skips
        # Thanksgiving and looks at Wednesday instead.
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            log = Provenance(tmp / "attempts.jsonl")
            base = replace(
                _config(tmp, log.jsonl_path),
                all_personas=False,
                force=False,
                dry_run=True,
                backfill=False,
            )
            evening = replace(base, now=datetime(2026, 11, 26, 22, 0, tzinfo=timezone.utc))
            # Only the missed Wednesday may be caught up on Thanksgiving itself.
            self.assertEqual(_sessions_for(evening, log), [date(2026, 11, 25)])
            evening_rows = run_wake(evening, gather_fn=fixture_gather)
            self.assertTrue(all(row["session_date"] == "2026-11-25" for row in evening_rows))
            log = Provenance(tmp / "fresh.jsonl")
            base = replace(base, provenance_path=log.jsonl_path)
            evening = replace(evening, provenance_path=log.jsonl_path)
            forced = replace(evening, all_personas=True, force=True)
            self.assertEqual(_sessions_for(forced, log), [])
            asked = replace(evening, session=date(2026, 11, 26))
            self.assertEqual(_sessions_for(asked, log), [])
            friday = replace(base, now=datetime(2026, 11, 27, 12, 0, tzinfo=timezone.utc))
            self.assertEqual(_sessions_for(friday, log), [date(2026, 11, 27), date(2026, 11, 25)])
            results = run_wake(friday, gather_fn=fixture_gather)
            self.assertTrue(results)
            self.assertTrue(all(row["session_date"] != "2026-11-26" for row in results))

    def test_wake_refuses_a_session_older_than_the_ledger_window(self):
        moment = datetime(2026, 11, 23, 18, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            config = replace(
                _config(tmp, tmp / "attempts.jsonl"),
                now=moment,
                session=date(2026, 11, 18),
                all_personas=False,
                force=False,
                dry_run=True,
                backfill=False,
            )
            log = Provenance(config.provenance_path)
            self.assertEqual(_sessions_for(config, log), [])
            results = run_wake(config, gather_fn=fixture_gather)
            self.assertEqual(results, [])

    def test_missing_ledger_token_message_is_soft(self):
        from harness.wake import MISSING_TOKEN

        self.assertIn("LEDGER_TOKEN", MISSING_TOKEN)
        self.assertIn("exits 0", MISSING_TOKEN)


if __name__ == "__main__":
    unittest.main()
