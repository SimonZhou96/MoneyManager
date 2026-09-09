# MoneyManager

**Tooling**: 文件搜索用 `semble search`，shell 命令用 `rtk`（如 `rtk find`、`rtk grep`、`rtk git`），节省 token。

Stock screening and analysis platform. The core app is `stock_screener/` — a database-driven rule engine that screens stocks across HK/US/A markets, with optional post-screening LLM signal analysis and Feishu delivery.

## Project Memory

Detailed context lives in the memory directory. Read the relevant files before working on these subsystems:

- **[Stock Screener Rule Engine](memory/stock-screener-rule-engine.md)** — Rule chain JSON DSL, `screening_rule_metadata` / `screening_rule_chains` tables, built-in filters and strategies, modification protocol.
- **[Signal Analysis Chain](memory/signal-analysis-chain.md)** — Post-screening search + LLM pipeline, provider fallback chain (`OpenAICompatible` / `Codex` / `DeepSeek`), environment variable reference, API quota discipline, artifact rules.
- **[Stock Sector Enrichment](memory/stock-sector-enrichment.md)** — Sector/industry enrichment pipeline, provider priority order, merge rules.
- **[Skill Abstraction Guidance](memory/skill-abstraction-guidance.md)** — When to propose new project skills from repeatable workflows.
- **[K-line Chart Dedup Rule](memory/kline-chart-dedup-rule.md)** — ⚠️ 每次修改 KlineChart.tsx 必读：任何 `series.setData()` 前必须去重，否则抛 "data must be asc ordered by time"

## Key Conventions

- **Three-mode compatibility**: All backend changes MUST work across desktop app (`desktop.py` + pywebview), web frontend (Vite → uvicorn), AND interactive shell (`interactive_screening.py`). See `memory/backend-compatibility-rule.md` for details. After any backend change, verify: `python3 desktop.py` starts, `curl localhost:8000/api/...` returns data.
- **Failure isolation**: AI analysis and sector enrichment failures must never block CSV generation or Feishu delivery.
- **Sync SQL and Python**: Whenever default rules/chains/schema change, update both the `sql/` deployment files and the Python default constants.
- **Quota awareness**: Before adding external API calls, estimate call counts for a realistic batch (e.g., 100 stocks) and prefer batching.
- **Environment variables**: Provider configuration is centralized in `signal_analysis/factories.py` — never add provider-specific branches in chain code.

## Fix Log

**Rule**: Every conversation session that includes bug fixes MUST append a dated entry below. Subsequent sessions MUST read this log before making changes to the same files, to avoid conflicting fixes.

### 2026-06-02 — yfinance Symbol Conversion & Fallback Chain Fixes

**Files changed**:
- `stock_screener/potential_analysis/service.py` — `_to_yf()`, `prefetch_batch()`
- `stock_screener/potential_analysis/builders.py` — `_resolve_ticker()`, Futu fallback helpers
- `stock_screener/kline_fetcher.py` — `_to_yf_code()`
- `stock_screener/stock_pool.py` — `fetch_recent_ipos()`
- `stock_screener/api/screen_service.py` — `run_screening_task()`
- `stock_screener/scheduled_daily_job.py` — `get_merged_pool_stocks()`
- `CLAUDE.md` — this entry

**Fixes applied**:

1. **HK/US/A code prefix stripping** (`_to_yf`, `_resolve_ticker`, `_to_yf_code`): DB/Futu codes come with market prefixes (`HK.00001`, `US.AAPL`, `SZ.000999`). All three conversion functions now strip prefixes before constructing yfinance ticker format. HK codes also have `.isdigit()` guard to prevent `int()` crash on non-numeric codes (SPAC units, etc.).

2. **A-share double-suffix defense** (`_to_yf`, `_resolve_ticker`, `_to_yf_code`): Added `endswith((".SS", ".SZ"))` early-return before prefix stripping to prevent `000999.SZ` → `000999.SZ.SZ`.

3. **SPAC unit/right/warrant filter** (`fetch_recent_ipos`): US market's `recent_ipo_2y` pool now skips codes ending in `.U`, `.UT`, `.RT`, `.WS`, codes with `-`, and 5-char all-caps tickers ending in `U` (Futu's dot-stripped SPAC unit format). These are never worth analyzing.

4. **Market code format validation** (`get_merged_pool_stocks`): Added `_code_ok_for_market()` validator that rejects codes not matching expected Futu format per market, preventing cross-market contamination.

5. **yfinance 404 noise suppression** (`prefetch_batch`): 404 errors from yfinance for uncovered stocks are redirected to stderr buffer during batch fetch. Our verbose progress logs now show `ok`/`yf失败`/`futu兜底` counts instead.

6. **Verbose progress logging** (`prefetch_batch`): Each chunk prints `⏳ [market] module: done/total (N ok, Xms)`, every line tagged with market label (`[HK]`/`[US]`/`[A]`) for interleaved concurrent output readability.

7. **Merged API calls** (`prefetch_batch`): `company` + `valuation` + `industry` now share a single `yf.Tickers()` call per chunk — they all use the same `.info` dict but previously made 3 independent API calls. `trading` stays separate because it needs `.history()`. Net reduction: 4→2 API calls per chunk (50% fewer requests, 50% less rate-limit pressure).

