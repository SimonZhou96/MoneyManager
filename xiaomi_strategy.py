#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import dataclass
from typing import Iterable, List, Optional, Tuple

import matplotlib.pyplot as plt
import mplfinance as mpf
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


def build_trade_markers(
    df: pd.DataFrame, trades: Iterable[Trade]
) -> Tuple[pd.Series, pd.Series]:
    buy_actions = {"open_long", "close_short", "stop_short"}
    sell_actions = {"open_short", "close_long", "stop_long"}

    buy_series = pd.Series(index=df.index, dtype="float64")
    sell_series = pd.Series(index=df.index, dtype="float64")

    for trade in trades:
        if trade.date not in df.index:
            continue
        if trade.action in buy_actions:
            buy_series.loc[trade.date] = trade.price
        elif trade.action in sell_actions:
            sell_series.loc[trade.date] = trade.price

    return buy_series, sell_series


def plot_chart(
    df: pd.DataFrame,
    trades: Iterable[Trade],
    *,
    last_n: int,
    output_file: str,
    show_plot: bool,
) -> None:
    plot_df = df.tail(last_n).copy()
    buy_markers, sell_markers = build_trade_markers(plot_df, trades)

    addplots = [
        mpf.make_addplot(plot_df["ema10"], color="tab:blue", width=1.0),
        mpf.make_addplot(plot_df["ema150"], color="tab:orange", width=1.0),
    ]
    if not buy_markers.isna().all():
        addplots.append(
            mpf.make_addplot(
                buy_markers,
                type="scatter",
                markersize=80,
                marker="^",
                color="tab:green",
            )
        )
    if not sell_markers.isna().all():
        addplots.append(
            mpf.make_addplot(
                sell_markers,
                type="scatter",
                markersize=80,
                marker="v",
                color="tab:red",
            )
        )

    mpf.plot(
        plot_df,
        type="candle",
        style="yahoo",
        addplot=addplots,
        volume=False,
        title="Xiaomi EMA10/EMA150 Strategy",
        ylabel="Price",
        savefig=output_file,
    )

    if show_plot:
        plt.show()


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
    parser.add_argument(
        "--plot-file",
        default="xiaomi_chart.png",
        help="Output image file for the candlestick chart.",
    )
    parser.add_argument(
        "--show-plot",
        action="store_true",
        help="Display the chart window (if supported).",
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

    if args.plot_file:
        plot_chart(
            data,
            trades,
            last_n=args.last_n,
            output_file=args.plot_file,
            show_plot=args.show_plot,
        )
        print(f"\nChart saved to: {args.plot_file}")


if __name__ == "__main__":
    main()
