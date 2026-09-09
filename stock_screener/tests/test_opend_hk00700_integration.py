"""Explicit live OpenD smoke test; disabled unless RUN_OPEND_INTEGRATION=1."""

import os
import unittest


@unittest.skipUnless(os.getenv("RUN_OPEND_INTEGRATION") == "1", "set RUN_OPEND_INTEGRATION=1")
class HK00700OpenDIntegrationTest(unittest.TestCase):
    def test_hk00700_daily_bars_are_from_ready_opend(self):
        self.assertEqual(os.getenv("KLINE_USE_FUTU_OPEND"), "1")
        from kline_fetcher import managed_fetcher_chain

        with managed_fetcher_chain() as fetchers:
            self.assertTrue(fetchers and fetchers[0].get_name() == "FutuOpenAPI")
            frame = fetchers[0].fetch("HK.00700", market="HK", timeframe="1d", max_count=30)

        self.assertIsNotNone(frame)
        self.assertGreaterEqual(len(frame), 20)
        self.assertTrue(frame["date"].is_monotonic_increasing)
        for _, row in frame.iterrows():
            values = [float(row[field]) for field in ("open", "high", "low", "close")]
            self.assertTrue(all(value > 0 for value in values))
            self.assertGreaterEqual(values[1], max(values[0], values[3], values[2]))
            self.assertLessEqual(values[2], min(values[0], values[3], values[1]))
