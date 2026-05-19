from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import List

from filters import StockInfo

from .broker import SimulatedBroker
from .metrics import calculate_metric_snapshot
from .models import Bar, EquityPoint, MetricSnapshot, Order, Signal, Trade


@dataclass(frozen=True)
class BacktestRequest:
    market: str
    symbols: List[str]
    start: date
    end: date
    initial_cash: float
    quantity: int = 1
    commission_rate: float = 0.001
    slippage_rate: float = 0.001
    max_position_weight: float = 1.0


@dataclass(frozen=True)
class BacktestResult:
    signals: List[Signal] = field(default_factory=list)
    orders: List[Order] = field(default_factory=list)
    trades: List[Trade] = field(default_factory=list)
    equity_curve: List[EquityPoint] = field(default_factory=list)
    metrics: MetricSnapshot = field(default_factory=MetricSnapshot)
    warnings: List[str] = field(default_factory=list)


class BacktestRunner:
    def __init__(self, data_provider, strategy):
        self.data_provider = data_provider
        self.strategy = strategy

    def run(self, request: BacktestRequest) -> BacktestResult:
        broker = SimulatedBroker(
            initial_cash=request.initial_cash,
            commission_rate=request.commission_rate,
            slippage_rate=request.slippage_rate,
            max_position_weight=request.max_position_weight,
        )
        signals: list[Signal] = []
        orders: list[Order] = []
        trades: list[Trade] = []
        equity: list[EquityPoint] = []
        warnings: list[str] = []

        for symbol in request.symbols:
            bars = self.data_provider.bars_for(request.market, symbol, request.start, request.end)
            if not bars:
                warnings.append(f"missing_bars:{symbol}")
                continue
            bar_by_date = {bar.ts: bar for bar in bars}
            dates = [bar.ts for bar in bars]
            stock = StockInfo(market=request.market, code=symbol, name=symbol)
            symbol_signals = self.strategy.evaluate(stock, dates)
            signals.extend(symbol_signals)
            for bar in bars:
                for signal in [item for item in symbol_signals if item.ts == bar.ts]:
                    order, trade = broker.apply_signal(signal, bar, quantity=request.quantity)
                    orders.append(order)
                    if trade:
                        trades.append(trade)
                equity.append(EquityPoint(ts=bar.ts, equity=broker.cash + _market_value(broker, symbol, bar), cash=broker.cash, drawdown=0))

            missing_signal_dates = sorted({signal.ts for signal in symbol_signals if signal.ts not in bar_by_date})
            for missing in missing_signal_dates:
                warnings.append(f"missing_signal_bar:{symbol}:{missing.isoformat()}")

        equity = _with_drawdowns(equity)
        metrics = calculate_metric_snapshot(equity, trades)
        return BacktestResult(signals=signals, orders=orders, trades=trades, equity_curve=equity, metrics=metrics, warnings=warnings)


def _market_value(broker: SimulatedBroker, symbol: str, bar: Bar) -> float:
    return broker.positions.get(symbol, 0) * float(bar.close)


def _with_drawdowns(points: list[EquityPoint]) -> list[EquityPoint]:
    peak = 0.0
    result = []
    for point in points:
        peak = max(peak, float(point.equity))
        drawdown = (float(point.equity) - peak) / peak if peak else 0
        result.append(EquityPoint(ts=point.ts, equity=point.equity, cash=point.cash, drawdown=drawdown, benchmark_value=point.benchmark_value))
    return result
