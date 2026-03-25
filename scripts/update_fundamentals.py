#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
使用 Futu OpenD 更新 stocks 表基本面（依赖 stock_screener）

用法（在仓库根目录）:
    MYSQL_PASSWORD=xxx python3 scripts/update_fundamentals.py --market HK
"""

from __future__ import annotations

import sys
from pathlib import Path

_STOCK = Path(__file__).resolve().parents[1] / "stock_screener"
if str(_STOCK) not in sys.path:
    sys.path.insert(0, str(_STOCK))

import argparse  # noqa: E402
import os  # noqa: E402
import time  # noqa: E402
from typing import Dict, List  # noqa: E402

from db import MarketDatabase, MySqlConfig  # noqa: E402
from market import normalize_market  # noqa: E402


def get_fundamentals_from_futu(quote_ctx, market: str, codes: List[str], verbose: bool = True) -> Dict[str, dict]:
    try:
        import futu as ft
    except ImportError:
        if verbose:
            print("✗ 未安装 futu-api，请运行: pip install futu-api")
        return {}

    market_map = {"US": ft.Market.US, "HK": ft.Market.HK, "A": ft.Market.CN}
    market_enum = market_map.get(market, ft.Market.HK)

    result = {}
    batch_size = 200

    for i in range(0, len(codes), batch_size):
        batch_codes = codes[i : i + batch_size]
        if verbose:
            print(f"  正在获取第 {i + 1}-{min(i + batch_size, len(codes))} 只股票的基本面数据...")

        try:
            ret, data = quote_ctx.get_stock_basicinfo(market=market_enum, stock_code=batch_codes)
            if ret == ft.RET_OK and not data.empty:
                for _, row in data.iterrows():
                    code = str(row.get("code", ""))
                    fund_data = {}
                    if "main_contract" in row and row["main_contract"]:
                        fund_data["sector"] = str(row["main_contract"])
                    if "industry" in row and row["industry"]:
                        fund_data["industry"] = str(row["industry"])
                    if "market_val" in row and row["market_val"]:
                        fund_data["market_cap"] = float(row["market_val"])
                    if "pe_ratio" in row and row["pe_ratio"]:
                        fund_data["pe_ratio"] = float(row["pe_ratio"])
                    if "pb_ratio" in row and row["pb_ratio"]:
                        fund_data["pb_ratio"] = float(row["pb_ratio"])
                    if fund_data:
                        result[code] = fund_data
            elif verbose:
                print(f"  ⚠️  获取失败: {ret}")
        except Exception as e:
            if verbose:
                print(f"  ⚠️  批次获取失败: {e}")

        time.sleep(0.3)

    return result


def update_stock_fundamentals(market: str, mysql_config: MySqlConfig, verbose: bool = True):
    market = normalize_market(market)
    db = MarketDatabase(mysql_config)

    stocks = db.get_stocks(market, include_fundamentals=False)
    if not stocks:
        if verbose:
            print(f"✗ 未找到{market}市场的股票数据")
        db.close()
        return

    codes = [s["code"] for s in stocks]
    if verbose:
        print(f"✓ 找到 {len(codes)} 只{market}市场股票")

    try:
        from futu import OpenQuoteContext

        quote_ctx = OpenQuoteContext(host="127.0.0.1", port=11111)
        if verbose:
            print("✓ 已连接 Futu OpenD")
    except Exception as e:
        if verbose:
            print(f"✗ 无法连接 Futu OpenD: {e}")
        db.close()
        return

    if verbose:
        print("\n开始获取基本面数据...")
    fundamentals = get_fundamentals_from_futu(quote_ctx, market, codes, verbose)

    if not fundamentals:
        if verbose:
            print("✗ 未获取到任何基本面数据")
        quote_ctx.close()
        db.close()
        return

    if verbose:
        print(f"✓ 成功获取 {len(fundamentals)} 只股票的基本面数据\n开始更新数据库...")

    updated_count = 0
    with db.conn.cursor() as cursor:
        for code, fund_data in fundamentals.items():
            try:
                set_clauses = []
                values = []
                if "sector" in fund_data:
                    set_clauses.append("sector=%s")
                    values.append(fund_data["sector"])
                if "industry" in fund_data:
                    set_clauses.append("industry=%s")
                    values.append(fund_data["industry"])
                if "market_cap" in fund_data:
                    set_clauses.append("market_cap=%s")
                    values.append(fund_data["market_cap"])
                if "pe_ratio" in fund_data:
                    set_clauses.append("pe_ratio=%s")
                    values.append(fund_data["pe_ratio"])
                if "pb_ratio" in fund_data:
                    set_clauses.append("pb_ratio=%s")
                    values.append(fund_data["pb_ratio"])
                if set_clauses:
                    sql = f"UPDATE stocks SET {', '.join(set_clauses)} WHERE market=%s AND code=%s"
                    values.extend([market, code])
                    cursor.execute(sql, tuple(values))
                    updated_count += 1
            except Exception as e:
                if verbose:
                    print(f"  ⚠️  更新 {code} 失败: {e}")
        db.conn.commit()

    if verbose:
        print(f"✓ 成功更新 {updated_count} 只股票的基本面数据")

    quote_ctx.close()
    db.close()


def main():
    parser = argparse.ArgumentParser(description="更新股票基本面数据")
    parser.add_argument("--market", type=str, default="HK", help="市场代码 (HK/US/A)")
    parser.add_argument("--host", type=str, default="127.0.0.1", help="MySQL 主机")
    parser.add_argument("--port", type=int, default=3306, help="MySQL 端口")
    parser.add_argument("--user", type=str, default="root", help="MySQL 用户名")
    parser.add_argument("--password", type=str, help="MySQL 密码（或 MYSQL_PASSWORD）")
    parser.add_argument("--database", type=str, default="market_data", help="数据库名")

    args = parser.parse_args()
    password = args.password or os.getenv("MYSQL_PASSWORD", "123456")

    mysql_config = MySqlConfig(
        host=args.host,
        port=args.port,
        user=args.user,
        password=password,
        database=args.database,
    )

    print("=" * 60)
    print(f"更新 {args.market} 市场股票基本面数据")
    print("=" * 60 + "\n")

    update_stock_fundamentals(args.market, mysql_config, verbose=True)

    print("\n" + "=" * 60)
    print("完成")
    print("=" * 60)


if __name__ == "__main__":
    main()
