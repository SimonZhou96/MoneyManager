# K线数据获取问题修复

## 问题描述

在股票池筛选过程中，大量股票显示"未获取到 K 线数据"，导致无法进行 EMA 突破策略和 RSI 策略的判断。

### 典型错误信息
```
[184/945] ❌ HK.01193 - CHINA RES GAS
⚠️  警告: 未获取到 K 线数据

  ❌ EMABreakoutStrategizer: fail
     原因: K线数据不足
     数值: kline_available=False
```

## 问题根因

### 1. Futu 获取器未被添加到 fetcher 链

**位置**: `api/screen_service.py` 第 228 行

**原代码**:
```python
fetchers = KlineFetcherFactory.create_fetcher_chain() if needs_kline else None
```

**问题**: 调用 `create_fetcher_chain()` 时没有传入 `quote_ctx` 参数

### 2. KlineFetcherFactory 的逻辑

**位置**: `kline_fetcher.py` 第 459 行

```python
# 3. Futu（需要 OpenD）
if quote_ctx is not None:
    try:
        fetchers.append(FutuKlineFetcher(quote_ctx, rate_limiter))
```

因为 `quote_ctx` 是 `None`，Futu 获取器被跳过了！

### 3. 实际的 fetcher 链

**修复前**:
- YFinance（优先级 1）- 对港股 15 分钟 K 线支持不好
- AKShare（优先级 2）- 主要支持 A 股，对港股支持有限
- ~~Futu~~（被跳过）

**结果**: 港股 15 分钟 K 线获取失败率极高

### 4. 验证 Futu 可以获取数据

测试证明 Futu API 可以成功获取 HK.01193 的 K 线数据：
- 15 分钟 K 线: 792 条
- 日 K 线: 283 条

但因为 Futu 获取器没有被添加，所以无法使用！

## 解决方案

### 修改 1: 添加 Futu OpenD 连接

**位置**: `api/screen_service.py` 第 227-243 行

```python
# 创建 K 线获取器（尝试连接 Futu OpenD）
fetchers = None
quote_ctx = None
if needs_kline:
    # 尝试连接 Futu OpenD
    try:
        from futu import OpenQuoteContext
        quote_ctx = OpenQuoteContext(host='127.0.0.1', port=11111)
        if verbose:
            print("✓ 已连接 Futu OpenD")
    except Exception as e:
        if verbose:
            print(f"⚠️  无法连接 Futu OpenD: {e}，将使用其他数据源")
        quote_ctx = None

    # 创建获取器链（传入 quote_ctx，如果可用）
    fetchers = KlineFetcherFactory.create_fetcher_chain(quote_ctx=quote_ctx)
```

### 修改 2: 在 finally 块中关闭连接

**位置**: `api/screen_service.py` 第 448-458 行

```python
finally:
    # 关闭 Futu quote_ctx
    if 'quote_ctx' in locals() and quote_ctx is not None:
        try:
            quote_ctx.close()
        except Exception:
            pass

    # 关闭数据库连接
    if db:
        db.close()
```

## 修复效果

### 修复前
```
获取器数量: 2
  - YFinance
  - AKShare

结果: 港股 15 分钟 K 线获取失败
```

### 修复后
```
获取器数量: 3
  - YFinance
  - AKShare
  - FutuOpenAPI

结果: ✓ 成功获取 2000 条 K 线数据
```

## 容错处理

### 1. Futu OpenD 不可用时
如果 Futu OpenD 未启动或连接失败：
- 自动捕获异常
- 输出警告信息
- 降级到 YFinance 和 AKShare
- 不影响程序运行

### 2. 判空处理
```python
if 'quote_ctx' in locals() and quote_ctx is not None:
    quote_ctx.close()
```

确保即使 quote_ctx 未创建或为 None，也不会报错。

## 优先级顺序

K 线获取器的尝试顺序（按优先级）：
1. **YFinance** - 全 timeframe 支持
2. **AKShare** - 日线 + A 股分钟线
3. **Futu** - 港股/美股全 timeframe（需要 OpenD）

如果前面的获取器失败，会自动尝试下一个。

## 测试验证

### 测试脚本
- `test_futu_fix.py` - 验证修复效果

### 测试结果
```bash
MYSQL_PASSWORD=123456 python3 test_futu_fix.py

✓ 成功连接 Futu OpenD
✓ 成功获取 2000 条 K 线数据
```

## 使用说明

### 前提条件
确保 Futu OpenD 已启动：
```bash
# 检查 Futu OpenD 是否运行
ps aux | grep FutuOpenD

# 或者测试连接
python3 -c "from futu import OpenQuoteContext; ctx = OpenQuoteContext(host='127.0.0.1', port=11111); print('OK'); ctx.close()"
```

### 运行筛选
```bash
# 启动 API 服务器
MYSQL_PASSWORD=123456 python3 -m api.main

# 访问前端
http://localhost:8000

# 点击"股票池" Tab，选择股票池，开始筛选
```

### 日志输出
修复后，筛选日志会显示：
```
✓ 已连接 Futu OpenD
[1/338] 📊 HK.00700 - 获取 K 线成功 (FutuOpenAPI, 2000 根)
```

如果 Futu 不可用：
```
⚠️  无法连接 Futu OpenD: ..., 将使用其他数据源
```

## 影响范围

### 受益场景
1. **港股筛选** - 所有 timeframe（1d/1w/1m/15m 等）
2. **美股筛选** - 所有 timeframe
3. **A 股筛选** - 分钟级 timeframe（日线已有 AKShare 支持）

### 不受影响场景
- 不需要 K 线的筛选器（如仅市值、PE 筛选）
- Futu OpenD 不可用时，自动降级到其他数据源

## 后续优化建议

1. **连接池管理** - 复用 quote_ctx，避免频繁创建连接
2. **缓存机制** - 对相同股票的 K 线数据进行缓存
3. **并发获取** - 批量获取 K 线数据，提高效率
4. **监控告警** - 监控 K 线获取成功率，及时发现问题

## 相关文件

### 修改的文件
- `api/screen_service.py` - 添加 Futu 连接和关闭逻辑

### 测试文件
- `test_futu_fix.py` - 验证修复效果
- `test_kline_logic.py` - 测试 K 线获取逻辑

### 文档
- `DEBUG_GUIDE.md` - 调试指南
- `KLINE_FIX.md` - 本文档

---

修复完成时间: 2026-02-26
修复人: Claude
