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

**Key architectural note**: `CompanySnapshotBuilder` docstring claims FutuOpenD `get_stock_filter` is a data source (priority 2 after yfinance), but this was **never implemented**. The Futu fallback added in this session (`fill_snapshots_from_futu()`) provides partial coverage via `get_market_snapshot` (price, market cap, PE, PB) — a different Futu endpoint than the one originally planned.
