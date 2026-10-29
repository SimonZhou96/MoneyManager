#!/usr/bin/env python3
"""Standalone ETF intraday research: run this file; outputs HTML and trades.json.

Historical quotes only. No trading context or orders. Dependencies are in
stock_screener/requirements.txt. Every monthly prediction is made out of sample.
Network comparison: three widths x seeds 42/43/44, with the same chronological
alpha/epoch/fee grid. Legacy fixed-parameter MLP remains a separate control.
Only the HTML report and flat JSON fill ledger are written by a normal run.
"""
from __future__ import annotations

import base64
import json
import math
import os
import sys
import time as clock
import warnings
import zlib
from collections import deque
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import date, datetime, time, timedelta, timezone
from functools import lru_cache
from math import ceil, floor, sqrt
from pathlib import Path
from statistics import fmean, pstdev, stdev
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
SCREENER_DIR = ROOT / "stock_screener"
OUTPUT_DIR = SCREENER_DIR / "logs" / "index_ema_backtest"
INITIAL_CASH_USD = 10_000.0
TIMEFRAME = "5m"
FUTU_HISTORY_YEARS = 8
HISTORY_START_TOLERANCE_DAYS = 10
HISTORY_END_TOLERANCE_DAYS = 10
RISK_FRACTION = 0.01
MAX_LEVERAGE = 4.0
TARGET_R = 10.0
NOISE_LOOKBACK_DAYS = 14
NOISE_TARGET_DAILY_VOL = 0.02
RECENT_PERIOD_TRADING_DAYS = 252
STRATEGY_VERSION = "etf-intraday-network-scaling-v3"
TRADING_DAYS_PER_YEAR = 252
US_TIMEZONE = "America/New_York"
SESSION_OPEN = time(9, 30)
FULL_SESSION_CLOSE = time(16, 0)
EARLY_SESSION_CLOSE = time(13, 0)
TRAIN_MONTHS = 36
VALIDATION_MONTHS = 6
MODEL_SEED = 42
NETWORK_SEEDS = (42, 43, 44)
NETWORK_ARCHITECTURES = {"small": (32, 16), "medium": (128, 64), "large": (256, 128, 64)}
NETWORK_ALPHAS = (0.01, 0.1)
NETWORK_EPOCHS = (50, 100, 200)
MODEL_WORKERS = min(4, os.cpu_count() or 1)
MIN_VALIDATION_TRADES = 20
FEE_MULTIPLIERS = (1, 2, 3)
BASELINE_VARIANT = "orb_range_10r"
NOISE_PAPER_VARIANT = "noise_area_paper"
NOISE_LOW_TURNOVER_VARIANT = "noise_area_low_turnover"
ATR_VARIANT = "atr_breakout"
PULLBACK_VARIANT = "atr_pullback"
TREE_VARIANT = "ml_hist_gradient_boosting"
MLP_VARIANT = "ml_mlp"
NOISE_VARIANTS = {NOISE_PAPER_VARIANT, NOISE_LOW_TURNOVER_VARIANT}
NETWORK_CONFIGS = {
    f"ml_mlp_{size}_seed{seed}": dict(size=size, hidden_layers=layers, seed=seed)
    for size, layers in NETWORK_ARCHITECTURES.items() for seed in NETWORK_SEEDS
}
ML_VARIANTS = (TREE_VARIANT, MLP_VARIANT, *NETWORK_CONFIGS)
ALL_VARIANTS = (BASELINE_VARIANT, NOISE_PAPER_VARIANT, NOISE_LOW_TURNOVER_VARIANT,
                ATR_VARIANT, PULLBACK_VARIANT, *ML_VARIANTS)
STRATEGY_LABELS = {
    BASELINE_VARIANT: "ORB · 首根高低点 / 10R",
    NOISE_PAPER_VARIANT: "Noise Area · 允许反手",
    NOISE_LOW_TURNOVER_VARIANT: "Noise Area · 每天最多开仓一次",
    ATR_VARIANT: "ATR · 直接突破",
    PULLBACK_VARIANT: "ATR · 回撤入场（改造版）",
    TREE_VARIANT: "机器学习 · 梯度提升树",
    MLP_VARIANT: "旧版神经网络 · 32/16 · 固定 200 轮 · seed 42",
    **{variant: f"神经网络 · {'/'.join(map(str, c['hidden_layers']))} · seed {c['seed']}"
       for variant, c in NETWORK_CONFIGS.items()},
}
SYMBOLS = {
    code: dict(label=label, currency="USD", timezone=US_TIMEZONE, tick=0.01,
               lot_size=1, fx_to_usd=1.0)
    for code, label in [("US.SPY", "标普 500 · SPY"), ("US.QQQ", "纳斯达克 100 · QQQ"),
                        ("US.TQQQ", "纳斯达克 100 · TQQQ 3x ETF")]
}

def _load_local_env() -> None:
    """Load simple KEY=VALUE settings without printing or exposing secrets."""
    for env_path in (ROOT / ".env", SCREENER_DIR / ".env"):
        if not env_path.is_file():
            continue
        try:
            for raw_line in env_path.read_text(encoding="utf-8").splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip("\"'")
                if key and key not in os.environ:
                    os.environ[key] = value
        except OSError:
            continue


def _subtract_years(value: date, years: int) -> date:
    try:
        return value.replace(year=value.year - years)
    except ValueError:
        return value.replace(year=value.year - years, day=28)


def _parse_timestamp(value: Any, symbol: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo(SYMBOLS[symbol]["timezone"]))
    return parsed.astimezone(timezone.utc)


def _timestamp_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


def _bar_close_timestamp(bar: dict[str, Any], symbol: str) -> str:
    return _timestamp_text(_parse_timestamp(bar["timestamp"], symbol) + timedelta(minutes=5))


def _local_datetime(value: Any, symbol: str) -> datetime:
    return _parse_timestamp(value, symbol).astimezone(ZoneInfo(SYMBOLS[symbol]["timezone"]))


def _normalise_frame(frame: Any, symbol: str) -> list[dict[str, Any]]:
    """Convert Futu DataFrames into sorted UTC OHLCV dictionaries."""
    import pandas as pd

    if frame is None or frame.empty:
        return []
    work = frame.copy()
    if isinstance(work.columns, pd.MultiIndex):
        work.columns = [str(column[0] if isinstance(column, tuple) else column) for column in work.columns]
    work.columns = [str(column).strip().lower() for column in work.columns]
    time_column = next(
        (name for name in ("time_key", "timestamp", "date", "datetime", "index") if name in work.columns),
        None,
    )
    if time_column is None and isinstance(work.index, pd.DatetimeIndex):
        work = work.reset_index().rename(columns={work.columns[0]: "date"})
        time_column = "date"
    if time_column is None or not all(key in work.columns for key in ("open", "high", "low", "close")):
        return []

    rows: list[dict[str, Any]] = []
    seen: dict[str, dict[str, float]] = {}
    for _, row in work.iterrows():
        try:
            # Futu labels intraday K-lines by their closing time: 09:35 is
            # the candle from 09:30 to 09:35. Normalize to candle-open time.
            timestamp = _timestamp_text(
                _parse_timestamp(row[time_column], symbol) - timedelta(minutes=5)
            )
            values = {key: float(row.get(key, 0) or 0) for key in ("open", "high", "low", "close", "volume")}
            if not all(math.isfinite(values[key]) and values[key] > 0 for key in ("open", "high", "low", "close")):
                continue
            if timestamp in seen:
                if seen[timestamp] != values:
                    raise RuntimeError(f"{symbol} {timestamp} 重复 K 线数据冲突")
                continue
            seen[timestamp] = values
            rows.append({"timestamp": timestamp, **values})
        except (TypeError, ValueError, OverflowError):
            continue
    rows.sort(key=lambda row: row["timestamp"])
    return rows



def _fee_native(symbol: str, action: str, quantity: int, price: float) -> float:
    """Approximate public Futu US equity fees in USD."""
    details = SYMBOLS[symbol]
    notional = max(0.0, quantity * price)
    commission = min(notional * 0.005, max(0.99, quantity * 0.0049))
    platform = min(notional * 0.005, max(1.0, quantity * 0.005))
    settlement = quantity * 0.003
    is_sale = action in {"sell", "sell_short"}
    regulatory = max(0.01, notional * 0.000008) if is_sale else 0.0
    activity = min(8.30, max(0.01, quantity * 0.000166)) if is_sale else 0.0
    return (commission + platform + settlement + regulatory + activity) * float(details["fx_to_usd"])


def _fee_usd(symbol: str, action: str, quantity: int, price: float) -> float:
    return _fee_native(symbol, action, quantity, price)


def _round_to_tick(value: float, tick: float, direction: str) -> float:
    scaled = value / tick
    if direction == "down":
        units = floor(scaled + 1e-10)
    elif direction == "up":
        units = ceil(scaled - 1e-10)
    else:
        units = round(scaled)
    return round(units * tick, 8)


def _adverse_fill_price(reference_price: float, action: str, tick: float) -> float:
    """Apply the selected zero-slippage assumption to a reference fill."""
    return max(tick, float(reference_price))


def size_position(
    symbol: str,
    equity_usd: float,
    entry_price: float,
    stop_price: float,
    position_side: str,
) -> int:
    """Size a whole-share position within 1% stop risk and 4x gross exposure."""
    if symbol not in SYMBOLS or position_side not in {"long", "short"}:
        return 0
    if equity_usd <= 0 or entry_price <= 0:
        return 0
    distance = entry_price - stop_price if position_side == "long" else stop_price - entry_price
    if distance <= 0:
        return 0
    risk_budget = equity_usd * RISK_FRACTION
    max_by_risk = int(risk_budget / distance)
    max_by_exposure = int((equity_usd * MAX_LEVERAGE) / entry_price)
    quantity = min(max_by_risk, max_by_exposure)
    entry_action = "buy" if position_side == "long" else "sell_short"
    exit_action = "sell" if position_side == "long" else "buy_to_cover"
    stop_exit_price = max(float(SYMBOLS[symbol]["tick"]), stop_price)
    while quantity > 0:
        total_risk = quantity * distance
        total_risk += _fee_usd(symbol, entry_action, quantity, entry_price)
        total_risk += _fee_usd(symbol, exit_action, quantity, stop_exit_price)
        if total_risk <= risk_budget + 1e-9:
            break
        quantity -= 1
    return quantity


def size_position_by_volatility(
    symbol: str,
    equity_usd: float,
    entry_price: float,
    daily_volatility: float,
) -> int:
    """Apply the paper's 2% daily-volatility target, capped at 4x gross exposure."""
    if symbol not in SYMBOLS or equity_usd <= 0 or entry_price <= 0 or daily_volatility <= 0:
        return 0
    leverage = min(MAX_LEVERAGE, NOISE_TARGET_DAILY_VOL / daily_volatility)
    return max(0, int((equity_usd * leverage) / entry_price))


