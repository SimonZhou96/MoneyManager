# Stock Pool System Architecture

## Overview

股票池管理系统，支持港股、美股、A股的5类股票池获取、存储和查询。

## System Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     User Interface Layer                     │
├─────────────────────────────────────────────────────────────┤
│  fetch_stock_pools.py  │  query_stock_pools.py  │  Python API│
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│                    Business Logic Layer                      │
├─────────────────────────────────────────────────────────────┤
│                    stock_pool.py                             │
│  ┌──────────────────────────────────────────────────────┐  │
│  │  StockPoolFetcher                                     │  │
│  │  ├─ fetch_best_stocks()        # 最好股票           │  │
│  │  ├─ fetch_index_constituents() # 指数成份股         │  │
│  │  ├─ fetch_industry_leaders()   # 行业龙头           │  │
│  │  ├─ fetch_recent_ipos()        # 新股               │  │
│  │  ├─ fetch_etf_list()           # ETF列表            │  │
│  │  └─ fetch_all_pools()          # 一次性获取所有池   │  │
│  └──────────────────────────────────────────────────────┘  │
│                                                              │
│  StockPoolCriteria (筛选条件数据类)                         │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│                    Data Access Layer                         │
├─────────────────────────────────────────────────────────────┤
│                       db.py                                  │
│  ┌──────────────────────────────────────────────────────┐  │
│  │  MarketDatabase                                       │  │
│  │  ├─ init_stock_pool_schema()   # 初始化表结构       │  │
│  │  ├─ upsert_stock_pool()        # 插入/更新股票池    │  │
│  │  ├─ get_stock_pool()           # 查询股票池         │  │
│  │  ├─ record_pool_update()       # 记录更新历史       │  │
│  │  └─ get_pool_last_update()     # 获取最后更新时间   │  │
│  └──────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│                    External Data Sources                     │
├─────────────────────────────────────────────────────────────┤
│  Futu API                    │  MySQL Database              │
│  ├─ get_stock_basicinfo()    │  ├─ stock_pools             │
│  ├─ get_market_snapshot()    │  └─ stock_pool_updates      │
│  ├─ get_plate_list()         │                              │
│  ├─ get_plate_stock()        │                              │
│  └─ get_ipo_list()           │                              │
└─────────────────────────────────────────────────────────────┘
```

## Core Components

### 1. StockPoolFetcher (stock_pool.py)

**职责**: 从 Futu API 获取各类股票池数据

**方法**:
- `fetch_best_stocks(market, criteria)` - 根据条件筛选优质股票
- `fetch_index_constituents(market, index_codes)` - 获取指数成份股
- `fetch_industry_leaders(market, top_n)` - 获取各行业龙头股
- `fetch_recent_ipos(market, days)` - 获取最近上市新股
- `fetch_etf_list(market)` - 获取ETF列表
- `fetch_all_pools(...)` - 一次性获取所有池

**数据流**:
```
Futu API → StockPoolFetcher → List[dict] → MarketDatabase → MySQL
```

### 2. MarketDatabase (db.py)

**职责**: 数据库操作和持久化

**表结构**:

#### stock_pools 表
```sql
- market: 市场 (HK/US/A)
- pool_type: 池类型 (best/index/industry/ipo/etf)
- code: 股票代码
- name: 股票名称
- market_cap: 市值
- price: 价格
- pe_ratio: 市盈率
- turnover: 成交额
- volume: 成交量
- listing_date: 上市日期
- index_code/index_name: 所属指数
- industry_code/industry_name: 所属行业
- rank_in_industry: 行业内排名
```

#### stock_pool_updates 表
```sql
- market: 市场
- pool_type: 池类型
- update_time: 更新时间
- stock_count: 股票数量
- status: 状态 (success/failed)
- error_msg: 错误信息
```

### 3. CLI Tools

#### fetch_stock_pools.py
- 命令行工具，用于获取和更新股票池
- 支持单个或多个池的批量获取
- 自动保存到数据库并记录更新历史

#### query_stock_pools.py
- 命令行工具，用于查询股票池数据
- 支持格式化输出不同类型的池
- 显示最后更新时间和统计信息

## Data Flow

### 获取流程

```
1. User Command
   ↓
