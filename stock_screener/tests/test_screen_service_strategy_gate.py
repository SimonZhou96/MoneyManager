#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import unittest
from pathlib import Path
from unittest.mock import patch

from filters import StockInfo, StockFilterResult
from filters import FilterOutput, FilterResult
from rule_engine import RuleMetadata
from strategizers import StrategizerOutput, StrategyChainResult
import api.screen_service as screen_service
from api.screen_service import evaluate_strategy_gate


def make_strategy_result(outputs):
    return StrategyChainResult(
        stock=StockInfo(market="HK", code="HK.00001", name="Test"),
        any_satisfied=any(output.satisfied for output in outputs),
        outputs=outputs,
    )


def output(name, satisfied):
    return StrategizerOutput(name=name, satisfied=satisfied)


class ScreenServiceStrategyGateTest(unittest.TestCase):
    def test_screen_service_wires_signal_and_market_intel_runtime_gates(self):
        content = (Path(__file__).resolve().parents[1] / "api" / "screen_service.py").read_text(encoding="utf-8")

        self.assertIn("requires_signal_analysis()", content)
        self.assertIn("requires_market_intel_macro_score()", content)
        self.assertIn("build_market_intel_service(mysql_config, enabled=True)", content)
        self.assertIn("market_intel_service", content)
        self.assertIn("macro_score_scorer", content)

    def test_screen_service_writes_score_summary_from_rule_details(self):
        content = (Path(__file__).resolve().parents[1] / "api" / "screen_service.py").read_text(encoding="utf-8")

        self.assertIn("aggregate_rule_scores", content)
        self.assertIn("technical_score", content)
        self.assertIn("macro_score", content)
        self.assertIn("final_score", content)
        self.assertIn("strategy_category", content)

    def test_zuoyi_and_other_strategy_passes(self):
        result = make_strategy_result([
            output("ZuoYiStrategizer", True),
            output("EMABreakoutStrategizer", True),
            output("RSIOversoldStrategizer", False),
        ])

        self.assertTrue(evaluate_strategy_gate(result, require_zuoyi_strategy=True))

    def test_other_strategy_without_zuoyi_does_not_pass(self):
        result = make_strategy_result([
            output("ZuoYiStrategizer", False),
            output("EMABreakoutStrategizer", True),
        ])

        self.assertFalse(evaluate_strategy_gate(result, require_zuoyi_strategy=True))

    def test_zuoyi_without_other_strategy_does_not_pass(self):
        result = make_strategy_result([
            output("ZuoYiStrategizer", True),
            output("EMABreakoutStrategizer", False),
            output("RSIOversoldStrategizer", False),
        ])

        self.assertFalse(evaluate_strategy_gate(result, require_zuoyi_strategy=True))

    def test_no_strategy_signal_does_not_pass(self):
        result = make_strategy_result([
            output("ZuoYiStrategizer", False),
            output("EMABreakoutStrategizer", False),
        ])

        self.assertFalse(evaluate_strategy_gate(result, require_zuoyi_strategy=True))

    def test_disabling_zuoyi_keeps_legacy_any_strategy_logic(self):
        result = make_strategy_result([
            output("EMABreakoutStrategizer", True),
            output("RSIOversoldStrategizer", False),
        ])

        self.assertTrue(evaluate_strategy_gate(result, require_zuoyi_strategy=False))

    def test_run_screening_task_keeps_strategy_details_when_zuoyi_gate_fails(self):
        class FakeDB:
            instances = []

            def __init__(self, config):
                self.records = []
                self.statuses = []
                self.__class__.instances.append(self)

            def init_schema(self, timeframe):
                pass

            def get_stocks_by_codes(self, market, codes, include_fundamentals=True):
                return []

            def update_task_progress(self, *args, **kwargs):
                pass

            def upsert_screening_results(self, check_date, results):
                self.records.extend(results)

            def update_task_status(self, task_id, status):
                self.statuses.append((task_id, status))

            def close(self):
                pass

        class FakeFilterChain:
            _filters = []

            def list_filters(self):
                return ["AlwaysPassFilter"]

            def apply(self, stock, context):
                return StockFilterResult(stock=stock, passed=True)

        class FakeStrategyChain:
            def list_strategizers(self):
                return []

            def apply(self, stock, context):
                return make_strategy_result([
                    output("ZuoYiStrategizer", False),
                    output("EMABreakoutStrategizer", True),
                ])

        FakeDB.instances = []
        with patch.object(screen_service, "MarketDatabase", FakeDB), \
                patch.object(screen_service, "create_filter_chain_from_params", return_value=FakeFilterChain()), \
                patch.object(screen_service, "create_strategizer_chain_from_params", return_value=FakeStrategyChain()):
            screen_service.run_screening_task(
                mysql_config=object(),
                task_id="task-1",
                market="HK",
                timeframe="1d",
                params={"use_db_rule_engine": False, "use_zuoyi_strategy": True},
                watchlist=[{"code": "HK.00001", "name": "Test"}],
            )

        record = FakeDB.instances[0].records[0]
        self.assertFalse(record["is_passed"])
        self.assertEqual(
            [item["filter_name"] for item in record["filter_details"]],
            ["ZuoYiStrategizer", "EMABreakoutStrategizer"],
        )
        self.assertEqual(
            [item["result"] for item in record["filter_details"]],
            ["fail", "pass"],
        )

    def test_run_screening_task_uses_db_rule_engine_by_default(self):
        class FakeDB:
            instances = []

            def __init__(self, config):
                self.records = []
                self.statuses = []
                self.__class__.instances.append(self)

            def init_schema(self, timeframe):
                pass

            def get_stocks_by_codes(self, market, codes, include_fundamentals=True):
                return []

            def update_task_progress(self, *args, **kwargs):
                pass

            def upsert_screening_results(self, check_date, results):
                self.records.extend(results)

            def update_task_status(self, task_id, status):
                self.statuses.append((task_id, status))

            def close(self):
                pass

        class FakeRuleEngine:
            class ChainConfig:
                chain_key = "fake_chain"

            chain_config = ChainConfig()
            metadata = [
                RuleMetadata(
                    market="HK",
                    rule_key="zuoyi_signal",
                    rule_name="左一战法",
                    rule_type="strategy",
                    strategy_category="technical",
                    implementation="ZuoYiStrategizer",
                ),
                RuleMetadata(
                    market="HK",
                    rule_key="ema_breakout",
                    rule_name="EMA突破",
                    rule_type="strategy",
                    strategy_category="technical",
                    implementation="EMABreakoutStrategizer",
                ),
                RuleMetadata(
                    market="HK",
                    rule_key="market_intel_macro_score_link",
                    rule_name="市场情报宏观评分",
                    rule_type="strategy",
                    strategy_category="macro",
                    implementation="MarketIntelMacroScoreStrategizer",
                ),
            ]
            metadata_by_key = {item.rule_key: item for item in metadata}

            def has_rules(self):
                return True

            def requires_kline(self):
                return False

            def requires_signal_analysis(self):
                return False

            def requires_market_intel_macro_score(self):
                return False

            def requires_macro_analysis(self):
                return False

            def evaluate_stock(self, stock, context, **kwargs):
                return StockFilterResult(
                    stock=stock,
                    passed=True,
                    filter_outputs=[
                        FilterOutput(
                            filter_name="ZuoYiStrategizer",
                            result=FilterResult.PASS,
                            reason="pass",
                            details={"direction": "bullish"},
                        ),
                        FilterOutput(
                            filter_name="EMABreakoutStrategizer",
                            result=FilterResult.PASS,
                            reason="pass",
                        ),
                        FilterOutput(
                            filter_name="MarketIntelMacroScoreStrategizer",
                            result=FilterResult.PASS,
                            reason="macro pass",
                            details={"macro_score": 50, "technical_weight": 0.6, "macro_weight": 0.4},
                        ),
                    ],
                )

        FakeDB.instances = []
        with patch.object(screen_service, "MarketDatabase", FakeDB), \
                patch.object(screen_service, "create_rule_engine_from_db", return_value=FakeRuleEngine()):
            screen_service.run_screening_task(
                mysql_config=object(),
                task_id="task-2",
                market="HK",
                timeframe="1d",
                params={},
                watchlist=[{"code": "HK.00001", "name": "Test"}],
            )

        record = FakeDB.instances[0].records[0]
        self.assertTrue(record["is_passed"])
        self.assertEqual(
            [item["filter_name"] for item in record["filter_details"]],
            ["ZuoYiStrategizer", "EMABreakoutStrategizer", "MarketIntelMacroScoreStrategizer"],
        )
        self.assertEqual(record["technical_score"], 100.0)
        self.assertEqual(record["macro_score"], 50.0)
        self.assertEqual(record["final_score"], 80.0)
        self.assertEqual(record["score_details"]["technical_weight"], 0.6)
        self.assertEqual(record["filter_details"][2]["strategy_category"], "macro")


if __name__ == "__main__":
    unittest.main()
