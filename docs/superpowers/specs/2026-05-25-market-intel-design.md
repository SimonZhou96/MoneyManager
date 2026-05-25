# Market Intel Design

## Background

MoneyManager already has a rule-chain-first stock screening platform under
`stock_screener`. It includes market-specific rule chains, single-stock
analysis, AI-assisted signal review, CSV and report artifacts, an Option Lab, a
Quant Lab, and a simple React/Vite web console.

The current gap is market and stock information breadth. Compared with
go-stock-style personal market terminals, MoneyManager does not yet have a
unified backend layer for F10 data, announcements, research reports, money
flow, long-tiger list data, global indexes, market news, and search-backed
latest events.

The selected direction is backend-data-first. The first version should make
structured market intelligence available through stable backend services and
simple web pages. It should not try to build a full desktop market terminal.

## Goals

- Add a `market_intel` backend data layer inside `stock_screener`.
- Keep the existing rule-chain screening flow as the first decision layer.
- Fetch and cache stock-level intelligence for rule-chain candidates and
  explicitly requested single stocks.
- Fetch and cache market-level intelligence for market digests.
- Integrate structured market intelligence, search results, and manual context
  into one LLM-ready evidence package.
- Preserve source, publish time, fetch time, provider status, and stale-cache
  state for every evidence item.
- Provide simple web APIs and a thin `市场情报` page for validation and review.
- Provide Chinese, plain-language report templates for one stock and for
  multiple stocks.
- Use tables and data charts as the default report presentation method.

## Non-Goals

- Do not replace the existing rule engine or screening result tables.
- Do not make market intelligence decide whether a stock passes screening.
- Do not default to one search request or one LLM call per stock.
- Do not build a go-stock-style full desktop行情 terminal in the first version.
- Do not place orders or automate trading decisions.
- Do not let LLM-generated text invent scores, amounts, ratios, or chart values.

## Selected Approach

Use a backend-data-first `market_intel` module.

The first version prioritizes stable data contracts, caching, provider status,
and downstream integration. The frontend remains intentionally simple: query a
stock, inspect grouped intelligence, inspect market digest data, and preview the
evidence pack before LLM analysis.

The analysis path becomes:

```text
stock pool or user input
-> K-line and base data preparation
-> rule-chain screening
-> candidate confirmation
-> market_intel structured data
-> search supplement
-> EvidencePack
-> signal_analysis LLM batch review
-> CSV, web detail, Markdown/PDF reports
```

## Architecture

Add these modules:

- `stock_screener/market_intel/models.py`
  - Defines `IntelItem`, `StockIntelBundle`, `MarketIntelBundle`,
    `DataSourceStatus`, and `EvidencePack`.
- `stock_screener/market_intel/providers/`
  - One provider per external source or source family.
  - Initial provider files:
    - `eastmoney.py`
    - `iwencai.py`
    - `news.py`
    - `global_index.py`
  - Providers normalize external responses into `IntelItem` objects.
- `stock_screener/market_intel/repository.py`
  - Reads and writes MySQL cache rows, bundle rows, and provider run rows.
- `stock_screener/market_intel/service.py`
  - Provides stable service methods:
    - `get_stock_intel(market, code, force_refresh=False)`
    - `get_market_digest(market, force_refresh=False)`
    - `refresh_stock_intel(market, code)`
    - `refresh_market_digest(market)`
- `stock_screener/market_intel/evidence.py`
  - Defines `EvidencePackBuilder`.
  - Merges `market_intel`, existing search providers, and manual context.
- `stock_screener/web/market_intel.py`
  - Adds FastAPI routes for stock intelligence, market digests, provider run
    status, and evidence-pack preview.
- `stock_screener/web_frontend`
  - Adds a simple `市场情报` page and a link from task detail / single-stock
    result pages.

The module should keep dependencies pointing inward:

```text
providers -> models
repository -> models
service -> providers + repository
evidence -> service + signal_analysis.search_providers + manual context
web route -> service + evidence
signal_analysis -> evidence builder
```

`market_intel` should not import frontend code or mutate screening decisions.

## Initial Data Sources

