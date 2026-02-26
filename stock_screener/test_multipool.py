#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试多股票池合并逻辑
"""

import os
from db import MarketDatabase, MySqlConfig

def test_multi_pool_merge():
    """测试合并多个股票池"""
    config = MySqlConfig(
        host='127.0.0.1',
        port=3306,
        user='root',
        password=os.getenv('MYSQL_PASSWORD', '123456'),
        database='market_data'
    )

    db = MarketDatabase(config)

    try:
        # 测试场景：合并 best + industry
        pool_types = ['best', 'industry']
        market = 'HK'

        print(f"测试合并股票池: {' + '.join(pool_types)}")
        print("=" * 60)

        all_stocks = {}
        for pool_type in pool_types:
            stocks = db.get_stock_pool(market, pool_type)
            print(f"\n{pool_type} 股票池: {len(stocks)} 只股票")

            for stock in stocks:
                code = stock['code']
                if code not in all_stocks:
                    all_stocks[code] = stock
                    print(f"  添加: {code} - {stock.get('name', '-')}")
                else:
                    print(f"  跳过（重复）: {code} - {stock.get('name', '-')}")

        print("\n" + "=" * 60)
        print(f"合并后总数: {len(all_stocks)} 只股票（去重）")
        print(f"原始总数: {sum(len(db.get_stock_pool(market, pt)) for pt in pool_types)} 只")
        print(f"去重数量: {sum(len(db.get_stock_pool(market, pt)) for pt in pool_types) - len(all_stocks)} 只")

        # 显示前10只股票
        print("\n前10只股票:")
        for i, (code, stock) in enumerate(list(all_stocks.items())[:10], 1):
            print(f"{i}. {code} - {stock.get('name', '-')} - 市值: {stock.get('market_cap', 0)/1e8:.2f}亿")

    finally:
        db.close()

if __name__ == '__main__':
    test_multi_pool_merge()
