#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
股票筛选任务（简化版）

流程：
1. 拉取股票列表
2. 对每只股票：API 获取 K 线（内存）→ 计算 EMA 突破 → 写入结果到 MySQL
不再存储 K 线数据到数据库。
"""

import argparse
import csv
import json
import os
import time
from datetime import date, datetime

import pandas as pd

from db import MarketDatabase, MySqlConfig
from kline_fetcher import KlineFetcherFactory
from market import market_label, parse_markets, normalize_market
from strategy import analyze_stock_ema_breakout
from timeframe import is_intraday, parse_timeframe
from universe import fetch_stock_list_akshare, fetch_stock_list_futu


# ------------------------------------------------------------------
# 辅助：计算每日平均成交量
# ------------------------------------------------------------------


def _compute_avg_daily_volume(df: pd.DataFrame, timeframe: str) -> float | None:
    """
    计算每日平均成交量。
    - 日线(1d/5d/1wk/1mo/3mo)：每行即一日，直接对 volume 取均值
    - 日内(1m/5m/15m 等)：按日期聚合后取日均
    """
    if df is None or df.empty or "volume" not in df.columns:
        return None
    try:
        if is_intraday(timeframe):
            # 日内：按日期聚合
            if isinstance(df.index, pd.DatetimeIndex):
                daily = df["volume"].groupby(df.index.date).sum()
            elif "date" in df.columns:
                dt = pd.to_datetime(df["date"])
                daily = df["volume"].groupby(dt.dt.date).sum()
            else:
                return float(df["volume"].mean())
            return float(daily.mean()) if len(daily) > 0 else None
        return float(df["volume"].mean())
    except Exception:
        return None


# ------------------------------------------------------------------
# 核心：对一只股票执行 K 线获取 + EMA 突破检查 + 写入结果
# ------------------------------------------------------------------

def _scan_single_stock(
    db: MarketDatabase,
    fetchers,
    market: str,
    code: str,
    timeframe: str,
    stock_info: dict | None = None,
    verbose: bool = True,
) -> dict | None:
    """
    扫描单只股票：拉 K 线 -> 内存计算 -> 写入 DB

    Returns:
        结果 dict 或 None（获取失败）
    """
    # 1) 用 fetcher 链获取 K 线
    df = None
    source = None
    for fetcher in fetchers:
        try:
            df = fetcher.fetch(code, market=market, timeframe=timeframe)
            if df is not None and not df.empty:
                source = fetcher.get_name()
                break
        except Exception:
            continue

    if df is None or df.empty:
        if verbose:
            print(f"  ✗ {code} 所有数据源获取失败")
        return None

    # 2) 内存计算 EMA 突破
    signal = analyze_stock_ema_breakout(
        market=market,
        code=code,
        df=df,
        check_date=None,  # 自动推断
    )

    # 3) 提取附加信息
    name = (stock_info or {}).get("name")
    sector = (stock_info or {}).get("sector")
    industry = (stock_info or {}).get("industry")
    market_cap = (stock_info or {}).get("market_cap")
    pe_ratio = (stock_info or {}).get("pe_ratio")

    # 4) 写入 EMA 信号表（按 timeframe 分表）
    db.upsert_ema_breakout_signal(
        timeframe=timeframe,
        market=signal.market,
        code=signal.code,
        check_date=signal.check_date,
        result_type=signal.result.value,
        is_satisfied=signal.result.is_satisfied(),
        breakout_date=signal.breakout_date,
        ema10=signal.ema10,
        ema150=signal.ema150,
        close_price=signal.close_price,
        data_rows=signal.data_rows,
        result_desc=signal.result.get_description(),
        name=name,
        sector=sector,
        industry=industry,
        market_cap=market_cap,
        pe_ratio=pe_ratio,
    )

    if verbose:
        mark = "✅" if signal.result.is_satisfied() else "❌"
        print(f"  {mark} {code} [{source}] {signal.result.value} ({len(df)} bars)")

    # 公司有盈利：PE > 0 且有限
    pe = pe_ratio
    is_profitable = None
    if pe is not None:
        try:
            is_profitable = pe > 0 and abs(pe) != float("inf")
        except (TypeError, ValueError):
            is_profitable = None

    return {
        "market": market,
        "code": code,
        "name": name,
        "result": signal.result.value,
        "is_satisfied": signal.result.is_satisfied(),
        "source": source,
        "data_rows": len(df),
        # CSV 额外字段（不写入 DB）
        "market_cap": market_cap,
        "pe_ratio": pe_ratio,
        "close_price": signal.close_price,
        "avg_daily_volume": _compute_avg_daily_volume(df, timeframe),
        "is_profitable": is_profitable,
    }


# ------------------------------------------------------------------
# CSV 导出（仅满足条件的股票）
# ------------------------------------------------------------------


def _write_satisfied_csv(records: list[dict], csv_path: str):
    """
    将满足 EMA 突破条件的股票写入 CSV。
    列：代码, 名称, 市值, 每日平均交易量, 股票价格, 市盈率, 公司有盈利
    """
    if not records:
        return
    os.makedirs(os.path.dirname(csv_path) or ".", exist_ok=True)
    # (表头, 字段名)
    columns = [
        ("代码", "code"),
        ("名称", "name"),
        ("市值", "market_cap"),
        ("每日平均交易量", "avg_daily_volume"),
        ("股票价格", "close_price"),
        ("市盈率", "pe_ratio"),
        ("公司有盈利", "is_profitable"),
    ]
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow([c[0] for c in columns])
        for r in records:
            row = []
            for _, key in columns:
                val = r.get(key)
                if key == "is_profitable":
                    if val is True:
                        val = "是"
                    elif val is False:
                        val = "否"
                    else:
                        val = "未知"
                row.append("" if val is None else val)
            w.writerow(row)


# ------------------------------------------------------------------
# Futu 连接
# ------------------------------------------------------------------

def _init_futu_context(host: str, port: int):
    try:
        import futu as ft
        quote_ctx = ft.OpenQuoteContext(host=host, port=port)
        return quote_ctx, ft
    except Exception:
        return None, None


# ------------------------------------------------------------------
# 主流程
# ------------------------------------------------------------------

def run_once(
    mysql: MySqlConfig,
    markets: list[str],
    timeframe: str,
    use_futu: bool = False,
    futu_host: str = "127.0.0.1",
    futu_port: int = 11111,
    limit: int | None = None,
    log_path: str | None = None,
    csv_path: str | None = None,
):
    db = MarketDatabase(mysql)
    db.init_schema(timeframe)

    futu_ctx = None
    if use_futu:
        futu_ctx, _ = _init_futu_context(futu_host, futu_port)

    fetchers = KlineFetcherFactory.create_fetcher_chain(quote_ctx=futu_ctx)
    log_records: list[dict] = []

    for market in markets:
        market = normalize_market(market)

        # 1) 拉取 / 补充股票列表
        if db.stock_count(market) == 0:
            stocks = fetch_stock_list_akshare(market)
            src = "AKShare"
            if not stocks and futu_ctx:
                stocks = fetch_stock_list_futu(futu_ctx, market)
                src = "Futu"
            if stocks:
                db.upsert_stocks(market, stocks, source=src)
                print(f"✓ {market_label(market)}股票列表入库 ({len(stocks)} 只)")

        stocks = db.get_stocks(market, include_fundamentals=True)
        if not stocks:
            print(f"✗ 未获取到{market_label(market)}股票列表，跳过")
            continue
        if limit:
            stocks = stocks[:limit]

        print(f"\n开始扫描 {len(stocks)} 只{market_label(market)}股票 (timeframe={timeframe})")

        # 2) 逐只扫描
        for i, stock in enumerate(stocks, 1):
            code = stock["code"]
            print(f"[{i}/{len(stocks)}] {code}", end=" ")
            result = _scan_single_stock(
                db, fetchers, market, code, timeframe,
                stock_info=stock, verbose=True,
            )
            if result:
                result["timestamp"] = datetime.utcnow().isoformat()
                log_records.append(result)
                print(f"输出结果:\n{result}\n")
            # 简单限速
            time.sleep(0.3)

    if futu_ctx:
        futu_ctx.close()
    db.close()

    satisfied = [r for r in log_records if r.get("is_satisfied")]
    print(f"\n扫描完成：共 {len(log_records)} 只，突破 {len(satisfied)} 只")

    if csv_path and satisfied:
        _write_satisfied_csv(satisfied, csv_path)
        print(f"✓ 突破股票已导出 CSV: {csv_path}")


def run_loop(interval_hours: int, **kwargs):
    while True:
        run_once(**kwargs)
        print(f"\n等待 {interval_hours} 小时后再次执行...")
        time.sleep(interval_hours * 3600)


# ------------------------------------------------------------------
# CLI
# ------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="股票 EMA 突破扫描（简化版）")

    # MySQL
    parser.add_argument("--mysql-host", default=os.getenv("MYSQL_HOST", "127.0.0.1"))
    parser.add_argument("--mysql-port", type=int, default=int(os.getenv("MYSQL_PORT", "3306")))
    parser.add_argument("--mysql-user", default=os.getenv("MYSQL_USER", "root"))
    parser.add_argument("--mysql-password", default=os.getenv("MYSQL_PASSWORD", "123456"))
    parser.add_argument("--mysql-database", default=os.getenv("MYSQL_DATABASE", "market_data"))

    # 扫描参数
    parser.add_argument("--markets", default="US", help="市场列表: HK,US,A")
    parser.add_argument("--timeframe", default="1d",
                        help="K 线周期: 1m,5m,15m,30m,60m,1h,1d,5d,1wk,1mo,3mo")
    parser.add_argument("--limit", type=int, default=None, help="限制股票数量（测试用）")

    # Futu
    parser.add_argument("--use-futu", action="store_true", default=False)
    parser.add_argument("--futu-host", default="127.0.0.1")
    parser.add_argument("--futu-port", type=int, default=11111)

    # 运行模式
    parser.add_argument("--log", default="logs/daily_sync.jsonl", help="日志文件路径")
    parser.add_argument("--csv", default=None, help="将满足条件的股票导出到 CSV 文件路径")
    parser.add_argument("--loop", action="store_true", help="循环执行")
    parser.add_argument("--interval-hours", type=int, default=24, help="循环间隔（小时）")

    args = parser.parse_args()

    timeframe = parse_timeframe(args.timeframe)
    mysql = MySqlConfig(
        host=args.mysql_host,
        port=args.mysql_port,
        user=args.mysql_user,
        password=args.mysql_password,
        database=args.mysql_database,
    )
    markets = parse_markets(args.markets)

    print(f"启动 | 市场: {args.markets} | timeframe: {timeframe} | limit: {args.limit or '全部'}")

    if args.loop:
        run_loop(
            interval_hours=args.interval_hours,
            mysql=mysql, markets=markets, timeframe=timeframe,
            use_futu=args.use_futu, futu_host=args.futu_host, futu_port=args.futu_port,
            limit=args.limit, log_path=args.log, csv_path=args.csv,
        )
    else:
        run_once(
            mysql=mysql, markets=markets, timeframe=timeframe,
            use_futu=args.use_futu, futu_host=args.futu_host, futu_port=args.futu_port,
            limit=args.limit, log_path=args.log, csv_path=args.csv,
        )


if __name__ == "__main__":
    main()