### Stock-Level Sources

The first stock-level sources are:

- Eastmoney F10 and data center:
  - latest financial summary;
  - quarterly financial summary;
  - valuation percentile;
  - organization forecast;
  - announcement list;
  - research report list;
  - money-flow summary;
  - long-tiger list / billboard data;
  - block trade and margin data when available.
- Iwencai:
  - optional natural-language stock query;
  - optional research report search;
  - optional news, investor relation, and announcement search.
- Existing `signal_analysis.search_providers`:
  - latest company events;
  - long-tail news not covered by structured providers.

### Market-Level Sources

The first market-level sources are:

- Cailianpress and Sina:
  - market flash news;
  - important event tags;
  - hot news summary.
- Global and major index providers:
  - global index snapshot;
  - major A/HK/US index snapshot;
  - market open/close state when available.
- Industry and concept data:
  - industry / concept money-flow ranking;
  - hot sector summary.
- Existing search providers:
  - market-wide hot events;
  - policy and macro news supplement.

## Data Model

Add these MySQL tables through a migration such as
`sql/015_market_intel.sql`.

### `market_intel_items`

Stores one normalized intelligence item.

Recommended fields:

- `id`
- `scope_type`: `stock` or `market`
- `market`
- `code`
- `source`
- `provider`
- `item_type`: `financial`, `announcement`, `research_report`, `money_flow`,
  `long_tiger`, `index_snapshot`, `market_news`, `search_document`,
  `hot_sector`, or `other`
- `title`
- `summary`
- `url`
- `published_at`
- `raw_json`
- `fetched_at`
- `expires_at`
- `is_stale`
- `dedupe_key`
- `created_at`
- `updated_at`

### `market_intel_bundles`

Stores one aggregated stock or market intelligence bundle.

Recommended fields:

- `id`
- `scope_type`
- `market`
- `code`
- `bundle_json`
- `freshness_status`: `fresh`, `partial`, `stale`, or `empty`
- `source_status_json`
- `created_at`
- `updated_at`

### `market_intel_provider_runs`

Stores each provider execution.

Recommended fields:

- `id`
- `provider`
- `scope_type`
- `market`
- `code`
- `status`: `success`, `failed`, `timeout`, or `skipped`
- `error_message`
- `duration_ms`
- `item_count`
- `started_at`
- `finished_at`

## Cache Policy

Default cache windows:

- Financial / F10 data: one trading day.
- Announcements and research reports: 6 to 12 hours.
- Money flow, long-tiger list, and hot sector data: 30 minutes to one day,
  depending on market and endpoint freshness.
- Market flash news: 5 to 15 minutes.
- Global and major index snapshots: 5 to 30 minutes.
- Search supplement: 30 to 120 minutes.

Rules:

- If a fresh cache exists, return the cache.
- If a provider fails and stale cache exists, return the stale cache and mark
  `freshness_status=stale`.
- If a provider fails and no cache exists, return an empty group plus the
  provider error in `source_status_json`.
- Provider failures must not block rule-chain screening or CSV generation.
- Every item passed to downstream analysis must preserve source, publish time,
  fetch time, and provider status.

## Evidence Pack Integration

Add `EvidencePackBuilder` as the boundary between data collection and LLM
analysis.

Inputs:

- `market_intel`
  - structured stock and market intelligence from cache and providers;
  - F10, announcements, reports, money flow, long-tiger list, indexes, market
    news, and hot sectors.
- `search_providers`
  - existing Tavily, Zhipu, or other configured search providers from
    `signal_analysis`;
  - used only to supplement latest company events, market hot events, and
    long-tail news.
- `manual context`
  - existing manually configured hot news;
  - manually configured company news;
  - manually curated sources.

Output:

- `EvidencePack`
  - `structured_items`
  - `search_documents`
  - `manual_items`
  - `market_context`
  - `stock_context`
  - `source_status`
  - `data_gaps`
  - `citations`

Integration flow:

```text
rule-chain candidates
-> market_intel structured cache/provider reads
-> batch-first search supplement
-> EvidencePackBuilder merge and dedupe
-> signal_analysis LLMBatchAnalysisStep
-> structured AI result
```

