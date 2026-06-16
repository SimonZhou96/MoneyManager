from __future__ import annotations

import uuid
from typing import Optional, Tuple

from .models import Bar, Order, RiskConfig, Signal, Trade


class SimulatedBroker:
    def __init__(
        self,
        initial_cash: float,
        commission_rate: float = 0.001,
        slippage_rate: float = 0.001,
        max_position_weight: float = 1.0,
        risk_config=None,   # RiskConfig | None
    ):
        self.initial_cash = float(initial_cash)
        self.cash = float(initial_cash)
        self.commission_rate = float(commission_rate)
        self.slippage_rate = float(slippage_rate)
        self.max_position_weight = float(max_position_weight)
        self.risk_config = risk_config if isinstance(risk_config, RiskConfig) else None
        self.positions: dict[str, int] = {}
        # 追踪每只标的的平均成本和历史最高价（用于风控）
        self._avg_cost: dict[str, float] = {}
        self._high_water: dict[str, float] = {}

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

        if side == "buy":
            if not self._within_position_limit(notional):
                return Order(**{**order.__dict__, "status": "rejected", "rejected_reason": "max_position_weight_exceeded"}), None
            if not self._can_buy(notional + fee):
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

    def check_risk(self, symbol: str, bar: Bar) -> Optional[Tuple[Order, Trade]]:
        """逐日风控检查 — 在当日信号处理后调用"""
        qty = self.positions.get(symbol, 0)
        if qty <= 0 or symbol not in self._avg_cost:
            return None
        if self.risk_config is None:
            return None

        close = float(bar.close)
        avg_cost = self._avg_cost[symbol]
        self._high_water[symbol] = max(self._high_water.get(symbol, avg_cost), close)
        pnl_pct = (close - avg_cost) / avg_cost if avg_cost > 0 else 0

        reason: Optional[str] = None

        if self.risk_config.take_profit_pct is not None and pnl_pct >= self.risk_config.take_profit_pct:
            reason = "take_profit"
        elif self.risk_config.stop_loss_pct is not None and pnl_pct <= self.risk_config.stop_loss_pct:
            reason = "stop_loss"
        elif self.risk_config.trailing_stop_pct is not None:
            high_water = self._high_water[symbol]
            if (high_water - close) / high_water >= self.risk_config.trailing_stop_pct:
                reason = "trailing_stop"

        if reason is None:
            return None

        order = Order(
            order_id=str(uuid.uuid4()),
            signal_id="",
            ts=bar.ts,
            symbol=symbol,
            side="sell",
            quantity=qty,
            status="filled",
        )
        fill_price = self._fill_price("sell", bar)
        fee = fill_price * qty * self.commission_rate
        trade = Trade(
            order_id=order.order_id,
            trade_id=str(uuid.uuid4()),
            symbol=symbol,
            side="sell",
            quantity=qty,
            price=round(fill_price, 6),
            fee=round(fee, 6),
            slippage=round(abs(fill_price - close), 6),
            ts=bar.ts,
        )
        self._apply_trade(trade)
        return order, trade

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
        symbol = trade.symbol
        if trade.side == "buy":
            self.cash -= notional + trade.fee
            prev_qty = self.positions.get(symbol, 0)
            prev_cost = self._avg_cost.get(symbol, 0.0)
            if prev_qty > 0:
                total_cost = prev_cost * prev_qty + notional + trade.fee
                self._avg_cost[symbol] = total_cost / (prev_qty + trade.quantity)
            else:
                self._avg_cost[symbol] = (notional + trade.fee) / trade.quantity
            self.positions[symbol] = prev_qty + trade.quantity
        elif trade.side == "sell":
            self.cash += notional - trade.fee
            new_qty = self.positions.get(symbol, 0) - trade.quantity
            if new_qty <= 0:
                self.positions.pop(symbol, None)
                self._avg_cost.pop(symbol, None)
                self._high_water.pop(symbol, None)
            else:
                self.positions[symbol] = new_qty
