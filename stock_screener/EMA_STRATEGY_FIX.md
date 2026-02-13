# EMA 突破策略集成修复

## 问题描述
在单循环重构后，发现 **EMA 突破策略筛选器** 没有添加到筛选器链中，导致核心的突破策略判断缺失。

## 修复内容

### 1. 添加 EMABreakoutFilter 到筛选器链 ✅

**修改文件**：`api/screen_service.py`

**修改位置**：`create_filter_chain_from_params()` 函数

**修改内容**：
```python
# EMA 突破筛选器（核心策略，默认启用）
use_ema = params.get("use_ema_breakout", True)  # 默认启用
if use_ema:
    chain.add_filter(EMABreakoutFilter(
        ema_short=params.get("ema_short", 10),
        ema_long=params.get("ema_long", 150),
    ))
```

### 2. API 请求模型增加参数 ✅

**修改文件**：`api/routes/screen.py`

**新增字段**：
```python
class ScreenRequest(BaseModel):
    use_ema_breakout: Optional[bool] = True  # 是否启用 EMA 突破策略
    ema_short: Optional[int] = 10  # EMA 短期周期
    ema_long: Optional[int] = 150  # EMA 长期周期
    # ... 其他字段
```

### 3. 前端添加 EMA 策略开关 ✅

**修改文件**：`frontend/index.html`、`frontend/app.js`

**新增组件**：
```html
<label>
    <input type="checkbox" id="useEmaBreakout" name="use_ema_breakout" checked>
    <span>启用 EMA 突破策略（EMA10 突破 EMA150）</span>
</label>
```

---

## 完整的筛选流程

### 单循环执行流程

```python
for 每只股票 in 所有股票:
    # 步骤1: 获取K线数据
    df = fetcher.fetch(stock.code, market, timeframe)
    stock.kline_df = df
    
    # 步骤2: 应用筛选器链（按顺序执行）
    result = filter_chain.apply(stock, context)
    
    # 筛选器链包含（默认顺序）：
    # ① EMABreakoutFilter - 检查 EMA10 是否突破 EMA150 ⭐核心策略
    # ② MarketCapFilter - 检查市值范围
    # ③ AvgDailyVolumeFilter - 检查每日平均交易量
    # ④ PriceFilter - 检查股票价格
    # ⑤ PEFilter - 检查市盈率
    # ⑥ ProfitabilityFilter - 检查公司是否盈利
    
    # 步骤3: 打印详细日志
    print(f"[{i}/{total}] {'✅' if passed else '❌'} {stock.code}")
    for filter_output in result.filter_outputs:
        print(f"  {filter_output.filter_name}: {filter_output.result}")
        print(f"     原因: {filter_output.reason}")
        print(f"     数值: {filter_output.details}")
    
    # 步骤4: 写入数据库
    db.upsert_screening_results([result])
```

---

## 日志输出示例

### EMA 突破成功的股票

```
[1/2733] 📊 HK.00700 - 获取 K 线成功 (YFinance, 252 根)

================================================================================
[1/2733] ✅ HK.00700 - 腾讯控股
================================================================================
市值: 30000.00亿
PE: 25.50
K线数据: 252 根

  ✅ EMABreakoutFilter: pass
     原因: EMA10向上突破EMA150（T-1突破）
     数值: result_type=BREAKOUT_T1, breakout_date=2026-02-12, ema10=355.20, ema150=320.10

  ✅ MarketCapFilter: pass
     原因: 市值 3000000000000.00 在范围内
     数值: market_cap=3000000000000.0

  ✅ AvgDailyVolumeFilter: pass
     原因: 每日平均交易量 50000000 在范围内
     数值: avg_daily_volume=50000000.0

  ✅ PriceFilter: pass
     原因: 股票价格 350.50 在范围内
     数值: price=350.5

  ✅ PEFilter: pass
     原因: PE 25.50 在范围内
     数值: pe=25.5

  ✅ ProfitabilityFilter: pass
     原因: 公司有盈利 (PE=25.50)
     数值: pe=25.5, is_profitable=True

总结: passed=6, failed=0, skipped=0
🎉 满足所有条件！
================================================================================
```

### EMA 突破失败的股票

