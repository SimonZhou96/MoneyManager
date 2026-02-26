# Futu API 调用参考 - 股票池系统

## 概述

本文档总结了股票池系统中使用的所有 Futu API 调用方法、参数和返回值。

## 连接管理

### 创建连接

```python
import futu as ft

quote_ctx = ft.OpenQuoteContext(host='127.0.0.1', port=11111)
```

### 关闭连接

```python
quote_ctx.close()
```

## 核心 API 调用

### 1. get_stock_basicinfo() - 获取股票基本信息

**用途**: 获取市场中所有股票的基本信息

**调用示例**:
```python
ret, data = quote_ctx.get_stock_basicinfo(
    market=ft.Market.HK,
    stock_type=ft.SecurityType.STOCK  # 或 ft.SecurityType.ETF
)
```

**参数**:
- `market`: 市场枚举
  - `ft.Market.HK` - 港股
  - `ft.Market.US` - 美股
  - `ft.Market.SH` - 上交所（A股）
  - `ft.Market.SZ` - 深交所（A股）
- `stock_type`: 证券类型
  - `ft.SecurityType.STOCK` - 股票
  - `ft.SecurityType.ETF` - ETF

**返回值**:
```python
# ret: ft.RET_OK 或 ft.RET_ERROR
# data: DataFrame
columns = [
    'code',              # 股票代码 (e.g., "HK.00700")
    'name',              # 股票名称
    'lot_size',          # 每手股数
    'stock_type',        # 股票类型
    'stock_child_type',  # 子类型
    'stock_owner',       # 所属市场
    'option_type',       # 期权类型
    'strike_time',       # 行权时间
    'strike_price',      # 行权价
    'suspension',        # 是否停牌
    'listing_date',      # 上市日期 (e.g., "2004-06-16")
    'stock_id',          # 股票ID
    'delisting',         # 是否退市
    'index_option_type', # 指数期权类型
    'main_contract',     # 主力合约
    'last_trade_time',   # 最后交易时间
    'exchange_type'      # 交易所类型
]
```

**使用场景**:
- 获取所有股票列表
- 筛选新股（通过 listing_date）
- 获取ETF列表

**示例**:
```python
# 获取港股列表
ret, stocks = quote_ctx.get_stock_basicinfo(ft.Market.HK, ft.SecurityType.STOCK)
if ret == ft.RET_OK:
    print(f"港股总数: {len(stocks)}")
    print(stocks[['code', 'name', 'listing_date']].head())

# 获取ETF列表
ret, etfs = quote_ctx.get_stock_basicinfo(ft.Market.HK, ft.SecurityType.ETF)
if ret == ft.RET_OK:
    print(f"ETF总数: {len(etfs)}")
```

---

### 2. get_market_snapshot() - 获取市场快照

**用途**: 获取股票的实时市场数据（市值、价格、PE等）

**调用示例**:
```python
codes = ['HK.00700', 'HK.09988', 'HK.00005']
ret, data = quote_ctx.get_market_snapshot(codes)
```

**参数**:
- `code_list`: 股票代码列表（建议每批不超过200只）

**返回值**:
```python
# 142个字段，主要包括：
columns = [
    'code',                  # 股票代码
    'name',                  # 股票名称
    'last_price',            # 最新价
    'open_price',            # 开盘价
    'high_price',            # 最高价
    'low_price',             # 最低价
    'prev_close_price',      # 昨收价
    'volume',                # 成交量
    'turnover',              # 成交额
    'turnover_rate',         # 换手率
    'total_market_val',      # 总市值 ⭐
    'circular_market_val',   # 流通市值
    'pe_ratio',              # 市盈率（静态）
    'pe_ttm_ratio',          # 市盈率（TTM）⭐
    'pb_ratio',              # 市净率
    'dividend_ttm',          # 股息（TTM）
    'dividend_ratio_ttm',    # 股息率（TTM）
    'issued_shares',         # 已发行股数
    'outstanding_shares',    # 流通股数
    'net_asset',             # 净资产
    'net_profit',            # 净利润
    'earning_per_share',     # 每股收益
    'net_asset_per_share',   # 每股净资产
    'listing_date',          # 上市日期
    'lot_size',              # 每手股数
    # ... 更多字段
]
```

