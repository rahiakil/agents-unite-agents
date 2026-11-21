import json
import unittest

import support  # noqa: F401

from harness.research import parse_chart


class ResearchTests(unittest.TestCase):
    def test_parse_chart_computes_change(self):
        stamps = [1_700_000_000 + i * 86400 for i in range(5)]
        closes = [100, 101, 102, 100, 104]
        payload = {
            "chart": {
                "result": [
                    {
                        "timestamp": stamps,
                        "indicators": {
                            "quote": [
                                {
                                    "close": closes,
                                    "high": [item + 1 for item in closes],
                                    "low": [item - 1 for item in closes],
                                    "volume": [1000, 1100, 900, 1200, 1500],
                                }
                            ]
                        },
                    }
                ]
            }
        }
        tape = parse_chart(json.dumps(payload).encode("utf-8"), "AAPL")
        self.assertIsNotNone(tape)
        self.assertAlmostEqual(tape.change_pct, 4.0, places=2)
        self.assertEqual(tape.bar_count, 5)
        self.assertTrue(tape.history_url.startswith("https://finance.yahoo.com/quote/AAPL"))


if __name__ == "__main__":
    unittest.main()