Constraints:

- Build an evidence pack only for stocks that passed the rule chain, user
  requested single stocks, or explicitly requested custom-list analysis rows.
- Search must default to batch-first. Do not default to one search request per
  stock.
- LLM must consume curated `EvidencePack` content. It should not directly issue
  extra external data calls.
- LLM output remains auxiliary. It must not change whether the rule engine
  passed a stock.
- `EvidencePackBuilder` owns deduplication and source preservation.

## Screening and Analysis Flow

### Full-Market Screening

1. User creates a screening task.
2. Existing K-line and stock-pool data preparation runs.
3. Existing rule chain evaluates each stock.
4. Failed stocks record rule details only.
5. Passed stocks become market-intel candidates.
6. `market_intel` reads fresh cache or refreshes selected structured providers.
7. `EvidencePackBuilder` supplements with batched search when enabled.
8. `signal_analysis` runs LLM batch review.
9. CSV, web task detail, and report artifacts display rule results, evidence
   summary, and AI review.

### Custom-List Screening

1. User submits explicit codes.
2. Existing parser resolves market and code values.
3. Existing rule chain evaluates the selected rows.
4. `market_intel` may run for passed rows and for explicitly selected rows when
   the request asks for full review.
5. Reports follow the same evidence and LLM flow as full-market screening.

### Single-Stock Analysis

1. User submits market, code, timeframe, and rule chain.
2. Existing single-stock rule evaluation runs.
3. `market_intel` builds a stock intelligence bundle regardless of pass/fail,
   because the user explicitly requested the stock.
4. Search supplement is optional and batch size is one.
5. LLM review explains both the rule result and the market intelligence.

## API Design

Add a FastAPI router under `/api/market-intel`.

### `GET /api/market-intel/stocks/{market}/{code}`

Returns `StockIntelBundle`.

Query parameters:

- `refresh=false`
- `include_search=false`
- `max_items_per_group=10`

### `POST /api/market-intel/stocks/{market}/{code}/refresh`

Refreshes stock intelligence.

Request body:

- `force`: boolean
- `include_search`: boolean
- `provider_keys`: optional list

### `GET /api/market-intel/markets/{market}/digest`

Returns `MarketIntelBundle`.

Query parameters:

- `refresh=false`
- `include_search=false`

### `GET /api/market-intel/provider-runs`

Returns recent provider run records.

Query parameters:

- `provider`
- `market`
- `code`
- `status`
- `limit`

### `POST /api/market-intel/evidence-pack/preview`

Returns the LLM-ready evidence pack without calling the LLM.

Request body:

- `market`
- `code`
- `timeframe`
- `analysis_profile`
- `include_search`
- `force_refresh`

## Frontend Design

Add a simple `市场情报` page.

First-version UI:

- Market selector.
- Code input.
- Refresh button.
- Source status panel.
- Grouped sections:
  - financial summary;
  - announcements;
  - research reports;
  - money flow;
  - long-tiger list;
  - search supplement;
  - market news;
  - global / major indexes.
- Each group shows:
  - source;
  - fetched time;
  - published time if available;
  - stale / fresh status;
  - failure reason when present.

Add lightweight entry links:

- From task detail row to stock market-intel detail.
- From single-stock result to stock market-intel detail.

Do not build advanced charting or a desktop-style market terminal in version
one.

## Report Design

Reports must be written in Chinese and optimized for ordinary investors. The
default style is direct, plain, and data-first.

Rules:

- Use simple Chinese explanations.
- Prefer tables and charts over long paragraphs.
- Use real structured data for all scores, amounts, ratios, and chart values.
- LLM may explain data but must not invent numbers.
- Every important claim should map to a source item or a known rule-chain
  result.
- Include data gaps and stale-source warnings.
- End with a clear disclaimer that the report is for observation and review,
  not investment advice.

### Single-Stock Report Template

