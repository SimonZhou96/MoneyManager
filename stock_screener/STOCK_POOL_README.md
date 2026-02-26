# 股票池管理系统

## 功能概述

支持港股、美股、A股的股票池获取和管理，包含5个子池：

1. **最好股票** (best) - 基于市值、价格、PE、成交量筛选的优质股票
2. **指数成份股** (index) - 主要指数的成份股
3. **行业龙头股** (industry) - 各行业前5名股票
4. **新股** (ipo) - 最近两年上市的股票
5. **ETF列表** (etf) - 所有ETF基金

## 数据来源

- **Futu API**: 提供实时市场数据、指数成份股、行业分类、IPO信息、ETF列表
- **yFinance**: 提供个股详细信息（备用）

## 快速开始

### 1. 环境准备

确保已安装依赖：
```bash
pip install futu-api yfinance pymysql pandas
```

确保 Futu OpenD 已启动：
```bash
# 默认端口 11111
```

设置数据库密码：
```bash
export MYSQL_PASSWORD=123456
```

### 2. 获取股票池数据

#### 获取所有股票池（港股）
```bash
python3 fetch_stock_pools.py --market HK --pools all
```

#### 获取特定股票池
```bash
# 只获取最好股票
python3 fetch_stock_pools.py --market HK --pools best

# 获取指数成份股和ETF
python3 fetch_stock_pools.py --market HK --pools index,etf

# 获取行业龙头股
python3 fetch_stock_pools.py --market HK --pools industry
```

#### 参数说明
- `--market`: 市场代码 (HK/US/A)，默认 HK
- `--pools`: 股票池类型，可选 all/best/index/industry/ipo/etf，多个用逗号分隔
- `--host`: Futu OpenD 主机，默认 127.0.0.1
- `--port`: Futu OpenD 端口，默认 11111

### 3. 查询股票池数据

#### 查询最好股票（前20只）
```bash
python3 query_stock_pools.py --market HK --pool best --limit 20
```

#### 查询指数成份股
```bash
python3 query_stock_pools.py --market HK --pool index
```

#### 查询行业龙头股
```bash
python3 query_stock_pools.py --market HK --pool industry
```

#### 查询新股
```bash
python3 query_stock_pools.py --market HK --pool ipo --limit 50
```

#### 查询ETF列表
```bash
python3 query_stock_pools.py --market HK --pool etf
```

## 港股筛选标准

### 最好股票 (best)
- 市值 ≥ 50亿港元
- 现价 ≥ 5港元
- 市盈率 ≥ 5倍
- 10天平均成交额 ≥ 2000万港元

### 三大指数 (index)
- HK.800000 - 恒生指数 (88只成份股)
- HK.800700 - 恒生科技指数 (30只成份股)
- HK.800100 - 恒生中国企业指数 (50只成份股)

### 行业龙头股 (industry)
- 按市值排序，每个行业取前5名
- 覆盖所有 Futu 定义的行业分类

### 新股 (ipo)
- 上市时间 ≤ 730天（约2年）
- 包含上市日期和上市天数信息

### ETF列表 (etf)
- 所有在港交所上市的ETF和REIT

## 数据库表结构

### stock_pools 表
存储所有股票池数据：
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

### stock_pool_updates 表
记录股票池更新历史：
- market: 市场
- pool_type: 池类型
- update_time: 更新时间
- stock_count: 股票数量
- status: 状态 (success/failed)

## 代码示例

### Python 代码调用

```python
from stock_pool import StockPoolFetcher, StockPoolCriteria
from db import MarketDatabase, MySqlConfig
import futu as ft

# 连接 Futu
quote_ctx = ft.OpenQuoteContext(host='127.0.0.1', port=11111)

# 连接数据库
db_config = MySqlConfig(
    host="127.0.0.1",
    port=3306,
    user="root",
    password="123456",
    database="market_data"
)
db = MarketDatabase(db_config)
db.init_stock_pool_schema()

# 创建获取器
fetcher = StockPoolFetcher(quote_ctx=quote_ctx, db=db)

# 获取最好股票
criteria = StockPoolCriteria(
    market_cap_min=5_000_000_000,
    price_min=5.0,
    pe_min=5.0,
    avg_volume_min=20_000_000
)
best_stocks = fetcher.fetch_best_stocks("HK", criteria)

# 保存到数据库
db.upsert_stock_pool("HK", "best", best_stocks)
db.record_pool_update("HK", "best", len(best_stocks), "success")

# 查询数据
stocks = db.get_stock_pool("HK", "best", limit=20)
for stock in stocks:
    print(f"{stock['code']} {stock['name']} - 市值: {stock['market_cap']/1e8:.2f}亿")

# 清理
quote_ctx.close()
db.close()
```

## 扩展到美股和A股

### 美股
```bash
# 获取美股股票池
python3 fetch_stock_pools.py --market US --pools all

# 查询美股最好股票
python3 query_stock_pools.py --market US --pool best --limit 20
```

### A股
```bash
# 获取A股股票池
python3 fetch_stock_pools.py --market A --pools all

# 查询A股行业龙头
python3 query_stock_pools.py --market A --pool industry
```

**注意**: 美股和A股的筛选标准可能需要根据市场特点调整。

## 定时更新

建议使用 cron 定时更新股票池数据：

```bash
# 每天收盘后更新港股股票池
0 17 * * 1-5 cd /path/to/stock_screener && MYSQL_PASSWORD=123456 python3 fetch_stock_pools.py --market HK --pools all >> /var/log/stock_pools.log 2>&1
```

## 性能优化

1. **批量获取**: 市场快照数据分批获取（每批200只），避免API限制
2. **数据库索引**: 已在 market, pool_type, code 等字段建立索引
3. **增量更新**: 使用 UPSERT 语句，避免重复数据
4. **缓存机制**: 可在应用层添加缓存，减少数据库查询

## 注意事项

1. **Futu API 限制**:
   - 每30秒最多10次请求
   - 建议在非交易时段批量更新

2. **数据准确性**:
   - 市场快照数据为实时数据
   - 建议在收盘后更新以获取准确的日终数据

3. **A股市场**:
   - Futu API 对A股使用 Market.SH (上交所) 作为代表
   - 实际包含上交所和深交所的股票

## 故障排查

### 连接 Futu OpenD 失败
```bash
# 检查 OpenD 是否运行
ps aux | grep FutuOpenD

# 检查端口
netstat -an | grep 11111
```

### 数据库连接失败
```bash
# 检查 MySQL 服务
mysql -u root -p123456 -e "SELECT 1"

# 检查数据库是否存在
mysql -u root -p123456 -e "SHOW DATABASES LIKE 'market_data'"
```

### 获取数据为空
- 检查 Futu 账户是否有相应市场的行情权限
- 检查网络连接是否正常
- 查看错误日志

## 更新日志

### 2026-02-26
- 初始版本发布
- 支持港股5个股票池的获取和查询
- 完成数据库表结构设计
- 添加命令行工具

## 许可证

MIT License
