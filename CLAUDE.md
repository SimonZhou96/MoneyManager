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
