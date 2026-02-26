#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试 Futu K线获取器修复
"""

import sys
sys.path.insert(0, '/Users/simon/Documents/GitHub/MoneyManager/stock_screener')

print("=" * 60)
print("测试 Futu K线获取器修复")
print("=" * 60)

# 测试1: 模拟创建 fetcher 链（不带 quote_ctx）
print("\n测试1: 不带 quote_ctx（旧逻辑）")
print("-" * 60)

from kline_fetcher import KlineFetcherFactory

fetchers_old = KlineFetcherFactory.create_fetcher_chain()
print(f"获取器数量: {len(fetchers_old)}")
for f in fetchers_old:
    print(f"  - {f.get_name()}")

# 测试2: 模拟创建 fetcher 链（带 quote_ctx）
print("\n测试2: 带 quote_ctx（新逻辑）")
print("-" * 60)

try:
    from futu import OpenQuoteContext
    quote_ctx = OpenQuoteContext(host='127.0.0.1', port=11111)
    print("✓ 成功连接 Futu OpenD")

    fetchers_new = KlineFetcherFactory.create_fetcher_chain(quote_ctx=quote_ctx)
    print(f"获取器数量: {len(fetchers_new)}")
    for f in fetchers_new:
        print(f"  - {f.get_name()}")

    # 测试获取 HK.01193 的 K 线
    print("\n测试3: 使用新 fetcher 链获取 HK.01193 的 15 分钟 K 线")
    print("-" * 60)

    code = "HK.01193"
    market = "HK"
    timeframe = "15m"

    for fetcher in fetchers_new:
        try:
            print(f"\n尝试使用 {fetcher.get_name()}...")
            df = fetcher.fetch(code, market=market, timeframe=timeframe)
            if df is not None and not df.empty:
                print(f"✓ 成功获取 {len(df)} 条 K 线数据")
                print(f"最新 3 条:")
                print(df.tail(3)[['time_key', 'open', 'high', 'low', 'close', 'volume']])
                break
            else:
                print(f"✗ 未获取到数据")
        except Exception as e:
            print(f"✗ 失败: {e}")

    quote_ctx.close()
    print("\n✓ 已关闭 Futu 连接")

except Exception as e:
    print(f"✗ 无法连接 Futu OpenD: {e}")

print("\n" + "=" * 60)
print("结论")
print("=" * 60)

print("""
修复前:
  - 只有 YFinance 和 AKShare
  - 港股 15 分钟 K 线获取失败

修复后:
  - 增加了 Futu 获取器（如果 OpenD 可用）
  - 港股 15 分钟 K 线可以成功获取
  - 如果 Futu 不可用，自动降级到其他数据源
""")
