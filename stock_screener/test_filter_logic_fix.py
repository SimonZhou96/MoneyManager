#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试筛选器逻辑修复
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from filters import FilterChain, FilterContext, StockInfo
from strategizers import StrategizerChain, EMABreakoutStrategizer
from datetime import date, timedelta
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

# 创建测试 K 线数据（模拟 T1 突破：前一个交易日 t1 发生 EMA10 上穿 EMA150）
# 策略要求：t2 时 ema10<=ema150，t1 时 ema10>ema150
# 构造：前 155 根平缓，最后几根先下落后急涨，使 t1 发生突破
import pandas as pd
from datetime import date, timedelta
base_dates = pd.date_range(end=date.today(), periods=200, freq="B")
kline_data = []
for i in range(156):
    kline_data.append({"date": base_dates[i], "open": 100., "high": 101., "low": 99., "close": 100., "volume": 1e6})
for i in range(156, 158):  # t3, t2
    kline_data.append({"date": base_dates[i], "open": 98., "high": 99., "low": 97., "close": 98. - (i - 156), "volume": 1e6})
kline_data.append({"date": base_dates[158], "open": 95., "high": 115., "low": 94., "close": 112., "volume": 2e6})  # t1 急涨
kline_data.append({"date": base_dates[159], "open": 111., "high": 113., "low": 110., "close": 111., "volume": 1e6})  # t0

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

# 创建不满足 EMA 的 K 线数据（价格恒定，EMA 平行无突破）
kline_data_fail = [{"date": base_dates[i], "open": 100., "high": 100., "low": 100., "close": 100., "volume": 1e6} for i in range(200)]
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
