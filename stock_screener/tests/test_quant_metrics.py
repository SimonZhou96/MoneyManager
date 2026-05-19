#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import unittest
from datetime import date

from quant_lab.metrics import calculate_metric_snapshot
from quant_lab.models import EquityPoint, Trade


class QuantMetricsTest(unittest.TestCase):
    def test_calculates_return_drawdown_win_rate_and_expected_value(self):
        equity = [
            EquityPoint(ts=date(2026, 1, 1), equity=10000, cash=10000, drawdown=0, benchmark_value=10000),
            EquityPoint(ts=date(2026, 1, 2), equity=11000, cash=8000, drawdown=0, benchmark_value=10100),
            EquityPoint(ts=date(2026, 1, 3), equity=10500, cash=10500, drawdown=-0.0454545, benchmark_value=10050),
        ]
        trades = [
            Trade(order_id="o1", trade_id="t1", symbol="US.AAPL", side="buy", quantity=10, price=100, fee=1, slippage=0),
            Trade(order_id="o2", trade_id="t2", symbol="US.AAPL", side="sell", quantity=10, price=110, fee=1, slippage=0),
            Trade(order_id="o3", trade_id="t3", symbol="US.MSFT", side="buy", quantity=5, price=200, fee=1, slippage=0),
            Trade(order_id="o4", trade_id="t4", symbol="US.MSFT", side="sell", quantity=5, price=190, fee=1, slippage=0),
        ]

        metrics = calculate_metric_snapshot(equity, trades)

        self.assertAlmostEqual(metrics.total_return, 0.05, places=4)
        self.assertAlmostEqual(metrics.max_drawdown, -0.0454545, places=4)
        self.assertEqual(metrics.trade_count, 2)
        self.assertAlmostEqual(metrics.win_rate, 0.5, places=4)
        self.assertAlmostEqual(metrics.average_win, 98.0, places=4)
        self.assertAlmostEqual(metrics.average_loss, -52.0, places=4)
        self.assertAlmostEqual(metrics.expected_value, 23.0, places=4)

    def test_empty_inputs_return_zero_metrics(self):
        metrics = calculate_metric_snapshot([], [])
        self.assertEqual(metrics.total_return, 0)
        self.assertEqual(metrics.trade_count, 0)
        self.assertEqual(metrics.win_rate, 0)


if __name__ == "__main__":
    unittest.main()
