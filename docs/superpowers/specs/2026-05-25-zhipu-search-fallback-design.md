# 智谱AI联网搜索兜底设计

日期：2026-05-25

## 背景

MoneyManager 的股票筛选、期权实验室宏观分析、报告生成都复用 `stock_screener/signal_analysis` 里的搜索 + LLM 分析链路。当前联网搜索只支持 Tavily。用户要求新增智谱AI（BigModel）Web Search 作为兜底能力：

1. 搜索优先级固定为 `Tavily > 智谱AI`。
2. 同一批 batch 搜索中，如果 Tavily 搜索失败，切换成智谱AI。
3. 如果智谱AI也失败，该 batch 跳过联网搜索，不阻塞后续分析。
4. 不额外放大不必要的搜索和 LLM 调用成本。

智谱官方 Web Search API 为 `POST https://open.bigmodel.cn/api/paas/v4/web_search`，使用 `Authorization: Bearer <token>`，请求字段包括 `search_query`、`search_engine`、`search_intent`、`count`、`search_recency_filter`、`content_size`；响应结果在 `search_result` 中，包含 `title`、`content`、`link`、`media`、`publish_date` 等字段。官方文档说明 `search_query` 建议不超过 70 字符，参数最大长度也是 70。

参考：

- https://docs.bigmodel.cn/cn/guide/tools/web-search
- https://docs.bigmodel.cn/api-reference/%E5%B7%A5%E5%85%B7-api/%E7%BD%91%E7%BB%9C%E6%90%9C%E7%B4%A2

## 目标

- 在现有 `SearchProvider` 抽象下新增智谱AI搜索 provider。
- 新增搜索 provider fallback：Tavily 成功时不调用智谱AI；Tavily 失败时再调用智谱AI；两者都失败时返回空结果并记录 warning。
- 保持现有 batch 搜索经济性，不把常规 batch 分析扩散为不必要的逐股搜索。
- 让搜索降级对股票和期权共用链路同时生效。
- 保持现有 Tavily 行为和默认配置兼容。

## 非目标

- 不新增前端配置页。
- 不改 LLM provider 调用链。
- 不使用智谱“对话中的网络搜索”或“搜索智能体”，只接入结构化 Web Search API。
- 不在 Tavily 正常时并行调用智谱AI。
- 不为每只股票额外追加搜索，除非 provider 本身因为官方 query 长度限制必须拆分，但这只发生在 Tavily batch 失败后的智谱兜底路径。

## 现有链路

当前关键文件：

- `stock_screener/signal_analysis/search_providers.py`
  - 定义 `SearchProvider`、`NullSearchProvider`、`TavilySearchProvider`。
  - Tavily 的 `search_companies_batch()` 会基于 `SIGNAL_COMPANY_SEARCH_QUERY_MAX_CHARS` 构造公司批量 query。
- `stock_screener/signal_analysis/factories.py`
  - `SearchProviderFactory.from_env()` 只在 `TAVILY_API_KEY` 存在时返回 `TavilySearchProvider`。
- `stock_screener/signal_analysis/service.py`
  - `_filter_search_provider_by_preflight()` 做搜索 endpoint DNS 预检，失败会降级为 `NullSearchProvider`。
- `stock_screener/signal_analysis/chain.py`
  - 先搜市场上下文和热点板块，再按 batch 搜公司事件。
  - 公司 batch 搜索失败时记录 warning，并把该 batch 的公司文档设为空。

## 设计方案

### 1. 新增 `ZhipuWebSearchProvider`

位置：`stock_screener/signal_analysis/search_providers.py`

职责：

- 直接使用 `requests.post` 调用智谱 Web Search API，避免引入 `zai-sdk` 新依赖。
- 把智谱 `search_result` 标准化为现有 `SearchDocument`。
- 支持单次 `search(query, max_results)`。
- 支持 `search_companies_batch(market, rows, max_results)`，但使用智谱专用的短 query 构造策略。

建议字段：

- `name = "zhipuai"`
- `endpoint = "https://open.bigmodel.cn/api/paas/v4/web_search"`
- `api_key`
- `search_engine`
- `content_size`
- `recency_filter`
- `timeout_sec`

请求：

```json
{
  "search_query": "短 query",
  "search_engine": "search_std",
  "search_intent": false,
  "count": 5,
  "search_recency_filter": "noLimit",
  "content_size": "medium"
}
```

响应转换：

- `title` -> `SearchDocument.title`
- `link` -> `SearchDocument.url`
- `content` -> `SearchDocument.content`
- `media`、`publish_date` 可追加到 `content` 中，或保留在内容摘要里，避免改 `SearchDocument` 数据结构。
- `query` 保留原始 query。

### 2. 新增 `FallbackSearchProvider`

位置：`stock_screener/signal_analysis/search_providers.py`

职责：

- 顺序持有多个 provider。
- `search()` 按顺序尝试：Tavily 失败才调用智谱AI。
- `search_companies_batch()` 也按同样顺序尝试整个 batch。
- 最后都失败时返回空结果，不抛出到调用方。

行为细节：

- Tavily 成功但返回空列表，不视为失败；不触发智谱AI，避免双重搜索成本。
- Tavily 抛异常、HTTP 4xx/5xx、网络超时才视为失败，并切换智谱AI。
- 智谱AI失败后返回空结果，并通过 `last_errors` 或 warning helper 暴露失败原因，供上层记录。
- 返回空结果时，公司 batch 应返回 `{row.code: []}`，保持调用方兼容。

### 3. Provider 工厂顺序

位置：`stock_screener/signal_analysis/factories.py`

新增配置：