8. **Yahoo Finance rate-limit compliance** (`prefetch_batch`): Per yfinance community research (GitHub issues #2125, #2128, #2289): Yahoo enforces ~60 req/min and ~950 tickers/session since Nov 2024. Applied community recommendations:
   - Chunk size: 50 tickers per `yf.Tickers()` call (was 200, then removed, now 50)
   - Inter-chunk delay: 1.5s base + 0.1s per chunk up to 5s max (exponential-ish backoff)
   - Errors show as `gaps` not blocking failures — Yahoo rate-limiting explains earlier 0-ok valuation/industry results

9. **SPAC filter enhancement** (`fetch_recent_ipos`): Extended to catch Futu's dot-stripped SPAC unit format (e.g. `COPAU` vs `COPL.U`). Filters codes where `bare` is all-caps, ends in `U` (not `UU`), ≥4 chars, ASCII-only — catches SPAC units like `COPAU` `ALUBU` `EVACU` while preserving legitimate tickers.

10. **Market label on every log line** (`prefetch_batch`, `screen_service`): All progress lines now include `[HK]`/`[US]`/`[A]` prefix so interleaved concurrent-market output remains readable.

### 2026-06-02 (evening) — YFinance HK Code Zero-Padding Bug & Main Force Risk Fix

**Files changed**:
- `stock_screener/kline_fetcher.py` — `_to_yf_code()` for HK market
- `stock_screener/market_intel/providers/eastmoney.py` — HK code conversion
- `CLAUDE.md` — this entry

**Root cause**: Yahoo Finance requires Hong Kong stock codes in **exactly 4-digit** format (e.g., `2097.HK`, `0805.HK`, `0002.HK`). The `_to_yf_code()` method was using `zfill(5)` which produced 5-digit codes (`02097.HK`, `00805.HK`, `00002.HK`) — all of which return HTTP 404 from Yahoo's history/download endpoints. (The `.info` endpoint accepts padded codes, masking the bug during snapshot fetching.)

**Cascading impact**: This single bug caused:
1. **YFinanceKlineFetcher** → returned empty for ALL HK stocks → `_analyze_kline` returned `status=insufficient` → `_level()` returned `"unknown"` → **all 30 stocks showed "数据不足" for main force risk**
2. **prefetch_batch K-line dependent data** → failed silently → **PE/market cap data missing from CSV**

**Fix**: Changed `code.zfill(5)` → `str(int(code)).zfill(4)` in both files. `int()` strips leading zeros, then `zfill(4)` pads to Yahoo's required 4-digit format. Verified: all 6 tested HK codes now return 180 K-line rows and main force risk analysis produces valid results (risk_level=low, scores 0-6).

**Key architectural note**: `CompanySnapshotBuilder` docstring claims FutuOpenD `get_stock_filter` is a data source (priority 2 after yfinance), but this was **never implemented**. The Futu fallback added in this session (`fill_snapshots_from_futu()`) provides partial coverage via `get_market_snapshot` (price, market cap, PE, PB) — a different Futu endpoint than the one originally planned.

### 2026-06-02/03 — V2 Report Framework: Transparent Scoring + PE Fix + Hot Sector Discovery + Feishu Files

**Files changed**:
- `stock_screener/signal_analysis/chain.py` — `_render_markdown_report()` rewritten to v2 6-section template with score formula, market context, dynamic rules
- `stock_screener/signal_analysis/hot_sectors.py` — added `WebSearchHotSectorProvider` (DeepSeek LLM → Tavily fallback → keyword extraction)
- `stock_screener/api/screen_service.py` — merge YFinance PE/market cap back into StockInfo after prefetch
- `stock_screener/feishu_notifier.py` — `send_screening_result()` now sends `.md` files alongside `.csv`
- `stock_screener/market_intel/providers/eastmoney.py` — same `zfill(5)→zfill(4)` fix as kline_fetcher
- `stock_screener/scripts/generate_signal_report.py` — standalone v2 report script with `--hot-sectors` CLI arg
- `.agents/skills/signal-analysis-report/SKILL.md` — reusable skill for CSV→search→LLM→report→Feishu pipeline
- `CLAUDE.md` — this entry

**V2 report structure** (replaces old 10-section template):
1. 一、市场背景 (market name, dynamic hot sectors)
2. 二、评分体系 (dynamic rules from CSV + auxiliary factors + macro 5-module)
3. 三、信号复核总览 (integrated table with score formula: `50 + 放量(+8) + 热点(+15) = 73.0`)
4. 四、宏观与市场环境影响
5. 五、精选推荐 (5 picks from different sectors, bullish only, buy/hold/avoid)
6. 六、风险提示 + 附录

**Hot sector discovery priority**: DeepSeek LLM (default) → Tavily search → manual env config → built-in defaults. Market-specific prompts produce differentiated results (HK: 金融/创新药/消费/博彩; US: AI/半导体/SaaS/生物科技; A: 机器人/算力/券商/军工).

**PE data fix**: `prefetch_batch()` fetches pe_trailing/market_cap from YFinance into context cache, but StockInfo objects never got updated. Added 5-line merge after prefetch call. Verified YFinance returns PE for all 3 markets (HK 蜜雪 14.8, US AAPL 38.1, A 茅台 19.4).

**Score transparency**: Replaced black-box LLM score with heuristic formula display. Each stock now shows the full calculation. `_recommendation()` fixed: neutral bias → 🟡 持有/观察 (was ⚠️ 回避).

**Feishu file delivery**: `send_screening_result()` previously only sent `.csv` files. Now sends `.md` reports as file attachments too, using the same Feishu app API upload path.

### 2026-06-06 — unified_bullish_top20 宏观后置 + 评分公式统一 + YFRateLimitError 修复

**Files changed**:
- `stock_screener/rule_engine.py` — 新增 `evaluate_macro_rules_for_top20()` 方法
- `stock_screener/api/screen_service.py` — Top20 后置宏观评估 + 评分重算；`typing.Any` 导入
- `stock_screener/signal_analysis/models.py` — `UNIFIED_SCORE_WEIGHTS` → 0/0.40/0.30/0.20/0.10；`compute_unified_score` formula 字符串更新
- `stock_screener/market_intel/macro_scoring.py` — `DEFAULT_TECHNICAL_WEIGHT=0.0, DEFAULT_MACRO_WEIGHT=1.0`；`aggregate_rule_scores` 对 macro 规则优先取 `total_score`
- `stock_screener/market_intel/reporting.py` — `_single_score_rows` 权重列表 30/30/20/10/10 → 0/40/30/20/10
- `stock_screener/signal_analysis/chain.py` — `_build_score_with_formula` 改为委托 `compute_unified_score`；报告模板 §1.1/1.2/1.3 权重更新
- `stock_screener/yf_ratelimit.py` (新建) — 共享频控模块：`yf_sleep()`、`per_ticker_sleep()`、`retry_on_rate_limit`
- `stock_screener/potential_analysis/service.py` — chunk 50→25, delay 1.5s→3s+jitter, Tickers() 429 重试, fallback 加 per_ticker_sleep
- `stock_screener/sector_resolver.py` — `YahooFinanceSectorProvider` 逐只加 `per_ticker_sleep()`
- `stock_screener/potential_analysis/providers.py` — `GlobalMacroProvider` 5 只 ticker 间加 `per_ticker_sleep()`
- `stock_screener/kline_fetcher.py` — `yf.download()` 包裹 `@retry_on_rate_limit`
- `stock_screener/tests/test_unified_bullish_top20_hk02685.py` — 26 个测试覆盖技术+宏观规则评估
- `stock_screener/tests/test_e2e_unified_bullish_top20_scoring.py` — 15 个端到端测试覆盖 DB/CSV/报告 评分一致性
- `CLAUDE.md` — this entry

**Fixes applied**:

1. **unified_bullish_top20 链路**：全部股票 → 21 条技术规则 → Top20 → **仅对这 20 只** 执行 4 条宏观规则（`macro_factor_analysis`, `enterprise_potential_analysis`, `company_event_hot_sector_link`, `company_event_hot_news_link`）→ 合并 filter_details → 重算 final_score → 写 DB。`evaluate_bullish_technical_rules()` 和 `evaluate_macro_rules_for_top20()` 是独立方法，互不污染。

2. **评分公式统一**：`aggregate_rule_scores`（DB 层）→ final_score = 技术×0.0 + 宏观×1.0。`compute_unified_score`（报告层）→ 技术×0% + 五模块×40% + 事件热点×30% + 资金风险×20% + LLM×10%。两个公式口径在报告 §1.2 和 CSV "最终评分公式" 列中完整展示。

3. **aggregate_rule_scores priority fix**：对 `strategy_category="macro"` 规则，优先取 `details.total_score`（EnterprisePotentialAnalysis 的五模块综合分），其次取 `details.macro_score`（MarketIntelMacroScore 的宏观分）。修复了误将五模块宏观子分（macro=100）当作宏观总分的问题。

4. **YFRateLimitError 修复**：新建 `yf_ratelimit.py` 共享频控模块，所有 yfinance 调用点统一使用 `yf_sleep()` / `per_ticker_sleep()` / `retry_on_rate_limit`。chunk 50→25，delay 1.5s→3s+jitter，429 指数退避重试（5s/10s/20s）。

**⚠️ 后续改动注意事项**：
- 修改 `evaluate_bullish_technical_rules()` 或 `bullish_technical_rule_keys()` 时，不要引入 `strategy_category != "technical"` 之外的过滤条件，也不要移除现有条件——这 4 层过滤（enabled/strategy/category/direction）是 unified_bullish_top20 的核心约定
- 修改 `aggregate_rule_scores()` 时，保持 `total_score` > `macro_score` 的优先级顺序
- 修改 `UNIFIED_SCORE_WEIGHTS` 时，同步更新 `market_intel/reporting.py` 和 `signal_analysis/chain.py` 报告模板中的权重表格
- yfinance 调用不要移除 `per_ticker_sleep()` 或增大 chunk >25，否则会重新触发 429

### 2026-06-12 — 大盘分析热力图重构 + K线图渲染修复 + 板块成分股查询修复

**Files changed**:
- `stock_screener/web/sectors.py` — 热力图数据源从纯 `stock_kline_cache` 改为 `stock_pools`+`stock_kline_cache` 双源；新增 40+ 英文板块中文翻译；`_query_sector_stocks` 从 `JOIN` 改为 `LEFT JOIN` 并加 `DISTINCT` 去重；API 返回新增 `data_date` 字段
- `stock_screener/web/main.py` — startup 加 `db.init_schema("1d")` 初始化规则表；新增 `GET /api/stocks/search`、`GET /api/screening/single-stock/history/{code}`、`GET /api/screening/single-stock/{id}/progress` (SSE)；新增 `run_single_stock_web_job` 后台线程执行单股筛选
- `stock_screener/web_frontend/src/features/marketAnalysis/components/SectorTreemap.tsx` — 重写为 ECharts treemap，16:9 比例，红绿按涨跌着色，支持阈值颜色
- `stock_screener/web_frontend/src/features/marketAnalysis/components/KlineChart.tsx` — 新增 `markers` prop 支持买卖点叠加；时间格式 `slice(0,19)`→`slice(0,10)` 修复 lightweight-charts 日线 `setData` 异常
- `stock_screener/web_frontend/src/features/marketAnalysis/components/SectorStockTable.tsx` — 新增"日期"列
- `stock_screener/web_frontend/src/features/marketAnalysis/MarketAnalysisPage.tsx` — 集成 markers 到 KlineChart，显示数据日期
- `stock_screener/web_frontend/src/features/marketAnalysis/types.ts` — `SectorStock` 加 `date`、`HotSectorsResponse` 加 `data_date`
- `stock_screener/web_frontend/src/features/ruleEditor/` (新建) — React Flow v12 可视化规则链编辑器：`RuleChainEditor`、`RuleSidebar`、`RuleInspector`、`converter`(DSL↔nodes/edges)、6 种自定义节点
- `stock_screener/web_frontend/src/main.tsx` — `CodeScreening` 重写为单股票名称搜索+自动补全+K线+进度+报告面板；SSE 进度监听；`Rules` 组件集成可视化编辑器双模式
- `stock_screener/web_frontend/src/styles.css` — React Flow 黑金主题覆盖、进度条、自动补全下拉、节点卡片样式
- `stock_screener/db.py` — 新增 `search_stocks_by_name()`、`list_single_stock_runs_by_code()`
- `CLAUDE.md` — this entry

**Key protocol**: lightweight-charts 日线图 `setData()` 要求时间格式为 `YYYY-MM-DD`（纯日期），不接受 ISO datetime `YYYY-MM-DDTHH:MM:SS`。传错格式会抛 `Error: Invalid date string, expected format=yyyy-mm-dd`，K线静默不渲染。所有前后端 K 线数据协议必须统一使用 `YYYY-MM-DD` 格式。参见 `KlineChart.tsx` 中 `time.replace(' ','T').slice(0,10)` 的处理。

### 2026-06-13 — 个股筛选器 K 线诊断 + 数据状态 UI 重构 + 富途 OpenD 数据源

**Files changed**:
- `stock_screener/kline_fetcher.py` — `KlineFetcherFactory.create_fetcher_chain()` 新增 `skip_db_cache` 参数；新增 `OpenDQuotedKlineFetcher` 类支持 `FUTU_OPEN_HOST`/`FUTU_OPEN_PORT` env var 自动连接 OpenD
- `stock_screener/stock_terminal/providers/eastmoney.py` — `fetch_klines()` 重写错误收集：逐源收集失败原因（异常+空返回），抛 `"K-line fetch failed: YFinance: ...; AKShare: ..."`；调用 factory 时 `skip_db_cache=True` 避免重复查 `stock_kline_cache`
- `stock_screener/web_frontend/src/main.tsx` — 新增 `DataStatusBadge`（4态）、`formatScoreWithStatus()`、`dataSourceLabel()`、`ErrorRecoveryCard`（重试/切换周期/仅技术面/复制错误）、`DataDiagnosticPanel`（6项诊断）、`ScoreOverviewCards`（4卡评分）；`fetchKline()` 读取 `source_status.kline`
- `stock_screener/web_frontend/src/features/marketAnalysis/components/KlineChart.tsx` — Props 新增 `diagnostics`；空/错误状态多层展示；loading 骨架屏
- `stock_screener/web_frontend/src/styles.css` — 新增 ~210 行样式
- `stock_screener/tests/test_kline_diagnostics.py` (新建) — 10 测试覆盖 skip_db_cache、OpenD、错误消息诊断、HK.00700 集成

**Key notes**:
- K 线两条路径：Display（`StockTerminalService`）和 Analysis（`_fetch_single_kline()`）。Display 已在 `MySqlStockTerminalRepository` 查过缓存，eastmoney provider 必须 `skip_db_cache=True`。
- KlineFetcher 优先级：DB缓存 > Futu OpenD(env var) > YFinance > AKShare
- DataStatusBadge 四态：computed=蓝/not_computed=灰/missing=黄/error=红
- yfinance/AKShare 的 `fetch()` 内部 catch 异常后返回 None（不抛），必须额外检查空返回

### 2026-06-13 (2) — K线图重复时间戳导致 setData 异常 + YFinance 缺去重

**Files changed**:
- `stock_screener/web_frontend/src/features/marketAnalysis/components/KlineChart.tsx` — 时间格式化改为 timeframe 感知：日线/周线/月线用 `slice(0,10)`→`YYYY-MM-DD`，分钟线用 `slice(0,19)`→`YYYY-MM-DDTHH:MM:SS`；candle/volume/markers 三处统一修复
- `stock_screener/kline_fetcher.py` — `YFinanceKlineFetcher.fetch()` 新增 `drop_duplicates(subset=["date"])`（Futu 和 AKShare 已有，仅 YFinance 缺失）
- `CLAUDE.md` — this entry

**Root cause**: 2026-06-12 fix log 记录了 `slice(0,19)→slice(0,10)` 修复，但实际代码未执行此改动。日线数据传 `"2026-02-26T00:00:00"`（19字符）给 lightweight-charts，内部转为 Unix timestamp `1772064000`。若数据有重复日期（YFinance 无去重），两个 bar 时间戳相同→抛 `Assertion failed: data must be asc ordered by time`。

**Fix**: 
1. 前端根据 timeframe 选择时间格式：日线/周线/月线→`YYYY-MM-DD`，分钟线→保留时分秒
2. YFinance fetcher 补上 `drop_duplicates`，与 Futu(第527行)/AKShare(第527行) 一致

### 2026-06-15 — list_single_stock_runs_by_code IndexError + KlineChart 前端去重

**Files changed**:
- `stock_screener/db.py` — `list_single_stock_runs_by_code()` 列索引修复
- `stock_screener/web_frontend/src/features/marketAnalysis/components/KlineChart.tsx` — `applyData()` candle/volume 去重
- `CLAUDE.md` — this entry

**Fixes applied**:

1. **History API 500 修复** (`list_single_stock_runs_by_code`): SELECT 返回 15 列（索引 0-14），`created_at` 在索引 13，`finished_at` 在索引 14，但代码错误地使用了 `row[14]` 和 `row[15]`。`row[15]` 越界导致 `IndexError: tuple index out of range`。修正为 `row[13]` 和 `row[14]`。

2. **KlineChart 前端去重**: `toChartTime()` 对日线数据做 `slice(0,10)` 可能将同日期不同时间的多条 K 线映射为相同 chart time。lightweight-charts `setData()` 要求时间严格递增且无重复，否则抛 `Assertion failed: data must be asc ordered by time`。在 `applyData()` 中新增 `Set` 去重逻辑，保留首次出现的条目，并同步过滤 volume 数据。

### 2026-06-15 (2) — 市场特定默认规则链 + 规则中文名展示 + K线图规则标记

**Files changed**:
- `stock_screener/db.py` — 新增 `ZUOYI_WITH_MACRO_STRICT_EXPRESSION`、`ZUOYI_WITH_MACRO_ENHANCED_EXPRESSION`、`MARKET_DEFAULT_CHAIN_MAP`、`_MARKET_SPECIFIC_CHAIN_DEFS` 常量；`_default_rule_chain_expression_for_market()` 支持市场特定链；`seed_default_screening_rules()` 按市场插入特定默认链（priority=50）+ UPDATE 修复已有部署优先级
- `stock_screener/web/rule_chains.py` — `resolve_rule_chain()` 改为每个市场独立加载活跃链，支持不同市场不同默认链；多市场时返回 `per_market_chains` 字典
- `stock_screener/web/main.py` — `run_single_stock_web_job()` 中 `rule_details` 的 `rule_name` 优先从 DB 元数据表查找中文名
- `stock_screener/api/screen_service.py` — `filter_details` 新增 `rule_name` 字段（来自 `RuleMetadata.rule_name`）
- `stock_screener/web_frontend/src/features/marketAnalysis/components/KlineChart.tsx` — `TradeMarker` 接口扩展 `label`/`color`/`shape` 字段；标记渲染逻辑支持自定义样式
- `stock_screener/web_frontend/src/features/screeningReport/utils.ts` — 新增 `ruleMarkersFromDetails()` 从 `rule_details` 提取规则满足日期并映射为 K 线图标记
- `stock_screener/web_frontend/src/main.tsx` — 导入 `ruleMarkersFromDetails`，useMemo 计算规则标记，传入 `KlineChart` 的 `markers` prop
- `CLAUDE.md` — this entry

**Changes applied**:

1. **市场特定默认规则链**: A 股默认使用 `zuoyi_with_macro_strict`（宏观因子必达标），港股/美股默认使用 `zuoyi_with_macro_enhanced`（宏观因子兜底不阻断）。通过 `MARKET_DEFAULT_CHAIN_MAP` 映射 + `seed_default_screening_rules()` 中 priority=50 确保新部署和已有部署均生效。

2. **规则中文名展示**: 修复了 `rule_details` 中 `rule_name` 显示英文实现类名（如 `ZuoYiStrategizer`）的问题。在 `screen_service.py` 中新增 `rule_name` 字段从 `RuleMetadata` 获取中文名；在 `web/main.py` 中单股筛选通过 DB 元数据表查找中文名。

3. **K线图规则标记**: 点击开始筛选后，将满足的规则（左一突破日、EMA突破日、放量日、技术形态日等）以标记点形式叠加到 K 线图上。不同规则类型有不同颜色和形状（绿色箭头=看涨突破、蓝色圆=EMA突破、橙色方块=放量等），鼠标悬停可看到规则中文名。

### 2026-06-15 (3) — K线图工具栏 MA/VOL/MACD/全屏 + volumeSeries 去重修复

**Files changed**:
- `stock_screener/web_frontend/src/features/marketAnalysis/components/KlineChart.tsx` — 重写支持 MA 均线、MACD 副图、成交量显隐；新增文件头去重铁律注释；`_applyAll()` volume 去重；`_syncMA()` 尾行去重兜底
- `stock_screener/web_frontend/src/main.tsx` — 新增 `showMA`/`showVolume`/`showMACD` 状态；工具栏按钮联动（高亮+点击切换）；全屏按钮使用 Fullscreen API；KlineChart 传入新 props
- `stock_screener/web_frontend/src/styles.css` — `.kline-tool-btn.active` 高亮样式 + `.kline-panel-wrap:fullscreen` 全屏样式
- `CLAUDE.md` — this entry

**Changes applied**:

1. **MA 均线**: 点击 MA 按钮切换 MA5/MA10/MA20/MA60 四条 SMA 均线，叠加在蜡烛图价格轴上。颜色：黄/橙/紫/蓝。

2. **VOL 成交量**: 点击 VOL 按钮切换成交量柱状图显示/隐藏（`series.applyOptions({ visible })`），默认开启。

3. **MACD 副图**: 点击 MACD 按钮添加 MACD 副图（DIF 黄线 + DEA 蓝线 + 红绿柱状图），激活时蜡烛图自动压缩腾出空间。

4. **全屏**: 点击 ⛶ 按钮使用 `element.requestFullscreen()` / `document.exitFullscreen()` 切换。

5. **volumeSeries 去重修复**: `_applyAll()` 中 volumeData 用 `candleTimeSet.has()` 过滤行但未去重——两个同日期 row 都通过检查导致 `setData()` 抛 `"data must be asc ordered by time"`。新增 `volSeen` Set 去重。

6. **记忆沉淀**: 创建 `memory/kline-chart-dedup-rule.md`，记录去重铁律及四个 setData 调用点的去重要求。KlineChart.tsx 文件头添加醒目注释引用该记忆文件。

### 2026-06-16 — EnergyPhaseClassifier 六态能量相位分类器

**Files changed**:
- `stock_screener/strategy.py` — 新增 `EnergyPhaseAnalysis` dataclass + `analyze_energy_phases()` 函数（~160行）；新增 `import numpy as np`
- `stock_screener/strategizers.py` — 新增 `EnergyPhaseClassifier` 类；更新双路径 imports
- `stock_screener/rule_engine.py` — 注册 `EnergyPhaseClassifier` 到 `RuleRegistry.default()` + `KLINE_IMPLEMENTATIONS`；更新双路径 imports
- `stock_screener/db.py` — `DEFAULT_RULE_METADATA` 新增 `energy_phase_bullish` 条目（display_order=180, direction=bullish, signal_group=bullish）
- `stock_screener/tests/test_energy_phase_classifier.py` — 新建，16 个测试覆盖六态+边界
- `stock_screener/tests/test_unified_bullish_top20_hk02685.py` — 技术规则计数 21→22
- `stock_screener/tests/test_e2e_unified_bullish_top20_scoring.py` — 技术规则计数 21→22
- `CLAUDE.md` — this entry

**Fixes applied**:

1. **六态能量相位框架**: 基于物理势能/动能隐喻，定义了股票价格运动的六种状态：COMPRESS（势能积蓄，观望）、RELEASE（势能释放→动能转化，买入）、TRENDING（动能持续，持有）、EXHAUSTION（动能衰竭，预警）、PEAK（到顶，卖出）、CRASH（空方动能，回避）。

2. **核心指标计算**: `analyze_energy_phases()` 计算 8 个连续能量指标：
   - `KE_signed = sign(ret%) × ret%²`（有向动能，平方放大极端波动）
   - `PE_norm = ((close − MA20) / MA20 × 100)²`（距均线的百分比偏离平方）
   - `KE_decay = 1 − KE/KE_peak`（动能衰减率）
   - `KE_consistency`（正动能比例，10日窗口）
   - `DeltaE_5`（5日能量转化速率）
   - `KE_path`（10日累积动能）
   - `EPR = |KE|/PE`（能量配分比）
   - `ke_negative_streak`（连续负动能天数）

3. **RELEASE 判定**: 移除了原始的 `PE_falling` 要求（快速突破会暂时推高PE，MA来不及跟上），仅需 `KE_signed>0 + DeltaE_5>10 + EPR>0.1`。覆盖底部反弹和突破确认两种场景。

4. **TRENDING 判定**: 用 `KE_path>0` 替代了 `KE_decay<0.5`（稳态上升中 KE_decay 因滚动峰值的比率问题不稳定），配合 `KE_consistency>0.7 + KE_signed>0 + PE_norm<100`。正确识别慢牛。

5. **Auto-discovery**: `energy_phase_bullish` 规则通过 `direction="bullish"` + `strategy_category="technical"` 自动被 `bullish_technical_rule_keys()` 发现，无需修改 unified_bullish_top20 链表达式。当前为第 22 条看涨技术规则。

**⚠️ 后续改动注意事项**:
- 修改 `analyze_energy_phases()` 的状态判定阈值时，注意优先级顺序（CRASH > PEAK > EXHAUSTION > RELEASE > TRENDING > COMPRESS）
- 新增 bullish 技术规则时，同步更新 `test_unified_bullish_top20_hk02685.py` 和 `test_e2e_unified_bullish_top20_scoring.py` 中的规则计数断言
- `EnergyPhaseClassifier` 需要至少 30 根 K 线（默认 min_rows=30），短于 30 天的股票返回 UNKNOWN

6. **记忆沉淀**: 创建 `memory/kline-chart-dedup-rule.md`，记录去重铁律及四个 setData 调用点的去重要求。KlineChart.tsx 文件头添加醒目注释引用该记忆文件。

### 2026-06-16 — 规则链保存 failure: `*` 通配符 timeframe 在 URL 路径中 + UPDATE/DELETE 缺乏兜底

**Files changed**:
- `stock_screener/db.py` — `update_screening_rule_chain` + `delete_screening_rule_chain`：timeframe 匹配从严格相等改为 `IN (%s, '*') ORDER BY CASE WHEN ... LIMIT 1`（与 SELECT 一致）
- `stock_screener/web_frontend/src/main.tsx` — `saveChain` + `deleteChain`：URL 路径中 `editor.timeframe === '*'` 时使用页面级 `timeframe` 替代
- `CLAUDE.md` — this entry

**Root cause**: All chains in DB use `timeframe='*'` (wildcard, meaning "applicable to all timeframes"). When the frontend loads a chain, `editor.timeframe` becomes `'*'`. On save, the URL path becomes `/api/rules/chains/HK/*/unified_bullish_top20`. While `validate_rule_chain_timeframe` accepts `'*'`, the `update_screening_rule_chain`/`delete_screening_rule_chain` methods used strict `WHERE timeframe=%s` — inconsistent with SELECT methods (`get_screening_rule_chain`, `get_active_screening_rule_chain`, `list_screening_rule_chains`) which all use `WHERE timeframe IN (%s, '*')` with CASE WHEN ordering.

**Fixes applied**:

1. **Backend UPDATE/DELETE timeframe fallback** (`db.py:4774-4808`): Both `update_screening_rule_chain` and `delete_screening_rule_chain` now use:
   ```sql
   WHERE market=%s AND chain_key=%s AND timeframe IN (%s, '*')
   ORDER BY CASE WHEN timeframe=%s THEN 0 ELSE 1 END
   LIMIT 1
   ```
   Same priority logic as SELECT. If the request passes `timeframe='1d'` but only a `'*'` wildcard chain exists, the update matches the wildcard. If both `'1d'` and `'*'` chains exist, exact match wins (CASE=0 → first).

2. **Frontend URL normalization** (`main.tsx:1938-1940, 1963`): `saveChain` and `deleteChain` now compute `effectiveTimeframe = editor.timeframe === '*' ? timeframe : editor.timeframe`. The `timeframe` variable is the page-level state (always a concrete value like `'1d'`/`'1wk'`/`'1mo'`). This prevents `*` from appearing in URL paths while the backend fallback ensures correct DB row targeting regardless.

**⚠️ 后续改动注意事项**:
- 不要在 URL 路径中使用 `*` 作为 timeframe — 前端已标准化处理
- `update_screening_rule_chain` / `delete_screening_rule_chain` 中的 `ORDER BY ... LIMIT 1` 确保有重复 chain_key（不同 timeframe）时只更新/删除一条
- 如果需要创建不同 timeframe 的同名 chain，使用 POST（新建）后用不同 timeframe 区分

### 2026-06-17 — EnergyPhaseClassifier 参数化 + 市场预设优化

**Files changed**:
- `stock_screener/strategy.py` — `analyze_energy_phases()` 新增 9 个状态判定参数；新增 `MARKET_ENERGY_PARAMS` 字典 + `get_market_energy_params()` 函数
- `stock_screener/strategizers.py` — `EnergyPhaseClassifier` 新增 `market` 参数 + 9 个状态判定参数 + `_coalesce` 解析链（显式值 > 市场预设 > 默认值）
- `stock_screener/tests/test_energy_phase_classifier.py` — 新增 16 个测试（参数传递、市场预设、边界条件）
- `stock_screener/tests/backtest_energy_phase.py` — 新建回测脚本，支持多参数集对比
- `memory/energy-phase-classifier.md` — 更新参数表和 HK/US/A 市场预设文档
- `CLAUDE.md` — this entry

**改动动机**: HK.800000 回测表现不佳。根因：KE（动能 ∝ ret²）与日收益率平方成正比，HK 低波动市场（日均 ~1-1.5%）的 KE 天然比 A 股（~2-3%）小 4-9 倍。固定阈值对 HK 过严，导致 RELEASE/TRENDING 信号过少。

**Fixes applied**:

1. **9 个硬编码阈值全部参数化**: CRASH(`crash_neg_streak=5`, `crash_ke_path=-20`)、PEAK(`peak_pe_threshold=80`, `peak_ke_silence=1.0`, `peak_delta_e=-10`)、RELEASE(`release_delta_e=10`)、TRENDING(`trending_consistency=0.7`)、COMPRESS(`compress_consistency=0.3`, `compress_ke_path=0.0`) 全部变为函数参数。

2. **市场特定预设 (MARKET_ENERGY_PARAMS)**:
   - **HK**（低波动）: `release_delta_e=5.0`, `trending_consistency=0.6`, `ke_threshold=2.5`, `crash_ke_path=-12`, `crash_neg_streak=4`, `peak_ke_silence=0.6`, `peak_delta_e=-6` — 降低阈值使低波动环境下也能捕获有效信号
   - **US**: 默认附近微调 (`release_delta_e=8.0`, `trending_consistency=0.65`)
   - **A**（高波动）: `release_delta_e=12.0`, `trending_consistency=0.75`, `ke_threshold=5.0`, `crash_ke_path=-25`, `crash_neg_streak=6` — 提高阈值过滤假突破

3. **EnergyPhaseClassifier(market="HK") 自动加载**: 构造时传 `market="HK"` 自动应用 HK 低波动预设，显式参数可覆盖市场预设。

4. **回测脚本**: `tests/backtest_energy_phase.py` 支持 `--code HK.800000 --market HK --compare` 对比 5 组参数集（默认/优化/激进/保守/短周期），输出信号频率、N日胜率、均收益、MAE、盈亏比。

**⚠️ 后续改动注意事项**:
- 修改 `analyze_energy_phases()` 状态判定逻辑时，确保所有阈值都来自参数名而非硬编码数字
- 新增市场预设时，添加到 `MARKET_ENERGY_PARAMS` 字典而非修改默认参数
- `EnergyPhaseClassifier` 需要 `market` 参数时，在 `RuleRegistry.create()` 的 params dict 中传入 `"market": "HK"`
- 回测时用 `--compare` 对比多组参数，关注 10 日胜率和盈亏比（10 日窗口最稳定）
- 所有参数变化必须在 details dict 中体现（用于 CSV 报告和 LLM 分析消费）

### 2026-06-18 — unified_bullish_top20 优化：双评分体系 + 6个持有层规则 + 报告重写

**Files changed**:
- `stock_screener/scoring/` (新建) — `__init__.py`, `models.py`, `constants.py`, `entry_scorer.py`, `holding_scorer.py`, `market_cache.py`
- `stock_screener/signal_analysis/renderers.py` (新建) — `RetailReportRenderer`
- `stock_screener/signal_analysis/hot_sectors.py` — 新增 `HotSectorClassifier`
- `stock_screener/strategizers.py` — 新增 6 个持有层 macro Strategizer + 5 个 EntryScore 聚合器
- `stock_screener/strategy.py` — 无变更（新规则计算在 market_cache.py 中）
- `stock_screener/rule_engine.py` — `RuleRegistry` 注册 11 条新规则；`evaluate_macro_rules_for_top20()` 支持 `market_cache` 注入
- `stock_screener/db.py` — `DEFAULT_RULE_METADATA` 移除 3 条 + 新增 11 条；`_migrate_add_scoring_columns`
- `stock_screener/api/screen_service.py` — 集成 MarketCache + EntryScorer + HoldingScorer；Top20 排序改为 entry_score
- `stock_screener/signal_analysis/chain.py` — 切换到 RetailReportRenderer（Feature flag `USE_NEW_RENDERER`）；加载 MarketTemperature
- `stock_screener/market_intel/providers/base.py` — P2 `MacroDataProvider` ABC 接口预留
- `stock_screener/market_intel/macro_scoring.py` — `aggregate_rule_scores` 标记 deprecated
- `stock_screener/market_intel/reporting.py` — `render_multi_stock_report` 标记 deprecated
- `stock_screener/signal_analysis/models.py` — `UNIFIED_SCORE_WEIGHTS` / `compute_unified_score` 标记 deprecated
- `stock_screener/tests/` — 新增 6 个测试文件（~150 个新测试）
- `CLAUDE.md` — this entry

**Fixes applied**:

1. **双评分体系** (`scoring/` 模块): 拆分 `entry_score`（入场信号，5模块加权）和 `holding_score`（持有价值，6维度加权）。RuleEngine 不变，EntryScorer/HoldingScorer 只消费其 filter_details 输出。entry_score 用于 Top20 排序（替代 total_match_count）。Feature flag `USE_NEW_SCORING=0` 可回退。

2. **6 个持有层宏观规则**: 
   - `CreditRiskRegime`（YFinance HYG/LQD/VIX ETF代理，纯算法）
   - `MarketBreadth`（stock_kline_cache 表查询全市场 MA50/MA200/52周新高新低）
   - `LiquidityNowcast`（A股 AKShare 两融+北向 / 港股南向+恒指 / 美股 VIX+SPY）
   - `EarningsRevisionMomentum`（个股级，Tavily搜索+关键词评分）
   - `PolicyEventRisk`（市场级，Tavily+LLM结构化）
   - `CommodityShock`（YFinance 5种大宗商品期货监控）
   5 个市场级规则通过 `MarketCache` 预计算缓存60分钟，`EarningsRevision` 逐只股票（仅Top20）执行。

3. **报告重写**: `RetailReportRenderer` 10-section 散户友好模板。市场温度计（5项emoji指标）、热点三分类、Top20简表（7列）、个股卡片（买点/持有/风险/观察点）、分类型建议（短线/中线/不追高）、5层风险提示。Feature flag `USE_NEW_RENDERER=0` 回退旧模板。

4. **热点三分类**: `HotSectorClassifier` 分行业（127个中文行业名）/主题（59个概念关键词）/地域（17个政策催化区域）。普通省份名无催化则过滤。

5. **规则去冗**: 移除 `rsi_overbought` / `daily_drop_6_65` / `rsi_oversold` 3条无用/冗余规则。注册 11 条新规则（6 holding + 5 scoring聚合器）。

6. **DB 扩展**: `screening_results` 表新增 8 字段：entry_score, entry_decision, holding_score, holding_decision, holding_period, position_suggestion, risk_level, score_formula。

7. **P2 接口预留**: `market_intel/providers/base.py` 定义 `MacroDataProvider` ABC（`get_credit_spread` / `get_financial_conditions` / `get_analyst_estimates`），供后续 FRED/Tushare Pro 实现。

**⚠️ 后续改动注意事项**:
- `RULE_TO_MODULE_MAP` 是 list-of-tuples 格式 `[(rule_key, module, weight), ...]`，同一 rule_key 可映射多个模块。不要改回 dict。
- 新增技术规则如需参与 entry_score，在 `RULE_TO_MODULE_MAP` 中添加 `(rule_key, module, weight)` 条目
- MarketCache 的市场级规则不逐只股票调用，结果通过 `MarketTemperature.to_filter_details()` 注入
- 报告和评分的 Feature flag 默认启用，`USE_NEW_SCORING=0` / `USE_NEW_RENDERER=0` 可回退
- 修改 `scoring/models.py` 中的 dataclass 时，确认 `to_filter_details()` 和 `HoldingScorer._extract_*()` 的字段引用同步更新

### 2026-06-18 — py_mini_racer 并发初始化 V8 崩溃修复

**Files changed**:
- `stock_screener/web/main.py` — 添加 py_mini_racer V8 预初始化块（load_dotenv 之后，FastAPI app 创建之前）
- `stock_screener/interactive_screening.py` — 同上，import 块之后，常量定义之前
- `stock_screener/desktop.py` — 同上，`_ensure_dotenv()` 之后，uvicorn 启动之前
- `CLAUDE.md` — this entry

**Root cause**: `py_mini_racer 0.14.1` 封装的 V8 引擎**并发初始化不安全**。`akshare` 的 `bond_zh_hs_daily`/`futures_zh_sina` 等函数首次调用时创建 `MiniRacer()` 实例，触发 `init_mini_racer()` → V8 platform 初始化。当多个 HTTP 请求线程同时首次调用 akshare 时，多线程并发进入 V8 的 `address_pool_manager` 初始化，`CHECK(!pool->IsInitialized())` 断言失败 → SIGTRAP → 进程崩溃。

**Reproduction**: 5 个线程并发调用 `akshare.bond_zh_hs_daily()` 可 100% 复现，exit code 133 (SIGTRAP)。

**Fix**: 在所有入口点（`web/main.py`、`interactive_screening.py`、`desktop.py`）的模块初始化阶段（单线程、任何 worker 线程启动前）预先创建一个 `MiniRacer` 实例。这会触发 `init_mini_racer()` 将 V8 的 `_is_initialized` 标志设为 True，后续所有线程的 `MiniRacer()` 调用都会短路返回，不再触发原生初始化。预 init 包裹在 `try/except` 中，`py_mini_racer` 未安装时静默通过。

**⚠️ 后续改动注意事项**:
- 新增入口点文件时（如 CLI 工具、cron 脚本），如果其线程可能访问 akshare，必须添加同样的 V8 预 init 块
- 如果升级 `py_mini_racer`，验证新版本是否已修复此并发缺陷——如已修复可移除预 init
- `akshare` 的 `futures_zh_sina`/`bond_zh_sina`/`movie_yien`/`video_yien`/`artist_yien` 五个模块使用了 `py_mini_racer`，均在 `akshare/__init__.py` 中被 eager import

### 2026-06-18 — YFinance 401 "Invalid Crumb" 会话恢复修复

**Files changed**:
- `stock_screener/yf_ratelimit.py` — 新增 `_is_crumb_error()` + `reset_yf_session()` 两个 helper
- `stock_screener/potential_analysis/service.py` — `prefetch_batch` 401 crumb 错误时先重置 YfData 单例重试，再降级
- `stock_screener/kline_fetcher.py` — `YFinanceKlineFetcher.fetch()` 401 crumb 错误时重置 YfData 单例重试一次
- `CLAUDE.md` — this entry

**Root cause**: yfinance 库的 `YfData` 是进程级单例（SingletonMeta），所有 `Ticker`/`Tickers`/`download` 共享同一个 HTTP session、cookie、crumb。在批量处理 ~380 只股票（约 30 次 `Tickers()` 调用 × 每 chunk 25 只的内部 API 调用）后，Yahoo 主动使 cookie/crumb 失效，后续所有请求返回 401 "Invalid Crumb"。yfinance 内建的 cookie-strategy-toggle 重试（basic↔csrf）不足以恢复——需要**全新的 HTTP session**。

**Cascading impact**: 一旦 YfData session 损坏：
1. `prefetch_batch` → `_is_fatal_http_error` 检测到 401 → 立即设置 `quota_exhausted=True` → 剩余全部股票降级到 Futu 兜底
2. `YFinanceKlineFetcher` → `yf.download()` 失败 → 返回 None → 降级到 AKShare
3. 所有后续 yfinance 调用（sector resolver、macro provider 等）全部失败

**Fix**:
1. `yf_ratelimit.py` 新增：
   - `_is_crumb_error(exc)` — 区分 "Invalid Crumb"（可恢复）和 "User unable to access"（IP 封禁，不可恢复）
   - `reset_yf_session()` — 清除 `YfData._instances[YfData]`，强制下次调用创建全新 session + cookie + crumb
2. `prefetch_batch` retry loop：401 crumb 错误时调用 `reset_yf_session()` 后重试一次（非 crumb 的 401 仍走原降级路径）
3. `YFinanceKlineFetcher.fetch()`：`_download()` 抛 401 crumb 错误时重置 session 后重试一次

**Verification**: 单例重置后 `yf.Ticker('MSFT').info` 返回 184 字段，currentPrice=378.91，新 crumb 与旧不同。10 个 K-line 诊断测试全部通过。

**⚠️ 后续改动注意事项**:
- 新增 yfinance 调用点时，考虑用 `try/except` + `_is_crumb_error` + `reset_yf_session` 模式包裹
- 不要移除 `_is_fatal_http_error`——它仍然正确识别真正的 IP 级封禁（"User is unable to access this feature"）
- `reset_yf_session()` 是幂等的——单例为空时返回 False 不报错
- 批处理时间越长（>500 只股票），YfData session 失效概率越高；chunk=25 的当前配置下约每 350-400 只会遇到一次

### 2026-06-22 — 港股报告数据缺失系统性调试：7 个问题根因定位与修复

**调试方法论**: 采用 systematic debugging 四阶段流程（Root Cause → Pattern → Hypothesis → Fix），逐只股票验证数据流。详见 `memory/debugging-methodology-and-known-pitfalls.md`。

**Files changed**:
- `stock_screener/kline_fetcher.py` — `DatabaseKlineFetcher` 新增 `max_staleness_days=4` 新鲜度门控
- `stock_screener/signal_analysis/chain.py` — `_build_hot_sectors()` 替换为 `HotSectorClassifier.classify()`；`ResolveHotSectorsStep` 使用 `find_hot_sectors_or_defaults()`
- `stock_screener/signal_analysis/search_providers.py` — `TavilySearchProvider` 新增 `_quota_exhausted` 跨批次配额管理；`FallbackSearchProvider` 动态 `_active_providers()`
- `stock_screener/main_force_risk.py` — K 线最低行数 20→10；`_level()` 区分真正 insufficient vs 分析仅供参考
- `stock_screener/potential_analysis/builders.py` — `_snapshot_is_empty()` 新增字段级完整性检查（PE=None 但有市值/价格 → 触发 Futu 兜底）
- `stock_screener/scoring/constants.py` — `SCALE_FACTOR` 12→15；`STRONG_BUY` 阈值 80→75
- `stock_screener/signal_analysis/renderers.py` — 中线组阈值 65→55；强入场阈值 80→75
- `stock_screener/scoring/market_cache.py` — `_build_summaries()` 记录 `dimension_sources` 数据来源状态
- `stock_screener/scoring/models.py` — `MarketTemperature` 新增 `dimension_sources` 字段
- `memory/debugging-methodology-and-known-pitfalls.md` (新建) — 6 个已知 pitfall 模式 + 排查命令

**Fixes applied**:

1. **P0 — DB 缓存新鲜度缺失** (`DatabaseKlineFetcher`): DB 缓存在 fetcher 链中优先级最高，但只检查行数不检查日期新鲜度。HK.00470/00600 等小盘股 DB 缓存停在 2026-06-04（17 天过期），直接挡住 YFinance 的 2026-06-18 新鲜数据。修复：`fetch()` 中检查 `normalized["date"].max()`，超过 `max_staleness_days`（默认 4 天，覆盖周末+节假日）返回 None 触发降级。**这是"数据不足"的首要根因——Issue 1 修复后，Issues 3/4 自然缓解**。

2. **P0 — HotSectorClassifier 从未被调用**: 分类器（127 行业 + 59 主题 + 17 地域）完整实现且有 25 个测试，但 `chain.py:1435` 的 `_build_hot_sectors()` 是临时存根（`return {"industry": [...], "theme": [], "region": []}`）。修复：替换为 `HotSectorClassifier().classify()`，theme/region 桶不再永远为空。`hot_clarity_summary` 从硬编码改为基于分类结果动态计算。

3. **P0 — Tavily API 配额耗尽无跨批次管理**: Tavily 前几批耗尽配额后，后续所有批次仍尝试调用并立即 429 失败。修复：`_quota_exhausted` 标志 + 连续 3 次 429 → `is_available=False` + `FallbackSearchProvider._active_providers()` 动态跳过。

4. **P1 — K 线最低行数 20 过严**: 降低到 10，10-19 行标注 "数据较少，分析仅供参考"。注意：真正根因是 Issue 1（DB 缓存过期），降低阈值只是防御性措施。

5. **P1 — PE 字段级空值被 provider_status 掩盖**: `_snapshot_is_empty()` 只检查 status 是否为 error，不检查字段。爱芯元智 YFinance 返回 ok 但 `trailingPE=None`。修复：增加字段级检查，PE 缺失但有市值/价格 → 触发 Futu 兜底。

6. **P1 — 中线关注阈值不匹配**: holding_score 实际分布 40-55，阈值 65 永远达不到。修复：65→55。

7. **P2 — 入场分分布保守**: `SCALE_FACTOR=12` + `STRONG_BUY≥80` 导致 3-4 条规则命中只能到 65-75。修复：SCALE_FACTOR→15，STRONG_BUY→75。

**⚠️ 后续改动注意事项**:
- **每次 debug 前必读** `memory/debugging-methodology-and-known-pitfalls.md`，检查是否匹配已知 pitfall 模式
- 新增数据缓存层时，必须同时添加新鲜度门控（`max_staleness_days`），否则会引入 Pitfall 1
- 新增功能模块后，确认生产代码路径确实调用了它（`grep -rn "ClassName" --include="*.py" | grep -v test`），避免 Pitfall 2
- 新增 API provider 时，添加跨请求的配额/错误状态管理（`_quota_exhausted` / `_consecutive_errors`），避免 Pitfall 3
- 修改评分阈值时，先取实际数据分布验证阈值是否可达（`score_distribution = [s.holding_score for s in stocks]; print(np.percentile(score_distribution, [50, 75, 90])`），避免 Pitfall 4
- 数据完整性检查要看字段值，不能只看 provider_status，避免 Pitfall 5
- K 线分析阈值调整前，先排查 DB 缓存是否过期（Pitfall 1 → 6 的因果链）
- DB 缓存新鲜度默认 4 天（`max_staleness_days=4`），如需调整通过 `DatabaseKlineFetcher(db, max_staleness_days=N)` 传入

### 2026-06-22 — 动量轮动策略：daily/周频调仓 + 连续确认 + 前端 select/bool 参数支持

**Files changed**:
- `stock_screener/quant_lab/strategies/momentum_rotation.py` — Config 新增 `rebalance_frequency`/`consecutive_days`；`_monthly_snapshots()`→`_rebalance_dates()` 支持 daily/weekly/monthly；Runner 和信号生成器新增 out_of_top_streak/in_top_streak 连续确认追踪
- `stock_screener/quant_lab/service.py` — `_run_momentum_rotation_backtest()` 传递新参数；**修复 P0 bug：params 嵌套层级错误（见下方）**
- `stock_screener/tests/backtest_momentum_rotation.py` — 新增 `--frequency`/`--consecutive-days` CLI 参数
- `stock_screener/web_frontend/src/features/quant/types.ts` — `StrategyParamDef` 新增 `'select'`/`'bool'` 类型，`params` 类型扩到 `number|string|boolean`
- `stock_screener/web_frontend/src/features/quant/QuantLab.tsx` — 新增 select 下拉/bool checkbox 渲染分支，修复 `Record<string, number>`→`Record<string, number|string|boolean>`
- `memory/e2e-testing-discipline.md` (新建) — E2E 测试铁律
- `CLAUDE.md` — this entry

**Fixes applied**:

1. **动量轮动日频/周频调仓**: `_rebalance_dates()` 按 frequency 分组取快照日期。daily 返回全部交易日，weekly 取每周最后交易日，monthly 取月末（原行为）。

2. **连续确认防 whipsaw（方案 C）**: 买卖不再即时触发。`out_of_top_streak[sym]` 追踪持仓股连续不在 TopN 的天数，`in_top_streak[sym]` 追踪候选股连续在 TopN 的天数，达到 `consecutive_days` 阈值才执行。`consecutive_days=1` 等价于旧行为（立即触发）。

3. **P0 — 后端参数嵌套层级错误** (`service.py:253`): 前端发送 `strategy.params.rebalance_frequency`，但后端从 `payload.get("strategy")` 直接读 `.get("rebalance_frequency")`——在 strategy 层级找 params 层级的键，永远找不到，回退到默认值。所有旧参数"正常"仅因为默认值恰好与前端一致。修复：`strategy_params = raw_strategy.get("params") or {}`，从正确的嵌套层级读取。

4. **前端 select 值不更新** (QuantLab.tsx): `params` state 类型为 `Record<string, number>`，select 参数值是 string，TypeScript 类型不匹配导致 React state 更新异常。修复：类型扩到 `Record<string, number | string | boolean>`。

5. **bool 参数渲染为空**: `use_market_filter`（type="bool"）无渲染分支，fallback 到 `<input type="number">`，value 显示为空。修复：新增 checkbox 渲染分支，checked 绑定 bool 值。

**⚠️ 后续改动注意事项**:
- **E2E 测试铁律**：必须验证 页面输入→HTTP body→后端解析→处理逻辑消费→响应→页面渲染 全链路。DOM 验证不算 E2E。最可靠的验证是**对比测试**——用不同参数发两个请求，确认结果有对应差异。详见 `memory/e2e-testing-discipline.md`
- 新增策略参数时，确认后端从 `payload["strategy"]["params"]` 读取，不是从 `payload["strategy"]` 直接读
- 新增非 number 类型的策略参数时，同步更新前端 `types.ts`（type 联合）、`QuantLab.tsx`（渲染分支）、`params` state 类型
- `_rebalance_dates()` 的 `as_of_date` 参数现在排除的是整日（`df["date"] < ref`），不再是整月。daily 频率下行为正确；monthly 频率下当前不完整月份的所有交易日都被排除（同旧行为）
- `out_of_top_streak`/`in_top_streak` 在市场破 EMA200 清仓时会 reset，避免空仓期间的 streak 积累

### 2026-06-26 — 产业拓扑 US.NVDA E2E 验收：schema 迁移 + 画布交互 + quote 刷新节流

**Files changed**:
- `stock_screener/db.py` — `industry_relations` 新增 `peer_market_cap` / `peer_market_cap_str` 建表字段和幂等旧库迁移
- `stock_screener/sql/industry_topology.sql` — 同步新增两个市值缓存字段
- `stock_screener/web_frontend/src/features/industryTopology/TopologyCanvas.tsx` — G6 `aftertransform` zoom 读取防御式处理
- `stock_screener/web_frontend/src/features/industryTopology/IndustryTopologyPanel.tsx` — graph 轮询合并既有 quote 状态，仅对初始/新增节点触发行情刷新
- `stock_screener/web_frontend/src/styles.css` — 产业拓扑工具栏/过滤栏窄视口响应式修复

**Root cause**:
1. 关系缓存代码已读写 `peer_market_cap_str`，但旧库 `industry_relations` 没有该列，且 `CREATE TABLE IF NOT EXISTS` 不会迁移旧表，导致 `/api/topology/graph` 对 US.NVDA 直接 500。
2. G6 v5 在画布初始化/transform 阶段可能先触发 `aftertransform`，此时 `graph.getZoom()` 内部 viewport 尚未就绪，控制台报 `Cannot read properties of undefined (reading 'getZoom')`。
3. 关系状态为 `generating` 时，前端每次 `/graph` 轮询都会再次 `quotes/refresh` 全量节点；同时 `/graph` 返回的 pending 节点覆盖已缓存 quote，详情面板持续显示 pending。
4. 窄视口下 `.topo-toolbar` 不换行，按钮和状态文本撑宽页面，内置浏览器验收出现横向裁切。

**Fixes applied**:
- 建表 SQL 和 Python init schema 同步新增 `peer_market_cap` / `peer_market_cap_str`，并用 `SELECT column` 探测 + `ALTER TABLE` 做幂等旧库迁移。
- `aftertransform` 中用 `try/catch` 包住 `graph.getZoom()`，避免 G6 早期 transform 事件污染控制台。
- `applyGraph()` 合并 `nodesRef.current` 中 terminal quote 状态；行情刷新只针对 reset 初始图或新增节点，避免 relation polling 引发刷新风暴。
- 拓扑容器增加 `min-width:0`，工具栏/过滤栏 `flex-wrap`，输入框自适应，按钮保持横排。

**Verification**:
- `npm run build` 通过；`python3 -m py_compile db.py industry_topology/*.py web/topology.py` 通过。
- `POST /api/topology/graph {"code":"US.NVDA","market":"US","depth":3}` 返回 23 nodes / 33 edges。
- 内置浏览器 E2E：点击“产业拓扑”→搜索 US.NVDA→选择 NVIDIA→开始拓扑→画布非空→节点详情可打开；控制台无新 error/warning。

**⚠️ 后续改动注意事项**:
- 修改 `industry_relations` 字段时必须同时更新 `sql/industry_topology.sql` 和 `MarketDatabase.init_industry_topology_schema()`，并考虑旧库 ALTER。
- 关系轮询期间不要全量刷新行情；只刷新新节点或显式 force refresh。
- 图谱接口当前 graph payload 里的 quote 字段仍可能是 pending，前端依赖 `/quotes` 批量接口补齐；如要服务端直接返回 cached quote，需要在 `TopologyService` 聚合层处理。

### 2026-08-05 — 进程复用与运行时资源生命周期（每次改动必检）

**适用范围**：任何代码改动都必须考虑当前代码是在一次性 CLI、长时间筛选任务、后台 worker、Web 服务进程、桌面进程，还是多线程/多进程环境中运行。不要默认“函数返回后进程状态会自动恢复”。

**本次根因**：全市场筛选会顺序处理 1,000+ 标的。旧的 `YFinanceKlineFetcher` 对每只标的调用 `yf.download()` 时都让 yfinance 新建 HTTP session；yfinance 的进程级 `YfData` 单例替换 session 时未关闭旧 session，文件描述符随标的数增长，最终导致 yfinance SQLite cache 报 `OperationalError('unable to open database file')`，并可能继续演变为 `Errno 24: Too many open files`。

**已建立的所有权边界**：
- `YFinanceKlineFetcher` 懒创建并复用一个 session，fetcher 自己拥有的 session 由 `close()` 释放。
- `api.screen_service.run_screening_task()` 与 `MainForceRiskService.close()` 必须在 `finally`/收尾路径关闭其创建的 fetcher 链；异常路径同样适用。
- 单标的 K 线接口不应创建无收益 worker；传入受管理 session，并使用 `threads=False`。

**后续改动强制检查项**：
1. 写代码前，列出新增或修改对象的生命周期与所有者：HTTP/DB session、文件、线程池、Futu context、缓存、定时器、子进程等由谁创建、复用、关闭。
2. 长循环、后台任务和 Web/桌面常驻进程中，禁止每次迭代隐式新建资源；必须复用受所有者管理的实例，并在最外层 `finally` 清理。
3. 新增入口点、worker 或并发路径时，确认资源初始化是否线程安全，关闭是否幂等，并补充成功与异常退出的释放测试；必要时采样 FD/连接/线程数验证不会随请求或标的数线性增长。
4. 源码修改不会热加载到已经运行的 Python 进程。涉及运行时资源、配置或依赖初始化的修复，交付时必须明确需要重启哪些 worker/筛选任务/服务；旧进程输出不能作为新代码的验证证据。
5. 排障时先区分“旧进程仍在执行旧代码”“资源泄漏”“缓存/权限路径错误”“外部网络故障”，不要仅根据相同错误文本直接归因。

**验证基线**：连续 5 个 yfinance 单标的请求在首次初始化后 FD 保持稳定；覆盖 session 复用、fetcher 关闭、主力风险收尾和全市场筛选收尾的回归测试必须保持通过。
