# MoneyManager

Stock screening and analysis platform. The core app is `stock_screener/` — a database-driven rule engine that screens stocks across HK/US/A markets, with optional post-screening LLM signal analysis and Feishu delivery.

## Project Memory

Detailed context lives in the memory directory. Read the relevant files before working on these subsystems:

- **[Stock Screener Rule Engine](memory/stock-screener-rule-engine.md)** — Rule chain JSON DSL, `screening_rule_metadata` / `screening_rule_chains` tables, built-in filters and strategies, modification protocol.
- **[Signal Analysis Chain](memory/signal-analysis-chain.md)** — Post-screening search + LLM pipeline, provider fallback chain (`OpenAICompatible` / `Codex` / `DeepSeek`), environment variable reference, API quota discipline, artifact rules.
- **[Stock Sector Enrichment](memory/stock-sector-enrichment.md)** — Sector/industry enrichment pipeline, provider priority order, merge rules.
- **[Skill Abstraction Guidance](memory/skill-abstraction-guidance.md)** — When to propose new project skills from repeatable workflows.

## Key Conventions

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
