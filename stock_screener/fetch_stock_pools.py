#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
股票池获取脚本

用法:
    python3 fetch_stock_pools.py --market HK --pools all
    python3 fetch_stock_pools.py --market HK --pools best,major_index,all_etf
"""

import argparse
import os
import sys
from datetime import datetime

import futu as ft

from db import MarketDatabase, MySqlConfig
from market import normalize_market
from stock_pool import (
    DEFAULT_POOL_TYPES_TEXT,
    POOL_TYPE_ALL_ETF,
    POOL_TYPE_BEST,
    POOL_TYPE_INDUSTRY_TOP5,
    POOL_TYPE_MAJOR_INDEX,
    POOL_TYPE_RECENT_IPO_2Y,
    StockPoolCriteria,
    StockPoolFetcher,
    get_major_index_codes,
    parse_pool_types,
)


def get_db_config() -> MySqlConfig:
    """从环境变量获取数据库配置"""
    return MySqlConfig(
        host=os.getenv("MYSQL_HOST", "127.0.0.1"),
        port=int(os.getenv("MYSQL_PORT", "3306")),
        user=os.getenv("MYSQL_USER", "root"),
        password=os.getenv("MYSQL_PASSWORD", "123456"),
        database=os.getenv("MYSQL_DATABASE", "market_data"),
    )


def fetch_and_save_best_stocks(
    fetcher: StockPoolFetcher, db: MarketDatabase, market: str
):
    """获取并保存最好股票"""
    print(f"\n=== 获取 {market} 最好股票 ===")

    # 港股筛选条件
    criteria = StockPoolCriteria(
        market_cap_min=5_000_000_000,  # 50亿
        price_min=5.0,
        pe_min=5.0,
        avg_volume_min=20_000_000,  # 2000万
    )

    try:
        stocks = fetcher.fetch_best_stocks(market, criteria)
        print(f"筛选出 {len(stocks)} 只股票")

        if stocks:
            db.upsert_stock_pool(market, POOL_TYPE_BEST, stocks)
            db.record_pool_update(market, POOL_TYPE_BEST, len(stocks), "success")
            print(f"已保存到数据库")

            # 显示前10只
            print("\n前10只股票:")
            for i, stock in enumerate(stocks[:10], 1):
                print(f"{i}. {stock['code']} {stock['name']} - "
                      f"市值: {stock['market_cap']/1e8:.2f}亿, "
                      f"价格: {stock['price']:.2f}, "
                      f"PE: {stock['pe_ratio']:.2f}")
    except Exception as e:
        print(f"错误: {e}")
        db.record_pool_update(market, POOL_TYPE_BEST, 0, "failed", str(e))


def fetch_and_save_major_index_constituents(
    fetcher: StockPoolFetcher, db: MarketDatabase, market: str
):
    """获取并保存核心指数成份股"""
    print(f"\n=== 获取 {market} 核心指数成份股 ===")

    index_codes = get_major_index_codes(market)

    try:
        stocks = fetcher.fetch_major_index_constituents(market, index_codes)
        print(f"获取到 {len(stocks)} 只成份股")

        if stocks:
            db.upsert_stock_pool(market, POOL_TYPE_MAJOR_INDEX, stocks)
            db.record_pool_update(market, POOL_TYPE_MAJOR_INDEX, len(stocks), "success")
            print(f"已保存到数据库")

            # 按指数统计
            from collections import Counter
            index_counts = Counter([s['index_code'] for s in stocks])
            print("\n各指数成份股数量:")
            for idx_code, count in index_counts.items():
                print(f"  {idx_code}: {count} 只")
    except Exception as e:
        print(f"错误: {e}")
        db.record_pool_update(market, POOL_TYPE_MAJOR_INDEX, 0, "failed", str(e))


def fetch_and_save_industry_top5(
    fetcher: StockPoolFetcher, db: MarketDatabase, market: str, top_n: int = 5
):
    """获取并保存主流行业前N名股票"""
    print(f"\n=== 获取 {market} 各行业前{top_n}名股票 ===")

    try:
        stocks = fetcher.fetch_industry_leaders(market, top_n)
        print(f"获取到 {len(stocks)} 只行业龙头股")

        if stocks:
            db.upsert_stock_pool(market, POOL_TYPE_INDUSTRY_TOP5, stocks)
            db.record_pool_update(market, POOL_TYPE_INDUSTRY_TOP5, len(stocks), "success")
            print(f"已保存到数据库")

            # 统计行业数量
            industries = set([s['industry_name'] for s in stocks])
            print(f"\n覆盖 {len(industries)} 个行业")
    except Exception as e:
        print(f"错误: {e}")
        db.record_pool_update(market, POOL_TYPE_INDUSTRY_TOP5, 0, "failed", str(e))


def fetch_and_save_recent_ipo_2y(
    fetcher: StockPoolFetcher, db: MarketDatabase, market: str, days: int = 730
):
    """获取并保存最近两年上市新股"""
    print(f"\n=== 获取 {market} 最近{days}天上市的新股 ===")

    try:
        stocks = fetcher.fetch_recent_ipos(market, days)
        print(f"获取到 {len(stocks)} 只新股")

        if stocks:
            db.upsert_stock_pool(market, POOL_TYPE_RECENT_IPO_2Y, stocks)
            db.record_pool_update(market, POOL_TYPE_RECENT_IPO_2Y, len(stocks), "success")
            print(f"已保存到数据库")

            # 显示最新的10只
            stocks_sorted = sorted(stocks, key=lambda x: x['listing_date'], reverse=True)
            print("\n最新上市的10只:")
            for i, stock in enumerate(stocks_sorted[:10], 1):
                print(f"{i}. {stock['code']} {stock['name']} - "
                      f"上市日期: {stock['listing_date']} "
                      f"({stock['days_since_listing']}天)")
    except Exception as e:
        print(f"错误: {e}")
        db.record_pool_update(market, POOL_TYPE_RECENT_IPO_2Y, 0, "failed", str(e))


def fetch_and_save_all_etf(
    fetcher: StockPoolFetcher, db: MarketDatabase, market: str
):
    """获取并保存全部ETF指数基金"""
    print(f"\n=== 获取 {market} ETF列表 ===")

    try:
        stocks = fetcher.fetch_etf_list(market)
        print(f"获取到 {len(stocks)} 只ETF")

        if stocks:
            db.upsert_stock_pool(market, POOL_TYPE_ALL_ETF, stocks)
            db.record_pool_update(market, POOL_TYPE_ALL_ETF, len(stocks), "success")
            print(f"已保存到数据库")

            # 显示前20只
            print("\n前20只ETF:")
            for i, stock in enumerate(stocks[:20], 1):
                print(f"{i}. {stock['code']} {stock['name']}")
    except Exception as e:
        print(f"错误: {e}")
        db.record_pool_update(market, POOL_TYPE_ALL_ETF, 0, "failed", str(e))


def main():
    parser = argparse.ArgumentParser(description="获取股票池数据")
    parser.add_argument(
        "--market",
        type=str,
        default="HK",
        help="市场: HK/US/A (默认: HK)",
    )
    parser.add_argument(
        "--pools",
        type=str,
        default="all",
        help=f"要获取的池: all/{DEFAULT_POOL_TYPES_TEXT}，多个用逗号分隔 (默认: all)",
    )
    parser.add_argument(
        "--host",
        type=str,
        default="127.0.0.1",
        help="Futu OpenD 主机 (默认: 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=11111,
        help="Futu OpenD 端口 (默认: 11111)",
    )

    args = parser.parse_args()

    market = normalize_market(args.market)
    try:
        pools = parse_pool_types(args.pools)
    except ValueError as exc:
        parser.error(str(exc))

    print(f"开始时间: {datetime.now()}")
    print(f"市场: {market}")
    print(f"股票池: {', '.join(pools)}")

    # 连接 Futu OpenD
    print(f"\n连接 Futu OpenD ({args.host}:{args.port})...")
    quote_ctx = ft.OpenQuoteContext(host=args.host, port=args.port)

    # 连接数据库
    print("连接数据库...")
    db_config = get_db_config()
    db = MarketDatabase(db_config)

    # 初始化股票池表结构
    print("初始化数据库表...")
    db.init_stock_pool_schema()

    # 创建获取器
    fetcher = StockPoolFetcher(quote_ctx=quote_ctx, db=db)

    try:
        # 执行获取
        if POOL_TYPE_BEST in pools:
            fetch_and_save_best_stocks(fetcher, db, market)

        if POOL_TYPE_MAJOR_INDEX in pools:
            fetch_and_save_major_index_constituents(fetcher, db, market)

        if POOL_TYPE_INDUSTRY_TOP5 in pools:
            fetch_and_save_industry_top5(fetcher, db, market, top_n=5)

        if POOL_TYPE_RECENT_IPO_2Y in pools:
            fetch_and_save_recent_ipo_2y(fetcher, db, market, days=730)

        if POOL_TYPE_ALL_ETF in pools:
            fetch_and_save_all_etf(fetcher, db, market)

        print(f"\n完成时间: {datetime.now()}")
        print("\n所有股票池已更新完成！")

    finally:
        quote_ctx.close()
        db.close()


if __name__ == "__main__":
    main()