**批量获取示例**:
```python
# 分批获取，避免API限制
codes = basic_info["code"].tolist()
batch_size = 200
all_snapshots = []

for i in range(0, len(codes), batch_size):
    batch_codes = codes[i:i + batch_size]
    ret, snapshot = quote_ctx.get_market_snapshot(batch_codes)
    if ret == ft.RET_OK:
        all_snapshots.append(snapshot)

df = pd.concat(all_snapshots, ignore_index=True)
```

**使用场景**:
- 获取市值、PE、价格等实时数据
- 筛选优质股票
- 计算平均成交额

---

### 3. get_plate_list() - 获取板块列表

**用途**: 获取市场中的行业板块、概念板块等

**调用示例**:
```python
ret, data = quote_ctx.get_plate_list(
    market=ft.Market.HK,
    plate_type=ft.Plate.INDUSTRY
)
```

**参数**:
- `market`: 市场枚举
- `plate_type`: 板块类型
  - `ft.Plate.ALL` - 所有板块
  - `ft.Plate.INDUSTRY` - 行业板块 ⭐
  - `ft.Plate.CONCEPT` - 概念板块
  - `ft.Plate.REGION` - 地域板块
  - `ft.Plate.OTHER` - 其他板块

**返回值**:
```python
columns = [
    'code',        # 板块代码 (e.g., "HK.LIST1001")
    'plate_name',  # 板块名称 (e.g., "Dairy")
    'plate_id'     # 板块ID
]
```

**示例**:
```python
# 获取港股行业板块
ret, industries = quote_ctx.get_plate_list(ft.Market.HK, ft.Plate.INDUSTRY)
if ret == ft.RET_OK:
    print(f"行业板块总数: {len(industries)}")
    print(industries.head(10))
```

**使用场景**:
- 获取所有行业分类
- 遍历行业获取龙头股

---

### 4. get_plate_stock() - 获取板块成份股

**用途**: 获取指定板块或指数的成份股

**调用示例**:
```python
ret, data = quote_ctx.get_plate_stock(plate_code='HK.800000')
```

**参数**:
- `plate_code`: 板块代码或指数代码
  - 行业板块: `HK.LIST1001`, `HK.LIST1002`, ...
  - 指数: `HK.800000` (恒生指数), `HK.800700` (恒生科技), `HK.800100` (国企指数)

**返回值**:
```python
columns = [
    'code',        # 股票代码
    'stock_name',  # 股票名称（可能为空）
    'plate_name'   # 板块名称（可能为空）
]
```

**示例**:
```python
# 获取恒生指数成份股
ret, stocks = quote_ctx.get_plate_stock('HK.800000')
if ret == ft.RET_OK:
    print(f"恒生指数成份股: {len(stocks)} 只")
    print(stocks[['code', 'stock_name']].head())

# 获取某行业的所有股票
ret, stocks = quote_ctx.get_plate_stock('HK.LIST1003')  # Insurance
if ret == ft.RET_OK:
    print(f"保险行业股票: {len(stocks)} 只")
```

**使用场景**:
- 获取指数成份股
- 获取行业内所有股票
- 行业龙头股筛选

---

### 5. get_ipo_list() - 获取IPO列表

**用途**: 获取即将上市或最近上市的新股信息

**调用示例**:
```python
ret, data = quote_ctx.get_ipo_list(market=ft.Market.HK)
```

**参数**:
- `market`: 市场枚举

