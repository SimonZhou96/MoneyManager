# A 股 / 港股 / 美股 API 调用顺序与逻辑梳理

## 一、数据获取模块概览

| 模块 | 作用 | 涉及文件 |
|------|------|----------|
| **股票列表** | 获取全量股票 code/name + 市值/PE | universe.py |
| **K 线** | 获取历史/实时 K 线 | kline_fetcher.py |
| **板块信息** | 获取股票所属行业/板块 | sector_fetcher.py |
| **自选股** | 获取用户自选股列表 | watchlist_fetcher.py |

---

## 二、股票列表（Universe）—— 市值/PE 问题集中点

### 2.1 当前调用顺序

| 市场 | 第 1 优先 | 第 2 兜底 | 数据来源 |
|------|-----------|-----------|----------|
| **A 股** | `stock_zh_a_spot_em` | 无 | 东方财富 |
| **港股** | `stock_hk_spot` | `stock_hk_spot_em` | 新浪 → 东方财富 |
| **美股** | `stock_us_spot` | `stock_us_spot_em` | 新浪 → 东方财富 |

**代码位置**：`universe.py` 第 60–96 行。

### 2.2 各接口返回的市值/PE

| 接口 | 市值 | PE | 备注 |
|------|-----|----|------|
| `stock_zh_a_spot_em` | ✓ 总市值、流通市值 | ✓ 市盈率-动态 | A 股正常 |
| `stock_hk_spot` | ✗ | ✗ | 新浪，无基本面 |
| `stock_hk_spot_em` | ✗ | ✓ 市盈率-动态 | 东方财富，无市值 |
| `stock_us_spot` | ✗ | ✗ | 新浪，无基本面 |
| `stock_us_spot_em` | ✓ 总市值 | ✓ 市盈率 | 东方财富 |
| `fetch_stock_list_futu` | ✗ | ✗ | get_stock_basicinfo 仅有 code/name |

### 2.3 问题根因

- **港股**：优先用 `stock_hk_spot`，有数据时不再调用 `stock_hk_spot_em`；两接口都**无市值**。
- **美股**：优先用 `stock_us_spot`，有数据时不再调用 `stock_us_spot_em`；后者才有市值和 PE。
- **Futu 兜底**：`get_stock_basicinfo` 不返回市值/PE，仅返回 code/name。

---

## 三、K 线（Kline）获取

### 3.1 调用顺序（全局统一）

**优先级**：`YFinance` → `AKShare` → `Futu`（需 OpenD）

**代码位置**：`kline_fetcher.py` 第 431–464 行。

### 3.2 各市场支持情况

| 数据源 | A 股 | 港股 | 美股 | 日线 | 分钟线 |
|--------|------|------|------|------|--------|
| **YFinance** | ✓ | ✓ | ✓ | ✓ | ✓（有限回溯） |
| **AKShare** | ✓ | ✓ | ✓ | ✓ | 仅 A 股 1/5/15/30/60 分钟 |
| **Futu** | ✓ | ✓ | ✓ | ✓ | ✓ |

### 3.3 各市场具体接口

| 市场 | AKShare 日线接口顺序 | 备注 |
|------|----------------------|------|
| A 股 | `stock_zh_a_hist` | 日线；分钟线用 `stock_zh_a_hist_min_em` |
| 港股 | `stock_hk_daily` → `stock_hk_hist` | 二选一成功即可 |
| 美股 | `stock_us_daily` → `stock_us_hist` | 二选一成功即可 |

---

## 四、板块信息（Sector）

### 4.1 调用顺序

**优先级**：`AKShare` → `Futu`（若有 quote_ctx）

**代码位置**：`sector_fetcher.py` 第 579–601 行。

### 4.2 各市场数据来源

| 市场 | AKShare 接口 | Futu 接口 |
|------|-------------|-----------|
| A 股 | `stock_zh_a_spot_em` 行业列 | `get_plate_stock` 等 |
| 港股 | `stock_hk_industry_spot_em` 等 | `get_plate_stock` |
| 美股 | `stock_us_spot_em` 行业列 | `get_plate_stock` |

---

## 五、自选股（Watchlist）

### 5.1 调用顺序

**优先级**：`Futu get_user_security` → DB 缓存

**代码位置**：`watchlist_fetcher.py`。

---

## 六、daily_job / screen_service 中的使用

### 6.1 股票列表来源

```
DB 为空 → fetch_stock_list_akshare(market)
        → 失败且 Futu 可用 → fetch_stock_list_futu(quote_ctx, market)
        → upsert_stocks 写入 DB
否则 → db.get_stocks(market, include_fundamentals=True)
```

**代码位置**：`daily_job.py` 第 243–254 行；`api/screen_service.py` 第 171–193 行。