def _make_fill(
    *,
    timestamp: str,
    symbol: str,
    scenario_id: str,
    strategy_variant: str,
    action: str,
    position_side: str,
    role: str,
    price: float,
    quantity: int,
    reason: str,
    fee_usd: float,
    trade_id: str,
    initial_risk_usd: float,
    gross_pnl_usd: float | None = None,
    net_pnl_usd: float | None = None,
    realized_r: float | None = None,
) -> dict[str, Any]:
    fill = {
        "strategy_version": STRATEGY_VERSION,
        "timestamp": timestamp,
        "session_date": _local_datetime(timestamp, symbol).date().isoformat(),
        "symbol": symbol,
        "scenario_id": scenario_id,
        "strategy_variant": strategy_variant,
        "trade_id": trade_id,
        "action": action,
        "position_side": position_side,
        "role": role,
        "price": float(price),
        "quantity": int(quantity),
        "fee_usd": round(float(fee_usd), 6),
        "initial_risk_usd": round(float(initial_risk_usd), 6),
        "reason": reason,
    }
    if gross_pnl_usd is not None:
        fill["gross_pnl_usd"] = round(float(gross_pnl_usd), 6)
    if net_pnl_usd is not None:
        fill["net_pnl_usd"] = round(float(net_pnl_usd), 6)
    if realized_r is not None:
        fill["realized_r"] = round(float(realized_r), 6)
    return fill


def _entry_stop(
    variant: str,
    symbol: str,
    first_bar: dict[str, Any],
    entry_price: float,
    side: str,
) -> tuple[float, str] | None:
    if variant == BASELINE_VARIANT:
        if side == "long":
            return float(first_bar["low"]), "首根 5 分钟 K 线低点止损"
        return float(first_bar["high"]), "首根 5 分钟 K 线高点止损"
    raise ValueError(f"Unknown strategy variant: {variant}")



