#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import unittest
from datetime import date

import pandas as pd

from filters import FilterContext, StockInfo
from strategizers import ZuoYiStrategizer
from strategy import check_zuoyi_strategy


def make_kline(rows):
    return pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=len(rows), freq="D"),
        "high": [r[0] for r in rows],
        "low": [r[1] for r in rows],
        "close": [r[2] for r in rows],
        "volume": [1000 + i for i in range(len(rows))],
    })


class ZuoYiStrategyTest(unittest.TestCase):
    def test_bullish_breakout_on_second_bar(self):
        df = make_kline([
            (10.0, 8.0, 9.0),
            (9.0, 8.5, 8.8),
            (9.2, 7.0, 7.4),
            (9.4, 7.3, 9.0),
            (10.5, 8.7, 10.2),
        ])

        result = check_zuoyi_strategy(df, signal_window=3)

        self.assertTrue(result.satisfied)
        self.assertEqual(result.result_type, "bullish_breakout")
        self.assertEqual(len(result.signals), 1)
        signal = result.signals[0]
        self.assertEqual(signal.direction, "bullish")
        self.assertEqual(signal.left_one_date, date(2026, 1, 1))
        self.assertEqual(signal.median_date, date(2026, 1, 3))
        self.assertEqual(signal.breakout_date, date(2026, 1, 5))
        self.assertEqual(signal.bars_to_breakout, 2)

    def test_bearish_breakdown_on_third_bar(self):
        df = make_kline([
            (12.0, 10.0, 11.0),
            (13.5, 11.0, 12.0),
            (14.0, 10.5, 13.8),
            (13.6, 10.8, 12.0),
            (12.2, 10.1, 10.5),
            (11.8, 9.7, 9.8),
        ])

        result = check_zuoyi_strategy(df, signal_window=3)

        self.assertTrue(result.satisfied)
        self.assertEqual(result.result_type, "bearish_breakdown")
        signal = result.signals[0]
        self.assertEqual(signal.direction, "bearish")
        self.assertEqual(signal.left_one_date, date(2026, 1, 1))
        self.assertEqual(signal.median_date, date(2026, 1, 3))
        self.assertEqual(signal.breakout_date, date(2026, 1, 6))
        self.assertEqual(signal.bars_to_breakout, 3)

    def test_containment_bars_are_skipped_when_finding_left_one(self):
        df = make_kline([
            (10.0, 8.0, 9.0),
            (9.0, 8.5, 8.8),
            (9.2, 7.0, 7.4),
            (10.5, 8.7, 10.2),
        ])

        result = check_zuoyi_strategy(df, signal_window=3)

        self.assertTrue(result.satisfied)
        self.assertEqual(result.signals[0].left_one_date, date(2026, 1, 1))
        self.assertEqual(result.signals[0].left_one_high, 10.0)

    def test_same_low_uses_higher_high_as_bullish_median(self):
        df = make_kline([
            (10.0, 8.0, 9.0),
            (9.5, 7.0, 7.8),
            (9.8, 7.0, 7.5),
            (9.1, 7.5, 8.7),
            (10.2, 8.0, 10.1),
        ])

        result = check_zuoyi_strategy(df, signal_window=3)

        self.assertTrue(result.satisfied)
        self.assertEqual(result.signals[0].direction, "bullish")
        self.assertEqual(result.signals[0].median_date, date(2026, 1, 3))

    def test_same_high_uses_lower_low_as_bearish_median(self):
        df = make_kline([
            (12.0, 10.0, 11.0),
            (14.0, 10.7, 13.5),
            (14.0, 10.3, 13.7),
            (13.6, 10.8, 12.0),
            (12.5, 10.5, 10.4),
            (12.0, 9.8, 9.9),
        ])

        result = check_zuoyi_strategy(df, signal_window=3)

        self.assertTrue(result.satisfied)
        self.assertEqual(result.signals[0].direction, "bearish")
        self.assertEqual(result.signals[0].median_date, date(2026, 1, 3))

    def test_breakout_after_signal_window_is_not_satisfied(self):
        df = make_kline([
            (10.0, 8.0, 9.0),
            (9.0, 8.5, 8.8),
            (9.2, 7.0, 7.4),
            (9.4, 7.3, 9.0),
            (9.8, 7.8, 9.8),
            (9.9, 8.1, 9.9),
            (10.5, 8.7, 10.2),
        ])

        result = check_zuoyi_strategy(df, signal_window=3)

        self.assertFalse(result.satisfied)
        self.assertEqual(result.result_type, "no_signal")

    def test_invalid_data_reports_missing_columns(self):
        df = pd.DataFrame({
            "date": pd.date_range("2026-01-01", periods=3, freq="D"),
            "high": [10.0, 9.2, 10.5],
            "close": [9.0, 7.4, 10.2],
        })

        result = check_zuoyi_strategy(df, signal_window=3)

        self.assertFalse(result.satisfied)
        self.assertEqual(result.result_type, "invalid_data")
        self.assertIn("缺少字段", result.reason)

    def test_insufficient_data_reports_clear_reason(self):
        df = make_kline([
            (10.0, 8.0, 9.0),
            (9.2, 7.0, 7.4),
        ])

        result = check_zuoyi_strategy(df, signal_window=3)

        self.assertFalse(result.satisfied)
        self.assertEqual(result.result_type, "insufficient_data")
        self.assertIn("至少需要", result.reason)

    def test_check_date_truncates_future_breakout(self):
        df = make_kline([
            (10.0, 8.0, 9.0),
            (9.0, 8.5, 8.8),
            (9.2, 7.0, 7.4),
            (9.4, 7.3, 9.0),
            (10.5, 8.7, 10.2),
        ])

        result = check_zuoyi_strategy(df, check_date=date(2026, 1, 4), signal_window=3)

        self.assertFalse(result.satisfied)
        self.assertEqual(result.result_type, "no_signal")
        self.assertEqual(result.data_rows, 4)

    def test_zuoyi_strategizer_outputs_directional_details(self):
        df = make_kline([
            (10.0, 8.0, 9.0),
            (9.0, 8.5, 8.8),
            (9.2, 7.0, 7.4),
            (9.4, 7.3, 9.0),
            (10.5, 8.7, 10.2),
        ])
        stock = StockInfo(market="HK", code="HK.00001", name="Test", kline_df=df)
        context = FilterContext(check_date=date(2026, 1, 5), market="HK")

        output = ZuoYiStrategizer(signal_window=3).apply(stock, context)

        self.assertTrue(output.satisfied)
        self.assertEqual(output.name, "ZuoYiStrategizer")
        self.assertEqual(output.details["direction"], "bullish")
        self.assertEqual(output.details["signals"][0]["direction"], "bullish")


if __name__ == "__main__":
    unittest.main()
