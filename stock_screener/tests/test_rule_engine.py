#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import unittest
from datetime import date
from pathlib import Path

import pandas as pd

from filters import AvgDailyVolumeFilter, Filter, FilterContext, FilterResult, StockInfo
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
    def __init__(self, satisfied=True, name="StaticStrategizer", result=""):
        super().__init__(name=name)
        self.satisfied = satisfied
        self.result = result

    def apply(self, stock, context):
        return StrategizerOutput(
            name=self.name,
            satisfied=self.satisfied,
            result=self.result,
            reason="strategy pass" if self.satisfied else "strategy fail",
        )


def metadata(rule_key, rule_type, implementation, enabled=True, params=None, order=1, strategy_category=""):
    return RuleMetadata(
        market="HK",
        rule_key=rule_key,
        rule_name=rule_key,
        rule_type=rule_type,
        implementation=implementation,
        strategy_category=strategy_category,
        params=params or {},
        enabled=enabled,
        display_order=order,
    )


def chain(expression):
    return RuleChainConfig(
        market="HK",
        timeframe="*",
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
            result=params.get("result", ""),
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

    def test_macro_strategy_skip_is_treated_as_non_blocking(self):
        result = self.evaluate(
            [
                metadata(
                    "company_event_hot_news_link",
                    "strategy",
                    "StaticStrategizer",
                    params={"satisfied": False, "result": "skip"},
                    strategy_category="macro",
                ),
            ],
            {"ref": "company_event_hot_news_link"},
        )

        self.assertTrue(result.passed)
        self.assertEqual(result.filter_outputs[0].result, FilterResult.SKIP)

    def test_rule_metadata_supports_strategy_category(self):
        item = RuleMetadata.from_row({
            "market": "HK",
            "rule_key": "company_event_hot_news_link",
            "rule_name": "公司时事与热点新闻关联",
            "rule_type": "strategy",
            "strategy_category": "macro",
            "implementation": "CompanyEventHotNewsStrategizer",
            "params_json": {},
            "enabled": True,
            "display_order": 220,
            "description": "macro strategy",
        })

        self.assertEqual(item.rule_type, "strategy")
        self.assertEqual(item.strategy_category, "macro")

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

            def get_active_screening_rule_chain(self, market, timeframe="*"):
                return {
                    "market": market,
                    "timeframe": timeframe,
                    "chain_key": "default",
                    "chain_name": "默认",
                    "expression_json": {"ref": "zuoyi_signal"},
                    "enabled": True,
                    "priority": 100,
                    "description": "chain",
                }

            def get_screening_rule_chain(self, market, chain_key, timeframe="*"):
                return {
                    "market": market,
                    "timeframe": timeframe,
                    "chain_key": chain_key,
                    "chain_name": "试跑链",
                    "expression_json": {"ref": "zuoyi_signal"},
                    "enabled": False,
                    "priority": 200,
                    "description": "trial",
                }

            def list_screening_rule_chains(self, market, timeframe=None):
                return [
                    self.get_active_screening_rule_chain(market, timeframe or "*"),
                    self.get_screening_rule_chain(market, "trial", timeframe or "*"),
                ]

        repo = RuleRepository(FakeDB())
        metadata_items = repo.load_metadata("HK")
        chain_config = repo.load_active_chain("HK", "1d")
        trial_chain = repo.load_chain("HK", "trial", "5m")
        all_chains = repo.load_chains("HK", "15m")

        self.assertEqual(metadata_items[0].rule_key, "zuoyi_signal")
        self.assertEqual(metadata_items[0].params["signal_window"], 3)
        self.assertEqual(chain_config.expression, {"ref": "zuoyi_signal"})
        self.assertEqual(chain_config.timeframe, "1d")
        self.assertEqual(trial_chain.chain_key, "trial")
        self.assertEqual(trial_chain.timeframe, "5m")
        self.assertFalse(trial_chain.enabled)
        self.assertEqual([item.timeframe for item in all_chains], ["15m", "15m"])
        self.assertEqual([item.chain_key for item in all_chains], ["default", "trial"])

    def test_rule_chain_from_row_defaults_to_wildcard_timeframe(self):
        chain_config = RuleChainConfig.from_row({
            "market": "HK",
            "chain_key": "default",
            "chain_name": "默认",
            "expression_json": {"ref": "zuoyi_signal"},
            "enabled": True,
        })

        self.assertEqual(chain_config.timeframe, "*")

    def test_deployment_sql_contains_tables_and_market_seed_data(self):
        sql_path = Path(__file__).resolve().parents[1] / "sql" / "001_screening_rules.sql"
        content = sql_path.read_text(encoding="utf-8")

        self.assertIn("CREATE TABLE IF NOT EXISTS screening_rule_metadata", content)
        self.assertIn("CREATE TABLE IF NOT EXISTS screening_rule_chains", content)
        self.assertIn("strategy_category VARCHAR(16)", content)
        self.assertIn("timeframe VARCHAR(16) NOT NULL DEFAULT '*'", content)
        self.assertIn("UNIQUE KEY uk_rule_chains_market_timeframe_key (market, timeframe, chain_key)", content)
        self.assertIn("company_event_hot_sector_link", content)
        self.assertIn("company_event_hot_news_link", content)
        for market in ("HK", "A"):
            self.assertIn(f"('{market}', 'zuoyi_signal'", content)
            self.assertIn(f"('{market}', '*', 'default_zuoyi_and_other'", content)
            self.assertIn(f"('{market}', '*', 'trend_capital_accumulation_watch'", content)
            self.assertIn(
                f"('{market}', 'market_cap_range', '市值范围', 'filter', '', 'MarketCapFilter', "
                """'{"min_cap": null, "max_cap": null}', 1,""",
                content,
            )
        self.assertIn("('US', 'zuoyi_signal'", content)
        self.assertIn("('US', '*', 'default_zuoyi_and_other'", content)
        self.assertIn(
            "('US', 'market_cap_range', '市值范围', 'filter', '', 'MarketCapFilter', "
            """'{"min_cap": 5000000000, "max_cap": null, "min_exclusive": true}', 1,""",
            content,
        )
        self.assertIn(
            "('US', 'avg_daily_volume_range', '10天平均成交额范围', 'filter', '', 'AvgDailyVolumeFilter', "
            """'{"min_volume": 20000000, "max_volume": null, "lookback_days": 10, "metric": "turnover", "min_exclusive": true}', 1,""",
            content,
        )
        self.assertNotIn("AvgTurnoverFilter", content)

    def test_avg_daily_volume_filter_can_reuse_turnover_metric(self):
        df = pd.DataFrame({
            "date": pd.date_range("2026-01-01", periods=10, freq="D"),
            "close": [10.0] * 10,
            "volume": [2_500_000] * 10,
        })
        stock = StockInfo(market="US", code="AAPL", name="Apple", kline_df=df)
        context = FilterContext(check_date=date(2026, 1, 10), market="US")
        context.timeframe = "1d"

        output = AvgDailyVolumeFilter(
            min_volume=20_000_000,
            lookback_days=10,
            metric="turnover",
            min_exclusive=True,
        ).apply(stock, context)

        self.assertEqual(output.result, FilterResult.PASS)
        self.assertEqual(output.details["avg_daily_turnover"], 25_000_000)
        self.assertEqual(output.details["source"], "close_volume")

    def test_avg_daily_volume_filter_turnover_metric_uses_strict_min_when_configured(self):
        df = pd.DataFrame({
            "date": pd.date_range("2026-01-01", periods=10, freq="D"),
            "close": [10.0] * 10,
            "volume": [2_000_000] * 10,
        })
        stock = StockInfo(market="US", code="AAPL", name="Apple", kline_df=df)
        context = FilterContext(check_date=date(2026, 1, 10), market="US")
        context.timeframe = "1d"

        output = AvgDailyVolumeFilter(
            min_volume=20_000_000,
            lookback_days=10,
            metric="turnover",
            min_exclusive=True,
        ).apply(stock, context)

        self.assertEqual(output.result, FilterResult.FAIL)


if __name__ == "__main__":
    unittest.main()
