from __future__ import annotations

from typing import Iterable, List

import numpy as np

from .models import EquityPoint, MetricSnapshot, Trade


def _round(value: float) -> float:
    return round(float(value or 0), 6)


def _pair_trade_pnls(trades: Iterable[Trade]) -> List[float]:
    open_positions: dict[str, dict] = {}
    pnls: List[float] = []
    for trade in trades:
        key = trade.symbol
        if trade.side == "buy":
            qty = int(trade.quantity)
            price = float(trade.price)
            fee = float(trade.fee or 0)
            if key in open_positions:
                # 加权平均累加，而非覆盖（修复连续买入时 win_rate=0 的 bug）
                old = open_positions[key]
                total_qty = old["quantity"] + qty
                old["price"] = (old["price"] * old["quantity"] + price * qty) / total_qty
                old["quantity"] = total_qty
                old["fee"] = old["fee"] + fee
            else:
                open_positions[key] = {"quantity": qty, "price": price, "fee": fee}
            continue
        if trade.side == "sell" and key in open_positions:
            opened = open_positions[key]
            sell_qty = int(trade.quantity)
            matched_qty = min(sell_qty, int(opened["quantity"]))
            gross = (float(trade.price) - float(opened["price"])) * matched_qty
            buy_fee_share = float(opened["fee"]) * (matched_qty / int(opened["quantity"]))
            pnls.append(gross - buy_fee_share - float(trade.fee or 0))
            remaining = int(opened["quantity"]) - matched_qty
            if remaining <= 0:
                open_positions.pop(key)
            else:
                opened["quantity"] = remaining
                opened["fee"] = float(opened["fee"]) - buy_fee_share
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
    days = max(1, (equity[-1].ts - equity[0].ts).days if hasattr(equity[-1].ts, "days") else len(equity))
    years = days / 365.25

    total_return = (end - start) / start if start > 0 else 0
    cagr = ((end / start) ** (1 / years) - 1) if start > 0 and years > 0 else 0
    max_drawdown = min((point.drawdown for point in equity), default=0)

    # 日收益率序列
    eq_values = np.array([float(p.equity) for p in equity], dtype=float)
    daily_returns = np.diff(eq_values) / eq_values[:-1] if len(eq_values) > 1 else np.array([])
    annual_vol = float(np.std(daily_returns) * np.sqrt(252)) if len(daily_returns) > 0 else 0

    # Sharpe (rf=0.02)
    rf_daily = 0.02 / 252
    excess = daily_returns - rf_daily
    sharpe = float(np.mean(excess) / np.std(excess) * np.sqrt(252)) if len(excess) > 0 and np.std(excess) > 0 else 0

    # Sortino (下行波动率)
    downside = daily_returns[daily_returns < 0]
    downside_std = float(np.std(downside)) if len(downside) > 0 else 0
    sortino = float(np.mean(excess) / downside_std * np.sqrt(252)) if downside_std > 0 else 0

    # Calmar
    calmar = cagr / abs(max_drawdown) if max_drawdown < 0 else 0

    benchmark_return = 0
    if equity[0].benchmark_value and equity[-1].benchmark_value:
        b_start = float(equity[0].benchmark_value)
        b_end = float(equity[-1].benchmark_value)
        benchmark_return = (b_end - b_start) / b_start if b_start > 0 else 0

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
        annualized_return=_round(cagr),
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
        sharpe=_round(sharpe),
        sortino=_round(sortino),
        calmar=_round(calmar),
        cagr=_round(cagr),
        annual_volatility=_round(annual_vol),
    )
