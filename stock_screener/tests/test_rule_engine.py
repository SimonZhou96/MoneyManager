#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from stock_screener.filters import AvgDailyVolumeFilter, Filter, FilterContext, FilterResult, StockInfo
from stock_screener.rule_engine import (
    RuleChainConfig,
    RuleEngine,
    RuleMetadata,
    RuleRegistry,
    RuleRepository,
)
from stock_screener.strategizers import Strategizer, StrategizerOutput

from stock_screener.market_intel.models import IntelItem, MarketIntelBundle, StockIntelBundle


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


class FakeMarketIntelService:
    def __init__(self, stock_items=None, market_items=None):
        self.calls = []
        self.stock_items = stock_items
        self.market_items = market_items

    def get_stock_intel(self, market, code, force_refresh=False):
        self.calls.append(("stock", market, code, force_refresh))
        now = datetime(2026, 5, 26, 10, 0, tzinfo=timezone.utc)
        items = self.stock_items
        if items is None:
            items = [
                IntelItem(
                    scope_type="stock",
                    market=market,
                    code=code,
                    source="test",
                    provider="test",
                    item_type="announcement",
                    title="AI 订单公告",
                    summary="新增 AI 订单",
                    event_time=now,
                    published_at=now,
                    fetched_at=now,
                    expires_at=now + timedelta(hours=6),
                    dedupe_key="order",
                )
            ]
        return StockIntelBundle(
            market=market,
            code=code,
            items=items,
            freshness_status="fresh" if items else "empty",
        ).to_dict()

    def get_market_digest(self, market, force_refresh=False):
        self.calls.append(("market", market, "", force_refresh))
        return MarketIntelBundle(
            market=market,
            items=list(self.market_items or []),
            freshness_status="fresh" if self.market_items else "empty",
        ).to_dict()


