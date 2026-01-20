#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import time
from typing import Iterable, Optional

import pandas as pd
from futu import OpenQuoteContext, RET_OK, TickerDirect


BUY_VALUES = {TickerDirect.BUY, "BUY", "B"}
SELL_VALUES = {TickerDirect.SELL, "SELL", "S"}


def normalize_direction(value: object) -> Optional[str]:
    if value is None:
        return None
    val = str(value).upper()
    if val in BUY_VALUES:
        return "BUY"
    if val in SELL_VALUES:
        return "SELL"
    return None


def ensure_date_column(df: pd.DataFrame, date_str: str) -> pd.DataFrame:
    if "date" not in df.columns:
        df = df.copy()
        df["date"] = date_str
    return df


def collect_rt_ticks(
    quote_ctx: OpenQuoteContext,
    *,
    code: str,
    num: int,
    duration_min: float,
    interval_sec: float,
) -> pd.DataFrame:
    frames = []
    last_seq: Optional[int] = None
    date_str = dt.date.today().isoformat()
    end_time = time.time() + max(0.0, duration_min) * 60

    while True:
        ret, data = quote_ctx.get_rt_ticker(code, num=num)
        if ret != RET_OK:
            raise RuntimeError(f"get_rt_ticker failed: {data}")

        data = ensure_date_column(data, date_str)
        if last_seq is not None:
            data = data[data["sequence"] > last_seq]

        if not data.empty:
            last_seq = int(data["sequence"].max())
            frames.append(data)

        if duration_min <= 0:
            break
        if time.time() >= end_time:
            break
        time.sleep(interval_sec)

    if not frames:
        return pd.DataFrame(columns=["date", "time", "price", "volume", "ticker_direction"])

    result = pd.concat(frames, ignore_index=True)
    return result.drop_duplicates(subset=["date", "sequence", "price", "volume"])


def load_ticks(csv_path: str) -> pd.DataFrame:
    df = pd.read_csv(csv_path)
    if "date" not in df.columns:
        df["date"] = dt.date.today().isoformat()
    return df


def save_ticks(df: pd.DataFrame, csv_path: str) -> None:
    df.to_csv(csv_path, index=False)


def aggregate_daily_volumes(ticks: pd.DataFrame) -> pd.DataFrame:
    df = ticks.copy()
    df["direction"] = df["ticker_direction"].apply(normalize_direction)
    df = df[df["direction"].notna()]
    df["date"] = pd.to_datetime(df["date"]).dt.date.astype(str)
    df["volume"] = pd.to_numeric(df["volume"], errors="coerce")
    df = df.dropna(subset=["volume"])

    return (
        df.groupby(["date", "direction"], as_index=False)["volume"]
        .sum()
        .sort_values(["direction", "volume"], ascending=[True, False])
    )


def print_top10(daily: pd.DataFrame, direction: str, top_n: int) -> None:
    subset = daily[daily["direction"] == direction].nlargest(top_n, "volume")
    if subset.empty:
        print(f"{direction} top {top_n}: no data")
        return
    print(f"{direction} top {top_n} (daily volume):")
    print(subset.to_string(index=False))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Futu daily buy/sell volume top10 for Xiaomi (HK.01810)."
    )
    parser.add_argument("--host", default="127.0.0.1", help="Futu OpenD host.")
    parser.add_argument("--port", type=int, default=11111, help="Futu OpenD port.")
    parser.add_argument("--code", default="HK.01810", help="Stock code.")
    parser.add_argument("--num", type=int, default=1000, help="RT ticks per pull.")
    parser.add_argument(
        "--duration-min",
        type=float,
        default=0,
        help="Collect RT ticks for N minutes; 0 means single pull.",
    )
    parser.add_argument(
        "--interval-sec",
        type=float,
        default=2.0,
        help="Polling interval when duration-min > 0.",
    )
    parser.add_argument(
        "--ticks-csv",
        help="Load existing tick CSV to compute daily top10.",
    )
    parser.add_argument(
        "--fetch",
        action="store_true",
        help="Fetch latest RT ticks from OpenD and merge with CSV.",
    )
    parser.add_argument(
        "--save-csv",
        help="Save collected ticks to CSV for future aggregation.",
    )
    parser.add_argument("--top-n", type=int, default=10, help="Top N days to show.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    ticks = pd.DataFrame()
    if args.ticks_csv:
        ticks = load_ticks(args.ticks_csv)

    if args.fetch or args.ticks_csv is None:
        quote_ctx = OpenQuoteContext(host=args.host, port=args.port)
        try:
            rt_ticks = collect_rt_ticks(
                quote_ctx,
                code=args.code,
                num=args.num,
                duration_min=args.duration_min,
                interval_sec=args.interval_sec,
            )
        finally:
            quote_ctx.close()
        ticks = pd.concat([ticks, rt_ticks], ignore_index=True)

    if ticks.empty:
        print("No tick data available.")
        return

    if args.save_csv:
        save_ticks(ticks, args.save_csv)

    daily = aggregate_daily_volumes(ticks)
    print_top10(daily, "BUY", args.top_n)
    print_top10(daily, "SELL", args.top_n)


if __name__ == "__main__":
    main()
