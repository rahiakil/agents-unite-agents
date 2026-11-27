"""Several personas filed one after another must each push a clean branch.

A catch-up wake files many slots in one process. Each submission has to start
from a fresh clone of ledger main and carry only that persona's DATE/TICKER
files, even when main moved (earlier reports merged) between submissions and
even when the shared output directory already holds other personas' files.
"""

import shutil
import subprocess
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import support  # noqa: F401
from support import LEDGER, fixture_gather

import harness.submit as submit
import harness.wake as wake
from harness.submit import SubmitResult


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        env={
            "GIT_AUTHOR_NAME": "test",
            "GIT_AUTHOR_EMAIL": "test@example.com",
            "GIT_COMMITTER_NAME": "test",
            "GIT_COMMITTER_EMAIL": "test@example.com",
            "PATH": "/usr/bin:/bin:/usr/local/bin",
            "HOME": str(cwd),
        },
    )
    return result.stdout


class IsolatedSequentialSubmitTests(unittest.TestCase):
    def setUp(self):
        if not (LEDGER / "scripts" / "validate_report.py").is_file():
            self.skipTest(f"ledger checkout missing at {LEDGER}")
        if shutil.which("git") is None:
            self.skipTest("git is not installed")

    def _remote(self, tmp: Path) -> Path:
        seed = tmp / "seed"
        ignore = shutil.ignore_patterns(".git", "data", "website", "raw", "node_modules", ".cache")
        shutil.copytree(LEDGER, seed, ignore=ignore)
        (seed / "data").mkdir(exist_ok=True)
        (seed / "data" / ".keep").write_text("", encoding="utf-8")
        _git(seed, "init", "-q", "-b", "main")
        _git(seed, "add", "-A")
        _git(seed, "commit", "-q", "-m", "seed")
        bare = tmp / "remote.git"
        _git(tmp, "clone", "-q", "--bare", str(seed), str(bare))
        return bare

    def test_sequential_personas_push_only_their_own_files(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            bare = self._remote(tmp)
            maint = tmp / "maint"
            _git(tmp, "clone", "-q", str(bare), str(maint))
            scratch_dirs: list[Path] = []
            branches: list[str] = []

            def fake_clone(dest, repo, ref, token):
                self.assertFalse(dest.exists(), "a submission reused a ledger directory")
                scratch_dirs.append(dest)
                dest.parent.mkdir(parents=True, exist_ok=True)
                _git(dest.parent, "clone", "-q", "--branch", ref, str(bare), str(dest))

            def fake_open_pr(repo, branch, title, body, token):
                # Simulate the ledger auto-merging each PR before the next
                # persona files, so main moves between submissions.
                branches.append(branch)
                _git(maint, "fetch", "-q", "origin")
                _git(maint, "checkout", "-q", "main")
                _git(maint, "reset", "-q", "--hard", "origin/main")
                _git(maint, "merge", "-q", "--no-ff", "-m", f"merge {branch}", f"origin/{branch}")
                _git(maint, "push", "-q", "origin", "main")
                return len(branches), f"https://example.invalid/pull/{len(branches)}", ""

            patches = {
                (wake, "clone_ledger"): fake_clone,
                (wake, "open_pr"): fake_open_pr,
                (wake, "request_auto_merge"): lambda *a, **k: ("auto_merge_requested", ""),
                (submit, "remote_branch_exists"): lambda repo, branch, token: False,
            }
            originals = {key: getattr(*key) for key in patches}
            for (module, name), value in patches.items():
                setattr(module, name, value)
            try:
                out = tmp / "out"
                # Leftovers from another persona in the shared output dir must
                # never reach a submission branch.
                stray = out / "data" / "2026-11-23" / "ZZZZ" / "report.au-stray.md"
                stray.parent.mkdir(parents=True)
                stray.write_text("stray", encoding="utf-8")
                config = wake.WakeConfig(
                    now=datetime(2026, 11, 23, 16, 0, tzinfo=timezone.utc),
                    dry_run=False,
                    all_personas=True,
                    persona_id="",
                    session=None,
                    lookback_minutes=40,
                    output_dir=out,
                    provenance_path=tmp / "attempts.jsonl",
                    ledger_root=LEDGER,
                    personas_dir=None,
                    ledger_repo="rahiakil/agents-unite",
                    ledger_ref="main",
                    provider_name="rule",
                    merge_mode="on",
                    token="test-token",
                    force=True,
                    backfill=False,
                )
                results = wake.run_wake(config, gather_fn=fixture_gather)
            finally:
                for (module, name), value in originals.items():
                    setattr(module, name, value)

            submitted = [row for row in results if row.get("pr_url")]
            self.assertGreaterEqual(len(submitted), 3, msg=[r.get("failure_reason") for r in results])
            self.assertEqual(len(set(scratch_dirs)), len(scratch_dirs))
            self.assertEqual(len(branches), len(submitted))
            for row in submitted:
                branch = row["branch"]
                user = row["github_username"]
                ticker = row["ticker"].upper().replace(".", "-")
                session = row["session_date"]
                expected = {
                    f"data/{session}/{row['ticker']}/report.{user}.md",
                    f"data/{session}/{row['ticker']}/sources.{user}.json",
                }
                tip = f"refs/heads/{branch}"
                own_commit = set(_git(bare, "diff-tree", "--no-commit-id", "--name-only", "-r", tip).split())
                self.assertEqual(own_commit, expected, msg=branch)
                parents = _git(bare, "rev-list", "--parents", "-n", "1", tip).split()
                self.assertEqual(len(parents), 2, msg=f"{branch} must be one commit on top of main")
                fork = parents[1]
                self.assertEqual(_git(bare, "rev-list", "--count", f"{fork}..{tip}").strip(), "1")
                changed = set(_git(bare, "diff", "--name-only", fork, tip).split())
                self.assertEqual(changed, expected, msg=branch)
                self.assertTrue(branch.startswith(f"report/{session}-{ticker}-"), msg=branch)
                self.assertNotIn("au-stray", " ".join(changed))


if __name__ == "__main__":
    unittest.main()
