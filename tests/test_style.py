import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP = {".git", ".cache", "out", "__pycache__", "universe.json"}


class StyleTests(unittest.TestCase):
    def test_repo_has_no_em_or_en_dash(self):
        bad = []
        for path in ROOT.rglob("*"):
            if not path.is_file():
                continue
            if any(part in SKIP for part in path.parts):
                continue
            if path.suffix in {".png", ".sqlite", ".pyc"}:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            if "\u2014" in text or "\u2013" in text:
                bad.append(str(path.relative_to(ROOT)))
        self.assertEqual(bad, [])


if __name__ == "__main__":
    unittest.main()
