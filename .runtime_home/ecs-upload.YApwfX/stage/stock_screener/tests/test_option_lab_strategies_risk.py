import unittest

from option_lab.market_data import FakeOptionMarketDataProvider
from option_lab.models import RiskProfile
from option_lab.risk import apply_risk_profile
from option_lab.strategies import STRATEGY_LABELS, generate_strategy_candidates, strategy_label


class OptionLabStrategiesRiskTests(unittest.TestCase):
    def test_strategy_labels_are_chinese(self):
        self.assertEqual(strategy_label("long_call"), "买入看涨期权")
        self.assertEqual(strategy_label("iron_condor"), "铁鹰式")

    def test_generate_candidates_contains_contract_details(self):
        snapshot = FakeOptionMarketDataProvider().fetch_snapshot("US", "US.AAPL")
        candidates = generate_strategy_candidates("run-1", snapshot)

        self.assertGreater(len(candidates), 0)
        first = candidates[0]
        self.assertIn("期权", first.strategy_name)
        self.assertGreaterEqual(len(first.contract_details), 1)
        self.assertIn("最大亏损", first.risk_metrics)

    def test_generate_candidates_covers_full_strategy_basket(self):
        snapshot = FakeOptionMarketDataProvider().fetch_snapshot("US", "US.AAPL")
        candidates = generate_strategy_candidates("run-1", snapshot)
        generated = {item.strategy_key for item in candidates}

        self.assertEqual(set(STRATEGY_LABELS), generated)

    def test_conservative_profile_filters_unlimited_loss(self):
        snapshot = FakeOptionMarketDataProvider().fetch_snapshot("US", "US.AAPL")
        candidates = generate_strategy_candidates("run-1", snapshot)
        filtered = apply_risk_profile(candidates, RiskProfile.CONSERVATIVE, max_loss=1000)

        self.assertTrue(all("无限亏损" not in item.warnings for item in filtered))
        self.assertGreater(len(filtered), 0)

    def test_aggressive_profile_keeps_more_candidates_than_conservative(self):
        snapshot = FakeOptionMarketDataProvider().fetch_snapshot("US", "US.AAPL")
        candidates = generate_strategy_candidates("run-1", snapshot)
        conservative = apply_risk_profile(candidates, RiskProfile.CONSERVATIVE, max_loss=1000)
        aggressive = apply_risk_profile(candidates, RiskProfile.AGGRESSIVE, max_loss=1000)

        self.assertGreaterEqual(len(aggressive), len(conservative))

    def test_strategy_scope_limits_generated_candidates(self):
        snapshot = FakeOptionMarketDataProvider().fetch_snapshot("US", "US.AAPL")
        candidates = generate_strategy_candidates("run-1", snapshot, strategy_scope=["long_call"])

        self.assertEqual([item.strategy_key for item in candidates], ["long_call"])


if __name__ == "__main__":
    unittest.main()