```markdown
# {股票名称}（{代码}）市场情报与AI复核报告

报告日期：{YYYY-MM-DD}
市场：{A股/港股/美股}
分析周期：{1d / 5d / 1w}
结论类型：{重点关注 / 谨慎观察 / 暂不建议关注}

## 1. 一句话结论

{用一段通俗中文说明：这只股票当前为什么被筛出来，主要机会是什么，最大风险是什么。}

## 2. 核心评分

| 指标 | 分数 | 说明 |
|---|---:|---|
| 技术信号强度 | {0-100} | 来自规则链、K线、成交量、趋势指标 |
| 资金面评分 | {0-100} | 来自主力资金、北向/南向、龙虎榜等 |
| 基本面评分 | {0-100} | 来自财务、盈利、估值、机构预测 |
| 消息面评分 | {0-100} | 来自公告、研报、新闻、热点 |
| 综合可靠性 | {0-100} | 综合以上证据后的辅助判断 |

## 3. 分数构成图

饼图：综合评分来源占比

| 来源 | 占比 |
|---|---:|
| 技术信号 | {xx}% |
| 资金面 | {xx}% |
| 基本面 | {xx}% |
| 消息面 | {xx}% |

## 4. 关键数据表

| 类别 | 最新数据 | 变化 | 解读 |
|---|---:|---:|---|
| 收盘价 | {price} | {change_pct}% | {解读} |
| 成交量 | {volume} | {volume_change}% | {解读} |
| 主力净流入 | {amount} | {rank/变化} | {解读} |
| PE / PB | {pe}/{pb} | {分位数} | {解读} |
| 营收同比 | {revenue_yoy}% | {变化} | {解读} |
| 净利润同比 | {profit_yoy}% | {变化} | {解读} |

## 5. 资金面分析

柱状图：近 N 日资金流入/流出

| 日期 | 主力净流入 | 散户净流入 | 股价涨跌幅 |
|---|---:|---:|---:|
| {date} | {amount} | {amount} | {pct}% |

通俗解读：
{说明资金是持续流入、短线脉冲，还是高位流出。}

## 6. 公告 / 研报 / 新闻摘要

| 类型 | 标题 | 时间 | 影响判断 | 来源 |
|---|---|---|---|---|
| 公告 | {title} | {date} | 利好/利空/中性 | {source} |
| 研报 | {title} | {date} | 利好/分歧/下调 | {source} |
| 新闻 | {title} | {date} | 热点相关/风险提示 | {source} |

## 7. 机会与风险

| 方向 | 内容 | 证据来源 |
|---|---|---|
| 机会 1 | {通俗描述} | {公告/研报/资金/规则链} |
| 机会 2 | {通俗描述} | {来源} |
| 风险 1 | {通俗描述} | {来源} |
| 风险 2 | {通俗描述} | {来源} |

## 8. AI 辅助复核

| 项目 | 判断 |
|---|---|
| 辅助方向 | {偏多 / 中性 / 偏空 / 信息不足} |
| 模型置信度 | {高 / 中 / 低} |
| 新闻影响 | {利好 / 利空 / 中性 / 混合} |
| 数据完整度 | {完整 / 部分缺失 / 明显不足} |

AI 总结：
{用普通中文解释，不使用过多金融术语。}

## 9. 观察建议

| 操作 | 条件 |
|---|---|
| 继续观察 | {例如：资金连续流入、价格站上某均线} |
| 谨慎 | {例如：放量下跌、公告风险、研报下调} |
| 移出观察池 | {例如：规则信号失效、成交量萎缩、风险事件确认} |

## 10. 数据来源与免责声明

数据来源：
{东方财富 / 问财 / 财联社 / 新浪 / market_intel 缓存 / 搜索 provider}

说明：
本报告仅用于辅助观察和复盘，不构成投资建议。
```

### Multi-Stock Report Template

Multi-stock reports should not repeat the single-stock template for every stock.
The default structure is summary, ranking, distribution charts, top-stock
details, and appendix tables.

