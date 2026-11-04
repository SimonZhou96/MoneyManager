#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import unittest
from datetime import date

from quant_lab.backtest import BacktestRequest, BacktestRunner
from quant_lab.models import Bar, Signal


class StaticStrategy:
    mode = "trade_backtest"

    def evaluate(self, stock, dates):
        return [
            Signal(signal_id="s1", ts=date(2026, 1, 1), market="US", symbol="US.AAPL", direction="buy", reason="entry"),
            Signal(signal_id="s2", ts=date(2026, 1, 3), market="US", symbol="US.AAPL", direction="sell", reason="exit"),
        ]


class StaticDataProvider:
    def bars_for(self, market, symbol, start, end):
        return [
            Bar(ts=date(2026, 1, 1), open=100, high=101, low=99, close=100, volume=1000),
            Bar(ts=date(2026, 1, 2), open=105, high=106, low=104, close=105, volume=1000),
            Bar(ts=date(2026, 1, 3), open=110, high=111, low=109, close=110, volume=1000),
        ]


class BacktestRunnerTest(unittest.TestCase):
    def test_runner_generates_trades_equity_and_metrics(self):
        request = BacktestRequest(
            market="US",
            symbols=["US.AAPL"],
            start=date(2026, 1, 1),
            end=date(2026, 1, 3),
            initial_cash=10000,
            quantity=10,
        )
        runner = BacktestRunner(data_provider=StaticDataProvider(), strategy=StaticStrategy())

        result = runner.run(request)

        self.assertEqual(len(result.signals), 2)
        self.assertEqual(len(result.trades), 2)
        self.assertGreater(result.metrics.total_return, 0)
        self.assertEqual(result.metrics.trade_count, 1)


if __name__ == "__main__":
    unittest.main()
