# Market Intel Source Expansion Design

## Background

`stock-screening-terminal` solves a different problem from news breadth. It
merges code screening, selected-stock quote/K-line/minute/fund-flow panels, and
existing market-intel review into one terminal-style UI. It should not become
the owner of external news-source coverage.

The current `market_intel` layer already provides the right extension point:
providers normalize external responses into `IntelItem`, cache them, expose
provider status, and feed `EvidencePackBuilder`. The missing work is to expand
source coverage toward the useful parts of go-stock without copying its desktop
runtime or changing rule-chain pass/fail decisions.

## Goals

- Expand market and company news breadth in `market_intel`.
- Keep `stock_terminal` focused on selected-stock market data and UI.
- Keep rule-chain screening as the only pass/fail decision layer.
- Preserve source, URL, publish time, fetch time, provider status, and stale
  fallback for every item.
- Make richer sources available to search, Evidence Pack, reports, and
  downstream LLM analysis through one normalized contract.
- Keep search and LLM calls batch-first and cost-aware.
- Make provider failures visible but non-blocking.

## Non-Goals

- Do not rework the merged stock-terminal layout in this effort.
- Do not let news providers change screening pass/fail results.
- Do not default to one search request, browser session, or LLM call per stock.
- Do not copy go-stock's Wails, Vue, NaiveUI, or chromedp runtime.
- Do not make cookie/browser-dependent sources required for normal screening.
- Do not invent numeric sentiment, impact, or chart values with an LLM.

## Existing Plans This Complements

This design complements, not replaces:

- `2026-05-25-market-intel-design.md`
  - Owns the backend-first `market_intel` architecture, schema, service,
    Evidence Pack, and report contract.
- `2026-05-25-stock-screening-terminal-design.md`
  - Owns the merged screener/terminal UI and selected-stock quote/K-line/minute
    loading behavior.
- `2026-05-25-stock-screening-terminal-implementation.md`
  - Owns the `stock_terminal` package and API wiring.

This design specifically fills the source-coverage gap left by those plans.

## Source Coverage Gap

Current code has only a narrow set of live market-intel providers:

- Eastmoney stock-level announcement, research-report, and financial summary.
- Basic Cailianpress market flash news through `cls.cn/nodeapi/telegraphList`.
- Eastmoney index snapshot.
- Generic `signal_analysis` search providers such as Tavily and Zhipu.

Compared with go-stock, this is missing:

- Cailianpress HTML fallback and keyword search.
- Sina finance live feed.
- TradingView news list and detail.
- Iwencai news search.
- Eastmoney Miaoxiang-style finance/news search.
- Xueqiu hot stocks and hot events.
- A source registry for reliability, language, market coverage, and preferred
  use cases.

## Selected Approach

Add a staged `market_intel` source-expansion layer.

The first stage should prioritize stable HTTP sources that can run inside the
existing backend without a browser:

1. Fix and split the existing Cailianpress provider.
2. Add Sina finance live-feed provider.
3. Add TradingView news provider.
4. Add Iwencai and Eastmoney-search adapters only if their request contracts can
   be verified with fixtures.
5. Feed all normalized items into `EvidencePackBuilder`.

Xueqiu is deferred to a second stage because it depends on cookie/session or
browser-style acquisition. It should be optional and disabled by default.

## Backend Architecture

Add focused provider modules under `stock_screener/market_intel/providers/`.

Recommended modules:

- `cailianpress.py`
  - Handles Cailianpress flash news, HTML fallback, and keyword search.
  - Replaces or delegates the current generic `news.py`.
- `sina.py`
  - Handles Sina finance live-feed market news.
- `tradingview.py`
  - Handles TradingView news-flow list and optional detail fetches.
- `iwencai.py`
  - Handles Iwencai search results if the current request contract is verified.
- `eastmoney_search.py`
  - Handles Eastmoney finance/news search if the request contract is verified.
- `source_registry.py`
  - Defines source metadata such as reliability tier, supported markets, item
    types, expected freshness, and whether the source is browser-dependent.

Keep the dependency direction:

```text
provider modules -> market_intel.models
provider factory -> provider modules + source_registry
market_intel.service -> provider factory + repository
market_intel.evidence -> cached bundles + search documents + manual context
signal_analysis -> market_intel service and EvidencePackBuilder
```

Provider modules must not import frontend code, rule-chain code, or LLM
providers directly.

## Provider Scope

### Stage 1: Stable HTTP Providers

#### Cailianpress

Capabilities:

- Market flash news through `https://www.cls.cn/nodeapi/telegraphList`.
- Parse both observed response shapes:
  - top-level `roll_data`;
  - nested `data.roll_data`.
- Optional HTML fallback from `https://www.cls.cn/telegraph`.
- Optional keyword search through Cailianpress web search endpoint.

Normalized output:

- `scope_type=market` for flash news.
- `scope_type=stock` for keyword hits that clearly match a selected stock.
- `item_type=market_news` or `news`.
- `source=财联社`.

