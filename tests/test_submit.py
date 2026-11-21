import unittest

import support  # noqa: F401

from harness.submit import PushRefused, assert_safe_push, auto_merge_command, direct_merge_command, push_command


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


if __name__ == "__main__":
    unittest.main()
