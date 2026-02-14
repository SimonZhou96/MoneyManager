# 港股/美股 股票列表 + yfinance 补全逻辑（方案 B）

> 基于建议 2：用 yfinance 补齐港股、美股的市值和 PE。本文档仅描述调用逻辑，不改代码。

---

## 一、总体流程

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         fetch_stock_list(market)                              │
│                    （对外统一入口：daily_job / screen_service 调用）             │
└─────────────────────────────────────────────────────────────────────────────┘
                                        │
                                        ▼
        ┌───────────────────────────────┴───────────────────────────────┐
        │                                                                │
        ▼                                                                ▼
   market == "A"                                                    market in ["HK","US"]
        │                                                                │
        ▼                                                                ▼
  [1] fetch_stock_list_akshare("A")                           [1] fetch_stock_list_akshare(market)
        │                                                                │
        │  stock_zh_a_spot_em → 已有 总市值、市盈率-动态                     │  stock_hk_spot / stock_hk_spot_em
        │                                                                │  或 stock_us_spot / stock_us_spot_em
        │                                                                 │  可能缺 market_cap / pe_ratio
        ▼                                                                ▼
  [2] 直接返回 stocks（无需补全）                              [2] 若 stocks 为空 且 quote_ctx 可用
                                                                        → fetch_stock_list_futu(market)
                                                                        → 返回仅含 code/name
                                                                        │
                                                                        ▼
                                                              [3] enrich_with_yfinance(stocks, market)
                                                                        对 market_cap 或 pe_ratio 为 None 的股票
                                                                        用 yfinance 补全
                                                                        │
                                                                        ▼
                                                              [4] 返回补全后的 stocks
```

---

## 二、各市场调用顺序

### 2.1 A 股（不变）

| 步骤 | 操作 | 数据源 | 市值 | PE |
|------|------|--------|------|-----|
| 1 | `fetch_stock_list_akshare("A")` | AKShare `stock_zh_a_spot_em` | ✓ | ✓ |
| 2 | 直接返回 | — | — | — |

**不调用 yfinance**，AKShare 已提供完整基本面。

---

### 2.2 港股

| 步骤 | 操作 | 说明 |
|------|------|------|
| 1 | `fetch_stock_list_akshare("HK")` | 保持当前顺序：`stock_hk_spot` → `stock_hk_spot_em`（失败兜底） |
| 2 | 若返回空 且 quote_ctx 可用 | `fetch_stock_list_futu(quote_ctx, "HK")` 兜底 |
| 3 | `enrich_with_yfinance(stocks, "HK")` | 对 `market_cap is None` 或 `pe_ratio is None` 的股票补全 |
| 4 | 返回 stocks | — |

**AKShare 港股**：
- `stock_hk_spot`：无市值、无 PE
- `stock_hk_spot_em`：无市值、有 PE

因此港股列表中多数或全部需要 yfinance 补全市值，部分需补全 PE。

---

### 2.3 美股

| 步骤 | 操作 | 说明 |
|------|------|------|
| 1 | `fetch_stock_list_akshare("US")` | 保持当前顺序：`stock_us_spot` → `stock_us_spot_em`（失败兜底） |
| 2 | 若返回空 且 quote_ctx 可用 | `fetch_stock_list_futu(quote_ctx, "US")` 兜底 |
| 3 | `enrich_with_yfinance(stocks, "US")` | 对 `market_cap is None` 或 `pe_ratio is None` 的股票补全 |
| 4 | 返回 stocks | — |

**AKShare 美股**：
- `stock_us_spot`：无市值、无 PE
- `stock_us_spot_em`：有市值、有 PE

因此：若优先用 `stock_us_spot` 且成功，几乎全部需 yfinance 补全；若用 `stock_us_spot_em`，多数已有数据，仅部分缺失时补全。

---

## 三、yfinance 补全逻辑（enrich_with_yfinance）

### 3.1 触发条件

**仅对 HK、US 市场执行**，且满足：

- `stocks` 非空
- 存在至少一只股票满足：`item.get("market_cap") is None` 或 `item.get("pe_ratio") is None`

### 3.2 补全规则（哪些股票）

对每只 `item in stocks`：

- 若 `item["market_cap"] is not None` 且 `item["pe_ratio"] is not None` → **跳过**，不调用 yfinance
- 否则 → 调用 yfinance 补全，且**只覆盖为 None 的字段**，不覆盖已有值

### 3.3 代码到 yfinance 的 symbol 映射

| 市场 | 内部格式 | yfinance symbol |
|------|----------|-----------------|
| 港股 | `HK.00700` 或 `00700` | `0700.HK` |
| 港股 | `HK.00001` | `00001.HK` |
| 美股 | `US.AAPL` 或 `AAPL` | `AAPL` |
| 美股 | `US.TSLA` | `TSLA` |

**转换规则**：
- **港股**：去掉 `HK.` 前缀 → 5 位补零（如 `700` → `00700`）→ 拼接 `.HK`
- **美股**：去掉 `US.` 前缀 → 取 ticker 部分（若带交易所后缀如 `.NS` 则需处理）→ 直接使用

### 3.4 yfinance 返回字段映射

| yfinance 字段 | 内部字段 | 说明 |
|---------------|----------|------|
| `info.get("marketCap")` | `market_cap` | 市值（单位：USD） |
| `info.get("trailingPE")` | `pe_ratio` | 市盈率 TTM，优先 |
| `info.get("forwardPE")` | （可选）`pe_ratio` | trailingPE 缺失时的兜底 |

### 3.5 调用方式与限速

```
for item in stocks:
    if item.get("market_cap") is not None and item.get("pe_ratio") is not None:
        continue
    yf_symbol = _to_yf_symbol(item["code"], market)
    try:
        info = yf.Ticker(yf_symbol).info
        if item.get("market_cap") is None and info.get("marketCap"):
            item["market_cap"] = float(info["marketCap"])
        if item.get("pe_ratio") is None:
            pe = info.get("trailingPE") or info.get("forwardPE")
            if pe is not None:
                item["pe_ratio"] = float(pe)
    except Exception:
        pass  # 单只失败不影响其他
    time.sleep(0.2)  # 限速，建议 0.2～0.5 秒/只
