#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from datetime import date, timedelta
import unittest

import pandas as pd

from filters import FilterContext, StockInfo
from rule_engine import RuleMetadata, RuleRegistry
from strategizers import TechnicalPatternStrategizer
from strategy import analyze_technical_pattern
from api.screen_service import build_condition_labels, get_strategy_condition_labels


def _df(rows):
    start = date(2026, 1, 1)
    data = []
    for idx, row in enumerate(rows):
        item = {
            "date": start + timedelta(days=idx),
            "open": row[0],
            "high": row[1],
            "low": row[2],
            "close": row[3],
            "volume": row[4] if len(row) > 4 else 1000 + idx * 10,
        }
        data.append(item)
    return pd.DataFrame(data)


class TechnicalPatternTest(unittest.TestCase):
    def test_condition_labels_use_rule_names_and_never_leak_internal_keys(self):
        labels = build_condition_labels([
            {"rule_key": "ema_breakout", "rule_name": "均线突破", "filter_name": "EMABreakoutStrategizer", "result": "pass", "reason": "收盘价突破均线"},
            {"rule_key": "profitability", "rule_name": "盈利能力", "filter_name": "ProfitabilityFilter", "result": "fail", "reason": "净利润为负"},
            {"rule_key": "unknown_rule", "filter_name": "UnknownRule", "result": "error", "reason": "数据缺失"},
        ])

        self.assertEqual(labels["matched_conditions"], [{"label": "均线突破", "reason": "收盘价突破均线"}])
        self.assertEqual(labels["rejected_conditions"], [
            {"label": "盈利能力", "reason": "净利润为负"},
            {"label": "未命名规则", "reason": "数据缺失"},
        ])

    def test_analyze_bullish_engulfing_detects_latest_two_candles(self):
        df = _df([
            (10.0, 10.3, 9.7, 9.8),
            (9.7, 11.2, 9.6, 10.8),
        ])

        signal = analyze_technical_pattern(df, "bullish_engulfing")

        self.assertTrue(signal.satisfied)
        self.assertEqual(signal.pattern_label, "看涨吞没")
        self.assertEqual(signal.direction, "bullish")
        self.assertEqual(signal.details["pattern_label"], "看涨吞没")

    def test_analyze_three_black_crows_detects_bearish_sequence(self):
        df = _df([
            (11.0, 11.2, 10.4, 10.5),
            (10.4, 10.6, 9.7, 9.8),
            (9.7, 9.9, 9.0, 9.1),
        ])

        signal = analyze_technical_pattern(df, "three_black_crows")

        self.assertTrue(signal.satisfied)
        self.assertEqual(signal.pattern_label, "三只乌鸦")
        self.assertEqual(signal.direction, "bearish")

    def test_technical_pattern_strategizer_outputs_chinese_label_details(self):
        stock = StockInfo(market="HK", code="HK.00001", name="测试")
        stock.kline_df = _df([
            (10.0, 10.2, 9.6, 9.7),
            (9.6, 11.0, 9.5, 10.9),
        ])
        strategizer = TechnicalPatternStrategizer(pattern_key="bullish_engulfing")

        output = strategizer.apply(stock, FilterContext(check_date=date(2026, 1, 2), market="HK"))

        self.assertTrue(output.satisfied)
        self.assertEqual(output.details["pattern_label"], "看涨吞没")
        self.assertEqual(get_strategy_condition_labels(output.name, output.details), ["看涨吞没"])

    def test_rule_registry_builds_technical_pattern_strategizer(self):
        metadata = RuleMetadata(
            market="HK",
            rule_key="bullish_engulfing",
            rule_name="看涨吞没",
            rule_type="strategy",
            strategy_category="technical",
            implementation="TechnicalPatternStrategizer",
            params={"pattern_key": "bullish_engulfing"},
            enabled=True,
            display_order=300,
        )

        created = RuleRegistry.default().create(metadata)

        self.assertIsInstance(created, TechnicalPatternStrategizer)

    def test_macd_bullish_cross_pattern_is_supported(self):
        closes = [20, 19, 18, 17, 16, 15, 14, 13, 12, 11, 10, 10.5]
        rows = []
        for idx, close in enumerate(closes):
            open_price = close - 0.1
            rows.append((open_price, close + 0.5, close - 0.5, close, 2000 + idx * 20))

        signal = analyze_technical_pattern(
            _df(rows),
            "macd_bullish_cross",
            min_rows=5,
            fast_period=3,
            slow_period=8,
            signal_period=5,
        )

        self.assertTrue(signal.satisfied)
        self.assertEqual(signal.pattern_label, "MACD金叉")
        self.assertEqual(signal.direction, "bullish")
        self.assertIn("macd", signal.details)


if __name__ == "__main__":
    unittest.main()