2. fetch_stock_pools.py
   ↓
3. StockPoolFetcher.fetch_xxx()
   ↓
4. Futu API (批量获取)
   ├─ get_stock_basicinfo() → 基本信息
   ├─ get_market_snapshot() → 市场快照 (分批200只)
   ├─ get_plate_list() → 板块列表
   └─ get_plate_stock() → 板块成份股
   ↓
5. Data Processing
   ├─ 应用筛选条件
   ├─ 数据格式化
   └─ 排序和排名
   ↓
6. MarketDatabase.upsert_stock_pool()
   ↓
7. MySQL (持久化)
   ↓
8. MarketDatabase.record_pool_update()
```

### 查询流程

```
1. User Query
   ↓
2. query_stock_pools.py
   ↓
3. MarketDatabase.get_stock_pool()
   ↓
4. MySQL Query
   ↓
5. Format & Display
```

## Design Patterns

### 1. Factory Pattern
- `StockPoolFetcher` 根据 pool_type 调用不同的 fetch 方法

### 2. Strategy Pattern
- `StockPoolCriteria` 封装不同的筛选策略

### 3. Repository Pattern
- `MarketDatabase` 封装所有数据库操作

### 4. Batch Processing
- 市场快照数据分批获取（每批200只），避免API限制

## Key Design Decisions

### 1. 为什么使用 Futu API 而不是 AKShare？

**Futu API 优势**:
- ✅ 提供实时市场快照（市值、PE、价格、成交量）
- ✅ 支持板块和行业分类
- ✅ 提供指数成份股查询
- ✅ 数据质量高，更新及时
- ✅ 支持批量查询

**AKShare 限制**:
- ❌ 港股指数成份股接口不完善
- ❌ 行业分类主要针对A股
- ❌ 缺少港股IPO接口
- ❌ 数据更新可能有延迟

### 2. 为什么分5个独立的池？

**优势**:
- 每个池有独立的更新频率和筛选逻辑
- 用户可以按需获取特定类型的池
- 便于扩展和维护
- 数据库查询更高效（通过 pool_type 索引）

### 3. 为什么使用 UPSERT 而不是 DELETE + INSERT？

**优势**:
- 保留历史数据的 created_at 时间戳
- 避免主键冲突
- 性能更好（单次操作）
- 支持增量更新

### 4. 为什么批量获取市场快照？

**原因**:
- Futu API 限制：每30秒最多10次请求
- 单次请求可以获取多只股票的快照
- 批量大小200是经验值（平衡性能和API限制）

## Performance Considerations

### 1. 数据库索引

```sql
-- 主要索引
UNIQUE KEY uk_pool_market_type_code (market, pool_type, code)
KEY idx_pool_market (market)
KEY idx_pool_type (pool_type)
KEY idx_pool_market_cap (market_cap)
KEY idx_pool_industry (industry_code)
KEY idx_pool_index (index_code)
```

### 2. 批量操作

```python
# 使用 executemany 而不是循环 execute
cursor.executemany(sql, rows)
```

### 3. 连接复用

```python
# 在整个获取过程中复用 Futu 连接
quote_ctx = ft.OpenQuoteContext(...)
try:
    # 多次调用 API
finally:
    quote_ctx.close()
```

## Extensibility

### 添加新的股票池类型

1. 在 `StockPoolFetcher` 中添加新方法：
```python
def fetch_xxx_pool(self, market: str, ...) -> List[dict]:
    # 实现逻辑
    pass
```

2. 在 `fetch_stock_pools.py` 中添加处理逻辑：
```python
if "xxx" in pools:
    fetch_and_save_xxx_pool(fetcher, db, market)
```

3. 在 `query_stock_pools.py` 中添加显示逻辑：
```python
def display_xxx_pool(stocks):
    # 格式化输出
    pass