```markdown
# 多股票市场情报与AI复核报告

报告日期：{YYYY-MM-DD}
市场范围：{A股 / 港股 / 美股 / 混合}
股票数量：{N}
分析来源：规则链筛选 / 自定义股票池 / 单次批量分析

## 1. 总体结论

本次共分析 {N} 只股票，其中：

| 分类 | 数量 | 占比 | 说明 |
|---|---:|---:|---|
| 重点关注 | {n} | {pct}% | 信号、资金、消息面相对一致 |
| 谨慎观察 | {n} | {pct}% | 有亮点但风险或数据不足 |
| 暂不关注 | {n} | {pct}% | 信号弱、风险高或缺少支撑 |

一句话总结：
{用通俗中文说明本批股票整体机会、主要风险、最值得关注的方向。}

## 2. 结论分布图

饼图：股票评级分布

| 评级 | 数量 | 占比 |
|---|---:|---:|
| 重点关注 | {n} | {pct}% |
| 谨慎观察 | {n} | {pct}% |
| 暂不关注 | {n} | {pct}% |

饼图：行业 / 板块分布

| 行业 / 板块 | 数量 | 占比 |
|---|---:|---:|
| {行业A} | {n} | {pct}% |
| {行业B} | {n} | {pct}% |
| {行业C} | {n} | {pct}% |

## 3. 综合排名

柱状图：综合评分 Top 10

| 排名 | 股票 | 代码 | 综合评分 | 技术 | 资金 | 基本面 | 消息面 | 结论 |
|---:|---|---|---:|---:|---:|---:|---:|---|
| 1 | {name} | {code} | {score} | {score} | {score} | {score} | {score} | 重点关注 |
| 2 | {name} | {code} | {score} | {score} | {score} | {score} | {score} | 谨慎观察 |

## 4. 资金面对比

柱状图：Top 10 主力净流入 / 净流出

| 股票 | 主力净流入 | 股价涨跌幅 | 解读 |
|---|---:|---:|---|
| {name} | {amount} | {pct}% | {资金持续流入 / 短线脉冲 / 高位流出} |

## 5. 消息面与事件分布

柱状图：利好 / 利空 / 中性事件数量

| 股票 | 公告 | 研报 | 新闻 | 影响判断 | 关键事件 |
|---|---:|---:|---:|---|---|
| {name} | {n} | {n} | {n} | 利好 / 利空 / 混合 | {一句话} |

## 6. 重点关注股票

### 6.1 {股票名称}（{代码}）

| 项目 | 结果 |
|---|---|
| 综合评分 | {score} |
| 规则命中 | {左一战法 / EMA / RSI / 成交量等} |
| 资金面 | {一句话} |
| 基本面 | {一句话} |
| 消息面 | {一句话} |
| 主要机会 | {通俗描述} |
| 主要风险 | {通俗描述} |
| 观察条件 | {继续观察 / 谨慎 / 移出观察池条件} |

简短结论：
{2-4 句中文解释，避免术语堆叠。}

## 7. 谨慎观察股票

| 股票 | 代码 | 亮点 | 风险 | 建议观察条件 |
|---|---|---|---|---|
| {name} | {code} | {亮点} | {风险} | {条件} |

## 8. 暂不关注股票

| 股票 | 代码 | 主要原因 |
|---|---|---|
| {name} | {code} | {信号不足 / 资金流出 / 消息偏空 / 数据不足} |

## 9. 共性机会与风险

| 类型 | 内容 | 涉及股票 |
|---|---|---|
| 共性机会 | {例如：行业景气、政策催化、资金流入} | {股票列表} |
| 共性风险 | {例如：估值偏高、业绩下修、事件不确定} | {股票列表} |

## 10. 数据缺失与来源说明

| 来源 | 状态 | 影响 |
|---|---|---|
| 东方财富 F10 | 成功 / 失败 / 旧缓存 | {说明} |
| 公告 / 研报 | 成功 / 部分缺失 | {说明} |
| 搜索补充 | 成功 / 跳过 / 失败 | {说明} |
| LLM 复核 | 成功 / 跳过 | {说明} |

免责声明：
本报告仅用于辅助观察和复盘，不构成投资建议。
```

### Chart Requirements

First-version report rendering should support these chart types:

- Pie chart:
  - single-stock score source distribution;
  - multi-stock rating distribution;
  - multi-stock industry / sector distribution.
