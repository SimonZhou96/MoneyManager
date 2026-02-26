#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试K线获取问题
"""

import sys
sys.path.insert(0, '/Users/simon/Documents/GitHub/MoneyManager/stock_screener')

from api.screen_service import create_strategizer_chain_from_params

# 测试1: 检查策略链是否正确创建
print("=" * 60)
print("测试1: 检查策略链创建")
print("=" * 60)

params = {
    "use_ema_breakout": True,
    "ema_short": 10,
    "ema_long": 150
}

chain = create_strategizer_chain_from_params(params)
strategizers = chain.list_strategizers()

print(f"策略器数量: {len(strategizers)}")
print(f"策略器列表: {strategizers}")
print(f"len(chain.list_strategizers()) > 0: {len(chain.list_strategizers()) > 0}")

# 测试2: 检查 needs_kline 逻辑
print("\n" + "=" * 60)
print("测试2: 检查 needs_kline 逻辑")
print("=" * 60)

from api.screen_service import create_filter_chain_from_params

filter_chain = create_filter_chain_from_params(params)

needs_kline_from_filters = any(
    f.__class__.__name__ in ["PriceFilter", "AvgDailyVolumeFilter"]
    for f in filter_chain._filters if f.enabled
)

needs_kline_from_strategizers = len(chain.list_strategizers()) > 0

needs_kline = needs_kline_from_filters or needs_kline_from_strategizers

print(f"筛选器需要K线: {needs_kline_from_filters}")
print(f"策略器需要K线: {needs_kline_from_strategizers}")
print(f"最终 needs_kline: {needs_kline}")

# 测试3: 检查 KlineFetcherFactory
print("\n" + "=" * 60)
print("测试3: 检查 KlineFetcherFactory")
print("=" * 60)

from kline_fetcher import KlineFetcherFactory

fetchers = KlineFetcherFactory.create_fetcher_chain() if needs_kline else None

if fetchers:
    print(f"✓ 创建了 {len(fetchers)} 个K线获取器")
    for fetcher in fetchers:
        print(f"  - {fetcher.get_name()}")
else:
    print("✗ 没有创建K线获取器")

print("\n" + "=" * 60)
print("结论")
print("=" * 60)

if needs_kline and fetchers:
    print("✓ 逻辑正常，应该会获取K线数据")
else:
    print("✗ 逻辑有问题，不会获取K线数据")
    if not needs_kline:
        print("  原因: needs_kline = False")
    if not fetchers:
        print("  原因: fetchers = None")
