#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
股票池查询 CLI（依赖 stock_screener 包路径）

用法（在仓库根目录）:
    python3 scripts/query_stock_pools.py --market HK --pool best --limit 20
"""

from __future__ import annotations

import sys
from pathlib import Path

_STOCK = Path(__file__).resolve().parents[1] / "stock_screener"
if str(_STOCK) not in sys.path:
    sys.path.insert(0, str(_STOCK))

import argparse  # noqa: E402
import os  # noqa: E402

from db import MarketDatabase, MySqlConfig  # noqa: E402
from market import normalize_market  # noqa: E402


def get_db_config() -> MySqlConfig:
    return MySqlConfig(
        host=os.getenv("MYSQL_HOST", "127.0.0.1"),
        port=int(os.getenv("MYSQL_PORT", "3306")),
        user=os.getenv("MYSQL_USER", "root"),
        password=os.getenv("MYSQL_PASSWORD", "123456"),
        database=os.getenv("MYSQL_DATABASE", "market_data"),
    )


def display_best_stocks(stocks):
    print(f"\n{'代码':<12} {'名称':<20} {'市值(亿)':<12} {'价格':<10} {'PE':<8} {'成交额(万)':<12}")
    print("-" * 90)
    for stock in stocks:
        market_cap = stock["market_cap"] / 1e8 if stock["market_cap"] else 0
        turnover = stock["turnover"] / 1e4 if stock["turnover"] else 0
        print(
            f"{stock['code']:<12} {stock['name']:<20} {market_cap:>10.2f}  "
            f"{stock['price']:>8.2f}  {stock['pe_ratio']:>6.2f}  {turnover:>10.2f}"
        )


def display_index_constituents(stocks):
    from collections import defaultdict

    by_index = defaultdict(list)
    for stock in stocks:
        index_code = stock.get("index_code", "Unknown")
        by_index[index_code].append(stock)

    for index_code, index_stocks in by_index.items():
        print(f"\n=== {index_code} ({len(index_stocks)} 只) ===")
        for i, stock in enumerate(index_stocks[:10], 1):
            print(f"{i:2d}. {stock['code']:<12} {stock['name']}")
        if len(index_stocks) > 10:
            print(f"    ... 还有 {len(index_stocks) - 10} 只")


def display_industry_leaders(stocks):
    from collections import defaultdict

    by_industry = defaultdict(list)
    for stock in stocks:
        industry = stock.get("industry_name", "Unknown")
        by_industry[industry].append(stock)

    for industry, industry_stocks in sorted(by_industry.items()):
        print(f"\n=== {industry} ===")
        for stock in sorted(industry_stocks, key=lambda x: x.get("rank_in_industry", 999)):
            rank = stock.get("rank_in_industry", "-")
            market_cap = stock["market_cap"] / 1e8 if stock["market_cap"] else 0
            print(f"  {rank}. {stock['code']:<12} {stock['name']:<25} 市值: {market_cap:>8.2f}亿")


def display_recent_ipos(stocks):
    print(f"\n{'代码':<12} {'名称':<30} {'上市日期':<12} {'上市天数':<10}")
    print("-" * 80)
    for stock in stocks:
        days = stock.get("days_since_listing", "-")
        print(f"{stock['code']:<12} {stock['name']:<30} {stock['listing_date']:<12} {days:>8}")


def display_etf_list(stocks):
    print(f"\n{'代码':<12} {'名称':<60}")
    print("-" * 80)
    for stock in stocks:
        print(f"{stock['code']:<12} {stock['name']}")


def main():
    parser = argparse.ArgumentParser(description="查询股票池数据")
    parser.add_argument("--market", type=str, default="HK", help="市场: HK/US/A")
    parser.add_argument(
        "--pool",
        type=str,
        required=True,
        help="股票池类型: best/index/industry/ipo/etf",
    )
    parser.add_argument("--limit", type=int, default=None, help="限制返回数量")

    args = parser.parse_args()

    market = normalize_market(args.market)
    pool_type = args.pool.lower()

    if pool_type not in ["best", "index", "industry", "ipo", "etf"]:
        print(f"错误: 无效的股票池类型 '{pool_type}'")
        return

    db_config = get_db_config()
    db = MarketDatabase(db_config)

    try:
        last_update = db.get_pool_last_update(market, pool_type)
        if last_update:
            print(f"最后更新: {last_update['update_time']}")
            print(f"股票数量: {last_update['stock_count']}")
            print(f"状态: {last_update['status']}")
        else:
            print(f"警告: {market} 市场的 {pool_type} 池尚未更新")
            return

        stocks = db.get_stock_pool(market, pool_type, limit=args.limit)

        if not stocks:
            print(f"\n{market} 市场的 {pool_type} 池暂无数据")
            return

        if pool_type == "best":
            display_best_stocks(stocks)
        elif pool_type == "index":
            display_index_constituents(stocks)
        elif pool_type == "industry":
            display_industry_leaders(stocks)
        elif pool_type == "ipo":
            display_recent_ipos(stocks)
        elif pool_type == "etf":
            display_etf_list(stocks)

        print(f"\n总计: {len(stocks)} 只股票")

    finally:
        db.close()


if __name__ == "__main__":
    main()
