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

    def test_caps_quantity_when_position_limit_restricts_full_amount(self):
        """仓位上限不足以买入全部请求数量时，自动缩减到可买数量"""
        broker = SimulatedBroker(initial_cash=1000, commission_rate=0.001, slippage_rate=0, max_position_weight=0.1)
        signal = Signal(signal_id="s1", ts=date(2026, 1, 2), market="US", symbol="US.AAPL", direction="buy", reason="rule pass")
        bar = Bar(ts=date(2026, 1, 2), open=100, high=101, low=99, close=100, volume=100000)

        order, trade = broker.apply_signal(signal, bar, quantity=10)

        # 仓位上限 = 1000*0.1=100，买一股需~100，只能买1股
        self.assertEqual(order.status, "filled")
        self.assertIsNotNone(trade)
        assert trade is not None
        self.assertEqual(trade.quantity, 1)

    def test_rejects_buy_when_cannot_afford_even_one_share(self):
        """现金不够买1股时，拒绝买入"""
        broker = SimulatedBroker(initial_cash=50, commission_rate=0.001, slippage_rate=0, max_position_weight=1.0)
        signal = Signal(signal_id="s1", ts=date(2026, 1, 2), market="US", symbol="US.AAPL", direction="buy", reason="rule pass")
        bar = Bar(ts=date(2026, 1, 2), open=100, high=101, low=99, close=100, volume=100000)

        order, trade = broker.apply_signal(signal, bar, quantity=10)

        self.assertEqual(order.status, "rejected")
        self.assertEqual(order.rejected_reason, "insufficient_cash")
        self.assertIsNone(trade)

    def test_rejects_sell_when_no_position(self):
        """没有持仓时，卖出信号应被拒绝"""
        broker = SimulatedBroker(initial_cash=10000, commission_rate=0.001, slippage_rate=0, max_position_weight=1.0)
        signal = Signal(signal_id="s1", ts=date(2026, 1, 2), market="US", symbol="US.AAPL", direction="sell", reason="exit")
        bar = Bar(ts=date(2026, 1, 2), open=100, high=101, low=99, close=100, volume=100000)

        order, trade = broker.apply_signal(signal, bar, quantity=10)

        self.assertEqual(order.status, "rejected")
        self.assertEqual(order.rejected_reason, "no_position")
        self.assertIsNone(trade)

    def test_caps_sell_quantity_to_current_position(self):
        """卖出数量不能超过当前持仓"""
        broker = SimulatedBroker(initial_cash=10000, commission_rate=0.001, slippage_rate=0, max_position_weight=1.0)
        # 先买入3股
        buy_signal = Signal(signal_id="s1", ts=date(2026, 1, 2), market="US", symbol="US.AAPL", direction="buy", reason="entry")
        bar = Bar(ts=date(2026, 1, 2), open=100, high=101, low=99, close=100, volume=100000)
        broker.apply_signal(buy_signal, bar, quantity=3)

        # 试图卖10股，实际只能卖3股
        sell_signal = Signal(signal_id="s2", ts=date(2026, 1, 3), market="US", symbol="US.AAPL", direction="sell", reason="exit")
        bar2 = Bar(ts=date(2026, 1, 3), open=100, high=101, low=99, close=100, volume=100000)
        order, trade = broker.apply_signal(sell_signal, bar2, quantity=10)

        self.assertEqual(order.status, "filled")
        self.assertIsNotNone(trade)
        assert trade is not None
        self.assertEqual(trade.quantity, 3)
        self.assertEqual(broker.positions.get("US.AAPL", 0), 0)

    def test_hold_signal_is_ignored(self):
        """hold 信号被忽略"""
        broker = SimulatedBroker(initial_cash=10000)
        signal = Signal(signal_id="s1", ts=date(2026, 1, 2), market="US", symbol="US.AAPL", direction="hold", reason="neutral")
        bar = Bar(ts=date(2026, 1, 2), open=100, high=101, low=99, close=100, volume=100000)

        order, trade = broker.apply_signal(signal, bar, quantity=10)

        self.assertEqual(order.status, "ignored")
        self.assertIsNone(trade)


if __name__ == "__main__":
    unittest.main()
