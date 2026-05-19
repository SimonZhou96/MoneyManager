from __future__ import annotations

from typing import Iterable, List

from .models import EquityPoint, MetricSnapshot, Trade


def _round(value: float) -> float:
    return round(float(value or 0), 6)


def _pair_trade_pnls(trades: Iterable[Trade]) -> List[float]:
    open_positions = {}
    pnls: List[float] = []
    for trade in trades:
        key = trade.symbol
        if trade.side == "buy":
            open_positions[key] = {
                "quantity": int(trade.quantity),
                "price": float(trade.price),
                "fee": float(trade.fee or 0),
            }
            continue
        if trade.side == "sell" and key in open_positions:
            opened = open_positions.pop(key)
            qty = min(int(trade.quantity), int(opened["quantity"]))
            gross = (float(trade.price) - float(opened["price"])) * qty
            pnls.append(gross - float(opened["fee"]) - float(trade.fee or 0))
    return pnls


def _max_consecutive_losses(pnls: List[float]) -> int:
    best = 0
    current = 0
    for pnl in pnls:
        if pnl < 0:
            current += 1
            best = max(best, current)
        else:
            current = 0
    return best


def calculate_metric_snapshot(equity: List[EquityPoint], trades: List[Trade]) -> MetricSnapshot:
    if not equity:
        return MetricSnapshot()

    start = float(equity[0].equity or 0)
    end = float(equity[-1].equity or 0)
    total_return = (end - start) / start if start > 0 else 0
    max_drawdown = min((point.drawdown for point in equity), default=0)
    benchmark_return = 0
    if equity[0].benchmark_value and equity[-1].benchmark_value:
        benchmark_return = (float(equity[-1].benchmark_value) - float(equity[0].benchmark_value)) / float(equity[0].benchmark_value)

    pnls = _pair_trade_pnls(trades)
    wins = [pnl for pnl in pnls if pnl > 0]
    losses = [pnl for pnl in pnls if pnl < 0]
    trade_count = len(pnls)
    win_rate = len(wins) / trade_count if trade_count else 0
    average_win = sum(wins) / len(wins) if wins else 0
    average_loss = sum(losses) / len(losses) if losses else 0
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    profit_factor = gross_win / gross_loss if gross_loss else 0
    expected_value = sum(pnls) / trade_count if trade_count else 0

    return MetricSnapshot(
        total_return=_round(total_return),
        benchmark_return=_round(benchmark_return),
        excess_return=_round(total_return - benchmark_return),
        max_drawdown=_round(max_drawdown),
        trade_count=trade_count,
        win_rate=_round(win_rate),
        average_win=_round(average_win),
        average_loss=_round(average_loss),
        win_loss_ratio=_round(abs(average_win / average_loss)) if average_loss else 0,
        profit_factor=_round(profit_factor),
        expected_value=_round(expected_value),
        max_consecutive_losses=_max_consecutive_losses(pnls),
    )
