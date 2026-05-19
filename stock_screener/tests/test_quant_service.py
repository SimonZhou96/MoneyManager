#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import unittest
from datetime import date
from unittest.mock import patch

import pandas as pd

from db import InMemoryQuantRepository
from quant_lab.backtest import BacktestResult
from quant_lab.models import Bar, Signal
from quant_lab.service import QuantLabService, _chart_payload, _build_default_data_provider


class RepositoryBackedQuantRepository(InMemoryQuantRepository):
    def get_screening_rule_metadata(self, market):
        return [
            {
                "market": market,
                "rule_key": "price_in_range",
                "rule_name": "价格区间",
                "rule_type": "filter",
                "implementation": "PriceFilter",
                "params_json": {"min_price": 1, "max_price": 200},
                "enabled": True,
                "display_order": 1,
                "description": "",
            }
        ]

    def get_screening_rule_chain(self, market, chain_key, timeframe="*"):
        if chain_key != "default_zuoyi_and_other":
            return None
        return {
            "market": market,
            "timeframe": timeframe,
            "chain_key": chain_key,
            "chain_name": "测试规则链",
            "expression_json": {"ref": "price_in_range"},
            "enabled": True,
            "priority": 100,
            "description": "",
        }

    def get_active_screening_rule_chain(self, market, timeframe="*"):
        return self.get_screening_rule_chain(market, "default_zuoyi_and_other", timeframe)

    def get_kline_cache(self, market, code, timeframe, max_count=500):
        if market != "US" or code != "US.AAPL" or timeframe != "1d":
            return pd.DataFrame()
        return pd.DataFrame(
            [
                {"date": "2026-01-01", "open": 100, "high": 101, "low": 99, "close": 100, "volume": 1000},
                {"date": "2026-01-02", "open": 101, "high": 102, "low": 100, "close": 101, "volume": 1000},
            ]
        )


class QuantLabServiceTest(unittest.TestCase):
    def test_run_backtest_uses_repository_rule_chain_and_kline_cache(self):
        repo = RepositoryBackedQuantRepository()
        service = QuantLabService(repository=repo)
        response = service.submit_backtest(
            {
                "market": "US",
                "symbols": ["US.AAPL"],
                "entry_chain_key": "default",
                "exit_policy": {"type": "fixed_holding_days", "days": 1},
                "start": "2026-01-01",
                "end": "2026-01-02",
                "initial_cash": 10000,
                "quantity": 10,
                "commission_rate": 0.001,
                "slippage_rate": 0.001,
                "max_position_weight": 1.0,
            },
            user_id=7,
        )

        service.run_backtest(response["run_id"])

        row = repo.get_quant_backtest_run(response["run_id"])
        assert row is not None
        self.assertEqual(row["status"], "completed")
        self.assertEqual(row["progress_pct"], 100)
        self.assertGreater(row["metrics"]["total_return"], 0)
        self.assertEqual(len(row["chart"]["symbols"][0]["bars"]), 2)
        self.assertEqual([item["direction"] for item in row["chart"]["symbols"][0]["signals"]], ["buy", "sell"])
        self.assertEqual([item["side"] for item in row["chart"]["symbols"][0]["trades"]], ["buy", "sell"])
        self.assertTrue(any("生成" in item["message"] for item in row["progress_logs"]))

    def test_default_data_provider_uses_futu_opend_when_quant_local_mode_enabled(self):
        quote_ctx = object()
        with patch.dict("os.environ", {"QUANT_ENABLE_FUTU_OPEND": "1", "FUTU_HOST": "127.0.0.1", "FUTU_PORT": "11111"}, clear=False):
            with patch("quant_lab.service.OpenQuoteContext", return_value=quote_ctx) as open_quote:
                provider = _build_default_data_provider(RepositoryBackedQuantRepository(), {"timeframe": "1d"})

        open_quote.assert_called_once_with(host="127.0.0.1", port=11111)
        self.assertIs(provider.quote_ctx, quote_ctx)

    def test_chart_payload_includes_default_rule_strategy_overlays(self):
        bars = [
            Bar(ts=date(2026, 1, 1), open=10, high=11, low=9, close=10, volume=100),
            Bar(ts=date(2026, 1, 2), open=10, high=12, low=9, close=11, volume=120),
            Bar(ts=date(2026, 1, 3), open=11, high=13, low=10, close=12, volume=180),
            Bar(ts=date(2026, 1, 4), open=12, high=14, low=11, close=13, volume=130),
        ]
        signal = Signal(
            signal_id="s1",
            ts=date(2026, 1, 3),
            market="US",
            symbol="US.AAPL",
            direction="buy",
            reason="rule chain passed",
            rule_chain_snapshot={
                "rule_outputs": [
                    {
                        "rule_key": "zuoyi_signal",
                        "rule_name": "左一战法",
                        "rule_type": "strategy",
                        "implementation": "ZuoYiStrategizer",
                        "result": "pass",
                        "details": {
                            "signals": [
                                {
                                    "direction": "bullish",
                                    "left_one_date": "2026-01-01",
                                    "median_date": "2026-01-02",
                                    "breakout_date": "2026-01-03",
                                    "left_one_high": 11,
                                    "left_one_low": 9,
                                    "median_high": 12,
                                    "median_low": 9,
                                    "breakout_close": 12,
                                }
                            ]
                        },
                    },
                    {
                        "rule_key": "ema_breakout",
                        "rule_name": "EMA 突破",
                        "rule_type": "strategy",
                        "implementation": "EMABreakoutStrategizer",
                        "result": "pass",
                        "details": {"breakout_date": "2026-01-03", "ema10": 11.5, "ema150": 10.5},
                    },
                    {
                        "rule_key": "rsi_oversold",
                        "rule_name": "RSI 超卖",
                        "rule_type": "strategy",
                        "implementation": "RSIOversoldStrategizer",
                        "result": "fail",
                        "details": {"period": 14, "threshold": 30},
                    },
                    {
                        "rule_key": "volume_spike_prior3",
                        "rule_name": "放量超前三日",
                        "rule_type": "strategy",
                        "implementation": "TodayVolumeExceedsPrior3MaxStrategizer",
                        "result": "pass",
                        "details": {"today_volume": 180, "max_volume_prior3": 120},
                    },
                    {
                        "rule_key": "daily_rise_4_45",
                        "rule_name": "当日涨 4%~4.5%",
                        "rule_type": "strategy",
                        "implementation": "DailyRise4To45Strategizer",
                        "result": "pass",
                        "details": {"pct_change": 4.2, "band_min": 4.0, "band_max": 4.5},
                    },
                ]
            },
        )

        chart = _chart_payload(BacktestResult(signals=[signal], bars_by_symbol={"US.AAPL": bars}))

        overlays = chart["symbols"][0]["overlays"]
        self.assertEqual([item["type"] for item in overlays], ["zuoyi", "ema", "rsi", "volume", "pct_change"])
        self.assertEqual(overlays[0]["items"][0]["left_one_high"], 11)
        self.assertEqual(overlays[1]["lines"][0]["name"], "EMA10")
        self.assertEqual(overlays[2]["thresholds"], [30, 70])
        self.assertEqual(overlays[3]["signals"][0]["date"], "2026-01-03")
        self.assertEqual(overlays[4]["signals"][0]["pct_change"], 4.2)


if __name__ == "__main__":
    unittest.main()