**返回值**:
```python
columns = [
    'code',                      # 股票代码
    'name',                      # 股票名称
    'list_time',                 # 上市时间
    'list_timestamp',            # 上市时间戳
    'apply_code',                # 申购代码
    'issue_size',                # 发行量
    'online_issue_size',         # 网上发行量
    'apply_upper_limit',         # 申购上限
    'apply_limit_market_value',  # 申购限额市值
    'is_estimate_ipo_price',     # 是否预估发行价
    'ipo_price',                 # 发行价
    'industry_pe_rate',          # 行业市盈率
    'is_estimate_winning_ratio', # 是否预估中签率
    'winning_ratio',             # 中签率
    'issue_pe_rate',             # 发行市盈率
    'apply_time',                # 申购时间
    'apply_timestamp',           # 申购时间戳
    'winning_time',              # 公布中签时间
    'winning_timestamp',         # 中签时间戳
    'is_has_won',                # 是否已中签
    'winning_num_data',          # 中签数据
    'ipo_price_min',             # 发行价下限
    'ipo_price_max',             # 发行价上限
    'list_price',                # 上市价
    'lot_size',                  # 每手股数
    'entrance_price',            # 入场费
    'is_subscribe_status',       # 是否可申购
    'apply_end_time',            # 申购截止时间
    'apply_end_timestamp'        # 申购截止时间戳
]
```

**示例**:
```python
ret, ipos = quote_ctx.get_ipo_list(ft.Market.HK)
if ret == ft.RET_OK:
    print(f"IPO总数: {len(ipos)}")
    print(ipos[['code', 'name', 'list_time', 'ipo_price']].head())
```

**使用场景**:
- 获取即将上市的新股
- 监控IPO市场
- 新股申购提醒

**注意**: 此API返回的是即将上市或正在申购的新股，不是历史所有新股。历史新股需要通过 `get_stock_basicinfo()` 的 `listing_date` 字段筛选。

---

## 枚举类型参考

### Market (市场)
```python
ft.Market.HK        # 港股
ft.Market.US        # 美股
ft.Market.SH        # 上交所
ft.Market.SZ        # 深交所
ft.Market.AU        # 澳股
ft.Market.CA        # 加拿大
ft.Market.JP        # 日本
ft.Market.SG        # 新加坡
```

### SecurityType (证券类型)
```python
ft.SecurityType.STOCK       # 股票
ft.SecurityType.ETF         # ETF
ft.SecurityType.WARRANT     # 窝轮
ft.SecurityType.BOND        # 债券
ft.SecurityType.INDEX       # 指数
```

### Plate (板块类型)
```python
ft.Plate.ALL        # 所有板块
ft.Plate.INDUSTRY   # 行业板块
ft.Plate.CONCEPT    # 概念板块
ft.Plate.REGION     # 地域板块
ft.Plate.OTHER      # 其他板块
```

### StockField (股票字段)
```python
ft.StockField.MARKET_VAL        # 市值
ft.StockField.PE_TTM            # 市盈率（TTM）
ft.StockField.PE_ANNUAL         # 市盈率（年度）
ft.StockField.CUR_PRICE         # 当前价格
ft.StockField.VOLUME            # 成交量
ft.StockField.TURNOVER          # 成交额
# ... 更多字段
```

---

## API 限制和最佳实践

### 1. 频率限制
- **限制**: 每30秒最多10次请求
- **建议**:
  - 批量获取数据（如 get_market_snapshot 一次获取200只）
  - 在非交易时段更新数据
  - 添加请求间隔

### 2. 批量大小
- **get_market_snapshot**: 建议每批不超过200只股票
- **get_plate_stock**: 单次调用即可获取所有成份股

### 3. 错误处理
```python
ret, data = quote_ctx.get_xxx()
if ret != ft.RET_OK:
    print(f"Error: {data}")
    return []
```

### 4. 连接管理
```python
# 使用 try-finally 确保连接关闭
quote_ctx = ft.OpenQuoteContext(host='127.0.0.1', port=11111)
try:
    # 执行多次API调用
    ret1, data1 = quote_ctx.get_xxx()
    ret2, data2 = quote_ctx.get_yyy()
finally:
    quote_ctx.close()
```