```
[2/2733] 📊 HK.00001 - 获取 K 线成功 (YFinance, 252 根)

================================================================================
[2/2733] ❌ HK.00001 - 长和
================================================================================
市值: 1500.00亿
PE: 18.50
K线数据: 252 根

  ❌ EMABreakoutFilter: fail
     原因: EMA10未突破EMA150（当前EMA10低于EMA150）
     数值: result_type=NO_BREAKOUT, ema10=45.20, ema150=48.50

  ✅ MarketCapFilter: pass
     原因: 市值在范围内
     数值: market_cap=150000000000.0

  ✅ PriceFilter: pass
     原因: 股票价格在范围内
     数值: price=45.2

总结: passed=2, failed=1, skipped=0
❌ 未通过的筛选器 (1): EMABreakoutFilter

关键失败原因:
  • EMABreakoutFilter: EMA10未突破EMA150（当前EMA10低于EMA150）
================================================================================
```

---

## 筛选器执行顺序

建议将 **EMABreakoutFilter 放在第一个**，因为：
1. 这是核心策略，最重要的筛选条件
2. 如果不满足突破条件，可以快速排除，节省后续计算

当前筛选器顺序：
1. **EMABreakoutFilter** ⭐ - EMA 突破策略（核心）
2. MarketCapFilter - 市值筛选
3. AvgDailyVolumeFilter - 交易量筛选
4. PriceFilter - 价格筛选
5. PEFilter - 市盈率筛选
6. ProfitabilityFilter - 盈利性筛选

---

## EMA 突破策略说明

### 策略逻辑
检查 EMA10 是否向上突破 EMA150：
- **T-1 突破**：前一天 EMA10 < EMA150，今天 EMA10 > EMA150
- **T-2 突破**：前两天 EMA10 < EMA150，前一天 EMA10 > EMA150

### 结果类型
- `BREAKOUT_T1`: T-1 突破（✅ 满足条件）
- `BREAKOUT_T2`: T-2 突破（✅ 满足条件）
- `NO_BREAKOUT`: 未突破（❌ 不满足）
- `INSUFFICIENT_DATA`: 数据不足（⊝ 跳过）
- `INVALID_DATA`: 数据异常（⚠️ 错误）

### 数据要求
- 至少需要 152 根 K 线（150 + 2）
- K 线必须包含 close 价格

---

## 前端界面

### 新增开关
```
☑ 启用 EMA 突破策略（EMA10 突破 EMA150）
```

**默认状态**：勾选（启用）

**功能**：
- 勾选：筛选器链中包含 EMABreakoutFilter
- 不勾选：跳过 EMA 突破检查，只使用其他指标筛选

---

## API 请求示例

### 启用 EMA 突破策略（默认）

```json
POST /api/screen
{
  "market": "HK",
  "timeframe": "1d",
  "use_ema_breakout": true,
  "ema_short": 10,
  "ema_long": 150,
  "market_cap_min": 5000000000,
  "pe_min": 10,
  "pe_max": 100
}
```

### 不使用 EMA 突破策略

```json
POST /api/screen
{
  "market": "HK",
  "timeframe": "1d",
  "use_ema_breakout": false,
  "market_cap_min": 5000000000,
  "pe_min": 10,
  "pe_max": 100
}
```

---

## 验证方法

### 1. 查看命令行日志

启动服务后，在命令行会看到 `EMABreakoutFilter` 的执行结果：

```bash
./start_api.sh

# 日志输出
[1/2733] 📊 HK.00700 - 获取 K 线成功
================================================================================
[1/2733] ✅ HK.00700 - 腾讯控股
================================================================================
  ✅ EMABreakoutFilter: pass
     原因: EMA10向上突破EMA150（T-1突破）
     数值: result_type=BREAKOUT_T1, ema10=355.20, ema150=320.10
...
```

### 2. 检查筛选器链

可以在日志开头看到启用的筛选器列表：

```
开始逐个处理 2733 只股票
流程: 获取K线 → 筛选判断 → 打印日志 → 写入数据库
启用的筛选器: EMABreakoutFilter, MarketCapFilter, PriceFilter, PEFilter, ...
```

---

## 总结

✅ **EMA 突破策略已集成**到筛选流程中  
✅ **默认启用**，作为核心筛选条件  
✅ **前端可控制**，用户可选择是否启用  
✅ **日志完整**，显示突破判断结果和 EMA 数值  
✅ **单循环执行**，逻辑清晰高效  

现在每只股票都会经过完整的判断流程：
1. 获取 K 线
2. 检查 EMA 突破 ⭐
3. 检查市值、交易量、价格、PE 等指标
4. 打印详细日志
5. 写入数据库

重启服务后，您将在命令行看到完整的 EMA 突破判断过程！