#### Sina

Capabilities:

- Market live-feed items from Sina finance zhibo API.
- JSONP response parsing with strict error handling.

Normalized output:

- `scope_type=market`.
- `item_type=market_news`.
- `source=新浪财经`.

#### TradingView

Capabilities:

- News-flow list from TradingView Chinese news API.
- Optional detail request for selected items only, bounded by a small limit.

Normalized output:

- `scope_type=market` by default.
- `scope_type=stock` only when the item contains ticker-level symbol metadata or
  is matched by explicit query context.
- `item_type=market_news` or `news`.
- `source=TradingView`.

### Stage 1.5: Search-Like Finance Providers

These providers are valuable but should be added only after fixture-level
contract verification.

#### Iwencai

Capabilities:

- Company news and research style natural-language search.
- Market hot-event search.

Constraints:

- Must preserve the existing `SearchProvider` batch-first economics.
- Should not be called once per stock by default.

Recommended integration:

- Implement both `MarketIntelProvider` stock/market fetch methods and, if
  useful, a `SearchProvider` adapter.
- Use batch queries for multiple stock codes where the endpoint supports it.

#### Eastmoney Search / Miaoxiang

Capabilities:

- Announcement, research, policy, and finance news search.
- Structured finance data search if the endpoint can be called reliably.

Constraints:

- Treat as optional because auth, headers, and rate limits may differ from public
  Eastmoney data-center APIs.
- Disable by default until request contract and terms are verified.

### Stage 2: Browser/Cookie-Dependent Providers

#### Xueqiu

Capabilities:

- Hot stocks.
- Hot events.
- Possible ticker-level sentiment hints.

Constraints:

- Requires cookie/session handling or browser-style acquisition.
- Must be disabled by default.
- Must not run during normal full-market screening.
- If implemented, it should run only on explicit refresh or selected-stock
  inspection and should record provider health clearly.

Recommended design:

- `XueqiuProvider` accepts a configured cookie first.
- Browser acquisition, if ever added, lives behind a separate optional helper and
  must be bounded by timeout, frequency limit, and feature flag.

## Source Registry

Add a small registry that describes each provider and source.

Example fields:

```json
{
  "provider": "cailianpress",
  "source": "财联社",
  "reliability_tier": "high",
  "markets": ["A", "HK", "US"],
  "item_types": ["market_news", "news"],
  "freshness_minutes": 15,
  "requires_auth": false,
  "requires_browser": false,
  "default_enabled": true
}
```

Use this registry for:

- default provider factory ordering;
- source labels in the UI and report;
- Evidence Pack ordering;
- provider health display;
- deciding which providers are allowed in automated screening.

## Evidence Pack and LLM Integration

All new provider output should enter downstream analysis through
`market_intel` bundles and `EvidencePackBuilder`.

Flow:

```text
passed rows or explicit selected rows
-> market_intel service reads cache
-> enabled providers refresh missing/stale data
-> provider items become IntelItem
-> EvidencePackBuilder dedupes and ranks evidence
-> signal_analysis uses curated evidence in batch LLM prompts
-> CSV/report/web detail shows citations and source status
```

Constraints:

- LLM providers never call external news APIs directly.
- Search providers remain batch-first.
- Per-stock evidence expansion remains opt-in or bounded.
- Provider failure returns source-status warnings and stale/empty groups.
- Evidence Pack must include data gaps when important providers fail or return
  no usable items.

## Dedupe and Ranking

Normalize every item before dedupe:

- canonical title;
- canonical URL when present;
- source;
- provider;
- publish time;
- item type;
- stock code if applicable.

Recommended dedupe key priority:

1. Exact canonical URL.
2. Provider source ID.
3. Normalized title plus publish date.
4. Normalized title plus source when publish date is absent.

Ranking should prefer:

1. User/manual context.
2. Official exchange or company announcements.
3. High-reliability structured sources.
4. Recent market flash news.
5. Search documents.
6. Low-confidence inferred matches.

Do not remove all duplicates blindly. If the same event appears in multiple
trusted sources, keep one primary item and attach `related_sources` or
`duplicate_sources` metadata when feasible.

## Cost and Rate-Limit Policy

Default automated screening behavior:

- Market-level flash/news providers: one refresh per market per TTL window.
- Stock-level structured providers: only for passed rows or explicit custom-list
  rows when AI analysis is enabled.
- Iwencai/Eastmoney search-style providers: batch-first or disabled by default.
- TradingView detail calls: only for top N list items, default N <= 5.
- Xueqiu/browser-dependent providers: disabled by default.

For a 100-stock candidate set:

- Market news refresh: O(1) per market per TTL.
- Stock-level Eastmoney intel: O(passed stocks), cache-first.
- Search-style finance providers: O(batch count), not O(stock count) by default.
- LLM: existing batch behavior only.

## API and UI Impact

No new top-level UI entry is required.

