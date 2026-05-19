#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import unittest
from datetime import date

from quant_lab.broker import SimulatedBroker
from quant_lab.models import Bar, Signal


class SimulatedBrokerTest(unittest.TestCase):
    def test_buy_signal_creates_filled_trade_with_fee_and_slippage(self):
        broker = SimulatedBroker(initial_cash=10000, commission_rate=0.001, slippage_rate=0.002, max_position_weight=1.0)
        signal = Signal(signal_id="s1", ts=date(2026, 1, 2), market="US", symbol="US.AAPL", direction="buy", reason="rule pass")
        bar = Bar(ts=date(2026, 1, 2), open=100, high=101, low=99, close=100, volume=100000)

        order, trade = broker.apply_signal(signal, bar, quantity=10)

        self.assertEqual(order.status, "filled")
        self.assertIsNotNone(trade)
        assert trade is not None
        self.assertAlmostEqual(trade.price, 100.2, places=4)
        self.assertAlmostEqual(trade.fee, 1.002, places=4)
        self.assertLess(broker.cash, 10000)

    def test_rejects_order_when_max_position_weight_is_exceeded(self):
        broker = SimulatedBroker(initial_cash=1000, commission_rate=0.001, slippage_rate=0, max_position_weight=0.1)
        signal = Signal(signal_id="s1", ts=date(2026, 1, 2), market="US", symbol="US.AAPL", direction="buy", reason="rule pass")
        bar = Bar(ts=date(2026, 1, 2), open=100, high=101, low=99, close=100, volume=100000)

        order, trade = broker.apply_signal(signal, bar, quantity=10)

        self.assertEqual(order.status, "rejected")
        self.assertEqual(order.rejected_reason, "max_position_weight_exceeded")
        self.assertIsNone(trade)


if __name__ == "__main__":
    unittest.main()