```

### 3.6 异常与边界

| 情况 | 处理 |
|------|------|
| yfinance 未安装 | 跳过补全，返回原始 stocks |
| 单只股票 info 为空或异常 | 跳过该只，继续下一只 |
| `marketCap`/`trailingPE` 为 NaN 或无穷 | 不写入，保留 None |
| 股票已退市/不存在 | info 可能为空，跳过即可 |

---

## 四、调用方适配

### 4.1 daily_job.py

**当前逻辑**（约 243–250 行）：
```
if db.stock_count(market) == 0:
    stocks = fetch_stock_list_akshare(market)
    if not stocks and futu_ctx:
        stocks = fetch_stock_list_futu(futu_ctx, market)
    if stocks:
        db.upsert_stocks(market, stocks, source="AKShare"/"Futu")
```

**调整后**：入口统一为 `fetch_stock_list(market, quote_ctx)`，内部完成：
1. AKShare 拉取（或 Futu 兜底）
2. HK/US 的 yfinance 补全
3. 返回最终 stocks

`daily_job` 只需调用 `fetch_stock_list`，不再直接调用 `fetch_stock_list_akshare` 和 `fetch_stock_list_futu`。

### 4.2 api/screen_service.py

**当前逻辑**（约 172–178 行）：
```
stocks = db.get_stocks(market, include_fundamentals=True)
if not stocks:
    api_stocks = fetch_stock_list_akshare(market)
    db.upsert_stocks(market, api_stocks, source="AKShare")
    stocks = api_stocks
```

**调整后**：DB 为空时调用 `fetch_stock_list(market, quote_ctx=None)`，内部完成 AKShare + yfinance 补全，再 upsert。

### 4.3 新增统一入口建议

在 `universe.py` 中增加：

```
def fetch_stock_list(market: str, quote_ctx=None, enrich_fundamentals: bool = True) -> List[dict]:
    """
    获取股票列表（统一入口）
    - market: A / HK / US
    - quote_ctx: Futu 连接，用于 AKShare 失败时的兜底
    - enrich_fundamentals: 是否对 HK/US 用 yfinance 补全市值、PE
    """
```

`fetch_stock_list_akshare` 和 `fetch_stock_list_futu` 保留为内部函数，由 `fetch_stock_list` 按需调用。

---

## 五、数据流小结

| 市场 | AKShare 列表 | Futu 兜底 | yfinance 补全 | 最终 |
|------|--------------|-----------|---------------|------|
| A 股 | ✓ 已有市值、PE | 不适用 | 不执行 | 直接返回 |
| 港股 | ✓ 可能缺市值、缺 PE | 仅 code/name | 补全市值、PE | 补全后返回 |
| 美股 | ✓ 可能缺市值、缺 PE | 仅 code/name | 补全市值、PE | 补全后返回 |

---

## 六、实现要点 Checklist

1. [ ] 新增 `enrich_with_yfinance(stocks, market)`，仅处理 HK、US
2. [ ] 新增 `_to_yf_symbol(code, market)`，处理 HK.00700 → 0700.HK、US.AAPL → AAPL
3. [ ] 只对 `market_cap` 或 `pe_ratio` 为 None 的股票调用 yfinance
4. [ ] 限速：每次请求后 `time.sleep(0.2)`（可配置）
5. [ ] 单只失败不中断，continue 到下一只
6. [ ] 新增 `fetch_stock_list(market, quote_ctx, enrich_fundamentals)` 作为统一入口
7. [ ] daily_job、screen_service 改为调用 `fetch_stock_list` 而非直接调用 akshare/futu

---

## 七、性能与可配置项

| 项目 | 建议 |
|------|------|
| 港股数量 | ~2500 | 全量补全约 500～800 秒（按 0.2s/只） |
| 美股数量 | ~8000+ | 全量补全时间更长 |
| 可选策略 | `enrich_max_count`：最多补全 N 只，如 500，其余保持 None |
| 可选策略 | `enrich_fundamentals=False`：跳过补全，适用于仅需 code/name 的场景 |
