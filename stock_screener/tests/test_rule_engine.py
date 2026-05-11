#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import unittest
from datetime import date
from pathlib import Path

from filters import Filter, FilterContext, FilterResult, StockInfo
from rule_engine import (
    RuleChainConfig,
    RuleEngine,
    RuleMetadata,
    RuleRegistry,
    RuleRepository,
)
from strategizers import Strategizer, StrategizerOutput


class StaticFilter(Filter):
    def __init__(self, result="pass", name="StaticFilter"):
        super().__init__(name=name)
        self.result = result

    def apply(self, stock, context):
        if self.result == "pass":
            return self._pass("filter pass")
        if self.result == "skip":
            return self._skip("filter skip")
        if self.result == "error":
            return self._error("filter error")
        return self._fail("filter fail")


class StaticStrategizer(Strategizer):
    def __init__(self, satisfied=True, name="StaticStrategizer"):
        super().__init__(name=name)
        self.satisfied = satisfied

    def apply(self, stock, context):
        return StrategizerOutput(
            name=self.name,
            satisfied=self.satisfied,
            reason="strategy pass" if self.satisfied else "strategy fail",
        )


def metadata(rule_key, rule_type, implementation, enabled=True, params=None, order=1):
    return RuleMetadata(
        market="HK",
        rule_key=rule_key,
        rule_name=rule_key,
        rule_type=rule_type,
        implementation=implementation,
        params=params or {},
        enabled=enabled,
        display_order=order,
    )


def chain(expression):
    return RuleChainConfig(
        market="HK",
        chain_key="test",
        chain_name="test",
        expression=expression,
    )


def registry():
    r = RuleRegistry()
    r.register_filter(
        "StaticFilter",
        lambda params: StaticFilter(
            result=params.get("result", "pass"),
            name=params.get("name", "StaticFilter"),
        ),
    )
    r.register_strategy(
        "StaticStrategizer",
        lambda params: StaticStrategizer(
            satisfied=bool(params.get("satisfied", True)),
            name=params.get("name", "StaticStrategizer"),
        ),
    )
    return r


