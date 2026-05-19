#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import unittest
from datetime import date

from filters import StockInfo
from quant_lab.rule_chain_adapter import RuleChainStrategyAdapter
from rule_engine import RuleChainConfig


class FakeRuleResult:
    def __init__(self, passed):
        self.passed = passed
        self.filter_outputs = []


class FakeRuleEngine:
    def __init__(self, passed_dates):
        self.passed_dates = set(passed_dates)

    def evaluate_stock(self, stock, context):
        return FakeRuleResult(context.check_date in self.passed_dates)


class RuleChainStrategyAdapterTest(unittest.TestCase):
    def test_entry_signal_is_emitted_when_rule_chain_passes(self):
        chain = RuleChainConfig(market="US", timeframe="1d", chain_key="zuoyi", chain_name="左一", expression={"ref": "zuoyi_signal"})
        adapter = RuleChainStrategyAdapter(
            chain=chain,
            rule_engine=FakeRuleEngine({date(2026, 1, 2)}),
            exit_policy={"type": "fixed_holding_days", "days": 2},
        )

        signals = adapter.evaluate(
            stock=StockInfo(market="US", code="US.AAPL", name="Apple"),
            dates=[date(2026, 1, 1), date(2026, 1, 2)],
        )

        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].direction, "buy")
        self.assertEqual(signals[0].rule_chain_key, "zuoyi")
        self.assertIn("rule chain passed", signals[0].reason)

    def test_entry_only_chain_without_exit_policy_is_signal_analysis(self):
        chain = RuleChainConfig(market="US", timeframe="1d", chain_key="entry_only", chain_name="entry", expression={"ref": "zuoyi_signal"})
        adapter = RuleChainStrategyAdapter(chain=chain, rule_engine=FakeRuleEngine({date(2026, 1, 2)}), exit_policy=None)

        self.assertEqual(adapter.mode, "signal_analysis")


if __name__ == "__main__":
    unittest.main()