Existing APIs remain:

- `GET /api/market-intel/stocks/{market}/{code}`
- `GET /api/market-intel/markets/{market}/digest`
- `GET /api/market-intel/provider-runs`
- `POST /api/market-intel/evidence-pack/preview`

Optional API additions:

- `GET /api/market-intel/sources`
  - Lists providers, enabled state, reliability tier, markets, item types, and
    whether the provider is automated-safe.
- `POST /api/market-intel/markets/{market}/refresh`
  - Allows explicit source refresh by provider key.

Frontend changes should be minimal:

- Show provider/source health in the existing Market Intel or merged terminal
  evidence panel.
- Show grouped items by source and item type.
- Show data gaps when a configured provider fails.
- Do not trigger provider calls for every row on page load.

## Environment Flags

Recommended flags:

```bash
MARKET_INTEL_ENABLE_LIVE_PROVIDERS=1
MARKET_INTEL_PROVIDER_TIMEOUT_SEC=10
SIGNAL_ENABLE_MARKET_INTEL=0

MARKET_INTEL_PROVIDER_ORDER=cailianpress,sina,tradingview,eastmoney,global_index
MARKET_INTEL_ENABLE_CAILIANPRESS=1
MARKET_INTEL_ENABLE_SINA=1
MARKET_INTEL_ENABLE_TRADINGVIEW=1
MARKET_INTEL_ENABLE_IWENCAI=0
MARKET_INTEL_ENABLE_EASTMONEY_SEARCH=0
MARKET_INTEL_ENABLE_XUEQIU=0

MARKET_INTEL_TRADINGVIEW_DETAIL_LIMIT=5
MARKET_INTEL_SEARCH_BATCH_SIZE=10
MARKET_INTEL_BROWSER_PROVIDERS_ALLOWED=0
```

`SIGNAL_ENABLE_MARKET_INTEL` should remain explicit. Enabling richer sources for
the Market Intel API should not automatically increase automated screening cost.

## Error Handling

- Provider timeout: record `status=timeout`, return stale cache if available.
- Provider HTTP error: record `status=failed` with HTTP status and short message.
- Parse error: record `status=failed` with parser name and source shape hint.
- Empty success: record `status=success`, `item_count=0`, and return an empty
  group.
- Auth/cookie missing: record `status=skipped` for optional providers.
- Browser provider disabled: record `status=skipped`, not failed.

No provider error may fail:

- rule-chain screening;
- CSV generation;
- Feishu delivery;
- selected-stock terminal quote/K-line/minute/fund-flow display.

## Testing Plan

Provider tests:

- Cailianpress parses top-level and nested `roll_data`.
- Cailianpress HTML fallback produces market-news items.
- Sina JSONP parser rejects malformed callback data safely.
- TradingView list parser handles missing detail fields.
- TradingView detail limit is enforced.
- Iwencai/Eastmoney search adapters are skipped when credentials or contracts
  are unavailable.
- Xueqiu is skipped when cookies or browser providers are disabled.

Service tests:

- Provider ordering follows env and registry defaults.
- Provider failure falls back to stale cache.
- Empty provider result remains non-blocking.
- Provider run rows include source, status, item count, and error message.

Evidence tests:

- Evidence Pack merges Cailianpress, Sina, TradingView, Eastmoney, search, and
  manual items.
- Duplicate news across sources is deduped but related source metadata is
  preserved when feasible.
- Evidence ranking prefers official/high-reliability and recent items.
- Batch-first search behavior is preserved.

Signal-analysis tests:

- Enabling `SIGNAL_ENABLE_MARKET_INTEL=1` injects market-intel documents before
  LLM analysis.
- Provider failures do not abort LLM batch analysis.
- Automated analysis does not introduce one search call per stock by default.

Frontend/API tests:

- Source health endpoint renders configured providers.
- Evidence panel displays provider errors and data gaps.
- No market-intel refresh is triggered before selecting a row or opening the
  relevant panel.

## Rollout Plan

1. Add source registry and provider feature flags.
2. Refactor current `news.py` into Cailianpress provider and fix response-shape
   parsing.
3. Add Sina provider with JSONP parser and tests.
4. Add TradingView list provider and bounded detail fetch.
5. Add provider health API/UI polish.
6. Connect expanded source items into Evidence Pack ranking.
7. Add Iwencai provider after request-contract verification.
8. Add Eastmoney-search provider after request-contract verification.
9. Evaluate Xueqiu as an optional selected-stock-only provider.

## Acceptance Criteria

- Market digest includes at least Cailianpress, Sina, and TradingView items when
  providers are enabled and reachable.
- Provider failures are visible in provider run status and evidence data gaps.
- Evidence Pack uses expanded source items without changing rule-chain pass/fail.
- Search and LLM call patterns remain batch-first.
- Automated screening still succeeds when every expanded provider fails.
- Xueqiu/browser-dependent sources are disabled by default and never required for
  normal screening.
