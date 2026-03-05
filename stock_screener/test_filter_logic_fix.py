#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试筛选器逻辑修复
"""

import sys
sys.path.insert(0, '/Users/simon/Documents/GitHub/MoneyManager/stock_screener')

from filters import FilterChain, FilterContext, StockInfo
from strategizers import StrategizerChain, EMABreakoutStrategizer
from datetime import date
import pandas as pd

print("=" * 60)
print("测试筛选器逻辑修复")
print("=" * 60)

# 创建测试股票
stock = StockInfo(
    code="HK.00699",
    name="JOYSON ELEC",
    market="HK"
)

# 创建测试 K 线数据（模拟 EMA10 突破 EMA150）
kline_data = []
for i in range(200):
    kline_data.append({
        'time_key': f'2026-02-{i+1:02d}',
        'open': 17.0 + i * 0.01,
        'high': 17.5 + i * 0.01,
        'low': 16.5 + i * 0.01,
        'close': 17.0 + i * 0.01,
        'volume': 1000000,
    })

stock.kline_df = pd.DataFrame(kline_data)

# 创建上下文
context = FilterContext(
    check_date=date.today(),
    market="HK",
    db=None,
    verbose=False
)

print("\n测试1: 空筛选器链 + EMA 满足")
print("-" * 60)

# 创建空筛选器链
filter_chain = FilterChain(mode="all", early_stop=False)
filter_result = filter_chain.apply(stock, context)

print(f"筛选器数量: {len(filter_chain.list_filters())}")
print(f"filter_result.passed: {filter_result.passed}")
print(f"filter_result.filter_outputs: {len(filter_result.filter_outputs)}")

# 创建策略链
strategy_chain = StrategizerChain()
strategy_chain.add_strategizer(EMABreakoutStrategizer(ema_short=10, ema_long=150))
strategy_result = strategy_chain.apply(stock, context)

print(f"strategy_result.any_satisfied: {strategy_result.any_satisfied}")

# 模拟 screen_service.py 的逻辑
final_passed = filter_result.passed and strategy_result.any_satisfied

print(f"\n最终结果: {final_passed}")
print(f"预期结果: True")
print(f"测试: {'✓ 通过' if final_passed == True else '✗ 失败'}")

print("\n" + "=" * 60)
print("测试2: 空筛选器链 + EMA 不满足")
print("-" * 60)

# 创建不满足 EMA 的 K 线数据
kline_data_fail = []
for i in range(200):
    kline_data_fail.append({
        'time_key': f'2026-02-{i+1:02d}',
        'open': 17.0,
        'high': 17.5,
        'low': 16.5,
        'close': 17.0,  # 价格不变，不会突破
        'volume': 1000000,
    })

stock2 = StockInfo(code="HK.00001", name="TEST", market="HK")
stock2.kline_df = pd.DataFrame(kline_data_fail)

filter_result2 = filter_chain.apply(stock2, context)
strategy_result2 = strategy_chain.apply(stock2, context)

final_passed2 = filter_result2.passed and strategy_result2.any_satisfied

print(f"filter_result.passed: {filter_result2.passed}")
print(f"strategy_result.any_satisfied: {strategy_result2.any_satisfied}")
print(f"最终结果: {final_passed2}")
print(f"预期结果: False")
print(f"测试: {'✓ 通过' if final_passed2 == False else '✗ 失败'}")

print("\n" + "=" * 60)
print("结论")
print("=" * 60)

if final_passed == True and final_passed2 == False:
    print("✓ 修复成功！")
    print("  - 空筛选器链 + EMA 满足 = 通过")
    print("  - 空筛选器链 + EMA 不满足 = 不通过")
else:
    print("✗ 修复失败")
    print(f"  - 测试1结果: {final_passed} (预期 True)")
    print(f"  - 测试2结果: {final_passed2} (预期 False)")