class RuleEngineTest(unittest.TestCase):
    def evaluate(self, metadata_items, expression):
        engine = RuleEngine(metadata_items, chain(expression), registry=registry())
        return engine.evaluate_stock(
            StockInfo(market="HK", code="HK.00001", name="Test"),
            FilterContext(check_date=date(2026, 1, 1), market="HK"),
        )

    def test_all_filters_zuoyi_and_one_other_strategy_passes(self):
        result = self.evaluate(
            [
                metadata("market_cap_range", "filter", "StaticFilter", params={"result": "pass"}),
                metadata("price_range", "filter", "StaticFilter", params={"result": "pass"}),
                metadata("zuoyi_signal", "strategy", "StaticStrategizer", params={"satisfied": True}),
                metadata("ema_breakout", "strategy", "StaticStrategizer", params={"satisfied": False}),
                metadata("rsi_oversold", "strategy", "StaticStrategizer", params={"satisfied": True}),
            ],
            {
                "and": [
                    {"all_enabled": ["market_cap_range", "price_range"]},
                    {"ref": "zuoyi_signal"},
                    {"any_enabled": ["ema_breakout", "rsi_oversold"]},
                ]
            },
        )

        self.assertTrue(result.passed)
        self.assertEqual(
            [output.filter_name for output in result.filter_outputs],
            ["StaticFilter", "StaticFilter", "StaticStrategizer", "StaticStrategizer", "StaticStrategizer"],
        )

    def test_failed_enabled_hard_filter_blocks_result(self):
        result = self.evaluate(
            [
                metadata("market_cap_range", "filter", "StaticFilter", params={"result": "fail"}),
                metadata("zuoyi_signal", "strategy", "StaticStrategizer", params={"satisfied": True}),
                metadata("ema_breakout", "strategy", "StaticStrategizer", params={"satisfied": True}),
            ],
            {
                "and": [
                    {"all_enabled": ["market_cap_range"]},
                    {"ref": "zuoyi_signal"},
                    {"any_enabled": ["ema_breakout"]},
                ]
            },
        )

        self.assertFalse(result.passed)

    def test_enabled_hard_filter_skip_does_not_block_result(self):
        result = self.evaluate(
            [
                metadata("market_cap_range", "filter", "StaticFilter", params={"result": "skip"}),
                metadata("price_range", "filter", "StaticFilter", params={"result": "pass"}),
                metadata("zuoyi_signal", "strategy", "StaticStrategizer", params={"satisfied": True}),
                metadata("ema_breakout", "strategy", "StaticStrategizer", params={"satisfied": True}),
            ],
            {
                "and": [
                    {"all_enabled": ["market_cap_range", "price_range"]},
                    {"ref": "zuoyi_signal"},
                    {"any_enabled": ["ema_breakout"]},
                ]
            },
        )

        self.assertTrue(result.passed)

    def test_zuoyi_is_required_even_when_other_strategy_passes(self):
        result = self.evaluate(
            [
                metadata("zuoyi_signal", "strategy", "StaticStrategizer", params={"satisfied": False}),
                metadata("ema_breakout", "strategy", "StaticStrategizer", params={"satisfied": True}),
            ],
            {"and": [{"ref": "zuoyi_signal"}, {"any_enabled": ["ema_breakout"]}]},
        )

        self.assertFalse(result.passed)

    def test_zuoyi_alone_is_not_enough(self):
        result = self.evaluate(
            [
                metadata("zuoyi_signal", "strategy", "StaticStrategizer", params={"satisfied": True}),
                metadata("ema_breakout", "strategy", "StaticStrategizer", params={"satisfied": False}),
            ],
            {"and": [{"ref": "zuoyi_signal"}, {"any_enabled": ["ema_breakout"]}]},
        )

        self.assertFalse(result.passed)

    def test_all_enabled_empty_passes_and_any_enabled_empty_fails(self):
        self.assertTrue(self.evaluate([], {"all_enabled": ["disabled"]}).passed)
        self.assertFalse(self.evaluate([], {"any_enabled": ["disabled"]}).passed)

    def test_disabled_or_missing_ref_fails(self):
        disabled = metadata(
            "zuoyi_signal",
            "strategy",
            "StaticStrategizer",
            enabled=False,
            params={"satisfied": True},
        )

        self.assertFalse(self.evaluate([disabled], {"ref": "zuoyi_signal"}).passed)
        self.assertFalse(self.evaluate([], {"ref": "missing_rule"}).passed)

    def test_registry_rejects_unknown_implementation(self):
        with self.assertRaisesRegex(ValueError, "未注册的规则实现"):
            registry().create(metadata("bad", "filter", "DoesNotExist"))

    def test_rule_repository_loads_dataclasses_from_db_rows(self):
        class FakeDB:
            def get_screening_rule_metadata(self, market):
                return [{
                    "market": market,
                    "rule_key": "zuoyi_signal",
                    "rule_name": "左一战法",
                    "rule_type": "strategy",
                    "implementation": "ZuoYiStrategizer",
                    "params_json": {"signal_window": 3},
                    "enabled": True,
                    "display_order": 10,
                    "description": "desc",
                }]

            def get_active_screening_rule_chain(self, market):
                return {
                    "market": market,
                    "chain_key": "default",
                    "chain_name": "默认",
                    "expression_json": {"ref": "zuoyi_signal"},
                    "enabled": True,
                    "priority": 100,
                    "description": "chain",
                }

            def get_screening_rule_chain(self, market, chain_key):
                return {
                    "market": market,
                    "chain_key": chain_key,
                    "chain_name": "试跑链",
                    "expression_json": {"ref": "zuoyi_signal"},
                    "enabled": False,
                    "priority": 200,
                    "description": "trial",
                }

            def list_screening_rule_chains(self, market):
                return [
                    self.get_active_screening_rule_chain(market),
                    self.get_screening_rule_chain(market, "trial"),
                ]

        repo = RuleRepository(FakeDB())
        metadata_items = repo.load_metadata("HK")
        chain_config = repo.load_active_chain("HK")
        trial_chain = repo.load_chain("HK", "trial")
        all_chains = repo.load_chains("HK")

        self.assertEqual(metadata_items[0].rule_key, "zuoyi_signal")
        self.assertEqual(metadata_items[0].params["signal_window"], 3)
        self.assertEqual(chain_config.expression, {"ref": "zuoyi_signal"})
        self.assertEqual(trial_chain.chain_key, "trial")
        self.assertFalse(trial_chain.enabled)
        self.assertEqual([item.chain_key for item in all_chains], ["default", "trial"])

    def test_deployment_sql_contains_tables_and_market_seed_data(self):
        sql_path = Path(__file__).resolve().parents[1] / "sql" / "001_screening_rules.sql"
        content = sql_path.read_text(encoding="utf-8")

        self.assertIn("CREATE TABLE IF NOT EXISTS screening_rule_metadata", content)
        self.assertIn("CREATE TABLE IF NOT EXISTS screening_rule_chains", content)
        for market in ("HK", "US", "A"):
            self.assertIn(f"('{market}', 'zuoyi_signal'", content)
            self.assertIn(f"('{market}', 'default_zuoyi_and_other'", content)
            self.assertIn(
                f"('{market}', 'market_cap_range', '市值范围', 'filter', 'MarketCapFilter', "
                """'{"min_cap": null, "max_cap": null}', 1,""",
                content,
            )


if __name__ == "__main__":
    unittest.main()
