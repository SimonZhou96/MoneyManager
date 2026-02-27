#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
更新股票基本面数据的工具脚本

使用 Futu API 获取股票的基本面数据（板块、行业、市值、市盈率等）并更新到数据库
"""

import os
import sys
import time
from typing import List, Dict

from db import MarketDatabase, MySqlConfig
from market import normalize_market


def get_fundamentals_from_futu(quote_ctx, market: str, codes: List[str], verbose: bool = True) -> Dict[str, dict]:
    """
    从 Futu API 批量获取股票基本面数据

    Args:
        quote_ctx: Futu OpenQuoteContext
        market: 市场代码 (HK/US/A)
        codes: 股票代码列表
        verbose: 是否输出详细日志

    Returns:
        {code: {sector, industry, market_cap, pe_ratio, pb_ratio}}
    """
    try:
        import futu as ft
    except ImportError:
        if verbose:
            print("✗ 未安装 futu-api，请运行: pip install futu-api")
        return {}

    market_map = {"US": ft.Market.US, "HK": ft.Market.HK, "A": ft.Market.CN}
    market_enum = market_map.get(market, ft.Market.HK)

    result = {}
    batch_size = 200  # Futu API 限制每次最多查询 200 只股票

    for i in range(0, len(codes), batch_size):
        batch_codes = codes[i:i + batch_size]
        if verbose:
            print(f"  正在获取第 {i+1}-{min(i+batch_size, len(codes))} 只股票的基本面数据...")

        try:
            ret, data = quote_ctx.get_stock_basicinfo(market=market_enum, stock_code=batch_codes)
            if ret == ft.RET_OK and not data.empty:
                for _, row in data.iterrows():
                    code = str(row.get('code', ''))

                    # 提取基本面数据
                    fund_data = {}

                    # 板块和行业
                    if 'main_contract' in row and row['main_contract']:
                        fund_data['sector'] = str(row['main_contract'])
                    if 'industry' in row and row['industry']:
                        fund_data['industry'] = str(row['industry'])

                    # 市值（Futu 返回的是港币/美元，需要统一单位）
                    if 'market_val' in row and row['market_val']:
                        fund_data['market_cap'] = float(row['market_val'])

                    # 市盈率
                    if 'pe_ratio' in row and row['pe_ratio']:
                        fund_data['pe_ratio'] = float(row['pe_ratio'])

                    # 市净率
                    if 'pb_ratio' in row and row['pb_ratio']:
                        fund_data['pb_ratio'] = float(row['pb_ratio'])

                    if fund_data:
                        result[code] = fund_data
            else:
                if verbose:
                    print(f"  ⚠️  获取失败: {ret}")
        except Exception as e:
            if verbose:
                print(f"  ⚠️  批次获取失败: {e}")

        # 限速，避免触发 API 频率限制
        time.sleep(0.3)

    return result


def update_stock_fundamentals(market: str, mysql_config: MySqlConfig, verbose: bool = True):
    """
    更新指定市场的股票基本面数据

    Args:
        market: 市场代码 (HK/US/A)
        mysql_config: MySQL 配置
        verbose: 是否输出详细日志
    """
    market = normalize_market(market)

    # 连接数据库
    db = MarketDatabase(mysql_config)

    # 获取所有股票代码
    stocks = db.get_stocks(market, include_fundamentals=False)
    if not stocks:
        if verbose:
            print(f"✗ 未找到{market}市场的股票数据")
        db.close()
        return

    codes = [s['code'] for s in stocks]
    if verbose:
        print(f"✓ 找到 {len(codes)} 只{market}市场股票")

    # 连接 Futu OpenD
    try:
        from futu import OpenQuoteContext
        quote_ctx = OpenQuoteContext(host='127.0.0.1', port=11111)
        if verbose:
            print("✓ 已连接 Futu OpenD")
    except Exception as e:
        if verbose:
            print(f"✗ 无法连接 Futu OpenD: {e}")
            print("  请确保 Futu OpenD 正在运行")
        db.close()
        return

    # 获取基本面数据
    if verbose:
        print(f"\n开始获取基本面数据...")
    fundamentals = get_fundamentals_from_futu(quote_ctx, market, codes, verbose)

    if not fundamentals:
        if verbose:
            print("✗ 未获取到任何基本面数据")
        quote_ctx.close()
        db.close()
        return

    if verbose:
        print(f"✓ 成功获取 {len(fundamentals)} 只股票的基本面数据")

    # 更新数据库
    if verbose:
        print(f"\n开始更新数据库...")

    updated_count = 0
    with db.conn.cursor() as cursor:
        for code, fund_data in fundamentals.items():
            try:
                # 构建 UPDATE 语句
                set_clauses = []
                values = []

                if 'sector' in fund_data:
                    set_clauses.append("sector=%s")
                    values.append(fund_data['sector'])

                if 'industry' in fund_data:
                    set_clauses.append("industry=%s")
                    values.append(fund_data['industry'])

                if 'market_cap' in fund_data:
                    set_clauses.append("market_cap=%s")
                    values.append(fund_data['market_cap'])

                if 'pe_ratio' in fund_data:
                    set_clauses.append("pe_ratio=%s")
                    values.append(fund_data['pe_ratio'])

                if 'pb_ratio' in fund_data:
                    set_clauses.append("pb_ratio=%s")
                    values.append(fund_data['pb_ratio'])

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

    # 清理
    quote_ctx.close()
    db.close()


def main():
    """主函数"""
    import argparse

    parser = argparse.ArgumentParser(description='更新股票基本面数据')
    parser.add_argument('--market', type=str, default='HK', help='市场代码 (HK/US/A)')
    parser.add_argument('--host', type=str, default='127.0.0.1', help='MySQL 主机')
    parser.add_argument('--port', type=int, default=3306, help='MySQL 端口')
    parser.add_argument('--user', type=str, default='root', help='MySQL 用户名')
    parser.add_argument('--password', type=str, help='MySQL 密码（也可通过 MYSQL_PASSWORD 环境变量设置）')
    parser.add_argument('--database', type=str, default='market_data', help='MySQL 数据库名')

    args = parser.parse_args()

    # 获取 MySQL 密码
    password = args.password or os.getenv('MYSQL_PASSWORD', '123456')

    mysql_config = MySqlConfig(
        host=args.host,
        port=args.port,
        user=args.user,
        password=password,
        database=args.database,
    )

    print(f"{'='*60}")
    print(f"更新 {args.market} 市场股票基本面数据")
    print(f"{'='*60}\n")

    update_stock_fundamentals(args.market, mysql_config, verbose=True)

    print(f"\n{'='*60}")
    print("完成")
    print(f"{'='*60}")


if __name__ == '__main__':
    main()
