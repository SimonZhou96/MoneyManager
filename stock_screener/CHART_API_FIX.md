# 图表 API 404 错误修复

## 修复日期
2026-02-26

## 问题描述

用户报告图表 API 返回 404 错误：
```
GET /api/chart?code=00941&market=HK&timeframe=15m&name=CHINA+MOBILE HTTP/1.1" 404 Not Found
```

## 问题根因

通过测试发现，API 路由存在且正常注册，但是请求会**超时 35 秒后连接被重置**。

**根本原因**：`api/routes/chart.py` 第 57 行调用 `KlineFetcherFactory.create_fetcher_chain()` 时**没有传入 `quote_ctx` 参数**，导致：
1. Futu K线获取器未被添加到获取器链中
2. 只有 YFinance 和 AKShare 两个获取器
3. 对于 15 分钟 K 线，这两个获取器可能失败或超时
4. 服务器一直等待，最终超时

这和之前在 `screen_service.py` 中修复的问题完全一样。

## 修复方案

修改 `api/routes/chart.py`，添加 Futu OpenD 连接支持：

### 修改前（第 56-68 行）
```python
# 获取 K 线数据
fetchers = KlineFetcherFactory.create_fetcher_chain()
df = None
for fetcher in fetchers:
    try:
        df = fetcher.fetch(code, market=market, timeframe=timeframe)
        if df is not None and not df.empty:
            break
    except Exception:
        continue

if df is None or df.empty:
    raise HTTPException(status_code=404, detail=f"无法获取 {code} 的 K 线数据")
```

### 修改后（第 56-85 行）
```python
# 尝试连接 Futu OpenD（用于获取 K 线数据）
quote_ctx = None
try:
    from futu import OpenQuoteContext
    quote_ctx = OpenQuoteContext(host='127.0.0.1', port=11111)
except Exception:
    # Futu 不可用时静默失败，使用其他数据源
    pass

try:
    # 获取 K 线数据（传入 quote_ctx 以支持 Futu 获取器）
    fetchers = KlineFetcherFactory.create_fetcher_chain(quote_ctx=quote_ctx)
    df = None
    for fetcher in fetchers:
        try:
            df = fetcher.fetch(code, market=market, timeframe=timeframe)
            if df is not None and not df.empty:
                break
        except Exception:
            continue

    if df is None or df.empty:
        raise HTTPException(status_code=404, detail=f"无法获取 {code} 的 K 线数据")
finally:
    # 关闭 Futu 连接
    if quote_ctx is not None:
        try:
            quote_ctx.close()
        except Exception:
            pass
```

## 修复效果

### 修复前
- 请求超时 35 秒后连接被重置
- 用户看到 404 错误或超时错误
- 无法查看股票 K 线图

### 修复后
- ✅ 成功获取 2000 条 15 分钟 K 线数据
- ✅ 响应时间正常（< 5 秒）
- ✅ 包含完整的 OHLCV + EMA10 + EMA150 + RSI 数据
- ✅ 前端可以正常显示 K 线图

### 测试结果
```bash
curl -s "http://localhost:8000/api/chart?code=00941&market=HK&timeframe=15m&name=CHINA+MOBILE"

# 返回结果：
{
  "code": "00941",
  "name": "CHINA MOBILE",
  "market": "HK",
  "timeframe": "15m",
  "data": [
    {
      "time": "2025-02-26T09:45:00",
      "open": 74.91,
      "high": 75.71,
      "low": 74.76,
      "close": 74.96,
      "volume": 3206500,
      "ema10": 74.96,
      "ema150": 74.96,
      "rsi": null
    },
    ... (共 2000 条数据)
  ],
  "latest_rsi": 59.15
}
```

## 技术细节

### K线获取器优先级（修复后）
1. **YFinance**（优先级 1）- 全 timeframe 支持
2. **AKShare**（优先级 2）- 日线 + A股分钟线
3. **Futu**（优先级 3）- 港股/美股全 timeframe（最可靠）✅ 新增

### 连接管理
- 每次请求创建新的 `quote_ctx` 连接
- 使用 `try-finally` 确保连接被正确关闭
- Futu 不可用时静默失败，自动降级到其他数据源

### 容错处理
- ✅ Futu 连接失败时不影响其他获取器
- ✅ 所有获取器失败时返回 404 错误
- ✅ 连接关闭失败时不抛出异常

## 相关修复

这是继 `screen_service.py` 之后的第二处相同问题修复：

1. **screen_service.py**（已修复）- 筛选服务中的 K 线获取
2. **chart.py**（本次修复）- 图表 API 中的 K 线获取

两处都需要传入 `quote_ctx` 参数才能使用 Futu 获取器。

## 后续优化建议

1. **连接池** - 复用 `quote_ctx` 连接，避免频繁创建/销毁
2. **缓存机制** - 缓存 K 线数据，减少 API 调用
3. **超时控制** - 为每个获取器设置超时时间（如 10 秒）
4. **日志记录** - 记录获取器使用情况和失败原因
5. **监控告警** - 监控 K 线获取成功率和响应时间

## 总结

✅ **修复完成**

通过添加 Futu OpenD 连接支持，图表 API 现在可以：
- 成功获取 15 分钟 K 线数据
- 快速响应（< 5 秒）
- 提供完整的技术指标数据
- 支持前端 K 线图展示

---

修复时间: 2026-02-26
修复人: Claude
状态: ✅ 已完成