def calculate_performance(
    fills: list[dict[str, Any]],
    initial_cash: float = INITIAL_CASH_USD,
    equity_curve: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Summarize realized trade outcomes and daily account-value statistics."""
    total_fees = sum(float(fill.get("fee_usd", 0.0)) for fill in fills)
    gross_profit = 0.0
    trade_results: list[float] = []
    trade_rs: list[float] = []
    holding_minutes: list[float] = []
    open_trades: dict[str, dict[str, Any]] = {}
    cash = float(initial_cash)
    for fill in fills:
        symbol = fill["symbol"]
        action = fill["action"]
        quantity = int(fill["quantity"])
        price = float(fill["price"])
        fee = float(fill.get("fee_usd", 0.0))
        key = str(fill.get("trade_id") or symbol)
        if action in {"buy", "buy_to_cover"}:
            cash -= quantity * price + fee
        elif action in {"sell", "sell_short"}:
            cash += quantity * price - fee

        if fill.get("role") == "entry" or action in {"buy", "sell_short"} and key not in open_trades:
            open_trades[key] = fill
            continue
        entry = open_trades.pop(key, None)
        if entry is None:
            continue
        side = str(entry.get("position_side", "long"))
        gross = fill.get("gross_pnl_usd")
        if gross is None:
            delta = price - float(entry["price"])
            gross = delta * quantity * (1 if side == "long" else -1)
        entry_fee = float(entry.get("fee_usd", 0.0))
        net = float(fill.get("net_pnl_usd", float(gross) - entry_fee - fee))
        risk = float(entry.get("initial_risk_usd", 0.0))
        gross_profit += float(gross)
        trade_results.append(net)
        if risk > 0:
            trade_rs.append(float(fill.get("realized_r", net / risk)))
        holding_minutes.append(max(0, (_parse_timestamp(fill["timestamp"], symbol) -
            _parse_timestamp(entry["timestamp"], symbol)).total_seconds() / 60))

    closed_trades = len(trade_results)
    wins = sum(result > 0 for result in trade_results)
    if equity_curve:
        equity_values = [float(point["equity_usd"]) for point in equity_curve]
        final_equity = equity_values[-1]
    else:
        final_equity = cash
        equity_values = []
    net_profit = final_equity - float(initial_cash)
    returns = []
    previous = float(initial_cash)
    for value in equity_values:
        returns.append(value / previous - 1.0 if previous > 0 else 0.0)
        previous = value
    volatility = pstdev(returns) * sqrt(TRADING_DAYS_PER_YEAR) if len(returns) > 1 else 0.0
    sharpe = (fmean(returns) / pstdev(returns) * sqrt(TRADING_DAYS_PER_YEAR)) if len(returns) > 1 and pstdev(returns) > 0 else 0.0
    peak = float(initial_cash)
    max_drawdown = 0.0
    for value in equity_values:
        peak = max(peak, value)
        if peak > 0:
            max_drawdown = min(max_drawdown, value / peak - 1.0)
    years = len(equity_values) / TRADING_DAYS_PER_YEAR
    annualized_return = ((final_equity / initial_cash) ** (1 / years) - 1) if years > 0 and initial_cash > 0 and final_equity > 0 else (-1.0 if final_equity <= 0 else 0.0)

    return {
        "initial_cash_usd": round(float(initial_cash), 2),
        "final_equity_usd": round(final_equity, 2),
        "net_profit_usd": round(net_profit, 2),
        "gross_profit_usd": round(gross_profit, 2),
        "total_fees_usd": round(total_fees, 2),
        "total_return_pct": round(net_profit / initial_cash * 100, 4) if initial_cash else 0.0,
        "annualized_return_pct": round(annualized_return * 100, 4),
        "volatility_pct": round(volatility * 100, 4),
        "sharpe_ratio": round(sharpe, 4),
        "max_drawdown_pct": round(max_drawdown * 100, 4),
        "closed_trades": closed_trades,
        "winning_trades": wins,
        "win_rate_pct": round(wins / closed_trades * 100, 4) if closed_trades else 0.0,
        "average_r": round(fmean(trade_rs), 4) if trade_rs else None,
        "average_holding_minutes": round(fmean(holding_minutes), 2) if holding_minutes else 0.0,
        "average_gross_profit_usd": round(gross_profit / closed_trades, 4) if closed_trades else 0.0,
        "average_fees_usd": round(total_fees / closed_trades, 4) if closed_trades else 0.0,
        "gross_turnover": round(sum(f["price"] * f["quantity"] for f in fills) / initial_cash, 4),
        "open_positions": len(open_trades),
    }




@lru_cache(maxsize=16)
def _calendar(year: int):
    import exchange_calendars as xc
    return xc.get_calendar("XNYS", start=f"{year - 1}-01-01", end=f"{year + 1}-12-31")


@lru_cache(maxsize=8192)
def _session_close(day: date) -> datetime | None:
    import pandas as pd
    cal = _calendar(day.year)
    stamp = pd.Timestamp(day)
    if not cal.is_session(stamp):
        return None
    return cal.session_close(stamp).to_pydatetime()


def _regular_sessions(bars, symbol):
    sessions = {}
    for bar in bars:
        local = _local_datetime(bar["timestamp"], symbol)
        if SESSION_OPEN <= local.time().replace(tzinfo=None) < FULL_SESSION_CLOSE:
            sessions.setdefault(local.date(), []).append(bar)
    return {day: sorted(rows, key=lambda r: r["timestamp"]) for day, rows in sorted(sessions.items())}


def _expected_session_times(session_date, close_time):
    start = datetime.combine(session_date, SESSION_OPEN, ZoneInfo(US_TIMEZONE))
    end = datetime.combine(session_date, close_time, ZoneInfo(US_TIMEZONE))
    return [start + timedelta(minutes=5 * n) for n in range(int((end - start).total_seconds() // 300))]


def _is_complete_session(rows, symbol):
    if not rows:
        return False
    day = _local_datetime(rows[0]["timestamp"], symbol).date()
    end = _session_close(day)
    if end is None:
        return False
    expected = _expected_session_times(day, end.astimezone(ZoneInfo(US_TIMEZONE)).time().replace(tzinfo=None))
    return [_local_datetime(r["timestamp"], symbol) for r in rows] == expected


def _complete_sessions(bars, symbol):
    return {d: r for d, r in _regular_sessions(bars, symbol).items() if _is_complete_session(r, symbol)}


def _requested_history_start(today=None):
    return _subtract_years(today or datetime.now(ZoneInfo(US_TIMEZONE)).date(), FUTU_HISTORY_YEARS)


def _number(value, default=0.0):
    try:
        value = float(value)
        return value if math.isfinite(value) else default
    except (TypeError, ValueError):
        return default


def _normalise_actions(frame):
    required = {"ex_div_date", "forward_adj_factorA", "forward_adj_factorB", "per_cash_div"}
    if frame is None or not required.issubset(frame.columns):
        raise RuntimeError("公司行动响应缺少必要字段")
    actions = {}
    for row in frame.to_dict("records"):
        day = date.fromisoformat(str(row["ex_div_date"])[:10])
        a, b = _number(row.get("forward_adj_factorA"), math.nan), _number(row.get("forward_adj_factorB"), math.nan)
        if not math.isfinite(a) or a <= 0 or not math.isfinite(b):
            raise RuntimeError(f"{day} 的复权因子无效")
        # Futu's split_ratio is old shares / new shares (confirmed against base/ert).
        share_factor = 1.0
        for kind in ("split", "join"):
            base, ert = _number(row.get(f"{kind}_base")), _number(row.get(f"{kind}_ert"))
            if base > 0 and ert > 0:
                share_factor *= ert / base
        for field in ("per_share_div_ratio", "per_share_trans_ratio", "allotment_ratio", "stk_spo_ratio", "spin_off_ratio"):
            if _number(row.get(field)):
                raise RuntimeError(f"{day} 含尚未支持的公司行动 {field}，停止回测")
        action = dict(price_a=a, price_b=b, share_factor=share_factor,
                      cash_dividend=_number(row.get("per_cash_div")) + _number(row.get("special_dividend")))
        if day in actions:
            raise RuntimeError(f"{day} 公司行动记录重复，无法确定处理顺序")
        actions[day] = action
    return actions


class QuoteLimiter:
    """Shared request budget; retries consume the same budget as first attempts."""
    def __init__(self):
        self.calls = deque()
        self.count = 0

    def call(self, fn, **kwargs):
        for attempt in range(3):
            now = clock.monotonic()
            while self.calls and now - self.calls[0] >= 30.1:
                self.calls.popleft()
            if len(self.calls) >= 20:
                clock.sleep(max(0, 30.1 - (now - self.calls[0])))
            self.calls.append(clock.monotonic())
            self.count += 1
            result = fn(**kwargs)
            if result[0] == 0:
                return result
            if attempt < 2:
                clock.sleep(2 ** attempt)
        raise RuntimeError(f"Futu 行情请求重试 3 次失败：{result[1]}")


def _validate_history_coverage(data_by_symbol, requested_start, requested_end):
    now = datetime.now(timezone.utc)
    expected = []
    day = requested_start
    while day <= requested_end:
        end = _session_close(day)
        if end is not None and end <= now:
            expected.append(day)
        day += timedelta(days=1)
    for symbol, bars in data_by_symbol.items():
        sessions = _complete_sessions(bars, symbol)
        missing = [d for d in expected if d not in sessions]
        if not sessions or missing:
            coverage = f"{min(sessions)} 至 {max(sessions)}" if sessions else "无完整交易日"
            raise RuntimeError(f"{symbol} 可用范围：{coverage}；缺失/不完整交易日 {len(missing)} 个："
                               + ", ".join(map(str, missing[:12])))


def _fetch_from_futu():
    import futu as ft
    import pandas as pd
    q = None
    limiter = QuoteLimiter()
    try:
        q = ft.OpenQuoteContext(host=os.getenv("FUTU_HOST", "127.0.0.1"),
                               port=int(os.getenv("FUTU_PORT", "11111")))
        ret, state = limiter.call(q.get_global_state)
        if str(state.get("program_status_type", "")).upper() != "READY" or not state.get("qot_logined"):
            raise RuntimeError("Futu OpenD 未就绪或未登录行情账号")
        end = datetime.now(ZoneInfo(US_TIMEZONE)).date()
        start = _requested_history_start(end)
        data = {}
        for symbol in SYMBOLS:
            _, action_frame = limiter.call(q.get_rehab, code=symbol)
            actions = _normalise_actions(action_frame)
            # These distributing ETFs must have action records in a multi-year window.
            if not any(start <= d <= end for d in actions):
                raise RuntimeError(f"{symbol} 公司行动历史为空，停止回测")
            frames, key = [], None
            while True:
                _, frame, key = limiter.call(q.request_history_kline, code=symbol,
                    start=start.isoformat(), end=end.isoformat(), ktype=ft.KLType.K_5M,
                    autype=ft.AuType.NONE, max_count=1000, page_req_key=key, extended_time=False)
                if frame is None or frame.empty:
                    if key is not None:
                        raise RuntimeError(f"{symbol} 分页返回空数据但仍有后续页")
                    break
                frames.append(frame)
                if len(frames) % 20 == 0:
                    print(f"行情 {symbol}：已读取 {len(frames) * 1000:,} 根，累计请求 {limiter.count}", flush=True)
                if key is None:
                    break
            if not frames:
                raise RuntimeError(f"{symbol} 没有历史行情")
            rows = _normalise_frame(pd.concat(frames, ignore_index=True), symbol)
            sessions = _regular_sessions(rows, symbol)
            kept = []
            for day, day_rows in sessions.items():
                close = _session_close(day)
                if close is None or close > datetime.now(timezone.utc):
                    continue
                for r in day_rows:
                    if not math.isfinite(r["volume"]) or r["volume"] < 0:
                        raise RuntimeError(f"{symbol} {day} 成交量无效")
                    if r["low"] > min(r["open"], r["close"]) or r["high"] < max(r["open"], r["close"]):
                        raise RuntimeError(f"{symbol} {day} OHLC 无效")
                day_rows[0]["corporate_action"] = actions.get(day, {})
                kept.extend(day_rows)
            data[symbol] = kept
            print(f"行情 {symbol} 完成：{len(kept):,} 根，{len(actions)} 条公司行动", flush=True)
        _validate_history_coverage(data, start, end)
        return data
    finally:
        if q is not None:
            q.close()


def load_market_data():
    _load_local_env()
    data = _fetch_from_futu()
    if not data:
        raise RuntimeError("Futu OpenD 未返回行情；不会切换到短区间数据源")
    return data, "Futu OpenD · 历史当日价格 / 公司行动按生效日处理"


def prepare_symbol(bars, symbol, lookback=14):
    """Point-in-time daily history. Future actions never rewrite earlier outputs."""
    sessions = _complete_sessions(bars, symbol)
    history = deque(maxlen=40)
    prepared = {}
    for day, rows in sessions.items():
        action = rows[0].get("corporate_action", {})
        a, b = action.get("price_a", 1.0), action.get("price_b", 0.0)
        shares = action.get("share_factor", 1.0)
        if a != 1 or b != 0 or shares != 1:
            for past in history:
                for field in ("open", "close", "high", "low"):
                    past[field] = a * past[field] + b
                past["closes"] = [a * c + b for c in past["closes"]]
                past["volumes"] = [v * shares for v in past["volumes"]]
        previous = history[-1]["close"] if history else None
        atr, vol = None, None
        if len(history) >= lookback + 1:
            h = list(history)[-lookback - 1:]
            trs = [max(cur["high"] - cur["low"], abs(cur["high"] - prev["close"]),
                       abs(cur["low"] - prev["close"])) for prev, cur in zip(h, h[1:])]
            atr = fmean(trs)
            vol = stdev([cur["close"] / prev["close"] - 1 for prev, cur in zip(h, h[1:])])
        opening = float(rows[0]["open"])
        turnover, volume = 0.0, 0.0
        points = []
        for i, row in enumerate(rows):
            close = float(row["close"])
            v = float(row["volume"])
            turnover += ((row["high"] + row["low"] + close) / 3) * v
            volume += v
            comparable = [past for past in history if len(past["closes"]) > i][-lookback:]
            sigma = fmean([abs(p["closes"][i] / p["open"] - 1) for p in comparable]) if len(comparable) == lookback else None
            avg_volume = fmean([p["volumes"][i] for p in comparable]) if len(comparable) == lookback else 0
            points.append(dict(timestamp=row["timestamp"], sigma=sigma,
                upper=max(opening, previous) * (1 + sigma) if sigma is not None and previous else None,
                lower=min(opening, previous) * (1 - sigma) if sigma is not None and previous else None,
                vwap=turnover / volume if volume else close,
                relative_volume=v / avg_volume if avg_volume > 0 else None))
        prepared[day] = dict(rows=rows, points=points, atr=atr, volatility=vol,
            open=opening, previous_close=previous,
            atr_upper=opening + 0.5 * atr if atr is not None else None,
            atr_lower=opening - 0.5 * atr if atr is not None else None,
            corporate_action=action)
        history.append(dict(open=opening, close=float(rows[-1]["close"]),
            high=max(r["high"] for r in rows), low=min(r["low"] for r in rows),
            closes=[float(r["close"]) for r in rows], volumes=[float(r["volume"]) for r in rows]))
    return prepared


def daily_volatility_by_session(bars, symbol, period=14):
    if period < 2:
        raise ValueError("period must be >= 2")
    return {d: p["volatility"] for d, p in prepare_symbol(bars, symbol, period).items() if p["volatility"] is not None}


def noise_area_by_session(bars, symbol, period=14):
    if period < 1:
        raise ValueError("period must be positive")
    return {d: p["points"] for d, p in prepare_symbol(bars, symbol, period).items()}


def _finish_scenario(symbol, variant, cash, fills, equity, period="full"):
    scenario_id = f"{symbol}:{variant}:{period}"
    for sequence, f in enumerate(fills):
        f["scenario_id"] = scenario_id
        f["evaluation_period"] = period
        f["execution_sequence"] = sequence
        close_bar_fill = f["role"] == "exit" and ("盘中触发" in f["reason"] or f["reason"] in {"10R 止盈", "收盘平仓"})
        f["bar_timestamp"] = _timestamp_text(_parse_timestamp(f["timestamp"], symbol) - timedelta(minutes=5)) if close_bar_fill else f["timestamp"]
        f.setdefault("model_version", None)
        f.setdefault("training_cutoff", None)
        f.setdefault("predicted_return", None)
        f.setdefault("fee_threshold_usd", None)
        f.setdefault("fee_multiplier", None)
        for key in ("model_random_seed", "network_size", "model_hidden_layers", "model_alpha",
                    "model_epochs", "model_parameter_count", "selection_cutoff"):
            f.setdefault(key, None)
        f.setdefault("exit_reason", f["reason"] if f["role"] == "exit" else None)
    return dict(scenario_id=scenario_id, symbol=symbol, strategy_variant=variant,
        evaluation_period=period, label=f"{SYMBOLS[symbol]['label']} · {STRATEGY_LABELS[variant]}",
        initial_cash_usd=cash, fills=fills, equity_curve=equity,
        summary=calculate_performance(fills, cash, equity))


def simulate_prepared(symbol, prepared, initial_cash, variant, start=None, end=None,
                      predictions=None, period="full"):
    """One chronological account. Signal decisions never consume execution-bar OHLC."""
    if variant not in ALL_VARIANTS:
        raise ValueError(f"Unknown strategy variant: {variant}")
    cash = float(initial_cash)
    bankrupt = False
    fills, equity = [], []
    predictions = predictions or {}
    for day, info in prepared.items():
        if (start and day < start) or (end and day >= end):
            continue
        if bankrupt:
            equity.append(dict(date=day.isoformat(), equity_usd=cash))
            continue
        rows, points = info["rows"], info["points"]
        position = None
        entries = 0

        def account_value(price):
            return cash if position is None else cash + position["sign"] * position["quantity"] * price

        def close_position(price, timestamp, signal_time, reason, prediction=None):
            nonlocal cash, position
            if position is None:
                return
            pos = position
            action = "sell" if pos["sign"] == 1 else "buy_to_cover"
            fee = _fee_usd(symbol, action, pos["quantity"], price)
            gross = (price - pos["entry_price"]) * pos["sign"] * pos["quantity"]
            net = gross - pos["entry_fee"] - fee
            cash += pos["sign"] * pos["quantity"] * price - fee
            f = _make_fill(timestamp=timestamp, symbol=symbol, scenario_id="", strategy_variant=variant,
                action=action, position_side=pos["side"], role="exit", price=price,
                quantity=pos["quantity"], reason=reason, fee_usd=fee, trade_id=pos["trade_id"],
                initial_risk_usd=pos["risk"], gross_pnl_usd=gross, net_pnl_usd=net,
                realized_r=net / pos["risk"] if pos["risk"] else 0.0)
            f.update(pos["metadata"])
            if prediction:
                f.update(prediction)
            f["signal_time"] = signal_time
            f["exit_reason"] = reason
            fills.append(f)
            position = None

        def open_position(sign, execution, signal_price, signal_time, reason,
                          stop=None, target=None, metadata=None, proposed_quantity=None):
            nonlocal cash, position, entries
            if cash <= 0 or position is not None:
                return
            price = float(execution["open"])
            if variant == BASELINE_VARIANT:
                quantity = min(size_position(symbol, cash, signal_price, stop, "long" if sign == 1 else "short"),
                               size_position(symbol, cash, price, stop, "long" if sign == 1 else "short"))
            else:
                vol = info["volatility"]
                if vol is None or vol <= 0:
                    return
                quantity = proposed_quantity if proposed_quantity is not None else size_position_by_volatility(symbol, cash, signal_price, vol)
                quantity = min(quantity, size_position_by_volatility(symbol, cash, price, vol))
            if stop is not None and sign * (price - stop) <= 0:
                return
            action = "buy" if sign == 1 else "sell_short"
            leverage = MAX_LEVERAGE if variant == BASELINE_VARIANT else min(MAX_LEVERAGE, NOISE_TARGET_DAILY_VOL / info["volatility"])
            while quantity > 0 and quantity * price > max(0, cash - _fee_usd(symbol, action, quantity, price)) * leverage:
                quantity -= 1
            if quantity <= 0:
                return
            fee = _fee_usd(symbol, action, quantity, price)
            risk = abs(price - stop) * quantity if stop is not None else 0.0
            if variant == BASELINE_VARIANT:
                target = _round_to_tick(price + sign * TARGET_R * abs(price - stop), 0.01, "up" if sign == 1 else "down")
            entries += 1
            trade_id = f"{symbol}:{variant}:{period}:{day}:{entries}"
            side = "long" if sign == 1 else "short"
            metadata = dict(metadata or {})
            f = _make_fill(timestamp=execution["timestamp"], symbol=symbol, scenario_id="", strategy_variant=variant,
                action=action, position_side=side, role="entry", price=price, quantity=quantity,
                reason=reason, fee_usd=fee, trade_id=trade_id, initial_risk_usd=risk)
            f.update(metadata)
            f["signal_time"] = signal_time
            fills.append(f)
            cash -= sign * quantity * price + fee
            position = dict(sign=sign, side=side, quantity=quantity, entry_price=price, entry_fee=fee,
                            trade_id=trade_id, risk=risk, stop=stop, target=target, metadata=metadata)

        if variant == BASELINE_VARIANT and cash > 0:
            first = rows[0]
            sign = 1 if first["close"] > first["open"] else -1 if first["close"] < first["open"] else 0
            if sign:
                stop = float(first["low"] if sign == 1 else first["high"])
                open_position(sign, rows[1], first["close"], _bar_close_timestamp(first, symbol),
                              "首根 5 分钟 K 线收涨，第二根开盘做多" if sign == 1 else "首根 5 分钟 K 线收跌，第二根开盘做空", stop=stop)
            for bar in rows[1:]:
                if position is None:
                    break
                stop, target = position["stop"], position["target"]
                stop_hit = bar["low"] <= stop if sign == 1 else bar["high"] >= stop
                target_hit = bar["high"] >= target if sign == 1 else bar["low"] <= target
                if stop_hit:
                    price = min(bar["open"], stop) if sign == 1 else max(bar["open"], stop)
                    close_position(price, _bar_close_timestamp(bar, symbol), _bar_close_timestamp(first, symbol), ("首根 5 分钟 K 线低点止损，盘中触发" if sign == 1 else "首根 5 分钟 K 线高点止损，盘中触发"))
                elif target_hit:
                    price = max(bar["open"], target) if sign == 1 else min(bar["open"], target)
                    close_position(price, _bar_close_timestamp(bar, symbol), _bar_close_timestamp(first, symbol), "10R 止盈")
        else:
            for i, bar in enumerate(rows[:-1]):
                timestamp = _bar_close_timestamp(bar, symbol)
                local = _local_datetime(timestamp, symbol)
                interval = 30 if variant in NOISE_VARIANTS else 15
                if local.minute % interval:
                    continue
                next_bar, price, point = rows[i + 1], float(bar["close"]), points[i]
                if account_value(price) <= 0:
                    bankrupt = True
                    close_position(next_bar["open"], next_bar["timestamp"], timestamp, "净值耗尽平仓")
                    break
                if variant in ML_VARIANTS:
                    pred = predictions.get(timestamp)
                    if not pred or pred.get("fee_multiplier") is None:
                        continue
                    mu = pred["predicted_return"]
                    sign = 1 if mu > 0 else -1 if mu < 0 else 0
                    if sign == 0 or (position and sign == position["sign"]):
                        continue
                    vol = info["volatility"]
                    if vol is None or vol <= 0:
                        continue
                    q = size_position_by_volatility(symbol, account_value(price), price, vol)
                    if q <= 0:
                        continue
                    entry_action, exit_action = ("buy", "sell") if sign == 1 else ("sell_short", "buy_to_cover")
                    fees = _fee_usd(symbol, entry_action, q, price) + _fee_usd(symbol, exit_action, q, price)
                    if position:
                        fees += _fee_usd(symbol, "sell" if position["sign"] == 1 else "buy_to_cover", position["quantity"], price)
                    threshold = pred["fee_multiplier"] * fees
                    if abs(mu) * q * price <= threshold:
                        continue
                    meta = dict(pred, fee_threshold_usd=threshold, estimated_gross_profit_usd=abs(mu) * q * price,
                                estimated_quantity=q, signal_reference_price=price, prediction_signal_time=timestamp)
                    if position:
                        close_position(next_bar["open"], next_bar["timestamp"], timestamp, "模型强反向信号，反手平仓", meta)
                    open_position(sign, next_bar, price, timestamp, "预测收益超过含最低收费的费用门槛", metadata=meta, proposed_quantity=q)
                    continue
                if variant in NOISE_VARIANTS:
                    upper, lower = point["upper"], point["lower"]
                    if upper is None or lower is None:
                        continue
                    signal = 1 if price > upper else -1 if price < lower else 0
                    if position:
                        sign = position["sign"]
                        reverse = signal == -sign and variant == NOISE_PAPER_VARIANT
                        stop_line = max(upper, point["vwap"]) if sign == 1 else min(lower, point["vwap"])
                        if reverse:
                            close_position(next_bar["open"], next_bar["timestamp"], timestamp, "半小时检查出现反向噪声区突破，反手平仓")
                        elif sign * (price - stop_line) <= 0:
                            close_position(next_bar["open"], next_bar["timestamp"], timestamp, "半小时检查触发当前噪声区 / VWAP 动态止损")
                            continue
                        else:
                            continue
                    if not signal or (variant == NOISE_LOW_TURNOVER_VARIANT and entries):
                        continue
                    stop_line = max(upper, point["vwap"]) if signal == 1 else min(lower, point["vwap"])
                    open_position(signal, next_bar, price, timestamp,
                        "半小时收盘价突破噪声区上边界，下一根开盘做多" if signal == 1 else "半小时收盘价跌破噪声区下边界，下一根开盘做空", stop=stop_line)
                    continue
                upper, lower = info["atr_upper"], info["atr_lower"]
                if upper is None or lower is None:
                    continue
                signal = 1 if price > upper else -1 if price < lower else 0
                if position:
                    if position["sign"] * (price - info["open"]) <= 0:
                        close_position(next_bar["open"], next_bar["timestamp"], timestamp, "15 分钟检查触发开盘价止损")
                    else:
                        continue
                if not signal:
                    continue
                recent_return = price / rows[i - 1]["close"] - 1 if i else 0.0
                if variant == PULLBACK_VARIANT and signal * recent_return >= 0:
                    continue
                open_position(signal, next_bar, price, timestamp,
                    "ATR 突破并满足 5 分钟反向回撤" if variant == PULLBACK_VARIANT else "15 分钟收盘价突破 ATR 边界", stop=info["open"])
        if position:
            close_position(rows[-1]["close"], _bar_close_timestamp(rows[-1], symbol),
                           _bar_close_timestamp(rows[-1], symbol), "收盘平仓")
        bankrupt = bankrupt or cash <= 0
        equity.append(dict(date=day.isoformat(), equity_usd=cash))
    return _finish_scenario(symbol, variant, initial_cash, fills, equity, period)


def _simulate_symbol(symbol, bars, initial_cash, strategy_variant, evaluation_start=None):
    return simulate_prepared(symbol, prepare_symbol(bars, symbol), initial_cash, strategy_variant, evaluation_start)


def run_backtest(data_by_symbol, initial_cash=INITIAL_CASH_USD, strategy_variant=BASELINE_VARIANT):
    return [f for symbol, bars in data_by_symbol.items()
            for f in _simulate_symbol(symbol, bars, initial_cash, strategy_variant)["fills"]]


FEATURE_NAMES = ("return_5m", "return_15m", "return_30m", "return_60m", "session_return",
                 "overnight_gap", "distance_vwap", "distance_atr_upper", "distance_atr_lower",
                 "relative_volume", "atr_fraction", "daily_volatility", "clock_sin", "clock_cos",
                 "spy_return_15m", "spy_return_60m", "qqq_return_15m", "qqq_return_60m")


def _intraday_return(rows, i, count):
    # Include the session open as the known price at 09:30, never yesterday's close.
    if i + 1 < count:
        return math.nan
    previous = rows[0]["open"] if i + 1 == count else rows[i - count]["close"]
    return float(rows[i]["close"]) / previous - 1.0


def build_samples(prepared_by_symbol):
    """Keep future labels separate from all signal-time feature values."""
    samples = {}
    for symbol, prepared in prepared_by_symbol.items():
        values = []
        for day, info in prepared.items():
            if info["atr"] is None or info["volatility"] is None:
                continue
            rows = info["rows"]
            for i in range(2, len(rows) - 1, 3):
                signal_time = _bar_close_timestamp(rows[i], symbol)
                close, point = rows[i]["close"], info["points"][i]
                angle = 2 * math.pi * ((i + 1) * 5) / 390
                features = [_intraday_return(rows, i, n) for n in (1, 3, 6, 12)]
                features += [close / info["open"] - 1,
                    info["open"] / info["previous_close"] - 1,
                    close / point["vwap"] - 1,
                    (close - info["atr_upper"]) / info["atr"] if info["atr"] > 0 else 0,
                    (close - info["atr_lower"]) / info["atr"] if info["atr"] > 0 else 0,
                    point["relative_volume"] if point["relative_volume"] is not None else math.nan,
                    info["atr"] / info["open"], info["volatility"], math.sin(angle), math.cos(angle)]
                for other in ("US.SPY", "US.QQQ"):
                    other_day = prepared_by_symbol.get(other, {}).get(day)
                    if other_day is None:
                        raise RuntimeError(f"{day} 缺少 {other} 同步行情，不能生成跨标的特征")
                    other_rows = other_day["rows"]
                    if i >= len(other_rows) or _bar_close_timestamp(other_rows[i], other) != signal_time:
                        raise RuntimeError(f"{day} {other} 同期 K 线不对齐")
                    features += [_intraday_return(other_rows, i, n) for n in (3, 12)]
                entry = rows[i + 1]["open"]
                endpoint = i + 4
                if endpoint < len(rows):
                    label_price = rows[endpoint]["open"]
                    label_end = rows[endpoint]["timestamp"]
                elif endpoint == len(rows):
                    label_price = rows[-1]["close"]
                    label_end = _bar_close_timestamp(rows[-1], symbol)
                else:
                    continue
                values.append(dict(date=day, signal_time=signal_time, features=features,
                    label=label_price / entry - 1, label_end=label_end))
        samples[symbol] = values
    return samples


def _month_shift(day, months):
    serial = day.year * 12 + day.month - 1 + months
    return date(serial // 12, serial % 12 + 1, 1)


def walk_forward_months(samples_by_symbol):
    if any(not rows for rows in samples_by_symbol.values()):
        raise RuntimeError("可训练的 15 分钟样本为空")
    first = max(rows[0]["date"] for rows in samples_by_symbol.values())
    first_full_month = _month_shift(first, 0 if first.day == 1 else 1)
    first_test = _month_shift(first_full_month, TRAIN_MONTHS + VALIDATION_MONTHS)
    last = min(rows[-1]["date"] for rows in samples_by_symbol.values())
    months = []
    month = first_test
    while month <= last:
        months.append(month)
        month = _month_shift(month, 1)
    if not months:
        raise RuntimeError("历史长度不足：需要 36 个月训练、6 个月验证及后续测试区间")
    return months


def make_model(variant, *, alpha=0.01, epochs=200):
    import numpy as np
    from sklearn.compose import TransformedTargetRegressor
    from sklearn.ensemble import HistGradientBoostingRegressor
    from sklearn.impute import SimpleImputer
    from sklearn.neural_network import MLPRegressor
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    if variant == TREE_VARIANT:
        estimator = HistGradientBoostingRegressor(max_iter=200, learning_rate=0.05,
            max_leaf_nodes=15, early_stopping=False, random_state=MODEL_SEED)
    elif variant == MLP_VARIANT or variant in NETWORK_CONFIGS:
        config = NETWORK_CONFIGS.get(variant, dict(hidden_layers=(32, 16), seed=MODEL_SEED))
        # An RNG instance preserves the shuffle stream across partial_fit epochs.
        seed = np.random.RandomState(config['seed']) if variant in NETWORK_CONFIGS else MODEL_SEED
        estimator = MLPRegressor(hidden_layer_sizes=config['hidden_layers'], activation="relu", solver="adam",
            max_iter=epochs, alpha=alpha, batch_size=256, learning_rate_init=0.001,
            early_stopping=False, tol=0.0, n_iter_no_change=201, random_state=seed)
    else:
        raise ValueError(variant)
    # Scaling the tiny return target is essential for the neural network loss.
    return TransformedTargetRegressor(regressor=make_pipeline(
        SimpleImputer(strategy="constant", fill_value=0.0, add_indicator=True, keep_empty_features=True),
        StandardScaler(), estimator), transformer=StandardScaler())


def _neural_checkpoints(variant, samples, alpha, checkpoints):
    """Fit preprocessing once on training rows; yield chronological epoch checkpoints.

    partial_fit preserves Adam's optimizer state. Validation rows never enter the
    imputer, either scaler, or the optimizer. Refit uses this identical procedure.
    """
    import numpy as np
    from sklearn.exceptions import ConvergenceWarning
    checkpoints = sorted(set(checkpoints))
    if not checkpoints or checkpoints[0] < 1:
        raise ValueError("训练轮数必须为正整数")
    x = np.asarray([r['features'] for r in samples], dtype=float)
    y = np.asarray([r['label'] for r in samples], dtype=float)
    model = make_model(variant, alpha=alpha, epochs=1)
    with warnings.catch_warnings():
        warnings.filterwarnings('ignore', category=ConvergenceWarning)
        # Initialize sklearn's target wrapper and both preprocessing pipelines.
        model.fit(x, y)
        estimator = model.regressor_.steps[-1][1]
        transformed_x = model.regressor_[:-1].transform(x)
        transformed_y = model.transformer_.transform(y.reshape(-1, 1)).ravel()
        for epoch in range(1, checkpoints[-1] + 1):
            if epoch > 1:
                estimator.partial_fit(transformed_x, transformed_y)
            if epoch in checkpoints:
                yield epoch, model


def _fit_model(variant, samples, *, alpha=0.01, epochs=200):
    import numpy as np
    from sklearn.exceptions import ConvergenceWarning
    if variant in NETWORK_CONFIGS:
        return next(_neural_checkpoints(variant, samples, alpha, (epochs,)))[1]
    model = make_model(variant)
    with warnings.catch_warnings():
        # The specified fixed 200 iterations intentionally need not meet a convergence tolerance.
        warnings.filterwarnings("ignore", category=ConvergenceWarning)
        model.fit(np.asarray([r["features"] for r in samples], dtype=float),
                  np.asarray([r["label"] for r in samples], dtype=float))
    return model


def _predict(model, samples):
    import numpy as np
    values = model.predict(np.asarray([r["features"] for r in samples], dtype=float))
    if not np.isfinite(values).all():
        raise RuntimeError("模型生成非有限预测值")
    return values.tolist()


def choose_fee_multiplier(symbol, prepared, variant, validation, estimates, start, end):
    candidates = []
    for k in FEE_MULTIPLIERS:
        preds = {row["signal_time"]: dict(predicted_return=estimate, fee_multiplier=k)
                 for row, estimate in zip(validation, estimates)}
        result = simulate_prepared(symbol, prepared, INITIAL_CASH_USD, variant,
                                   start, end, preds, period="validation")
        summary = result["summary"]
        turnover = sum(f["price"] * f["quantity"] for f in result["fills"]) / INITIAL_CASH_USD
        candidates.append(dict(k=k, net_profit_usd=summary["net_profit_usd"],
            sharpe=summary["sharpe_ratio"], trades=summary["closed_trades"], turnover=turnover))
    qualified = [c for c in candidates if c["trades"] >= MIN_VALIDATION_TRADES and c["net_profit_usd"] > 0]
    selected = max(qualified, key=lambda c: (c["sharpe"], -c["turnover"], c["k"])) if qualified else None
    return (selected["k"] if selected else None), candidates


def select_network_parameters(symbol, prepared, variant, train, validation, start, end):
    """Same preregistered alpha/epoch/k grid for every width and seed."""
    candidates = []
    parameter_count = None
    for alpha in NETWORK_ALPHAS:
        for epoch, model in _neural_checkpoints(variant, train, alpha, NETWORK_EPOCHS):
            _, choices = choose_fee_multiplier(symbol, prepared, variant, validation,
                                               _predict(model, validation), start, end)
            candidates.extend(dict(c, alpha=alpha, epochs=epoch) for c in choices)
            estimator = model.regressor_.steps[-1][1]
            parameter_count = sum(a.size for a in estimator.coefs_ + estimator.intercepts_)
    qualified = [c for c in candidates if c['trades'] >= MIN_VALIDATION_TRADES and c['net_profit_usd'] > 0]
    selected = max(qualified, key=lambda c: (c['sharpe'], -c['turnover'], c['k'],
                                            -c['epochs'], c['alpha'])) if qualified else None
    return selected, candidates, parameter_count


def _train_variant_history(symbol, prepared, samples, months, variant, progress):
    import sklearn
    from threadpoolctl import threadpool_limits
    predictions, folds = {}, []
    config = NETWORK_CONFIGS.get(variant)
    seed = config['seed'] if config else MODEL_SEED
    with threadpool_limits(limits=1):
        for month_index, test_start in enumerate(months):
            began = clock.monotonic()
            train_start = _month_shift(test_start, -TRAIN_MONTHS - VALIDATION_MONTHS)
            validation_start = _month_shift(test_start, -VALIDATION_MONTHS)
            test_end = _month_shift(test_start, 1)
            train = [r for r in samples if train_start <= r['date'] < validation_start]
            validation = [r for r in samples if validation_start <= r['date'] < test_start]
            test = [r for r in samples if test_start <= r['date'] < test_end]
            if not train or not validation or not test:
                raise RuntimeError(f'{symbol} {test_start} 月度训练/验证/测试样本缺失')
            for partition, boundary in ((train, validation_start), (validation, test_start)):
                if any(_local_datetime(r['label_end'], symbol).date() >= boundary for r in partition):
                    raise RuntimeError('标签跨越训练/验证/测试边界')
            alpha, epochs, parameter_count = None, None, None
            if config:
                choice, candidates, parameter_count = select_network_parameters(
                    symbol, prepared, variant, train, validation, validation_start, test_start)
                k = choice['k'] if choice else None
                if choice:
                    alpha, epochs = choice['alpha'], choice['epochs']
            else:
                model = _fit_model(variant, train)
                k, candidates = choose_fee_multiplier(symbol, prepared, variant, validation,
                    _predict(model, validation), validation_start, test_start)
                if variant == MLP_VARIANT:
                    alpha, epochs = 0.01, 200
            cutoff = max(r['label_end'] for r in (train + validation if k is not None else train))
            selection_cutoff = max(r['label_end'] for r in validation)
            version = f'{STRATEGY_VERSION}:{variant}:{symbol}:{test_start}:seed{seed}:sklearn{sklearn.__version__}'
            if k is not None:
                kwargs = dict(alpha=alpha, epochs=epochs) if config else {}
                model = _fit_model(variant, train + validation, **kwargs)
                estimates = _predict(model, test)
            else:
                estimates = [None] * len(test)
            meta = dict(model_random_seed=seed, network_size=config['size'] if config else None,
                model_hidden_layers=list(config['hidden_layers']) if config else ([32, 16] if variant == MLP_VARIANT else None),
                model_alpha=alpha, model_epochs=epochs, model_parameter_count=parameter_count,
                selection_cutoff=selection_cutoff)
            for row, estimate in zip(test, estimates):
                predictions[row['signal_time']] = dict(predicted_return=estimate,
                    fee_multiplier=k, model_version=version, training_cutoff=cutoff,
                    test_month=test_start.isoformat(), **meta)
            folds.append(dict(symbol=symbol, strategy_variant=variant, model_version=version,
                train_start=train_start.isoformat(), validation_start=validation_start.isoformat(),
                test_start=test_start.isoformat(), test_end=test_end.isoformat(), training_cutoff=cutoff,
                train_samples=len(train), validation_samples=len(validation), test_samples=len(test),
                refit_samples=len(train) + len(validation) if k is not None else 0,
                validation_cutoff=selection_cutoff, selected_k=k, candidates=candidates,
                active=k is not None, **meta))
            if progress:
                print(f'训练 {month_index + 1}/{len(months)} {test_start:%Y-%m} {symbol} {variant}: '
                      f'k={k if k is not None else "空仓"}，alpha={alpha}，轮数={epochs}，'
                      f'{clock.monotonic() - began:.1f}s', flush=True)
    return symbol, variant, predictions, folds


def train_walk_forward(prepared_by_symbol, samples_by_symbol, progress=True, *,
                       variants=None, workers=MODEL_WORKERS):
    """Parallelize independent symbol/variant histories; never share fitted models."""
    import multiprocessing
    variants = tuple(variants) if variants is not None else ML_VARIANTS
    if not variants or any(v not in ML_VARIANTS for v in variants):
        raise ValueError('未知或空的模型列表')
    months = walk_forward_months(samples_by_symbol)
    predictions = {s: {v: {} for v in variants} for s in prepared_by_symbol}
    folds = []
    jobs = [(s, prepared_by_symbol[s], samples_by_symbol[s], months, v, progress)
            for v in variants for s in prepared_by_symbol]

    def collect(result):
        symbol, variant, estimates, records = result
        predictions[symbol][variant] = estimates
        folds.extend(records)

    if workers <= 1:
        for job in jobs:
            collect(_train_variant_history(*job))
    else:
        with ProcessPoolExecutor(max_workers=min(workers, len(jobs)),
                                 mp_context=multiprocessing.get_context('spawn')) as pool:
            futures = [pool.submit(_train_variant_history, *job) for job in jobs]
            try:
                for future in as_completed(futures):
                    collect(future.result())
            except BaseException:
                for future in futures:
                    future.cancel()
                raise
    folds.sort(key=lambda f: (f['test_start'], f['symbol'], variants.index(f['strategy_variant'])))
    return predictions, folds


def _buy_and_hold_prepared(symbol, prepared, initial_cash, start, period):
    days = [(d, p) for d, p in prepared.items() if d >= start]
    if not days:
        raise RuntimeError("买入持有基准区间为空")
    price = days[0][1]["open"]
    quantity = int(initial_cash / price)
    while quantity > 0 and quantity * price + _fee_usd(symbol, "buy", quantity, price) > initial_cash:
        quantity -= 1
    if quantity <= 0:
        raise RuntimeError(f"{symbol} 本金不足买入一股")
    fees = _fee_usd(symbol, "buy", quantity, price)
    cash = initial_cash - quantity * price - fees
    equity, dividends = [], 0.0
    for index, (day, info) in enumerate(days):
        if index:
            action = info["corporate_action"]
            dividend = quantity * action.get("cash_dividend", 0.0)
            cash += dividend
            dividends += dividend
            new_quantity = quantity * action.get("share_factor", 1.0)
            quantity = int(math.floor(new_quantity + 1e-9))
            cash += (new_quantity - quantity) * info["open"]  # cash in lieu of split fractions
        mark = info["rows"][-1]["close"]
        if index == len(days) - 1:
            exit_fee = _fee_usd(symbol, "sell", quantity, mark) if quantity else 0.0
            fees += exit_fee
            cash -= exit_fee
        equity.append(dict(date=day.isoformat(), equity_usd=cash + quantity * mark))
    summary = calculate_performance([], initial_cash, equity)
    summary.update(total_fees_usd=round(fees, 2), gross_profit_usd=round(summary["net_profit_usd"] + fees, 2),
                   cash_dividends_usd=round(dividends, 2))
    return dict(symbol=symbol, evaluation_period=period, label=f"{SYMBOLS[symbol]['label']} · 买入持有含分红",
                summary=summary, equity_curve=equity)


def _buy_and_hold(symbol, bars, initial_cash, evaluation_start):
    return _buy_and_hold_prepared(symbol, prepare_symbol(bars, symbol), initial_cash, evaluation_start, "full")


def monthly_performance(scenario):
    """Include cash months, keeping each month's actual starting account value."""
    months = {}
    previous = scenario['initial_cash_usd']
    for point in scenario['equity_curve']:
        month = point['date'][:7]
        record = months.setdefault(month, dict(month=month, starting_equity_usd=previous,
            final_equity_usd=previous, gross_profit_usd=0.0, total_fees_usd=0.0, closed_trades=0))
        record['final_equity_usd'] = point['equity_usd']
        previous = point['equity_usd']
    for fill in scenario['fills']:
        month = fill['session_date'][:7]
        record = months[month]
        record['total_fees_usd'] += fill['fee_usd']
        if fill['role'] == 'exit':
            record['closed_trades'] += 1
            record['gross_profit_usd'] += fill.get('gross_pnl_usd', 0.0)
    for record in months.values():
        start, end = record['starting_equity_usd'], record['final_equity_usd']
        record['net_profit_usd'] = end - start
        record['total_return_pct'] = (end / start - 1) * 100 if start > 0 else None
    return list(months.values())


def network_comparison(scenarios, benchmarks):
    """Descriptive seed statistics, not an account that selects a winning seed."""
    by_id = {(s['symbol'], s['evaluation_period'], s['strategy_variant']): s for s in scenarios}
    output = []
    for symbol in SYMBOLS:
        for period in ('full', 'recent_year'):
            hold = next(b['summary']['total_return_pct'] for b in benchmarks
                        if b['symbol'] == symbol and b['evaluation_period'] == period)
            for size, layers in NETWORK_ARCHITECTURES.items():
                members, paired = [], []
                for seed in NETWORK_SEEDS:
                    scenario = by_id[(symbol, period, f'ml_mlp_{size}_seed{seed}')]
                    small = by_id[(symbol, period, f'ml_mlp_small_seed{seed}')]['summary']
                    summary = scenario['summary']
                    members.append(dict(seed=seed, strategy_variant=scenario['strategy_variant'],
                        **summary, positive_months=sum(m['net_profit_usd'] > 0 for m in scenario['monthly_performance']),
                        months=len(scenario['monthly_performance'])))
                    paired.append(summary['total_return_pct'] - small['total_return_pct'])
                returns = sorted(m['total_return_pct'] for m in members)
                output.append(dict(symbol=symbol, evaluation_period=period, size=size,
                    hidden_layers=list(layers), seeds=list(NETWORK_SEEDS), members=members,
                    mean_return_pct=fmean(returns), median_return_pct=returns[len(returns) // 2],
                    min_return_pct=min(returns), max_return_pct=max(returns),
                    return_std_pct=pstdev(returns), positive_seeds=sum(r > 0 for r in returns),
                    beats_hold_seeds=sum(r > hold for r in returns),
                    mean_sharpe=fmean(m['sharpe_ratio'] for m in members),
                    worst_drawdown_pct=min(m['max_drawdown_pct'] for m in members),
                    mean_closed_trades=fmean(m['closed_trades'] for m in members),
                    mean_fees_usd=fmean(m['total_fees_usd'] for m in members),
                    mean_delta_vs_small_pct=fmean(paired), beats_small_seeds=sum(d > 0 for d in paired)))
    return output


def run_scenarios(data_by_symbol, initial_cash=INITIAL_CASH_USD, *, prepared_by_symbol=None,
                  predictions=None, folds=None, evaluation_start=None, progress=True):
    missing = set(SYMBOLS) - set(data_by_symbol)
    if missing:
        raise ValueError(f"Missing required symbols: {', '.join(sorted(missing))}")
    prepared = prepared_by_symbol or {s: prepare_symbol(bars, s) for s, bars in data_by_symbol.items()}
    common = set.intersection(*(set(p) for p in prepared.values()))
    if not common:
        raise RuntimeError("三个标的没有共同完整交易日")
    if any(set(p) != common for p in prepared.values()):
        raise RuntimeError("三个标的交易日覆盖不一致，停止比较")
    if predictions is None:
        samples = build_samples(prepared)
        months = walk_forward_months(samples)
        evaluation_start = months[0]
        if progress:
            count = len(months) * len(SYMBOLS) * len(ML_VARIANTS)
            print(f"滚动训练：{len(months)} 个月 × {len(SYMBOLS)} 个标的 × {len(ML_VARIANTS)} 个模型/种子"
                  f" = {count} 个月度配置；{MODEL_WORKERS} 个训练进程。网络每月验证 18 个 alpha/轮数/k 组合。",
                  flush=True)
        predictions, folds = train_walk_forward(prepared, samples, progress)
    if evaluation_start is None:
        raise ValueError("预计算预测必须提供 evaluation_start")
    eligible = sorted(d for d in common if d >= evaluation_start)
    if not eligible:
        raise RuntimeError("样本外交易日为空")
    starts = dict(full=eligible[0], recent_year=eligible[max(0, len(eligible) - RECENT_PERIOD_TRADING_DAYS)])
    ranges = {k: dict(start=v.isoformat(), end=eligible[-1].isoformat()) for k, v in starts.items()}
    scenarios, benchmarks = [], []
    for period, start in starts.items():
        for symbol in SYMBOLS:
            for variant in ALL_VARIANTS:
                scenarios.append(simulate_prepared(symbol, prepared[symbol], initial_cash, variant, start,
                    predictions=predictions.get(symbol, {}).get(variant), period=period))
            benchmarks.append(_buy_and_hold_prepared(symbol, prepared[symbol], initial_cash, start, period))
    for scenario in scenarios:
        scenario['network_config'] = NETWORK_CONFIGS.get(scenario['strategy_variant'])
        scenario['monthly_performance'] = monthly_performance(scenario)
    fills = [f for s in scenarios for f in s["fills"]]
    fills.sort(key=lambda f: (f["timestamp"], f["scenario_id"], f["execution_sequence"]))
    conclusions = []
    for symbol in SYMBOLS:
        for variant in ALL_VARIANTS:
            items = []
            for period in starts:
                s = next(s["summary"] for s in scenarios if s["symbol"] == symbol and s["strategy_variant"] == variant and s["evaluation_period"] == period)
                base = next(s["summary"] for s in scenarios if s["symbol"] == symbol and s["strategy_variant"] == NOISE_LOW_TURNOVER_VARIANT and s["evaluation_period"] == period)
                bh = next(b["summary"] for b in benchmarks if b["symbol"] == symbol and b["evaluation_period"] == period)
                items.append(dict(period=period, positive=s["net_profit_usd"] > 0,
                    beats_hold=s["total_return_pct"] > bh["total_return_pct"],
                    return_delta_vs_low_turnover=s["total_return_pct"] - base["total_return_pct"],
                    sharpe_delta_vs_low_turnover=s["sharpe_ratio"] - base["sharpe_ratio"],
                    fee_delta_vs_low_turnover=s["total_fees_usd"] - base["total_fees_usd"]))
            conclusions.append(dict(symbol=symbol, strategy_variant=variant, periods=items,
                                    positive_both=all(i["positive"] for i in items)))
    return dict(scenarios=scenarios, benchmarks=benchmarks, fills=fills, folds=folds or [],
        conclusions=conclusions, evaluation_start=eligible[0].isoformat(), evaluation_end=eligible[-1].isoformat(),
        recent_year_start=starts["recent_year"].isoformat(), out_of_sample_start=eligible[0].isoformat(),
        period_ranges=ranges, data_start=min(common).isoformat(),
        feature_names=list(FEATURE_NAMES), strategy_version=STRATEGY_VERSION,
        network_comparison=network_comparison(scenarios, benchmarks))



def _chart_payload(data_by_symbol, results, prepared_by_symbol=None):
    import importlib.metadata
    versions = {name: importlib.metadata.version(name) for name in ("numpy", "pandas", "scikit-learn", "exchange-calendars")}
    versions["python"] = sys.version.split()[0]
    prepared = prepared_by_symbol or {s: prepare_symbol(rows, s) for s, rows in data_by_symbol.items()}
    symbol_data = {}
    for symbol, days in prepared.items():
        bars = []
        for day, info in days.items():
            if day.isoformat() < results["evaluation_start"]:
                continue
            for row, point in zip(info["rows"], info["points"]):
                bars.append([int(_parse_timestamp(row["timestamp"], symbol).timestamp() * 1000),
                    row["open"], row["high"], row["low"], row["close"], point["upper"], point["lower"],
                    point["vwap"], info["atr_upper"], info["atr_lower"], info["open"], row["volume"], day.isoformat()])
        symbol_data[symbol] = dict(label=SYMBOLS[symbol]["label"], bars=bars)
    panels = [{k: v for k, v in s.items() if k != "fills"} for s in results["scenarios"]]
    return {**{k: v for k, v in results.items() if k != "scenarios"},
        "symbols": symbol_data, "panels": panels, "strategy_labels": STRATEGY_LABELS,
        "report_runtime_versions": versions,
        "model_parameters": {"seed": MODEL_SEED, "train_months": TRAIN_MONTHS,
            "validation_months": VALIDATION_MONTHS, "fee_multipliers": list(FEE_MULTIPLIERS),
            "tree": {"iterations": 200, "learning_rate": 0.05, "max_leaf_nodes": 15},
            "mlp": {"hidden_layers": [32, 16], "iterations": 200, "alpha": 0.01,
                "batch_size": 256, "activation": "relu", "solver": "adam"},
            "network_scaling": {"configurations": NETWORK_CONFIGS, "seeds": list(NETWORK_SEEDS),
                "alpha_candidates": list(NETWORK_ALPHAS), "epoch_candidates": list(NETWORK_EPOCHS),
                "selection": "36 月训练，6 月验证按扣费 Sharpe 选择 alpha / epoch / k；至少 20 笔且净盈利",
                "optimizer": "Adam partial_fit，保持优化器及随机数状态；批量 256，学习率 0.001",
                "tie_break": "较低换手、较高 k、较少轮数、较强正则化",
                "workers": MODEL_WORKERS}},
        "assumptions": ["每个标的／策略／评价区间独立从 $10,000 起算；滑点 0；Futu 静态费用估算含最低收费。",
            "现金分红在除息日确认为应收现金、不再投资；拆股按生效日调整；成交使用历史当日价格。",
            "模型为 ETF 行情特征改造版，不是 272 因子论文复现。股票盘中可借，未计借券及融资费用。",
            "日波动率目标 2%，名义敞口上限 4 倍；TQQQ 自身包含杠杆。所有策略当天清仓。",
            "训练 36 月、验证 6 月、测试 1 月；验证不合格则下月空仓。回测样本外不等于前瞻实盘证据。",
            "网络扩容实验：三档宽度 × 三个固定种子，全部保留；种子均值/中位数是统计量，不是可交易组合。",
            "三档网络共用 alpha={0.01,0.1}、轮数={50,100,200}、k={1,2,3} 验证网格；测试集不参与选择。",
            "扩容请比较同种子的新版小/中/大网络；旧版 32/16 固定参数，仅作为历史对照。",
            "历史区间及最近一年已在此前研究中查看；本次属于回顾性滚动样本外比较，尚无新增前瞻样本。",
            "Sharpe 按每日净值、无风险利率 0 计算；最大回撤为日终口径；机器学习无固定止损，平均 R 不适用。"]}


def _dashboard_html(payload):
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()
    encoded = base64.b64encode(zlib.compress(raw, 9)).decode()
    template = r'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>ETF 日内策略 · 规则与机器学习</title>
<style>
:root{color-scheme:dark;--bg:#0b1020;--panel:#121c30;--grid:#28364f;--text:#e5edf9;--muted:#a1b2ca;--up:#44d5a0;--down:#ff7f88}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:14px/1.5 system-ui,sans-serif}main{max-width:1550px;margin:auto;padding:24px}h1{font-size:23px;margin:0 0 8px}h2{font-size:17px;margin:0 0 10px}.muted,small{color:var(--muted)}.toolbar{display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin:18px 0}select,input,button{background:#19253c;color:var(--text);border:1px solid #41516c;border-radius:6px;padding:7px}button{cursor:pointer}label{display:flex;align-items:center;gap:6px}section{background:var(--panel);border:1px solid var(--grid);border-radius:12px;padding:18px;margin:16px 0}.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(165px,1fr));gap:10px}.card{padding:12px;border:1px solid var(--grid);border-radius:8px}.card strong{display:block;font-size:20px;margin-top:6px}.positive{color:var(--up)}.negative{color:var(--down)}table{border-collapse:collapse;width:100%;font-size:12px;white-space:nowrap}th,td{padding:9px 10px;text-align:right;border-bottom:1px solid var(--grid)}th:first-child,td:first-child{text-align:left}tbody tr{cursor:pointer}tr.selected{background:#223753}.scroll{overflow:auto;max-height:470px}.chart{height:400px;position:relative}.equity{height:300px}canvas{width:100%;height:100%;display:block}#range{flex:1;min-width:160px}#tooltip{white-space:pre-wrap;min-height:46px;font:12px/1.5 ui-monospace,monospace;color:var(--muted)}.legend{display:flex;gap:15px;flex-wrap:wrap;font-size:12px}.status{padding:12px;border-left:3px solid #71aaff;background:#142441}summary{cursor:pointer}#error{color:#ff9ba3}a{color:#94beff}@media(max-width:700px){main{padding:12px}.chart{height:320px}section{padding:12px}}
</style></head><body><main>
<h1>QQQ / TQQQ / SPY · 日内规则与机器学习</h1>
<div class="muted" id="coverage">正在解压回测数据…</div><div id="error" role="alert"></div>
<div class="toolbar"><label>标的 <select id="symbol"></select></label><label>策略 <select id="strategy"></select></label>
<label>评价区间 <select id="period"><option value="recent_year">近一年 · 最近 252 个交易日</option><option value="full">全部滚动样本外</option></select></label></div>
<div class="cards" id="cards"></div><p class="status" id="conclusion"></p>
<section><h2>网络容量对照 · 每档 3 个随机种子</h2><p class="muted">相同特征、训练窗口、参数候选和交易规则。所有种子均展示；均值和中位数不是实际账户收益。扩大网络的效果请与新版 32/16 按相同种子比较。</p><div class="scroll"><table id="network-comparison"></table></div><p id="network-status" class="status"></p></section>
<section><h2>全部策略与买入持有对照</h2><div class="scroll"><table id="comparison"></table></div><small>点击策略行切换图表。交易次数指完整往返；每笔费用包含进出场。平均 R 对无固定止损的模型不适用。</small></section>
<section><h2>样本外净值曲线</h2><label>曲线 <select id="equity-scope"><option value="family">当前网络的 3 个种子与对照</option><option value="all">全部策略</option></select></label><div class="legend" id="equity-legend"></div><div class="chart equity"><canvas id="equity"></canvas></div></section>
<section><details><summary>当前账户逐月表现（含空仓月份）</summary><div class="scroll"><table id="monthly"></table></div></details></section>
<section><h2 id="chart-title">5 分钟 K 线与成交</h2><div class="legend"><span class="positive">▲ 买入 / 回补</span><span class="negative">▼ 卖出 / 做空</span><span>蓝：噪声区或 ATR 边界</span><span>黄：VWAP</span><span>灰：开盘价</span></div>
<div class="toolbar"><button id="prev">上一笔</button><button id="next">下一笔</button><label>可视 K 线 <select id="zoom"><option>78</option><option selected>156</option><option>390</option></select></label><input id="range" type="range" min="0" value="0" aria-label="K线历史范围"><span id="range-label"></span></div>
<div class="chart"><canvas id="candles"></canvas></div><div id="tooltip"></div></section>
<section><h2>当前场景成交账本</h2><small>与 trades.json 使用同一账本；展示当前可视 K 线内的成交。</small><div class="scroll"><table id="trades"></table></div></section>
<section><details><summary>月度训练与验证记录（包括空仓月份）</summary><p id="fold-status"></p><div class="scroll"><table id="folds"></table></div></details></section>
<section><details><summary>数据与计算假设</summary><ul id="assumptions"></ul><p>规则参考：<a href="https://concretumgroup.com/wp-content/uploads/2026/02/Improving-Performance-with-Fast-Alphas-A-Tactical-Overlay-for-Intraday-Trend-Trading.pdf">Fast Alphas</a>；模型参考：<a href="https://public.econ.duke.edu/~boller/Papers/MS_2025.pdf">Intraday Market Return Predictability</a>。</p></details></section>
</main><script id="chart-data" type="application/octet-stream">__PAYLOAD__</script><script>
let DATA,selected,bars=[],fills=[],from=0,visible=156,hitRows=[],fillMap=new Map();
const $=id=>document.getElementById(id),nf=new Intl.NumberFormat('en-US',{maximumFractionDigits:2}),fmt=x=>x==null?'—':nf.format(x),pct=x=>`${fmt(x)}%`,usd=x=>`$${fmt(x)}`;
const esc=x=>String(x??'—').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const colorAt=i=>`hsl(${(i*137.508)%360} 68% 70%)`;
const actions={buy:'买入',sell:'卖出',sell_short:'做空',buy_to_cover:'回补'};
const timeFmt=new Intl.DateTimeFormat('zh-CN',{timeZone:'America/New_York',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false});
const ft=ms=>timeFmt.format(new Date(ms));
function table(id,heads,rows){$(id).innerHTML=`<thead><tr>${heads.map(h=>`<th>${esc(h)}</th>`).join('')}</tr></thead><tbody>${rows.map(row=>`<tr>${row.map(v=>`<td>${esc(v)}</td>`).join('')}</tr>`).join('')}</tbody>`}
function options(id,entries){$(id).innerHTML=entries.map(([k,v])=>`<option value="${esc(k)}">${esc(v)}</option>`).join('')}
function activePanels(){return DATA.panels.filter(p=>p.symbol===$('symbol').value&&p.evaluation_period===$('period').value)}
function benchmark(){return DATA.benchmarks.find(p=>p.symbol===$('symbol').value&&p.evaluation_period===$('period').value)}
function render(){
 selected=activePanels().find(p=>p.strategy_variant===$('strategy').value);const s=selected.summary,b=benchmark().summary;
 const r=DATA.period_ranges[$('period').value];$('coverage').textContent=`数据 ${DATA.data_start} — ${DATA.evaluation_end}；初始训练/验证结束后，从 ${DATA.evaluation_start} 开始样本外交易。当前账本 ${r.start} — ${r.end}，独立本金 $10,000。`;
 const cards=[['净收益',usd(s.net_profit_usd)],['累计收益',pct(s.total_return_pct)],['年化收益',pct(s.annualized_return_pct)],['最大回撤（日终）',pct(s.max_drawdown_pct)],['Sharpe',fmt(s.sharpe_ratio)],['手续费',usd(s.total_fees_usd)],['完整交易',fmt(s.closed_trades)],['平均持仓',`${fmt(s.average_holding_minutes)} 分钟`]];
 $('cards').innerHTML=cards.map(([k,v])=>`<div class="card"><small>${esc(k)}</small><strong>${esc(v)}</strong></div>`).join('');
 const low=activePanels().find(p=>p.strategy_variant==='noise_area_low_turnover').summary;
 $('conclusion').textContent=`扣费后${s.net_profit_usd>0?'盈利':s.net_profit_usd<0?'亏损':'持平'}；相对低换手 Noise Area 收益差 ${pct(s.total_return_pct-low.total_return_pct)}，Sharpe 差 ${fmt(s.sharpe_ratio-low.sharpe_ratio)}；${s.total_return_pct>b.total_return_pct?'跑赢':'未跑赢'}买入持有（${pct(b.total_return_pct)}）。`;
 table('comparison',['策略','净收益率','年化','回撤','Sharpe','胜率','次数','每笔毛收益','每笔费用','费用合计','平均持仓/分','平均 R'],activePanels().concat([benchmark()]).map(p=>{const x=p.summary,trade=!!p.strategy_variant;return [p.label,pct(x.total_return_pct),pct(x.annualized_return_pct),pct(x.max_drawdown_pct),fmt(x.sharpe_ratio),trade?pct(x.win_rate_pct):'—',trade?x.closed_trades:'—',trade?usd(x.average_gross_profit_usd):'—',trade?usd(x.average_fees_usd):'—',usd(x.total_fees_usd),trade?fmt(x.average_holding_minutes):'—',fmt(x.average_r)]}));
 $('comparison').querySelectorAll('tbody tr').forEach((tr,i)=>{const p=activePanels()[i];if(p){tr.classList.toggle('selected',p.scenario_id===selected.scenario_id);tr.onclick=()=>{$('strategy').value=p.strategy_variant;render()}}});
 const groups=DATA.network_comparison.filter(g=>g.symbol===selected.symbol&&g.evaluation_period===selected.evaluation_period);
 table('network-comparison',['隐藏层','3 种子收益率','中位收益','平均收益','最差 / 最好','收益标准差/百分点','盈利种子','平均 Sharpe','最差回撤','平均交易数','平均费用','胜过同种子小网络','平均收益差/百分点'],groups.map(g=>[g.hidden_layers.join('/'),g.members.map(m=>`${m.seed}: ${pct(m.total_return_pct)}`).join('；'),pct(g.median_return_pct),pct(g.mean_return_pct),`${pct(g.min_return_pct)} / ${pct(g.max_return_pct)}`,fmt(g.return_std_pct),`${g.positive_seeds}/3`,fmt(g.mean_sharpe),pct(g.worst_drawdown_pct),fmt(g.mean_closed_trades),usd(g.mean_fees_usd),g.size==='small'?'基准':`${g.beats_small_seeds}/3`,g.size==='small'?'—':fmt(g.mean_delta_vs_small_pct)]));
 $('network-status').textContent=groups.map(g=>`${g.hidden_layers.join('/')}：${g.positive_seeds}/3 个种子扣费盈利、${g.beats_hold_seeds}/3 跑赢持有`).join('；')+'。历史区间已被查看，本次为回顾性比较，尚无新增前瞻样本。';
 table('monthly',['月份','月初净值','月末净值','净收益率','毛利润','手续费','净利润','交易数'],selected.monthly_performance.map(m=>[m.month,usd(m.starting_equity_usd),usd(m.final_equity_usd),m.total_return_pct==null?'—':pct(m.total_return_pct),usd(m.gross_profit_usd),usd(m.total_fees_usd),usd(m.net_profit_usd),m.closed_trades]));
 bars=DATA.symbols[selected.symbol].bars.filter(b=>b[12]>=r.start&&b[12]<=r.end);
 fills=DATA.fills.filter(f=>f.scenario_id===selected.scenario_id);fillMap=new Map();
 for(const f of fills){const ms=Date.parse(f.bar_timestamp);if(!fillMap.has(ms))fillMap.set(ms,[]);fillMap.get(ms).push(f)}
 visible=Number($('zoom').value);const last=fills.length?Date.parse(fills[fills.length-1].bar_timestamp):bars.at(-1)[0];
 from=Math.max(0,Math.min(bars.length-visible,bars.findIndex(b=>b[0]===last)-Math.floor(visible*.6)));
 $('range').max=Math.max(0,bars.length-visible);$('range').value=from;
 $('chart-title').textContent=`${selected.label} · 5 分钟 K 线（美东时间）`;
 const folds=DATA.folds.filter(f=>f.symbol===selected.symbol&&f.strategy_variant===selected.strategy_variant&&f.test_end>r.start&&f.test_start<=r.end);
 $('fold-status').textContent=folds.length?`${folds.length} 个月度模型，其中 ${folds.filter(f=>f.active).length} 个月验证合格，其余空仓。`:'该策略不需要训练。';
 table('folds',['测试月','种子','参数量','训练起点','验证起点','最终训练截止','训练样本','验证样本','alpha','训练轮数','选择 k','验证候选：alpha / 轮数 / k / 净利润 / 次数'],folds.map(f=>[f.test_start,f.model_random_seed,f.model_parameter_count,f.train_start,f.validation_start,ft(f.training_cutoff),f.train_samples,f.validation_samples,f.model_alpha,f.model_epochs,f.selected_k??'空仓',f.candidates.map(c=>`${c.alpha??'固定'} / ${c.epochs??200} / ${c.k} / ${fmt(c.net_profit_usd)} / ${c.trades}`).join('；')]));
 drawAll();
}
function context(id){const canvas=$(id),r=canvas.getBoundingClientRect(),d=window.devicePixelRatio||1;canvas.width=Math.floor(r.width*d);canvas.height=Math.floor(r.height*d);const c=canvas.getContext('2d');c.setTransform(d,0,0,d,0,0);c.fillStyle='#121c30';c.fillRect(0,0,r.width,r.height);return [c,r.width,r.height]}
function grid(c,w,h,min,max){const left=70,top=22,right=18,bottom=35,pw=w-left-right,ph=h-top-bottom;
 c.font='11px system-ui';c.textAlign='right';c.textBaseline='middle';c.strokeStyle='#28364f';c.fillStyle='#a1b2ca';for(let j=0;j<=5;j++){let y=top+ph*j/5;c.beginPath();c.moveTo(left,y);c.lineTo(w-right,y);c.stroke();c.fillText(fmt(max-(max-min)*j/5),left-6,y)}return {left,top,pw,ph,y:v=>top+(max-v)/(max-min||1)*ph}}
function line(c,points,color,width=1.3){c.strokeStyle=color;c.lineWidth=width;c.beginPath();let active=false;for(const p of points){if(!p||!Number.isFinite(p[1])){active=false;continue}if(active)c.lineTo(...p);else c.moveTo(...p);active=true}c.stroke()}
function drawCandles(){if(!selected)return;const [c,w,h]=context('candles'),rows=bars.slice(from,from+visible);hitRows=rows;if(!rows.length)return;
 const isNoise=selected.strategy_variant.startsWith('noise_area'),isATR=selected.strategy_variant.startsWith('atr_');const fields=isNoise?[5,6,7]:isATR?[8,9,10,7]:[7];
 const prices=rows.flatMap(r=>[r[2],r[3],...fields.map(j=>r[j]).filter(v=>v!=null)]);let lo=Math.min(...prices),hi=Math.max(...prices);const pad=(hi-lo)*.12||1;lo-=pad;hi+=pad;
 const g=grid(c,w,h,lo,hi),step=g.pw/rows.length,x=i=>g.left+(i+.5)*step;const wid=Math.max(1,Math.min(9,step*.7));
 for(let i=0;i<rows.length;i++){let r=rows[i],color=r[4]>=r[1]?'#44d5a0':'#ff7f88';c.strokeStyle=color;c.fillStyle=color;c.beginPath();c.moveTo(x(i),g.y(r[2]));c.lineTo(x(i),g.y(r[3]));c.stroke();c.fillRect(x(i)-wid/2,g.y(Math.max(r[1],r[4])),wid,Math.max(1,Math.abs(g.y(r[1])-g.y(r[4]))))}
 for(const j of fields){const color=j===7?'#ffcf6b':j===10?'#a1b2ca':'#789bef';line(c,rows.map((r,i)=>r[j]==null||i&&r[12]!==rows[i-1][12]?null:[x(i),g.y(r[j])]),color)}
 for(let i=0;i<rows.length;i++){const trades=fillMap.get(rows[i][0])||[];for(let n=0;n<trades.length;n++){const f=trades[n],up=['buy','buy_to_cover'].includes(f.action),y=g.y(f.price),xx=x(i);c.fillStyle=up?'#44d5a0':'#ff7f88';c.strokeStyle='#0b1020';c.lineWidth=1;c.beginPath();c.moveTo(xx,y+(up?-9:9));c.lineTo(xx-5,y);c.lineTo(xx+5,y);c.closePath();c.fill();c.stroke();c.font='bold 10px system-ui';c.textAlign='center';c.fillText(actions[f.action],xx,y+(up?-16:18)+(up?-1:1)*n*12)}}
 c.fillStyle='#a1b2ca';c.font='10px system-ui';c.textAlign='center';for(let j=0;j<5;j++){let i=Math.round((rows.length-1)*j/4);c.fillText(ft(rows[i][0]).slice(5),x(i),h-15)}
 $('range-label').textContent=`${ft(rows[0][0])} — ${ft(rows.at(-1)[0])}`;
 const visibleFills=fills.filter(f=>{let t=Date.parse(f.bar_timestamp);return t>=rows[0][0]&&t<=rows.at(-1)[0]});
 table('trades',['成交时间','动作','信号时间','价格','数量','费用','净盈亏','预测15分收益','门槛/$','原因'],visibleFills.map(f=>[ft(f.timestamp),actions[f.action],ft(f.signal_time),fmt(f.price),f.quantity,usd(f.fee_usd),f.net_pnl_usd==null?'—':usd(f.net_pnl_usd),f.predicted_return==null?'—':pct(f.predicted_return*100),fmt(f.fee_threshold_usd),f.reason]));
}
function drawEquity(){if(!selected)return;const [c,w,h]=context('equity'),family=selected.network_config?.size;const items=activePanels().filter(p=>$('equity-scope').value==='all'||p.scenario_id===selected.scenario_id||family&&p.network_config?.size===family||['noise_area_low_turnover','ml_mlp'].includes(p.strategy_variant)).concat([benchmark()]);const values=items.flatMap(p=>p.equity_curve.map(x=>x.equity_usd)).concat([10000]);let lo=Math.min(...values),hi=Math.max(...values),pad=(hi-lo)*.08||1;const g=grid(c,w,h,lo-pad,hi+pad);
 for(let k=0;k<items.length;k++){let p=items[k],series=[{equity_usd:10000},...p.equity_curve];line(c,series.map((pt,i)=>[g.left+i/(series.length-1)*g.pw,g.y(pt.equity_usd)]),colorAt(k),p.scenario_id===selected.scenario_id?2.8:1.2)}
 c.fillStyle='#a1b2ca';c.font='11px system-ui';c.textAlign='left';c.fillText(items[0].equity_curve[0].date,g.left,h-12);c.textAlign='right';c.fillText(items[0].equity_curve.at(-1).date,w-18,h-12);
 $('equity-legend').innerHTML=items.map((p,i)=>`<span style="color:${colorAt(i)}">${esc(p.strategy_variant?DATA.strategy_labels[p.strategy_variant]:'买入持有')}</span>`).join('');}
function drawAll(){drawEquity();drawCandles()}
function jump(direction){const center=bars[Math.min(bars.length-1,from+Math.floor(visible/2))][0];const times=[...fillMap.keys()].sort((a,b)=>a-b);const target=direction>0?times.find(t=>t>center):times.reverse().find(t=>t<center);if(target!=null){const i=bars.findIndex(b=>b[0]===target);from=Math.max(0,Math.min(bars.length-visible,i-Math.floor(visible/2)));$('range').value=from;drawCandles()}}
$('candles').onmousemove=e=>{if(!hitRows.length)return;const r=e.currentTarget.getBoundingClientRect(),i=Math.floor((e.clientX-r.left-70)/(r.width-88)*hitRows.length),b=hitRows[i];if(!b)return;const trades=fillMap.get(b[0])||[];$('tooltip').textContent=`${ft(b[0])}  O ${fmt(b[1])}  H ${fmt(b[2])}  L ${fmt(b[3])}  C ${fmt(b[4])}  V ${fmt(b[11])}\n`+trades.map(f=>`${actions[f.action]} ${f.quantity} 股 @ ${f.price}；${f.reason}`).join('\n')};
async function init(){const bytes=Uint8Array.from(atob($('chart-data').textContent.trim()),c=>c.charCodeAt(0));DATA=JSON.parse(await new Response(new Blob([bytes]).stream().pipeThrough(new DecompressionStream('deflate'))).text());options('symbol',Object.entries(DATA.symbols).map(([s,v])=>[s,v.label]));options('strategy',Object.entries(DATA.strategy_labels));$('symbol').value='US.QQQ';$('strategy').value='ml_mlp_small_seed42';$('assumptions').innerHTML=DATA.assumptions.map(t=>`<li>${esc(t)}</li>`).join('');for(const id of ['symbol','strategy','period'])$(id).onchange=render;$('equity-scope').onchange=drawEquity;$('range').oninput=()=>{from=Number($('range').value);drawCandles()};$('zoom').onchange=()=>{visible=Number($('zoom').value);from=Math.max(0,Math.min(from,bars.length-visible));$('range').max=Math.max(0,bars.length-visible);$('range').value=from;drawCandles()};$('prev').onclick=()=>jump(-1);$('next').onclick=()=>jump(1);window.addEventListener('resize',drawAll);render()}
init().catch(e=>{$('error').textContent=`图表加载失败：${e.message}。请使用支持 DecompressionStream 的新版浏览器。`;console.error(e)});
</script></body></html>'''
    return template.replace("__PAYLOAD__", encoded)


def write_outputs(data_by_symbol, results, output_dir=OUTPUT_DIR, prepared_by_symbol=None):
    output_dir.mkdir(parents=True, exist_ok=True)
    html_path, json_path = output_dir / "index_ema_backtest.html", output_dir / "trades.json"
    payload = _chart_payload(data_by_symbol, results, prepared_by_symbol)
    document = _dashboard_html(payload)
    ledger = json.dumps(results["fills"], ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    # Generate both fully before replacing the previous successful report.
    html_path.write_text(document, encoding="utf-8")
    json_path.write_text(ledger, encoding="utf-8")
    return html_path, json_path


def main():
    try:
        # Fail before consuming market-data quota when a runtime dependency is missing.
        import exchange_calendars
        import sklearn
        import threadpoolctl
        import futu
        raw, source = load_market_data()
        print("准备已完成交易日指标与滚动样本…", flush=True)
        prepared = {s: prepare_symbol(rows, s) for s, rows in raw.items()}
        results = run_scenarios(raw, prepared_by_symbol=prepared)
        paths = write_outputs(raw, results, prepared_by_symbol=prepared)
        print(f"数据源：{source}\n样本外区间：{results['evaluation_start']} 至 {results['evaluation_end']}")
        print(f"独立账本：{len(results['scenarios'])}（{len(SYMBOLS)} 个标的 × {len(ALL_VARIANTS)} 种策略/种子 × 2 个评价区间）；成交记录：{len(results['fills']):,}")
        for path in paths:
            print(path)
        return 0
    except ImportError as exc:
        print(f"缺少运行依赖：{exc}。请运行 python3 -m pip install -r stock_screener/requirements.txt", file=sys.stderr)
        return 1
    except (RuntimeError, ValueError, OSError) as exc:
        print(f"回测未完成：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
