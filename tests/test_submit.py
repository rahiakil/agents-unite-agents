import json
import tempfile
import unittest

import support  # noqa: F401

from harness.submit import (
    PushRefused,
    advance_open_pull_request,
    assert_safe_push,
    auto_merge_command,
    branch_update_sha,
    clone_ledger,
    direct_merge_command,
    fresh_submit_dir,
    needs_branch_update,
    push_command,
    scope_failed_validate_passed,
    update_branch_command,
)


def _completed(code: int, out: str = "", err: str = ""):
    class Result:
        returncode = code
        stdout = out
        stderr = err

    return Result()


BEHIND = {
    "state": "OPEN",
    "mergeStateStatus": "BEHIND",
    "headRefName": "report/2026-11-21-ABBV-f570eafc",
    "headRefOid": "4a93befec1941e14cc2134b20f5a34f937f6e2ad",
    "statusCheckRollup": [
        {"name": "scope-and-validate", "status": "COMPLETED", "conclusion": "SUCCESS"},
        {"name": "scope-and-validate", "status": "COMPLETED", "conclusion": "FAILURE"},
        {"name": "validate", "status": "COMPLETED", "conclusion": "SUCCESS"},
    ],
}


class SubmitSafetyTests(unittest.TestCase):
    def test_push_is_not_a_force(self):
        command = push_command("report/2026-11-23-NVDA-abcdef12")
        self.assertNotIn("--force", command)
        self.assertNotIn("-f", command)
        self.assertFalse(any(part.startswith("+") for part in command))

    def test_force_flags_are_refused(self):
        with self.assertRaises(PushRefused):
            assert_safe_push(["git", "push", "--force", "origin", "main"])
        with self.assertRaises(PushRefused):
            assert_safe_push(["git", "push", "origin", "+HEAD:refs/heads/report/x"])

    def test_auto_merge_command(self):
        command = auto_merge_command("rahiakil/agents-unite", 12)
        self.assertIn("--auto", command)
        self.assertIn("--squash", command)
        self.assertNotIn("--force", command)
        direct = direct_merge_command("rahiakil/agents-unite", 12)
        self.assertNotIn("--auto", direct)

    def test_fresh_submit_dir_is_not_created_yet(self):
        dest = fresh_submit_dir()
        self.assertFalse(dest.exists())
        self.assertTrue(dest.parent.is_dir())
        with self.assertRaises(RuntimeError) as caught:
            clone_ledger(dest.parent, "rahiakil/agents-unite", "main", "token")
        self.assertIn("refusing to reuse submit directory", str(caught.exception))
        self.assertTrue(str(dest.parent).startswith(tempfile.gettempdir()))

    def test_update_branch_command_does_not_rewrite_history(self):
        command = update_branch_command("rahiakil/agents-unite", 40)
        self.assertEqual(command[:3], ["gh", "pr", "update-branch"])
        self.assertIn("40", command)
        self.assertIn("rahiakil/agents-unite", command)
        self.assertNotIn("--force", command)
        self.assertNotIn("--rebase", command)
        self.assertNotIn("-f", command)

    def test_behind_report_pr_needs_an_update(self):
        self.assertEqual(needs_branch_update(BEHIND), "behind")
        self.assertTrue(scope_failed_validate_passed(BEHIND["statusCheckRollup"]))

    def test_stale_scope_needs_an_update_until_one_refresh(self):
        blocked = dict(BEHIND)
        blocked["mergeStateStatus"] = "BLOCKED"
        self.assertEqual(needs_branch_update(blocked), "stale_scope")
        self.assertEqual(needs_branch_update(blocked, prior_outcome="branch_updated"), "")

    def test_validate_failure_is_not_a_stale_scope_refresh(self):
        rollup = [
            {"name": "scope-and-validate", "status": "COMPLETED", "conclusion": "FAILURE"},
            {"name": "validate", "status": "COMPLETED", "conclusion": "FAILURE"},
        ]
        self.assertFalse(scope_failed_validate_passed(rollup))
        snapshot = {
            "state": "OPEN",
            "mergeStateStatus": "BLOCKED",
            "headRefName": "report/2026-11-21-ABBV-f570eafc",
            "statusCheckRollup": rollup,
        }
        self.assertEqual(needs_branch_update(snapshot), "")

    def test_non_report_and_dirty_prs_are_left_alone(self):
        other = dict(BEHIND)
        other["headRefName"] = "docs/readme"
        self.assertEqual(needs_branch_update(other), "")
        dirty = dict(BEHIND)
        dirty["mergeStateStatus"] = "DIRTY"
        self.assertEqual(needs_branch_update(dirty), "")
        closed = dict(BEHIND)
        closed["state"] = "CLOSED"
        self.assertEqual(needs_branch_update(closed), "")

    def test_behind_pr_updates_the_branch_and_does_not_merge(self):
        import harness.submit as submit

        calls = []

        def fake_run(argv, cwd, env, token):
            calls.append(list(argv))
            if argv[:3] == ["gh", "pr", "view"]:
                return _completed(0, json.dumps(BEHIND))
            if argv[:3] == ["gh", "pr", "update-branch"]:
                return _completed(0, "pull request branch updated")
            return _completed(1, "", "unexpected " + " ".join(argv))

        original = submit._run
        submit._run = fake_run
        try:
            outcome, detail = advance_open_pull_request("rahiakil/agents-unite", 40, "token", "auto")
        finally:
            submit._run = original
        self.assertEqual(outcome, "branch_updated")
        self.assertIn("behind", detail)
        self.assertEqual(branch_update_sha(detail), "4a93befec1941e14cc2134b20f5a34f937f6e2ad")
        self.assertTrue(any(argv[:3] == ["gh", "pr", "update-branch"] for argv in calls))
        self.assertFalse(any(argv[:3] == ["gh", "pr", "merge"] for argv in calls))
        update = next(argv for argv in calls if argv[:3] == ["gh", "pr", "update-branch"])
        self.assertNotIn("--force", update)
        self.assertNotIn("--rebase", update)

    def test_already_current_branch_waits_instead_of_merging(self):
        import harness.submit as submit

        calls = []

        def fake_run(argv, cwd, env, token):
            calls.append(list(argv))
            if argv[:3] == ["gh", "pr", "view"]:
                return _completed(0, json.dumps(BEHIND))
            if argv[:3] == ["gh", "pr", "update-branch"]:
                return _completed(1, "", "pull request branch is already up to date")
            return _completed(1, "", "unexpected")

        original = submit._run
        submit._run = fake_run
        try:
            outcome, detail = advance_open_pull_request("rahiakil/agents-unite", 40, "token", "auto")
        finally:
            submit._run = original
        self.assertEqual(outcome, "checks_pending")
        self.assertIn("up to date", detail)
        self.assertFalse(any(argv[:3] == ["gh", "pr", "merge"] for argv in calls))

    def test_green_pr_is_merged_without_update_branch(self):
        import harness.submit as submit

        calls = []
        green = {
            "state": "OPEN",
            "mergeStateStatus": "CLEAN",
            "mergeable": "MERGEABLE",
            "headRefName": "report/2026-11-21-NVDA-aaaaaaaa",
            "headRefOid": "abc1234567890",
            "statusCheckRollup": [
                {"name": "scope-and-validate", "status": "COMPLETED", "conclusion": "SUCCESS"},
                {"name": "validate", "status": "COMPLETED", "conclusion": "SUCCESS"},
            ],
        }

        def fake_run(argv, cwd, env, token):
            calls.append(list(argv))
            if argv[:3] == ["gh", "pr", "view"]:
                return _completed(0, json.dumps(green))
            if argv[:3] == ["gh", "pr", "merge"]:
                return _completed(0, "merged")
            return _completed(1, "", "unexpected")

        original = submit._run
        submit._run = fake_run
        try:
            outcome, detail = advance_open_pull_request("rahiakil/agents-unite", 39, "token", "auto")
        finally:
            submit._run = original
        self.assertEqual(outcome, "merged")
        self.assertIn("merged", detail)
        self.assertFalse(any(argv[:3] == ["gh", "pr", "update-branch"] for argv in calls))
        self.assertTrue(any("--squash" in argv and "--auto" not in argv for argv in calls))


if __name__ == "__main__":
    unittest.main()
