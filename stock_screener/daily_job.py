#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
每日数据同步任务：股票列表 + K线历史入库
"""

import argparse
import json
import os
import time
from datetime import date, datetime, timedelta

import pandas as pd

from db import MarketDatabase, MySqlConfig
from kline_fetcher import AKShareKlineFetcher, FutuKlineFetcher
from market import market_label, parse_markets, normalize_market
from universe import fetch_stock_list_akshare, fetch_stock_list_futu


def _previous_business_day(today: date) -> date:
    # 简化版：仅按周末回退（不处理交易所节假日）
    d = today - timedelta(days=1)
    while d.weekday() >= 5:  # 5=Saturday, 6=Sunday
        d -= timedelta(days=1)
    return d


def _should_fetch(last_date: str, target_end: date) -> bool:
    """
    是否需要拉取增量：
    - last_date 为空：需要（首次建库）
    - last_date < target_end：需要补齐到 target_end
    - last_date >= target_end：跳过
    """
    if not last_date:
        return True
    try:
        last_dt = pd.to_datetime(last_date).date()
    except Exception:
        return True
    return last_dt < target_end


def _normalize_kline_df(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    if "date" not in df.columns and "time_key" in df.columns:
        df = df.rename(columns={"time_key": "date"})
    if "date" in df.columns:
        df = df.copy()
        df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
    return df


def _build_log_record(market, code, name, status, reason, rows, last_date):
    return {
        "market": market,
        "code": code,
        "name": name,
        "status": status,
        "reason": reason,
        "rows": rows,
        "last_date": last_date,
        "timestamp": datetime.utcnow().isoformat(),
    }


def _init_futu_context(host: str, port: int):
    try:
        import futu as ft
    except Exception:
        return None, None
    try:
        quote_ctx = ft.OpenQuoteContext(host=host, port=port)
        return quote_ctx, ft
    except Exception:
        return None, None


def run_once(
    mysql: MySqlConfig,
    markets,
    use_futu: bool,
    futu_host: str,
    futu_port: int,
    log_path: str,
    limit: int | None,
):
    db = MarketDatabase(mysql)
    db.init_schema()
    ak_fetcher = AKShareKlineFetcher()
    futu_ctx = None
    futu_fetcher = None

    if use_futu:
        futu_ctx, _ = _init_futu_context(futu_host, futu_port)
        if futu_ctx:
            futu_fetcher = FutuKlineFetcher(futu_ctx)

    log_records = []
    today = date.today()
    target_end = _previous_business_day(today)
    # 仅保留近两年数据（含 target_end 当日）
    cutoff_date = target_end - timedelta(days=365 * 2)
    end_date = target_end.strftime("%Y-%m-%d")

    for market in markets:
        market = normalize_market(market)
        if db.stock_count(market) == 0:
            stocks_source = None
            stocks = fetch_stock_list_akshare(market)
            if stocks:
                stocks_source = "AKShare"
            elif futu_ctx:
                stocks = fetch_stock_list_futu(futu_ctx, market)
                if stocks:
                    stocks_source = "Futu"
            if stocks:
                db.upsert_stocks(market, stocks, source=stocks_source)
                print(f"✓ {market_label(market)}股票列表已入库 ({len(stocks)} 只)")
        stocks = db.get_stocks(market)
        if not stocks:
            print(f"✗ 未获取到{market_label(market)}股票列表，跳过")
            continue
        if limit:
            stocks = stocks[:limit]

        for stock in stocks:
            code = stock["code"]
            name = stock.get("name")
            last_date = db.last_kline_date(market, code)
            if not _should_fetch(last_date, target_end):
                log_records.append(
                    _build_log_record(
                        market, code, name, "skipped", "目标交易日已入库", 0, last_date
                    )
                )
                continue

            start_date = cutoff_date.strftime("%Y-%m-%d")
            if last_date:
                try:
                    next_day = pd.to_datetime(last_date).date() + timedelta(days=1)
                    if next_day > cutoff_date:
                        start_date = next_day.strftime("%Y-%m-%d")
                except Exception:
                    start_date = cutoff_date.strftime("%Y-%m-%d")

            df = ak_fetcher.fetch(
                code,
                market=market,
                start_date=start_date,
                end_date=end_date,
                max_count=5000,
            )
            source = "AKShare"
            if (df is None or df.empty) and futu_fetcher:
                df = futu_fetcher.fetch(
                    code,
                    market=market,
                    start_date=start_date,
                    end_date=end_date,
                    max_count=5000,
                )
                source = "Futu"

            if df is None or df.empty:
                log_records.append(
                    _build_log_record(
                        market, code, name, "failed", f"{source} 无数据", 0, last_date
                    )
                )
                continue

            df = _normalize_kline_df(df)
            if df is None or df.empty:
                log_records.append(
                    _build_log_record(
                        market, code, name, "failed", "数据格式异常", 0, last_date
                    )
                )
                continue

            # 仅写入近两年范围（避免数据源返回过长历史）
            try:
                df_dates = pd.to_datetime(df["date"]).dt.date
                df = df[df_dates >= cutoff_date]
            except Exception:
                pass

            db.upsert_klines(market, code, df, source=source, adj_type="qfq")
            pruned = db.prune_old_klines(market, code, cutoff_date, adj_type="qfq")
            log_records.append(
                _build_log_record(
                    market,
                    code,
                    name,
                    "updated",
                    f"{source} 写入成功; prune<{cutoff_date}={pruned}",
                    len(df),
                    last_date,
                )
            )

    if futu_ctx:
        futu_ctx.close()
    db.close()

    if log_path:
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        with open(log_path, "w", encoding="utf-8") as f:
            for record in log_records:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        print(f"✓ 日志已保存到: {log_path}")


def run_loop(
    interval_hours: int,
    **kwargs,
):
    while True:
        run_once(**kwargs)
        time.sleep(interval_hours * 3600)


def main():
    parser = argparse.ArgumentParser(description="每日股票/行情入库任务")
    parser.add_argument("--mysql-host", default=os.getenv("MYSQL_HOST", "127.0.0.1"), help="MySQL Host")
    parser.add_argument("--mysql-port", type=int, default=int(os.getenv("MYSQL_PORT", "3306")), help="MySQL Port")
    parser.add_argument("--mysql-user", default=os.getenv("MYSQL_USER", "root"), help="MySQL User")
    parser.add_argument("--mysql-password", default=os.getenv("MYSQL_PASSWORD", ""), help="MySQL Password")
    parser.add_argument("--mysql-database", default=os.getenv("MYSQL_DATABASE", "market_data"), help="MySQL Database")
    parser.add_argument("--mysql-charset", default=os.getenv("MYSQL_CHARSET", "utf8mb4"), help="MySQL Charset")
    parser.add_argument("--markets", default="HK,US", help="市场列表: HK,US")
    parser.add_argument("--use-futu", action="store_true", help="允许使用 Futu OpenD 作为备用数据源")
    parser.add_argument("--futu-host", default="127.0.0.1", help="Futu OpenD Host")
    parser.add_argument("--futu-port", type=int, default=11111, help="Futu OpenD Port")
    parser.add_argument("--log", default="logs/daily_sync.jsonl", help="日志输出文件(JSONL)")
    parser.add_argument("--limit", type=int, default=None, help="限制股票数量")
    parser.add_argument("--loop", action="store_true", help="循环执行(默认单次)")
    parser.add_argument("--interval-hours", type=int, default=24, help="循环间隔小时")
    args = parser.parse_args()

    markets = parse_markets(args.markets)
    mysql = MySqlConfig(
        host=args.mysql_host,
        port=args.mysql_port,
        user=args.mysql_user,
        password=args.mysql_password,
        database=args.mysql_database,
        charset=args.mysql_charset,
    )
    if args.loop:
        run_loop(
            interval_hours=args.interval_hours,
            mysql=mysql,
            markets=markets,
            use_futu=args.use_futu,
            futu_host=args.futu_host,
            futu_port=args.futu_port,
            log_path=args.log,
            limit=args.limit,
        )
    else:
        run_once(
            mysql=mysql,
            markets=markets,
            use_futu=args.use_futu,
            futu_host=args.futu_host,
            futu_port=args.futu_port,
            log_path=args.log,
            limit=args.limit,
        )


if __name__ == "__main__":
    main()