class FakeMacroScorer:
    model_name = "fake"

    def __init__(self, passed=True, score_value=72):
        self.calls = []
        self.passed = passed
        self.score_value = score_value

    def score(self, package, threshold=60):
        try:
            from market_intel.macro_scoring import MacroScoreResult
        except ModuleNotFoundError:
            from stock_screener.market_intel.macro_scoring import MacroScoreResult

        self.calls.append((package, threshold))
        return MacroScoreResult(
            macro_score=self.score_value,
            passed=self.passed,
            threshold=threshold,
            sub_scores={
                "company_event_strength": 80,
                "sector_heat": 70,
                "news_validation": 65,
                "impact_direction": 75,
                "source_credibility": 60,
                "freshness": 85,
            },
            weighted_contribution={},
            summary="宏观共振成立" if self.passed else "宏观评分未达标",
            temporal_summary="没有较新事件反转信号",
            risks=[],
            evidence_refs=[],
        )


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

    def test_market_intel_macro_score_rule_passes_and_returns_details(self):
        metadata_items = [
            metadata(
                "market_intel_macro_score_link",
                "strategy",
                "MarketIntelMacroScoreStrategizer",
                strategy_category="macro",
                params={"threshold": 60, "refresh_policy": "cache_or_refresh"},
            )
        ]
        engine = RuleEngine(metadata_items, chain({"ref": "market_intel_macro_score_link"}), registry=RuleRegistry.default())
        context = FilterContext(check_date=date(2026, 5, 26), market="A")
        service = FakeMarketIntelService()
        scorer = FakeMacroScorer()
        context.set_cache("market_intel_service", service)
        context.set_cache("macro_score_scorer", scorer)

        result = engine.evaluate_stock(StockInfo(market="A", code="SZ.000001", name="平安银行"), context)

        self.assertTrue(result.passed)
        output = result.filter_outputs[0]
        self.assertEqual(output.result, FilterResult.PASS)
        self.assertEqual(output.details["macro_score"], 72)
        self.assertEqual(output.details["temporal_summary"], "没有较新事件反转信号")
        self.assertEqual(output.details["model"], "fake")
        self.assertIn("evidence_digest", output.details)
        self.assertEqual(scorer.calls[0][1], 60.0)

    def test_market_intel_macro_score_missing_service_skips(self):
        metadata_items = [
            metadata(
                "market_intel_macro_score_link",
                "strategy",
                "MarketIntelMacroScoreStrategizer",
                strategy_category="macro",
            )
        ]
        engine = RuleEngine(metadata_items, chain({"ref": "market_intel_macro_score_link"}), registry=RuleRegistry.default())
        context = FilterContext(check_date=date(2026, 5, 26), market="A")
        context.set_cache("macro_score_scorer", FakeMacroScorer())

        result = engine.evaluate_stock(StockInfo(market="A", code="SZ.000001", name="平安银行"), context)

        self.assertTrue(result.passed)
        output = result.filter_outputs[0]
        self.assertEqual(output.result, FilterResult.SKIP)
        self.assertIn("market_intel_service", output.reason)

    def test_market_intel_macro_score_missing_scorer_errors_and_blocks(self):
        metadata_items = [
            metadata(
                "market_intel_macro_score_link",
                "strategy",
                "MarketIntelMacroScoreStrategizer",
                strategy_category="macro",
            )
        ]
        engine = RuleEngine(metadata_items, chain({"ref": "market_intel_macro_score_link"}), registry=RuleRegistry.default())
        context = FilterContext(check_date=date(2026, 5, 26), market="A")
        context.set_cache("market_intel_service", FakeMarketIntelService())

        result = engine.evaluate_stock(StockInfo(market="A", code="SZ.000001", name="平安银行"), context)

        self.assertFalse(result.passed)
        output = result.filter_outputs[0]
        self.assertEqual(output.result, FilterResult.ERROR)
        self.assertIn("macro_score_scorer", output.reason)
        self.assertTrue(output.details["error"])

    def test_market_intel_macro_score_force_refresh_fetches_stock_and_market(self):
        metadata_items = [
            metadata(
                "market_intel_macro_score_link",
                "strategy",
                "MarketIntelMacroScoreStrategizer",
                strategy_category="macro",
                params={"refresh_policy": "force_refresh"},
            )
        ]
        engine = RuleEngine(metadata_items, chain({"ref": "market_intel_macro_score_link"}), registry=RuleRegistry.default())
        context = FilterContext(check_date=date(2026, 5, 26), market="A")
        service = FakeMarketIntelService()
        context.set_cache("market_intel_service", service)
        context.set_cache("macro_score_scorer", FakeMacroScorer())

        engine.evaluate_stock(StockInfo(market="A", code="SZ.000001", name="平安银行"), context)

        self.assertEqual(
            service.calls,
            [
                ("stock", "A", "SZ.000001", True),
                ("market", "A", "", True),
            ],
        )

    def test_market_intel_macro_score_no_scoreable_evidence_skips_with_gaps(self):
        metadata_items = [
            metadata(
                "market_intel_macro_score_link",
                "strategy",
                "MarketIntelMacroScoreStrategizer",
                strategy_category="macro",
            )
        ]
        engine = RuleEngine(metadata_items, chain({"ref": "market_intel_macro_score_link"}), registry=RuleRegistry.default())
        context = FilterContext(check_date=date(2026, 5, 26), market="A")
        service = FakeMarketIntelService(stock_items=[])
        scorer = FakeMacroScorer()
        context.set_cache("market_intel_service", service)
        context.set_cache("macro_score_scorer", scorer)

        result = engine.evaluate_stock(StockInfo(market="A", code="SZ.000001", name="平安银行"), context)

        self.assertTrue(result.passed)
        output = result.filter_outputs[0]
        self.assertEqual(output.result, FilterResult.SKIP)
        self.assertIn("data_gaps", output.details)
        self.assertIn("source_status", output.details)
        self.assertEqual(scorer.calls, [])

    def test_market_intel_macro_score_failed_score_returns_fail_details(self):
        metadata_items = [
            metadata(
                "market_intel_macro_score_link",
                "strategy",
                "MarketIntelMacroScoreStrategizer",
                strategy_category="macro",
                params={"threshold": 80},
            )
        ]
        engine = RuleEngine(metadata_items, chain({"ref": "market_intel_macro_score_link"}), registry=RuleRegistry.default())
        context = FilterContext(check_date=date(2026, 5, 26), market="A")
        context.set_cache("market_intel_service", FakeMarketIntelService())
        context.set_cache("macro_score_scorer", FakeMacroScorer(passed=False, score_value=58))

        result = engine.evaluate_stock(StockInfo(market="A", code="SZ.000001", name="平安银行"), context)

        self.assertFalse(result.passed)
        output = result.filter_outputs[0]
        self.assertEqual(output.result, FilterResult.FAIL)
        self.assertEqual(output.details["macro_score"], 58)
        self.assertEqual(output.details["threshold"], 80.0)
        self.assertEqual(output.details["summary"], "宏观评分未达标")
        self.assertIn("sub_scores", output.details)

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
