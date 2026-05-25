# Zhipu Search Fallback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add 智谱 AI Web Search as a cost-controlled fallback behind Tavily for the shared stock/options `signal_analysis` search chain.

**Architecture:** Keep the search provider interface unchanged. Add a `ZhipuWebSearchProvider` that normalizes BigModel `search_result` records into `SearchDocument`, and wrap configured providers in `FallbackSearchProvider` so Tavily is tried first and 智谱 is only called after Tavily raises. Network preflight must filter each fallback provider independently.

**Tech Stack:** Python stdlib, `requests`, existing `unittest` tests, existing `signal_analysis` provider/factory/service modules.

---

### Task 1: Provider Tests

**Files:**
- Modify: `stock_screener/tests/test_signal_analysis.py`

- [x] **Step 1: Write failing tests**

Add tests for:
- 智谱 result parsing and Bearer auth.
- factory order `Tavily > Zhipu`.
- fallback search does not call 智谱 when Tavily succeeds.
- fallback search calls 智谱 when Tavily raises.
- fallback batch search returns empty per code if both providers fail.

- [x] **Step 2: Run tests to verify they fail**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=stock_screener python3 -m unittest stock_screener.tests.test_signal_analysis
```

Expected: fail because `ZhipuWebSearchProvider` and `FallbackSearchProvider` do not exist yet.

### Task 2: Provider Implementation

**Files:**
- Modify: `stock_screener/signal_analysis/search_providers.py`
- Modify: `stock_screener/signal_analysis/factories.py`

- [x] **Step 1: Implement providers**

Add:
- `ZhipuWebSearchProvider.search()` POSTing to `https://open.bigmodel.cn/api/paas/v4/web_search` with `Authorization: Bearer <key>`.
- `ZhipuWebSearchProvider.search_companies_batch()` using concise company batch queries within the 70-character provider limit.
- `FallbackSearchProvider.search()` and `search_companies_batch()` that return the first successful provider result and return empty results only after all providers fail.

- [x] **Step 2: Implement factory wiring**

Configure search providers from environment:
- `TAVILY_API_KEY` creates Tavily.
- `ZHIPUAI_API_KEY` or `BIGMODEL_API_KEY` creates 智谱.
- Default order is `tavily,zhipuai`.
- `SIGNAL_SEARCH_PROVIDER_ORDER` can override order without adding extra calls.

- [x] **Step 3: Run provider tests**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=stock_screener python3 -m unittest stock_screener.tests.test_signal_analysis
```

Expected: pass.

### Task 3: Network Preflight Tests And Implementation

**Files:**
- Modify: `stock_screener/tests/test_network_preflight.py`
- Modify: `stock_screener/signal_analysis/service.py`

- [x] **Step 1: Write failing tests**

Add tests that:
- Tavily DNS failure with 智谱 DNS success keeps 智谱 enabled.
- Tavily and 智谱 DNS failure downgrades to `NullSearchProvider`.

- [x] **Step 2: Run tests to verify they fail**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=stock_screener python3 -m unittest stock_screener.tests.test_network_preflight
```

Expected: fail because preflight currently treats search as a single endpoint.

- [x] **Step 3: Implement independent provider filtering**

Update `_filter_search_provider_by_preflight()` to inspect `FallbackSearchProvider.providers`, skip only providers whose endpoints fail, and rebuild the remaining provider or downgrade to `NullSearchProvider` if none remain.

- [x] **Step 4: Run targeted verification**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=stock_screener python3 -m unittest stock_screener.tests.test_signal_analysis stock_screener.tests.test_network_preflight
git diff --check
```

Expected: tests pass and diff check exits 0.

### Self-Review

- Spec coverage: Tavily priority, 智谱 fallback only on Tavily failure, skip after both fail, shared chain behavior, and cost control are covered.
- Placeholder scan: no deferred code tasks remain.
- Type consistency: provider signatures match the existing `SearchProvider` interface.
