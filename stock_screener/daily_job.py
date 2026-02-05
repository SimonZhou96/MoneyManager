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


def _load_kline_cache(code: str, market: str) -> pd.DataFrame | None:
    """
    复用 `cache/kline_data` 的本地文件（parquet 优先，csv fallback）。
    文件名规则与 `KlineDataManager` 保持一致：{MARKET}_{CODE_SAFE}.parquet
    例如：HK.00001 -> HK_HK_00001.parquet（注意：当前缓存实际为 HK_00001.parquet）
    """
    base_dir = os.path.dirname(os.path.abspath(__file__))
    cache_dir = os.path.join(base_dir, "cache", "kline_data")
    if not os.path.isdir(cache_dir):
        return None

    market_tag = str(market).upper()
    safe_code = str(code).replace(".", "_").replace("/", "_")

    # 兼容当前缓存命名：HK.00001 -> HK_00001.parquet（即去掉 market 前缀的 HK.）
    candidates = []
    if safe_code.startswith(f"{market_tag}_"):
        candidates.append(os.path.join(cache_dir, f"{safe_code}.parquet"))
        candidates.append(os.path.join(cache_dir, f"{safe_code}.csv"))
        candidates.append(os.path.join(cache_dir, f"{market_tag}_{safe_code}.parquet"))
        candidates.append(os.path.join(cache_dir, f"{market_tag}_{safe_code}.csv"))
    else:
        candidates.append(os.path.join(cache_dir, f"{market_tag}_{safe_code}.parquet"))
        candidates.append(os.path.join(cache_dir, f"{market_tag}_{safe_code}.csv"))

    file_path = next((p for p in candidates if os.path.exists(p)), None)
    if not file_path:
        return None

    try:
        if file_path.endswith(".parquet"):
            df = pd.read_parquet(file_path)
        else:
            df = pd.read_csv(file_path, encoding="utf-8")
        if df is None or df.empty:
            return None
        if "date" in df.columns:
            df = df.copy()
            df["date"] = pd.to_datetime(df["date"], errors="coerce")
            df = df[df["date"].notna()]
        return df
    except Exception:
        return None


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

            # 1) 优先复用本地 cache/kline_data
            df_cache = _load_kline_cache(code, market)
            df = None
            source = None

            start_dt = pd.to_datetime(start_date).date()
            end_dt = pd.to_datetime(end_date).date()

            cache_min = None
            cache_max = None
            if df_cache is not None and not df_cache.empty and "date" in df_cache.columns:
                try:
                    cache_min = pd.to_datetime(df_cache["date"]).dt.date.min()
                    cache_max = pd.to_datetime(df_cache["date"]).dt.date.max()
                except Exception:
                    cache_min, cache_max = None, None

            # 2) 用 cache 覆盖可用区间
            cache_used_rows = 0
            if cache_min and cache_max:
                # 过滤近两年 + 目标区间
                try:
                    df_cache_norm = df_cache.copy()
                    df_cache_norm["date"] = pd.to_datetime(df_cache_norm["date"]).dt.strftime("%Y-%m-%d")
                    df_cache_dates = pd.to_datetime(df_cache_norm["date"]).dt.date
                    df_cache_norm = df_cache_norm[(df_cache_dates >= cutoff_date) & (df_cache_dates >= start_dt) & (df_cache_dates <= end_dt)]
                    if not df_cache_norm.empty:
                        df_cache_norm = _normalize_kline_df(df_cache_norm)
                        db.upsert_klines(market, code, df_cache_norm, source="Cache", adj_type="qfq")
                        cache_used_rows = len(df_cache_norm)
                except Exception:
                    cache_used_rows = 0

            # 3) 若 cache 无法覆盖完整缺口，再按缺口区间调用外部数据源补齐
            # 缺口A：cache_min > start_dt，需要补 [start_dt, cache_min-1]
            # 缺口B：cache_max < end_dt，需要补 [cache_max+1, end_dt]
            fetch_ranges: list[tuple[date, date]] = []
            if cache_min is None or cache_max is None:
                fetch_ranges = [(start_dt, end_dt)]
            else:
                if start_dt < cache_min:
                    fetch_ranges.append((start_dt, min(end_dt, cache_min - timedelta(days=1))))
                if cache_max < end_dt:
                    fetch_ranges.append((max(start_dt, cache_max + timedelta(days=1)), end_dt))

            fetched_rows = 0
            for r_start, r_end in fetch_ranges:
                if r_start > r_end:
                    continue
                df_part = ak_fetcher.fetch(
                    code,
                    market=market,
                    start_date=r_start.strftime("%Y-%m-%d"),
                    end_date=r_end.strftime("%Y-%m-%d"),
                    max_count=5000,
                )
                part_source = "AKShare"
                if (df_part is None or df_part.empty) and futu_fetcher:
                    df_part = futu_fetcher.fetch(
                        code,
                        market=market,
                        start_date=r_start.strftime("%Y-%m-%d"),
                        end_date=r_end.strftime("%Y-%m-%d"),
                        max_count=5000,
                    )
                    part_source = "Futu"
                if df_part is None or df_part.empty:
                    continue

                df_part = _normalize_kline_df(df_part)
                if df_part is None or df_part.empty:
                    continue
                try:
                    df_dates = pd.to_datetime(df_part["date"]).dt.date
                    df_part = df_part[df_dates >= cutoff_date]
                except Exception:
                    pass
                db.upsert_klines(market, code, df_part, source=part_source, adj_type="qfq")
                fetched_rows += len(df_part)

            # 4) 日志用：若 cache/外部都没拿到任何数据，则认为失败
            if cache_used_rows == 0 and fetched_rows == 0:
                df = None
                source = "Cache/AKShare/Futu"
            else:
                df = pd.DataFrame()
                source = f"cache={cache_used_rows}, fetched={fetched_rows}"

            if df is None or df.empty:
                log_records.append(
                    _build_log_record(
                        market, code, name, "failed", f"{source} 无数据", 0, last_date
                    )
                )
                continue

            pruned = db.prune_old_klines(market, code, cutoff_date, adj_type="qfq")
            log_records.append(
                _build_log_record(
                    market,
                    code,
                    name,
                    "updated",
                    f"{source} 写入成功; prune<{cutoff_date}={pruned}",
                    0,
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