- `ZHIPUAI_API_KEY`：智谱AI API Key，优先读取。
- `BIGMODEL_API_KEY`：兼容兜底。
- `ZHIPUAI_WEB_SEARCH_ENDPOINT`：默认 `https://open.bigmodel.cn/api/paas/v4/web_search`。
- `ZHIPUAI_WEB_SEARCH_ENGINE`：默认 `search_std`。
- `ZHIPUAI_WEB_SEARCH_CONTENT_SIZE`：默认 `medium`。
- `ZHIPUAI_WEB_SEARCH_RECENCY_FILTER`：默认 `noLimit`。
- `SIGNAL_SEARCH_PROVIDER_ORDER`：默认 `tavily,zhipuai`。

构造规则：

1. 如果只配置 Tavily，行为与现在一致。
2. 如果只配置智谱AI，直接使用智谱AI。
3. 如果两者都配置，返回 `FallbackSearchProvider([TavilySearchProvider, ZhipuWebSearchProvider])`。
4. 如果都没配置，返回 `NullSearchProvider`。

用户已确认 API Key 变量规则：优先 `ZHIPUAI_API_KEY`，兼容 `BIGMODEL_API_KEY`。

### 4. DNS 预检兼容 fallback

位置：`stock_screener/signal_analysis/service.py`

当前 `_filter_search_provider_by_preflight()` 只能处理单个 endpoint。如果 `Tavily` 预检失败但 `智谱AI` 可用，不能整体降级为 `NullSearchProvider`。

调整：

- 如果 provider 有 `providers` 属性，则逐个 provider 做 endpoint 预检。
- 保留预检成功的 provider，并用原顺序重新构造 fallback。
- 如果 Tavily DNS 失败、智谱AI成功，则继续使用智谱AI，并记录 Tavily 预检 warning。
- 如果全部失败，返回 `NullSearchProvider`。

### 5. 智谱 batch query 长度控制

智谱 API `search_query` 最大 70 字符，不能复用 Tavily 的长 batch query。

智谱公司 batch 策略：

- 使用 provider 专属 query budget，默认 70。
- 每个 query 优先保留：
  - 市场前缀，如 `A股 股票 最新公告 新闻`
  - 股票代码，如 `600176` / `SH.600176`
  - 股票名称，如 `中国巨石`
  - `公告 新闻 财报`
- 如果多个 row 放不下，则拆成更小 batch。
- 拆分只发生在智谱兜底路径；Tavily 成功时不发生额外调用。

示例：

```text
A股 股票 最新公告 新闻 600176 中国巨石 600382 广东明珠
```

如果超长，则拆为：

```text
A股 股票 最新公告 新闻 600176 中国巨石
A股 股票 最新公告 新闻 600382 广东明珠
```

这个策略会在 Tavily 失败时增加少量智谱请求，但不会在 Tavily 正常时产生额外成本，也不会触发 LLM 额外调用。

### 6. Warning 和可观测性

需要让失败原因可追踪，但不刷屏。

建议 warning：

- `Tavily 搜索失败，已切换智谱AI: <error>`
- `智谱AI搜索失败，跳过联网检索: <error>`
- `搜索 provider 网络预检失败: <provider>/<host> ...`

批量公司搜索失败时沿用现有文案，但补充 provider 名称：

- `公司事件批量搜索失败: Tavily search failed ...; fallback=zhipuai; codes=...`
- `公司事件批量搜索失败: all providers failed ...; codes=...`

## 成本控制

本设计遵守当前系统的批量搜索经济性：

- Tavily 成功时，智谱AI不会被调用。
- Tavily 返回空结果不触发智谱AI，避免为了“补齐更多内容”产生双重搜索。
- Tavily 失败时才调用智谱AI。
- 智谱AI失败时直接跳过该 batch，不做更多 provider 或 per-stock 扩散。
- LLM 调用次数不变。
- 缓存、批量 CSV、股票与期权共享 `signal_analysis` 的既有链路不变。

## 测试计划

新增或更新 `stock_screener/tests/test_signal_analysis.py`：

1. `ZhipuWebSearchProvider` 能解析 `search_result` 为 `SearchDocument`。
2. 智谱请求使用 `Authorization: Bearer <key>`。
3. 智谱 query 长度不超过 70。
4. 两个 key 都存在时优先使用 `ZHIPUAI_API_KEY`。
5. 仅配置 Tavily 时保持返回 `TavilySearchProvider`。
6. 仅配置智谱AI时返回 `ZhipuWebSearchProvider`。
7. 同时配置时返回 `FallbackSearchProvider`，顺序为 Tavily、智谱AI。
8. Tavily 成功时不调用智谱AI。
9. Tavily batch 搜索抛错时调用智谱AI。
10. Tavily 和智谱AI都失败时返回空结果，不中断分析。
11. fallback 不引入额外 LLM 调用。

更新 `stock_screener/tests/test_network_preflight.py`：

1. Tavily DNS 失败、智谱AI DNS 成功时，不降级为 Null。
2. Tavily 和智谱AI DNS 都失败时，降级为 Null。

## 验收标准

- 无智谱配置时，现有 Tavily 行为不变。
- 同时配置 Tavily 和智谱AI时，搜索优先级是 Tavily > 智谱AI。
- 同一批公司搜索 Tavily 报错后，会用智谱AI重试。
- 两个 provider 都失败时，该 batch 返回空搜索结果，整体 signal analysis 继续运行。
- 智谱 API 返回的标题、链接、摘要能进入后续 evidence/source 链路。
- 测试覆盖 provider factory、fallback、智谱响应解析、DNS 预检、失败跳过。

## 自查

- 无待填项。
- 未要求前端改动，设计不包含前端范围。
- 未改变 LLM provider 顺序和调用次数。
- 已明确智谱 API Key 环境变量优先级。
- 已处理智谱 `search_query` 70 字符限制。
- 已保留用户要求的调用成本控制原则。