### 6.2 K 线获取

```
for fetcher in [YFinance, AKShare, Futu]:
    df = fetcher.fetch(code, market, timeframe)
    if df 有效: break
```

---

## 七、修改建议

### 7.1 股票列表：解决港股/美股市值、PE 缺失

#### 方案 A：调整 AKShare 调用顺序（推荐，改动小）

**思路**：需要市值/PE 时，港股、美股优先用 `*_spot_em`。

| 市场 | 调整前 | 调整后 |
|------|--------|--------|
| 港股 | stock_hk_spot → stock_hk_spot_em | **stock_hk_spot_em 优先**（或仅用） |
| 美股 | stock_us_spot → stock_us_spot_em | **stock_us_spot_em 优先**（或仅用） |

- **港股**：市值仍不可得，但 PE 会正常。
- **美股**：市值、PE 均可正常获取。

**修改文件**：`universe.py`，调整 `fetch_stock_list_akshare` 中港股、美股的接口顺序或仅保留 `*_spot_em`。

---

#### 方案 B：引入 yfinance 补齐市值/PE

**思路**：AKShare 返回列表后，对缺失 `market_cap`/`pe_ratio` 的股票，用 yfinance 逐个补全。

- `Ticker(symbol).info` 包含 `marketCap`、`trailingPE`。
- 港股格式：`0700.HK`；美股格式：`AAPL`。

**实现要点**：

1. 在 `universe.py` 中增加 `_enrich_with_yfinance(stocks, market)`。
2. 对 `market_cap` 或 `pe_ratio` 为 None 的股票，调用 yfinance 补全。
3. 建议加限速（如 `time.sleep(0.2)`），避免请求过多。
4. 可设置 `enrich_fundamentals: bool` 参数，按需开启。

**优点**：港股、美股均可补齐市值和 PE。  
**缺点**：逐只请求，全量补全较慢，需考虑并发或批量策略。

---

#### 方案 C：Futu 使用 get_market_snapshot 补充市值/PE

**思路**：在 Futu 兜底时，对返回的股票列表再调 `get_market_snapshot` 获取市值、PE 等。

- 需查 Futu 文档确认 `get_market_snapshot` 是否返回市值、PE。
- 一次可查多只，比 yfinance 逐只补全效率更高。

**修改文件**：`universe.py` 中 `fetch_stock_list_futu`，在返回前用 snapshot 批量补全。

---

### 7.2 股票列表：统一“是否需要市值/PE”的策略

**建议**：增加参数，让调用方显式控制“是否需要市值/PE”。

```python
def fetch_stock_list_akshare(market: str, require_fundamentals: bool = True) -> List[dict]:
    # require_fundamentals=True 时，港股/美股优先用 *_spot_em
    # require_fundamentals=False 时，保持原顺序（优先 stock_*_spot 获取更快）
```

这样可在“全量列表、无基本面”与“有基本面、用于筛选”两种场景下灵活选择。

---

### 7.3 K 线：当前逻辑说明

当前顺序 **YFinance → AKShare → Futu** 是合理的：

- YFinance 支持全 market、多 timeframe，且免费。
- AKShare 做 A 股分钟线、日线补充。
- Futu 作为有 OpenD 环境时的备用。

**建议**：保持现状，仅在有明确需求（如国内环境 YFinance 不稳定）时再调整优先级或增加可选配置。

---

### 7.4 板块信息

当前 AKShare → Futu 的顺序合理。若有 Futu 且 AKShare 板块数据不全，可考虑在 `SectorFetcherFactory` 中支持“按市场配置优先级”（例如港股/美股优先 Futu）。

---

## 八、修改优先级建议

| 优先级 | 修改项 | 预期效果 | 工作量 |
|--------|--------|----------|--------|
| P0 | 美股：优先使用 `stock_us_spot_em` | 美股有市值、PE | 小 |
| P1 | 港股：优先使用 `stock_hk_spot_em` | 港股有 PE（市值仍缺） | 小 |
| P2 | 引入 yfinance 补全港股/美股市值、PE | 港股也有市值、PE | 中 |
| P3 | `fetch_stock_list_futu` 用 snapshot 补全 | Futu 兜底时也有基本面 | 中 |

---

## 九、相关代码速查

| 功能 | 文件 | 关键函数/行号 |
|------|------|----------------|
| 股票列表 + 市值/PE | universe.py | fetch_stock_list_akshare 60–136, fetch_stock_list_futu 139–161 |
| K 线链 | kline_fetcher.py | KlineFetcherFactory.create_fetcher_chain 434–464 |
| 板块 | sector_fetcher.py | SectorFetcherManager 579–601 |
| 任务/筛选入口 | daily_job.py, api/screen_service.py | run_once 243–254, run_screening_task 171–193 |
