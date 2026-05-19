#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import unittest
from datetime import date

from filters import FilterOutput, FilterResult
from filters import StockInfo
from quant_lab.rule_chain_adapter import RuleChainStrategyAdapter
from rule_engine import RuleChainConfig, RuleMetadata


class FakeRuleResult:
    def __init__(self, passed, filter_outputs=None):
        self.passed = passed
        self.filter_outputs = filter_outputs or []


class FakeRuleEngine:
    def __init__(self, passed_dates, filter_outputs=None):
        self.passed_dates = set(passed_dates)
        self.filter_outputs = filter_outputs or []
        self.metadata = [
            RuleMetadata(
                market="US",
                rule_key="zuoyi_signal",
                rule_name="左一战法",
                rule_type="strategy",
                implementation="ZuoYiStrategizer",
                params={},
                enabled=True,
                display_order=110,
                description="",
            )
        ]

    def evaluate_stock(self, stock, context):
        return FakeRuleResult(context.check_date in self.passed_dates, self.filter_outputs)


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

    def test_fixed_holding_policy_adds_sell_signal(self):
        chain = RuleChainConfig(market="US", timeframe="1d", chain_key="entry", chain_name="entry", expression={"ref": "zuoyi_signal"})
        adapter = RuleChainStrategyAdapter(
            chain=chain,
            rule_engine=FakeRuleEngine({date(2026, 1, 1)}),
            exit_policy={"type": "fixed_holding_days", "days": 1},
        )

        signals = adapter.evaluate(
            stock=StockInfo(market="US", code="US.AAPL", name="Apple"),
            dates=[date(2026, 1, 1), date(2026, 1, 2)],
        )

        self.assertEqual([item.direction for item in signals], ["buy", "sell"])
        self.assertEqual(signals[1].ts, date(2026, 1, 2))
        self.assertIn("fixed holding", signals[1].reason)

    def test_signal_snapshot_keeps_rule_outputs_for_chart_overlays(self):
        chain = RuleChainConfig(market="US", timeframe="1d", chain_key="entry", chain_name="entry", expression={"ref": "zuoyi_signal"})
        rule_output = FilterOutput(
            filter_name="ZuoYiStrategizer",
            result=FilterResult.PASS,
            reason="左一战法看涨",
            details={
                "signals": [
                    {
                        "direction": "bullish",
                        "left_one_date": "2026-01-01",
                        "median_date": "2026-01-02",
                        "breakout_date": "2026-01-03",
                        "left_one_high": 10,
                        "left_one_low": 8,
                        "median_high": 9,
                        "median_low": 7,
                        "breakout_close": 11,
                    }
                ]
            },
        )
        adapter = RuleChainStrategyAdapter(
            chain=chain,
            rule_engine=FakeRuleEngine({date(2026, 1, 3)}, filter_outputs=[rule_output]),
            exit_policy={"type": "fixed_holding_days", "days": 1},
        )

        signals = adapter.evaluate(
            stock=StockInfo(market="US", code="US.AAPL", name="Apple"),
            dates=[date(2026, 1, 1), date(2026, 1, 2), date(2026, 1, 3), date(2026, 1, 4)],
        )

        rule_outputs = signals[0].rule_chain_snapshot["rule_outputs"]
        self.assertEqual(rule_outputs[0]["rule_key"], "zuoyi_signal")
        self.assertEqual(rule_outputs[0]["rule_name"], "左一战法")
        self.assertEqual(rule_outputs[0]["result"], "pass")
        self.assertEqual(rule_outputs[0]["details"]["signals"][0]["left_one_high"], 10)


if __name__ == "__main__":
    unittest.main()
