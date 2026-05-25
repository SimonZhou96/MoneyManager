# Stock Screening Terminal Design

## Background

MoneyManager currently has two related but separate web surfaces:

- `代码筛选`: accepts one or more stock codes, runs the existing custom-list
  screening task API, and displays rule-chain results.
- `市场情报`: lets the user query stock-level and market-level intelligence,
  including announcements, research reports, news, financial summaries,
  Evidence Pack preview, and provider run status.

The target is to merge these into one user-facing stock screener page and make
the selected-stock detail area closer to the go-stock individual-stock terminal
experience. The merged page should support screening, quote context, K-line
viewing, intraday minute view, fund-flow trend, market intelligence, and
Evidence Pack review without making market intelligence or live providers part
of rule-chain pass/fail decisions.

Graphify/grafhify is scoped as a development aid only. It may be used during
implementation to build a code knowledge graph over MoneyManager and the
go-stock reference checkout, but it is not a product feature and should not add
runtime dependencies to the web app.

## Goals

- Replace the separate `市场情报` navigation entry with a single merged
  `个股筛选器` or equivalent stock-screener entry.
- Preserve the existing custom-list screening contract for one-code and
  multi-code screening.
- Keep rule-chain screening as the only source of pass/fail decisions.
- Load selected-stock details only after the user clicks a result row.
- Add a stock-terminal backend layer with unified APIs for quote, K-line,
  minute, fund-flow, and source status data.
- Support A/HK/US in the first version with unified response shapes and
  market-specific providers.
- Use a cache-first, provider-fallback strategy: read cache first, fetch live
  data only when data is missing or stale, then write successful fetches back to
  cache.
- Reuse existing `market_intel` APIs and Evidence Pack logic inside the merged
  detail panel.
- Show stale, missing, and provider-error states at data-block level instead of
  failing the whole stock detail view.

## Non-Goals

- Do not restore the old frontend `/api/screening/single-stock` flow.
- Do not let market intelligence, live quotes, minute data, or fund flow change
  rule-chain pass/fail results.
- Do not batch-load market intelligence or live terminal data for all result
  rows by default.
- Do not expose Graphify/grafhify in the product UI.
- Do not place orders or add automated trading.
- Do not require A/HK/US to use the same external provider. The stable contract
  is the normalized backend response, not identical source coverage.

## Selected Approach

Use a unified stock terminal layer with market-specific providers and
cache-first behavior.

The merged page has one entry point. The left side remains a screening
workbench. The right side becomes a selected-stock terminal. Clicking a row
loads terminal data for that one stock only.

The flow is:

```text
input codes
-> /api/screening/custom-list-tasks
-> poll custom-list results
-> user selects one result row
-> stock-terminal summary loads selected-stock identity, quote, and block status
-> chart and intelligence tabs lazily load K-line/minute/fund-flow/market-intel data
-> right-side terminal renders available data and block-level status
```

## Backend Architecture

Add a focused `stock_terminal` module under `stock_screener`.

Recommended modules:

- `stock_screener/stock_terminal/models.py`
  - Normalized dataclasses or typed dictionaries for `QuoteSnapshot`,
    `KlineSeries`, `MinuteSeries`, `FundFlowSeries`, `TerminalDataBlockStatus`,
    and `StockTerminalSummary`.
- `stock_screener/stock_terminal/providers/`
  - Provider interfaces and market-specific adapters.
  - Initial providers should be selected per market and capability, not by one
    universal provider assumption.
  - go-stock should be used as a reference for Eastmoney/Sina style data
    contracts, especially K-line, minute data, and fund-flow trend behavior.
- `stock_screener/stock_terminal/repository.py`
  - Reads existing K-line cache through `MarketDatabase.get_kline_cache`.
  - Adds `stock_quote_cache`, `stock_minute_cache`, and
    `stock_fund_flow_cache` storage unless implementation finds an equivalent
    existing table with the same owner, TTL, and source-status semantics.
