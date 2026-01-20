#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import dataclass
from typing import Iterable, List, Optional

import pandas as pd
import yfinance as yf


@dataclass
class Position:
    side: str  # "long" or "short"
    entry_date: pd.Timestamp
    entry_price: float
    stop_loss: float


@dataclass
class Trade:
    date: pd.Timestamp
    action: str
    price: float
    stop_loss: Optional[float] = None
    entry_date: Optional[pd.Timestamp] = None
    entry_price: Optional[float] = None


def download_price_data(ticker: str, lookback_days: int) -> pd.DataFrame:
    df = yf.download(
        ticker,
        period=f"{lookback_days}d",
        interval="1d",
        auto_adjust=False,
        progress=False,
    )
    if df.empty:
        raise ValueError(f"No data returned for ticker: {ticker}")

    df = df.rename(columns=str.lower)
    required_cols = {"open", "high", "low", "close"}
    missing = required_cols - set(df.columns)
    if missing:
        raise ValueError(f"Missing required columns: {sorted(missing)}")

    df = df.loc[:, ["open", "high", "low", "close"]].dropna()
    df.index = df.index.tz_localize(None)
    return df


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["ema10"] = df["close"].ewm(span=10, adjust=False).mean()
    df["ema150"] = df["close"].ewm(span=150, adjust=False).mean()
    return df


def build_signals(df: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    prev_close = df["close"].shift(1)
    prev_ema10 = df["ema10"].shift(1)
    prev_ema150 = df["ema150"].shift(1)
    prev_high = df["high"].shift(1)
    prev_low = df["low"].shift(1)

    bullish = df["close"] > df["open"]
    bearish = df["close"] < df["open"]

    buy_signal = (
        (prev_close > prev_ema10)
        & (prev_ema10 > prev_ema150)
        & bullish
        & (df["close"] > prev_high)
    )

    sell_signal = (
        (prev_close < prev_ema10)
        & (prev_ema10 < prev_ema150)
        & bearish
        & (df["close"] < prev_low)
    )

    return buy_signal, sell_signal


def record_trade(
    trades: List[Trade],
    *,
    action: str,
    date: pd.Timestamp,
    price: float,
    position: Optional[Position] = None,
    stop_loss: Optional[float] = None,
) -> None:
    trades.append(
        Trade(
            date=date,
            action=action,
            price=float(price),
            stop_loss=None if stop_loss is None else float(stop_loss),
            entry_date=None if position is None else position.entry_date,
            entry_price=None if position is None else position.entry_price,
        )
    )


def run_strategy(
    df: pd.DataFrame,
    *,
    tick_size: float,
) -> List[Trade]:
    df = add_indicators(df)
    buy_signal, sell_signal = build_signals(df)

    position: Optional[Position] = None
    trades: List[Trade] = []

    for idx in range(1, len(df)):
        date = df.index[idx]
        row = df.iloc[idx]
        prev_row = df.iloc[idx - 1]

        if position is not None:
            if position.side == "long" and row["low"] <= position.stop_loss:
                record_trade(
                    trades,
                    action="stop_long",
                    date=date,
                    price=position.stop_loss,
                    position=position,
                )
                position = None
            elif position.side == "short" and row["high"] >= position.stop_loss:
                record_trade(
                    trades,
                    action="stop_short",
                    date=date,
                    price=position.stop_loss,
                    position=position,
                )
                position = None

        if buy_signal.iloc[idx]:
            if position is not None and position.side == "short":
                record_trade(
                    trades,
                    action="close_short",
                    date=date,
                    price=row["close"],
                    position=position,
                )
                position = None

            if position is None:
                stop_loss = prev_row["low"] - 10 * tick_size
                position = Position(
                    side="long",
                    entry_date=date,
                    entry_price=float(row["close"]),
                    stop_loss=float(stop_loss),
                )
                record_trade(
                    trades,
                    action="open_long",
                    date=date,
                    price=row["close"],
                    stop_loss=stop_loss,
                )

        elif sell_signal.iloc[idx]:
            if position is not None and position.side == "long":
                record_trade(
                    trades,
                    action="close_long",
                    date=date,
                    price=row["close"],
                    position=position,
                )
                position = None

            if position is None:
                stop_loss = prev_row["high"] + 10 * tick_size
                position = Position(
                    side="short",
                    entry_date=date,
                    entry_price=float(row["close"]),
                    stop_loss=float(stop_loss),
                )
                record_trade(
                    trades,
                    action="open_short",
                    date=date,
                    price=row["close"],
                    stop_loss=stop_loss,
                )

    return trades


def format_trades(trades: Iterable[Trade]) -> str:
    rows = []
    header = [
        "date",
        "action",
        "price",
        "stop_loss",
        "entry_date",
        "entry_price",
    ]
    rows.append(",".join(header))

    for trade in trades:
        rows.append(
            ",".join(
                [
                    trade.date.strftime("%Y-%m-%d"),
                    trade.action,
                    f"{trade.price:.2f}",
                    "" if trade.stop_loss is None else f"{trade.stop_loss:.2f}",
                    ""
                    if trade.entry_date is None
                    else trade.entry_date.strftime("%Y-%m-%d"),
                    "" if trade.entry_price is None else f"{trade.entry_price:.2f}",
                ]
            )
        )

    return "\n".join(rows)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="EMA10/EMA150 strategy example for Xiaomi (1810.HK)."
    )
    parser.add_argument("--ticker", default="1810.HK", help="Yahoo Finance ticker.")
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=220,
        help="Historical window to compute EMA150.",
    )
    parser.add_argument(
        "--last-n",
        type=int,
        default=10,
        help="Number of recent candles to display.",
    )
    parser.add_argument(
        "--tick-size",
        type=float,
        default=0.01,
        help="Minimum price increment; 10 points equals 10 * tick-size.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.lookback_days < 170:
        raise ValueError("lookback-days must be >= 170 to support EMA150.")

    data = download_price_data(args.ticker, args.lookback_days)
    data = add_indicators(data)

    last_n = data.tail(args.last_n)
    print("Recent candles with EMA10 and EMA150:")
    print(
        last_n[["open", "high", "low", "close", "ema10", "ema150"]]
        .round(2)
        .to_string()
    )

    trades = run_strategy(data, tick_size=args.tick_size)
    last_dates = set(last_n.index)
    recent_trades = [trade for trade in trades if trade.date in last_dates]

    print("\nTrades triggered within the recent window:")
    if recent_trades:
        print(format_trades(recent_trades))
    else:
        print("No trades triggered in the recent window.")


if __name__ == "__main__":
    main()
