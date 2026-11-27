import tempfile
import unittest
from pathlib import Path

import support  # noqa: F401
from harness import costs


class CostTests(unittest.TestCase):
    def test_free_tier_is_zero_but_list_price_is_kept(self):
        row = costs.make_row(run_id="r1", harness="persona-wake", date="2026-11-27", tickers_requested=2,
                             tickers_written=2, llm_provider="groq", model="openai/gpt-oss-120b",
                             prompt_tokens=1_000_000, completion_tokens=1_000_000, llm_calls=2)
        self.assertEqual(row["est_cost_usd"], 0.0)
        self.assertAlmostEqual(row["list_price_usd"], 0.75)
        self.assertEqual(row["total_tokens"], 2_000_000)
        self.assertEqual(row["tokens_per_stock"], 1_000_000)
        self.assertAlmostEqual(row["list_cost_per_50_stocks"], 18.75)

    def test_lexicon_run_and_views(self):
        with tempfile.TemporaryDirectory() as tmp:
            runs = Path(tmp)
            costs.write_run([costs.make_row(run_id="n1", harness="nightly-script", date="2026-11-27",
                                            tickers_requested=503, tickers_written=500, llm_provider="lexicon",
                                            model="lexicon-vader-fin-v1", runtime_s=600)], runs)
            costs.write_run([costs.make_row(run_id="g1", harness="grokbot-routine", date="2026-11-25",
                                            tickers_requested=100, tickers_written=100, llm_provider="grokbot",
                                            model="grokbot", total_tokens=300000, est_cost_usd=1.5)], runs)
            self.assertEqual(len((runs / "cost_log.jsonl").read_text().splitlines()), 2)
            md = (runs / "COSTS.md").read_text()
            self.assertIn("nightly-script", md)
            self.assertIn("$0.7500", md)  # grokbot: 1.5 per 100 stocks is 0.75 per 50
            self.assertNotIn("\u2014", md)
            page = costs.summary_html(costs.load_rows(runs))
            self.assertIn("viewport", page)
            self.assertIn("grokbot-routine", page)
            self.assertNotIn("\u2014", page)

    def test_persona_rows_group_by_model(self):
        results = [
            {"provider": "groq", "model": "openai/gpt-oss-20b", "prompt_tokens": 900, "completion_tokens": 100,
             "tokens_source": "provider", "merge_outcome": "auto_merge_requested", "session_date": "2026-11-27"},
            {"provider": "rule", "model": "rule", "tokens_source": "none", "merge_outcome": "not_submitted",
             "session_date": "2026-11-27"},
        ]
        rows = costs.persona_rows(results, run_id="w1", runtime_s=10, dry_run=False)
        self.assertEqual(len(rows), 2)
        groq = next(r for r in rows if r["llm_provider"] == "groq")
        self.assertEqual(groq["total_tokens"], 1000)
        self.assertEqual(groq["tickers_written"], 1)


if __name__ == "__main__":
    unittest.main()