```

### 支持新市场

1. 确认 Futu API 支持该市场
2. 调整 `market_map` 映射
3. 根据市场特点调整筛选标准
4. 更新指数代码列表

## Error Handling Strategy

### 1. API 调用失败
```python
try:
    ret, data = quote_ctx.get_xxx()
    if ret != ft.RET_OK:
        return []  # 返回空列表，不中断流程
except Exception as e:
    db.record_pool_update(market, pool_type, 0, "failed", str(e))
    raise  # 记录后重新抛出
```

### 2. 数据库操作失败
```python
try:
    db.upsert_stock_pool(...)
except Exception as e:
    logger.error(f"Database error: {e}")
    # 不影响其他池的更新
```

### 3. 部分数据缺失
```python
# 使用 .get() 和默认值
market_cap = row.get("market_cap", None)
pe_ratio = row.get("pe_ratio", None)
```

## Testing Strategy

### 1. 单元测试
- 测试每个 fetch 方法的返回格式
- 测试筛选条件的应用
- 测试数据库操作

### 2. 集成测试
- 测试完整的获取-保存-查询流程
- 测试 Futu API 连接
- 测试数据库连接

### 3. 端到端测试
```bash
# 测试完整流程
python3 fetch_stock_pools.py --market HK --pools etf
python3 query_stock_pools.py --market HK --pool etf
```

## Monitoring & Logging

### 1. 更新记录
- 每次更新都记录到 `stock_pool_updates` 表
- 包含时间、数量、状态、错误信息

### 2. 查询最后更新
```python
last_update = db.get_pool_last_update("HK", "best")
# 检查更新时间和状态
```

### 3. 日志输出
```python
print(f"获取到 {len(stocks)} 只股票")
print(f"已保存到数据库")
```

## Future Enhancements

### 1. 增量更新
- 只更新变化的股票
- 减少API调用次数

### 2. 缓存机制
- 内存缓存热门查询
- Redis 缓存跨进程共享

### 3. 异步处理
- 使用异步IO提高并发
- 后台任务队列

### 4. 数据验证
- 检查数据完整性
- 异常值检测和告警

### 5. 历史追踪
- 保留股票池的历史快照
- 分析池的变化趋势

## Dependencies

```
futu-api>=6.0.0      # Futu OpenD API
pymysql>=1.0.0       # MySQL driver
pandas>=1.5.0        # Data processing
yfinance>=0.2.0      # Backup data source (optional)
```

## Configuration

### Environment Variables
```bash
MYSQL_HOST=127.0.0.1
MYSQL_PORT=3306
MYSQL_USER=root
MYSQL_PASSWORD=123456
MYSQL_DATABASE=market_data
```

### Futu OpenD
```bash
# Default connection
host=127.0.0.1
port=11111
```

## Deployment

### Production Checklist
- [ ] 配置数据库连接池
- [ ] 设置 API 重试机制
- [ ] 配置日志轮转
- [ ] 设置监控告警
- [ ] 配置定时任务（cron）
- [ ] 备份数据库
- [ ] 限制 CORS 来源

### Cron Job Example
```bash
# 每天收盘后更新港股池
0 17 * * 1-5 cd /path/to/stock_screener && \
  MYSQL_PASSWORD=123456 python3 fetch_stock_pools.py \
  --market HK --pools all >> /var/log/stock_pools.log 2>&1
```

## Troubleshooting Guide

### 问题: 获取数据为空
**排查步骤**:
1. 检查 Futu OpenD 是否运行
2. 检查账户是否有行情权限
3. 检查网络连接
4. 查看 stock_pool_updates 表的错误信息

### 问题: 数据库连接失败
**排查步骤**:
1. 检查 MySQL 服务状态
2. 验证密码是否正确
3. 检查数据库是否存在
4. 检查防火墙设置

### 问题: API 调用超限
**解决方案**:
1. 增加批次间的延迟
2. 减少批量大小
3. 在非交易时段更新

## References

- [Futu OpenD API 文档](https://openapi.futunn.com/futu-api-doc/)
- [股票池系统 README](./STOCK_POOL_README.md)
- [MoneyManager 项目文档](../README.md)