- `stock_screener/stock_terminal/service.py`
  - Provides cache-first orchestration and provider fallback.
  - Returns data-block statuses instead of raising for partial provider
    failures.
- `stock_screener/web/stock_terminal.py`
  - FastAPI routes for selected-stock terminal data.

The module dependency direction should remain:

```text
providers -> models
repository -> models + MarketDatabase
service -> providers + repository
web route -> service
frontend -> web route + existing market_intel API
```

`stock_terminal` must not import frontend code, mutate screening results, or
call LLM analysis directly.

## API Design

Add routes under `/api/stock-terminal`.

### `GET /api/stock-terminal/{market}/{code}/summary`

Returns lightweight selected-stock context:

- normalized market and code;
- display name when available;
- selected screening row fields when provided by the frontend or task context;
- quote snapshot;
- high-level data block statuses;
- links or flags indicating which heavy blocks are available.

This endpoint should be safe to call when the user selects a row. It should not
block on every heavy chart dataset.

### `GET /api/stock-terminal/{market}/{code}/klines`

Query parameters:

- `timeframe`: defaults to the page-selected timeframe;
- `limit`: bounded by the backend;
- `refresh`: optional explicit refresh for the selected stock.

Behavior:

- read `stock_kline_cache` first;
- if missing or stale, fetch through provider chain;
- write successful rows using the existing K-line cache path;
- return normalized OHLCV rows and block status.

### `GET /api/stock-terminal/{market}/{code}/minute`

Returns intraday minute price and volume data using a short TTL cache.

Behavior:

- support A/HK/US with market-specific providers;
- return `empty` or `error` status when a market source is unavailable;
- never affect K-line or screening result display.

### `GET /api/stock-terminal/{market}/{code}/fund-flow`

Returns fund-flow trend data using a TTL cache.

Behavior:

- normalize fields such as `date`, `inflow`, `outflow`, `net_inflow`,
  `main_net_inflow`, and `retail_net_inflow` when available;
- include `source` and field-gap metadata because A/HK/US fund-flow sources
  may not expose identical semantics;
- degrade to available financial or market-intel items when full fund-flow data
  is unavailable.

### Existing Market Intel APIs

The merged frontend should reuse:

- `/api/market-intel/stocks/{market}/{code}`;
- `/api/market-intel/evidence-pack/preview`;
- `/api/market-intel/provider-runs`.

The standalone Market Intel navigation entry should be removed or hidden, but
the APIs remain available for the merged panel and tests.

## Data Status Contract

Every terminal data block should include:

```json
{
  "status": "fresh",
  "source": "eastmoney",
  "fetched_at": "2026-05-25T09:30:00Z",
  "expires_at": "2026-05-25T09:35:00Z",
  "stale": false,
  "error_message": ""
}
```

Allowed statuses:

- `fresh`: live or recently refreshed data;
- `cached`: valid cache hit;
- `stale`: stale cache returned because live refresh failed;
- `empty`: provider succeeded but no data was available;
- `error`: no usable data and provider failed.

Frontend rendering must rely on these statuses instead of assuming the entire
detail payload is either valid or invalid.

## Frontend Design

Rename or reframe the existing `代码筛选` page as the merged stock screener.
The page should remain an operational tool, not a marketing page.

### Top Control Area

Keep the current controls:

- market;
- timeframe;
- rule chain;
- AI analysis toggle;
- Feishu toggle;
- code input;
- start screening button.

The controls should remain compact and stable on desktop and mobile.

### Left Result Area

Show screening results in a dense list or table:

- original input;
- normalized code;
- name;
- pass/fail/invalid/duplicate status;
- rule result;
- sector;
- industry;
- latest close;
- filter summary;
- reason;
- task detail link.

Clicking a row selects the stock, highlights the row, and loads right-side
details for that stock only.

### Right Stock Terminal Area

Use tabs or compact grouped sections:

- `行情`: quote, K-line chart, minute chart, source status, update time.
- `资金`: fund-flow trend chart, available inflow/outflow fields, source
  status, field-gap notes.
