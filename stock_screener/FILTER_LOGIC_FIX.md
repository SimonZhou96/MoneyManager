# 筛选器逻辑修复 - 空筛选器问题

## 问题描述

当股票满足 EMA 突破策略时，仍然被标记为"未通过"。

### 典型案例
```
[251/945] ❌ HK.00699 - JOYSON ELEC

  ✅ EMABreakoutStrategizer: pass
     原因: 前两个交易日EMA10向上突破EMA150

  ❌ RSIOversoldStrategizer: fail
  ❌ RSIOverboughtStrategizer: fail

总结: passed=1, failed=2, skipped=0
❌ 未通过的筛选器 (2): RSIOversoldStrategizer, RSIOverboughtStrategizer
```

**预期行为**: EMA 满足就应该通过（策略是"任意一个满足即可"）
**实际行为**: 被标记为失败 ❌

## 问题根因

### 根因1: 空筛选器链返回 False

**位置**: `filters.py` 第 286-294 行

**原代码**:
```python
# 计算最终结果
if self._mode == "all":
    result.passed = all(
        o.result in (FilterResult.PASS, FilterResult.SKIP)
        for o in result.filter_outputs
    ) and any(
        o.result == FilterResult.PASS
        for o in result.filter_outputs
    )
```

**问题**: 当没有启用任何筛选器时（`filter_outputs` 为空）：
- `all(...)` = `True`（空集的全称量化为真）
- `any(...)` = `False`（空集的存在量化为假）
- 结果：`True and False = False` ❌

### 根因2: 最终判断逻辑

**位置**: `api/screen_service.py` 第 309 行

```python
result.passed = result.passed and strategy_result.any_satisfied
```

**问题**:
- `result.passed = False`（来自空筛选器链）
- `strategy_result.any_satisfied = True`（EMA 满足）
- 结果：`False and True = False` ❌

### 为什么会出现空筛选器链？

在股票池筛选时，前端只传递了基本参数：
```javascript
{
    market: 'HK',
    timeframe: '15m',
    watchlist: [...],
    use_ema_breakout: true,
    ema_short: 10,
    ema_long: 150
}
```

没有传递任何筛选器参数（如 `market_cap_min`、`pe_min` 等），所以：
- `create_filter_chain_from_params()` 返回空筛选器链
- `filter_chain.list_filters()` = `[]`
- `filter_result.passed` = `False`

## 解决方案

### 修复: 空筛选器链默认通过

**文件**: `filters.py`
**位置**: 第 285-297 行

```python
# 计算最终结果
if not result.filter_outputs:
    # 没有筛选器 = 默认通过
    result.passed = True
elif self._mode == "all":
    # 全部通过模式：所有筛选器都必须通过或跳过
    result.passed = all(
        o.result in (FilterResult.PASS, FilterResult.SKIP)
        for o in result.filter_outputs
    ) and any(
        o.result == FilterResult.PASS
        for o in result.filter_outputs
    )
```

### 修复逻辑

1. **如果没有筛选器** → `result.passed = True`
2. **如果有筛选器** → 按原逻辑判断

这样：
- 空筛选器链：`result.passed = True`
- 策略满足：`strategy_result.any_satisfied = True`
- 最终：`True and True = True` ✅

## 修复效果

### 修复前
```
filter_result.passed = False  (空筛选器链)
strategy_result.any_satisfied = True  (EMA 满足)
最终结果 = False and True = False  ❌
```

### 修复后
```
filter_result.passed = True  (空筛选器链默认通过)
strategy_result.any_satisfied = True  (EMA 满足)
最终结果 = True and True = True  ✅
```

## 测试验证

### 测试脚本
`test_filter_logic_fix.py`

### 测试结果
```bash
MYSQL_PASSWORD=123456 python3 test_filter_logic_fix.py

测试1: 空筛选器链 + EMA 满足
filter_result.passed: True  ✓ (修复前是 False)
```

## 影响范围

### 受益场景
1. **股票池筛选** - 只使用策略，不使用筛选器
2. **自选股筛选** - 只使用策略，不使用筛选器
3. **任何不启用筛选器的场景**

### 不受影响场景
- 启用了筛选器的场景（逻辑不变）
- 筛选器和策略都启用的场景（逻辑不变）

## 策略逻辑说明

### 当前策略逻辑（正确）
```python
# 三个策略器
1. EMABreakoutStrategizer - EMA10 突破 EMA150
2. RSIOversoldStrategizer - RSI <= 30（超卖）
3. RSIOverboughtStrategizer - RSI >= 70（超买）

# 判断逻辑
strategy_result.any_satisfied = 任意一个策略满足即可
```

### 最终判断逻辑
```python
result.passed = filter_result.passed and strategy_result.any_satisfied
```

**含义**:
- 筛选器必须通过（或没有筛选器）
- **且** 至少一个策略满足

## 示例场景

### 场景1: HK.00699 - JOYSON ELEC

**筛选器**: 无
**策略结果**:
- ✅ EMABreakoutStrategizer: pass
- ❌ RSIOversoldStrategizer: fail
- ❌ RSIOverboughtStrategizer: fail

**修复前**: ❌ 未通过（因为空筛选器返回 False）
**修复后**: ✅ 通过（空筛选器默认通过，EMA 满足）

### 场景2: 有筛选器 + 策略满足

**筛选器**: 市值 >= 50亿 ✅
**策略结果**: EMA 满足 ✅

**修复前**: ✅ 通过
**修复后**: ✅ 通过（逻辑不变）

### 场景3: 有筛选器 + 策略不满足

**筛选器**: 市值 >= 50亿 ✅
**策略结果**: 所有策略都不满足 ❌

**修复前**: ❌ 未通过
**修复后**: ❌ 未通过（逻辑不变）

## 相关代码

### 筛选器链创建
**文件**: `api/screen_service.py`
**函数**: `create_filter_chain_from_params()`

只有在参数中指定了筛选条件时才会添加筛选器：
```python
# 市值筛选器
if params.get("market_cap_min") is not None or params.get("market_cap_max") is not None:
    chain.add_filter(MarketCapFilter(...))

# PE 筛选器
if params.get("pe_min") is not None or params.get("pe_max") is not None:
    chain.add_filter(PEFilter(...))

# ... 其他筛选器
```

### 策略链创建
**文件**: `api/screen_service.py`
**函数**: `create_strategizer_chain_from_params()`

总是添加三个策略器：
```python
chain = StrategizerChain()

# EMA 突破策略
if params.get("use_ema_breakout", True):
    chain.add_strategizer(EMABreakoutStrategizer(...))

# RSI 超卖策略
chain.add_strategizer(RSIOversoldStrategizer(...))

# RSI 超买策略
chain.add_strategizer(RSIOverboughtStrategizer(...))
```

## 后续优化建议

1. **明确策略逻辑** - 在前端显示"任意策略满足即可"
2. **可配置策略** - 允许用户选择启用哪些策略
3. **策略组合** - 支持"全部满足"或"任意满足"模式
4. **日志优化** - 更清晰地显示筛选器和策略的判断结果

## 相关文件

### 修改的文件
- `filters.py` - 修复空筛选器链逻辑

### 测试文件
- `test_filter_logic_fix.py` - 验证修复效果

### 文档
- `FILTER_LOGIC_FIX.md` - 本文档

---

修复完成时间: 2026-02-26
状态: ✅ 已完成
