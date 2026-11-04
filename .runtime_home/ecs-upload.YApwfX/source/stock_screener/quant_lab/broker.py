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
        if side == "hold":
            order = Order(
                order_id=str(uuid.uuid4()),
                signal_id=signal.signal_id,
                ts=signal.ts,
                symbol=signal.symbol,
                side=side,
                quantity=0,
                status="ignored",
                rejected_reason="hold_signal",
            )
            return order, None

        fill_price = self._fill_price(side, bar)

        if side == "buy":
            actual_qty = self._max_buyable_quantity(fill_price, int(quantity))
            if actual_qty < 1:
                order = Order(
                    order_id=str(uuid.uuid4()),
                    signal_id=signal.signal_id,
                    ts=signal.ts,
                    symbol=signal.symbol,
                    side=side,
                    quantity=int(quantity),
                    status="rejected",
                    rejected_reason="insufficient_cash",
                )
                return order, None
        else:  # sell
            current_pos = self.positions.get(signal.symbol, 0)
            if current_pos <= 0:
                order = Order(
                    order_id=str(uuid.uuid4()),
                    signal_id=signal.signal_id,
                    ts=signal.ts,
                    symbol=signal.symbol,
                    side=side,
                    quantity=int(quantity),
                    status="rejected",
                    rejected_reason="no_position",
                )
                return order, None
            actual_qty = min(int(quantity), current_pos)

        order = Order(
            order_id=str(uuid.uuid4()),
            signal_id=signal.signal_id,
            ts=signal.ts,
            symbol=signal.symbol,
            side=side,
            quantity=actual_qty,
        )
        notional = fill_price * actual_qty
        fee = notional * self.commission_rate

        trade = Trade(
            order_id=order.order_id,
            trade_id=str(uuid.uuid4()),
            symbol=signal.symbol,
            side=side,
            quantity=actual_qty,
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

    def _max_buyable_quantity(self, fill_price: float, requested_qty: int) -> int:
        """计算实际可买入的最大股数（综合考虑现金和仓位上限）。

        返回 min(requested_qty, cash_permitted, position_limit_permitted)，
        确保不会买超现金，也不超过单标的最大仓位。
        """
        if fill_price <= 0 or self.cash <= 0:
            return 0
        # 每买入一股需要的现金（含佣金）
        per_share_cost = fill_price * (1.0 + self.commission_rate)
        # 可用现金能买多少股
        max_by_cash = int(self.cash / per_share_cost)
        # 仓位上限能买多少股
        max_by_position = int(self.initial_cash * self.max_position_weight / fill_price)
        return min(requested_qty, max_by_cash, max_by_position)

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