- `情报`: announcements, research reports, news, financial summaries.
- `证据`: Evidence Pack, citations, data gaps, provider run status.
- `操作`: refresh selected stock, open task detail, reload market intelligence.

The default state before row selection should explain that terminal data loads
after selecting a result row. It must not trigger background provider calls.

### go-stock Reference Behavior

The first version should borrow the interaction model, not copy the UI
framework:

- quick access from each stock to K-line, minute view, fund flow, announcement,
  research report, and AI/analysis evidence;
- dense market-terminal layout;
- explicit data source and refresh feedback;
- charts as functional inspection tools, not decorative elements.

MoneyManager stays React/Vite and does not adopt Wails, Vue, or NaiveUI.

## Data Flow

### Screening

```text
submit codes
-> create custom-list job
-> poll job results
-> render left result table
```

This remains the current custom-list API path.

### Selected Stock Details

```text
click result row
-> fetch stock-terminal summary
-> render identity, quote, and available statuses
-> lazily fetch K-line, minute, fund-flow, market-intel, and Evidence Pack by tab
```

The recommended first-page load is:

- fetch summary on row selection;
- fetch K-line when `行情` opens;
- fetch minute when minute subview opens or the `行情` tab needs it;
- fetch fund-flow when `资金` opens;
- fetch market-intel and Evidence Pack when `情报` or `证据` opens.

This preserves a low-cost default while still supporting a complete terminal
when the user inspects a stock.

## Error Handling

- Screening task errors appear in the left panel and do not attempt terminal
  loading.
- Quote failure still allows K-line, minute, fund-flow, and market-intel tabs
  to try loading independently.
- K-line failure shows cached data if available, otherwise an empty chart state
  with provider error.
- Minute failure does not affect K-line.
- Fund-flow failure does not affect market intelligence.
- Market-intel failure does not affect quote or charts.
- Evidence Pack gaps are shown as data gaps, not silently hidden.
- A/HK/US provider field differences are shown as missing fields or source
  notes, not coerced into invented values.

All live provider calls should use bounded timeouts and should return partial
results when possible.

## Testing Plan

Backend tests:

- stock-terminal code normalization for A/HK/US;
- K-line cache hit returns cached rows without calling provider;
- stale or missing K-line cache calls provider and writes successful rows;
- provider failure returns block-level `error` or `stale` instead of making the
  whole endpoint fail;
- minute and fund-flow endpoints return the normalized block status structure;
- summary endpoint tolerates one failed data block;
- market-intel existing API and Evidence Pack tests continue to pass.

Frontend tests:

- navigation no longer shows a separate `市场情报` entry;
- merged screener still creates tasks through
  `/api/screening/custom-list-tasks`;
- frontend does not call `/api/screening/single-stock`;
- terminal data loading is tied to selecting a result row, not to initial page
  render or job completion;
- right panel contains `行情`, `资金`, `情报`, and `证据` sections;
- stale/error/source statuses are rendered for terminal data blocks.

Build and smoke checks:

```bash
PYTHONPATH=stock_screener python3 -m unittest stock_screener.tests.test_code_screening_frontend stock_screener.tests.test_custom_list -v
PYTHONPATH=stock_screener python3 -m unittest stock_screener.tests.test_market_intel_api stock_screener.tests.test_market_intel_frontend -v
cd stock_screener/web_frontend && npm run build
```

After implementation, start the local web app and verify in the browser:

- the merged page can submit one code and multiple codes;
- results appear in input order;
- selecting a row loads right-side terminal data;
- provider failures show block-level degradation instead of breaking the page;
- the old standalone Market Intel navigation entry is absent.

## Implementation Notes

- Keep source-text frontend contract tests because this repo already uses them
  effectively for web page contracts.
- Do not commit generated cache files such as `__pycache__`.
- Do not refactor unrelated screening, option, or quant flows.
- If Graphify/grafhify is used, generate development artifacts outside the
  product runtime path or keep them ignored. Its output may guide code
  understanding, but product code should not depend on it.