### 5. 数据验证
```python
# 检查返回值
if ret == ft.RET_OK and data is not None and not data.empty:
    # 处理数据
    pass
```

---

## 常见问题

### Q1: 如何获取A股数据？
A: A股需要分别使用 `ft.Market.SH` (上交所) 和 `ft.Market.SZ` (深交所)，或者使用 `ft.Market.SH` 作为代表（Futu会返回两个交易所的股票）。

### Q2: 为什么 get_plate_stock 返回空数据？
A: 检查板块代码是否正确。港股指数代码格式为 `HK.800000`，行业板块为 `HK.LIST1001`。

### Q3: 如何获取历史新股？
A: 使用 `get_stock_basicinfo()` 获取所有股票，然后根据 `listing_date` 字段筛选。

### Q4: 市场快照数据是实时的吗？
A: 是的，但需要账户有相应市场的实时行情权限。

### Q5: 如何处理API超限？
A:
- 减少请求频率
- 增加批量大小
- 在非交易时段更新
- 添加重试机制

---

## 完整示例

### 获取港股最好股票

```python
import futu as ft
import pandas as pd

# 连接
quote_ctx = ft.OpenQuoteContext(host='127.0.0.1', port=11111)

try:
    # 1. 获取所有港股
    ret, basic_info = quote_ctx.get_stock_basicinfo(
        market=ft.Market.HK,
        stock_type=ft.SecurityType.STOCK
    )
    if ret != ft.RET_OK:
        print(f"Error: {basic_info}")
        exit(1)

    print(f"港股总数: {len(basic_info)}")

    # 2. 批量获取市场快照
    codes = basic_info["code"].tolist()
    batch_size = 200
    all_snapshots = []

    for i in range(0, len(codes), batch_size):
        batch_codes = codes[i:i + batch_size]
        ret, snapshot = quote_ctx.get_market_snapshot(batch_codes)
        if ret == ft.RET_OK:
            all_snapshots.append(snapshot)
        print(f"已获取 {i + len(batch_codes)}/{len(codes)}")

    df = pd.concat(all_snapshots, ignore_index=True)

    # 3. 应用筛选条件
    filtered = df[
        (df["total_market_val"] >= 5_000_000_000) &  # 市值 >= 50亿
        (df["last_price"] >= 5.0) &                   # 价格 >= 5
        (df["pe_ttm_ratio"] >= 5.0) &                 # PE >= 5
        (df["turnover"] >= 20_000_000)                # 成交额 >= 2000万
    ]

    # 4. 按市值排序
    filtered = filtered.sort_values("total_market_val", ascending=False)

    print(f"\n筛选出 {len(filtered)} 只优质股票")
    print(filtered[['code', 'name', 'total_market_val', 'last_price', 'pe_ttm_ratio']].head(10))

finally:
    quote_ctx.close()
```

### 获取恒生指数成份股

```python
import futu as ft

quote_ctx = ft.OpenQuoteContext(host='127.0.0.1', port=11111)

try:
    # 获取恒生指数成份股
    ret, stocks = quote_ctx.get_plate_stock('HK.800000')
    if ret == ft.RET_OK:
        print(f"恒生指数成份股: {len(stocks)} 只")

        # 获取这些股票的市场快照
        codes = stocks['code'].tolist()
        ret, snapshot = quote_ctx.get_market_snapshot(codes)
        if ret == ft.RET_OK:
            # 按市值排序
            snapshot = snapshot.sort_values('total_market_val', ascending=False)
            print("\n市值前10名:")
            print(snapshot[['code', 'name', 'total_market_val']].head(10))

finally:
    quote_ctx.close()
```

---

## 参考资料

- [Futu OpenD API 官方文档](https://openapi.futunn.com/futu-api-doc/)
- [股票池系统架构](./STOCK_POOL_ARCHITECTURE.md)
- [股票池使用指南](./STOCK_POOL_README.md)
