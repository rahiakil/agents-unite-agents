import json
import sys
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

from support import LEDGER, ROOT

sys.path.insert(0, str(ROOT / "nightly"))
import collect_nightly as N  # noqa: E402


def _rec():
    items = [
        {"title": "Acme shares jump after record quarter", "url": "https://example.com/a", "publisher": "Wire",
         "published": "2026-11-24T15:00:00+00:00", "published_date": "2026-11-24", "date_precision": "timestamp"},
        {"title": "Acme faces probe over pricing", "url": "https://example.com/b", "publisher": "Daily",
         "published": "2026-11-23T08:00:00+00:00", "published_date": "2026-11-23", "date_precision": "day"},
    ]
    try:
        lex, scored = N.score_items(items)
    except ImportError:
        lex, scored = 0.1, [{**i, "compound": 0.5 - k} for k, i in enumerate(items)]
    return {"ticker": "AAPL", "name": "Apple", "items": items, "scored": scored, "lex": lex,
            "news_stats": {"query": "q", "raw": 2, "error": None},
            "filings": [{"form": "8-K", "filing_date": "2026-11-24", "url": "https://www.sec.gov/x", "items": "2.02",
                         "description": "8-K"}],
            "price": {"source_url": "https://finance.yahoo.com/quote/AAPL/history", "close": 101.0, "prev_close": 100.0,
                      "change_pct": 1.0, "volume": 1000, "high_52w": 120.0, "low_52w": 80.0, "pos_52w_pct": 52.5,
                      "bar_date": "2026-11-24"}}


class CalendarTests(unittest.TestCase):
    def test_holidays_and_early_close(self):
        self.assertFalse(N.is_trading_day(date(2026, 11, 26)))
        self.assertFalse(N.is_trading_day(date(2026, 11, 28)))
        self.assertTrue(N.is_trading_day(date(2026, 11, 27)))
        self.assertEqual(N.close_dt(date(2026, 11, 27)).hour, 18)  # 1:00 pm ET
        self.assertEqual(N.close_dt(date(2026, 11, 24)).hour, 21)  # 4:00 pm ET

    def test_auto_session(self):
        self.assertEqual(N.auto_session(datetime(2026, 11, 25, 1, 30, tzinfo=timezone.utc)), date(2026, 11, 24))
        self.assertIsNone(N.auto_session(datetime(2026, 11, 27, 1, 30, tzinfo=timezone.utc)))  # Thanksgiving
        self.assertIsNone(N.auto_session(datetime(2026, 11, 24, 15, 0, tzinfo=timezone.utc)))  # before close

    def test_name_stripping(self):
        self.assertEqual(N.strip_name("Apple Inc."), "Apple")
        self.assertEqual(N.strip_name("Brown-Forman"), "Brown-Forman")


class RenderTests(unittest.TestCase):
    def test_report_is_factual_and_marked(self):
        md, sj = N.render(_rec(), date(2026, 11, 24), "2026-11-25T01:40:00Z", "rahiakil-grokbot", "rahiakil", False)
        self.assertIn("harness: agents-unite-nightly-script", md)
        self.assertIn("score_source: lexicon", md)
        self.assertIn("llm_tokens: 0", md)
        self.assertNotIn("\u2014", md)
        self.assertEqual(sj["sources"][-1]["type"], "other")
        self.assertTrue(all(s["url"] for s in sj["sources"]))

    @unittest.skipUnless((LEDGER / "scripts" / "validate_report.py").is_file(), "ledger checkout not available")
    def test_ledger_validator_accepts_report(self):
        from harness.ledger import validate_report_dir

        md, sj = N.render(_rec(), date(2026, 11, 24), "2026-11-25T01:40:00Z", "rahiakil-grokbot", "rahiakil", True)
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "data" / "2026-11-24" / "AAPL"
            d.mkdir(parents=True)
            (d / "report.rahiakil-grokbot.md").write_text(md, encoding="utf-8")
            (d / "sources.rahiakil-grokbot.json").write_text(json.dumps(sj), encoding="utf-8")
            self.assertEqual(validate_report_dir(LEDGER, d), [])


class UniverseTests(unittest.TestCase):
    def test_sp500_list_is_committed(self):
        uni = N.load_universe(ROOT / "nightly" / "sp500.csv", ROOT / "nightly" / "aliases.json")
        self.assertGreaterEqual(len(uni), 500)
        self.assertEqual(len({u["ticker"] for u in uni}), len(uni))


if __name__ == "__main__":
    unittest.main()