- Bar chart:
  - single-stock recent money-flow series;
  - multi-stock Top 10 comprehensive score comparison;
  - multi-stock Top 10 main-money inflow / outflow comparison;
  - event impact count distribution.
- Tables:
  - core data;
  - announcements / reports / news;
  - opportunities and risks;
  - observation conditions;
  - source and data-gap status.

Charts must be generated from structured fields. If the required structured
field is missing, the chart should be omitted and the report should show the
missing reason in the data-source section.

## Error Handling

- External provider failures do not fail screening tasks.
- Search failures do not fail CSV generation.
- LLM failures do not fail rule-chain results.
- Stale cache should be returned with explicit stale warnings.
- Empty data should be represented as an empty group with a provider status
  explanation.
- Provider requests need timeout, retry limit, and run status logging.
- API responses should include machine-readable error codes and Chinese
  user-facing messages.

## Cost and Quota Controls

- Default market-intel fetch scope is passed stocks only.
- Single-stock analysis can always request market intelligence because the user
  explicitly selected the stock.
- Search supplement is opt-in at the API level and controlled by analysis
  profile in automated flows.
- Search must use batch-first queries for company-news supplement.
- LLM analysis must remain batch-based.
- Market-level context should be shared across stocks in the same market run.
- Reused cache should avoid repeated provider calls during the same task.

Example call-count target for 100 passed stocks in one market:

- Structured market digest: 1 to 3 provider calls.
- Stock structured data: provider-specific batch calls where available;
  otherwise bounded and cached per stock.
- Search: market-level query plus batched company-news queries.
- LLM: batched by existing `LLM_ANALYSIS_BATCH_SIZE`.

## Security and Compliance

- Store source URLs and provider names for traceability.
- Do not expose provider secrets in API responses or reports.
- Respect existing web authentication.
- Keep provider-specific API keys in environment/config only.
- Do not persist unnecessary full HTML pages unless needed for debugging.
- Include a disclaimer in every investor-facing report.

## Testing Strategy

Provider tests:

- Use fixtures or mocked HTTP responses.
- Do not require live external network for unit tests.
- Verify normalization to `IntelItem`.

Repository tests:

- Cache hit.
- Cache expiry.
- Stale fallback.
- Provider run status persistence.

Evidence tests:

- Merge `market_intel`, search documents, and manual context.
- Deduplicate overlapping items.
- Preserve source, publish time, and fetch time.
- Enforce batch-first search behavior.

Signal-analysis tests:

- Verify LLM input can be built from `EvidencePack`.
- Verify automated runs do not default to one search request per stock.
- Verify market-intel failure does not fail CSV or task output.

Web API tests:

- Authentication.
- Market and code validation.
- Refresh behavior.
- Provider failure downgrade.
- Evidence-pack preview without LLM invocation.

Frontend tests:

- Build passes.
- Market-intel page has the expected input, refresh action, grouped sections,
  source status, and stale/error display.

Report tests:

- Single-stock report renders Chinese sections.
- Multi-stock report renders summary distribution, ranking, top-stock detail,
  and appendix tables.
- Charts are omitted with a data-gap note when source data is missing.
- LLM-generated text cannot introduce numeric chart values that are not present
  in structured data.

## Rollout Plan

1. Add schema and repository.
2. Add models and provider interfaces.
3. Add Eastmoney and basic news/index providers.
4. Add service layer and web APIs.
5. Add `EvidencePackBuilder` and connect it to `signal_analysis`.
6. Add simple frontend page and task/single-stock links.
7. Add report renderer support for single-stock and multi-stock templates.
8. Add focused tests and fixture coverage.

## Acceptance Criteria

- A stock intelligence bundle can be fetched for one stock and includes grouped
  source status.
- A market digest can be fetched for one market and includes market news and
  index context.
- Provider failures are visible but do not fail screening.
- Evidence-pack preview shows structured market-intel items, search supplement,
  manual context, data gaps, and citations.
- Automated screening still runs with AI disabled or provider failures.
- Single-stock and multi-stock reports use Chinese, plain-language sections,
  tables, and chart-ready structured data.
- The first version does not default to per-stock search or per-stock LLM calls.
