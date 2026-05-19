from __future__ import annotations

import uuid
from typing import Optional, Tuple

from .models import Bar, Order, Signal, Trade


class SimulatedBroker:
    def __init__(
        self,
        initial_cash: float,
        commission_rate: float = 0.001,
        slippage_rate: float = 0.001,
        max_position_weight: float = 1.0,
    ):
        self.initial_cash = float(initial_cash)
        self.cash = float(initial_cash)
        self.commission_rate = float(commission_rate)
        self.slippage_rate = float(slippage_rate)
        self.max_position_weight = float(max_position_weight)
        self.positions = {}

    def apply_signal(self, signal: Signal, bar: Bar, quantity: int = 1) -> Tuple[Order, Optional[Trade]]:
        side = "buy" if signal.direction == "buy" else "sell" if signal.direction == "sell" else "hold"
        order = Order(
            order_id=str(uuid.uuid4()),
            signal_id=signal.signal_id,
            ts=signal.ts,
            symbol=signal.symbol,
            side=side,
            quantity=int(quantity),
        )
        if side == "hold":
            return Order(**{**order.__dict__, "status": "ignored", "rejected_reason": "hold_signal"}), None

        fill_price = self._fill_price(side, bar)
        notional = fill_price * int(quantity)
        fee = notional * self.commission_rate
        if side == "buy" and not self._within_position_limit(notional):
            return Order(**{**order.__dict__, "status": "rejected", "rejected_reason": "max_position_weight_exceeded"}), None
        if side == "buy" and not self._can_buy(notional + fee):
            return Order(**{**order.__dict__, "status": "rejected", "rejected_reason": "insufficient_cash"}), None

        trade = Trade(
            order_id=order.order_id,
            trade_id=str(uuid.uuid4()),
            symbol=signal.symbol,
            side=side,
            quantity=int(quantity),
            price=round(fill_price, 6),
            fee=round(fee, 6),
            slippage=round(abs(fill_price - float(bar.close)), 6),
            ts=signal.ts,
        )
        self._apply_trade(trade)
        return Order(**{**order.__dict__, "status": "filled"}), trade

    def _fill_price(self, side: str, bar: Bar) -> float:
        close = float(bar.close)
        if side == "buy":
            return close * (1 + self.slippage_rate)
        return close * (1 - self.slippage_rate)

    def _can_buy(self, required_cash: float) -> bool:
        return self.cash >= required_cash

    def _within_position_limit(self, new_notional: float) -> bool:
        return new_notional <= self.initial_cash * self.max_position_weight

    def _apply_trade(self, trade: Trade) -> None:
        notional = trade.price * trade.quantity
        if trade.side == "buy":
            self.cash -= notional + trade.fee
            self.positions[trade.symbol] = self.positions.get(trade.symbol, 0) + trade.quantity
        elif trade.side == "sell":
            self.cash += notional - trade.fee
            self.positions[trade.symbol] = self.positions.get(trade.symbol, 0) - trade.quantity
