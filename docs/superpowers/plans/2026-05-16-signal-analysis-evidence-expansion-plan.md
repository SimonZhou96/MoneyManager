# Signal Analysis Evidence Expansion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make macro evidence expansion and citation tags work for HK/US/A option analysis and the stock-screening AI analysis path.

**Architecture:** Create a shared `signal_analysis.evidence` module for market-specific source expansion, evidence link normalization, factor-source matching, and data-gap detection. Option Lab and stock-screening AI analysis both consume this module, so CSV, DB, single-stock response, and frontend rendering use the same fields.

**Tech Stack:** Python unittest, existing `signal_analysis` chain, Tavily-compatible `SearchProvider`, React/Vite frontend.

---

### Task 1: Shared Evidence Module

**Files:**
- Create: `stock_screener/signal_analysis/evidence.py`
- Modify: `stock_screener/tests/test_option_lab_macro_analysis.py`
- Modify: `stock_screener/tests/test_signal_analysis.py`

- [ ] Add failing tests that assert HK/US/A source expansion queries include HKEX, SEC, CNINFO/SSE/SZSE.
- [ ] Implement `expand_company_documents`, `build_evidence_links`, `build_factor_citations`, and `build_data_gaps`.
- [ ] Refactor Option Lab to use the shared helpers.

### Task 2: Stock Screening AI Evidence Fields

**Files:**
- Modify: `stock_screener/signal_analysis/models.py`
- Modify: `stock_screener/signal_analysis/chain.py`
- Modify: `stock_screener/db.py`
- Modify: `stock_screener/sql/002_signal_analysis.sql`

- [ ] Add failing tests for CSV output and DB rows containing `数据缺失原因`, `引用来源`, and `因素引用`.
- [ ] Add fields to `SignalAnalysisResult`, CSV columns, DB serialization, and schema migration.
- [ ] Add a normalize step that binds source documents to each result after the LLM returns.

### Task 3: Frontend Single-Stock Display

**Files:**
- Modify: `stock_screener/web/single_stock.py`
- Modify: `stock_screener/web_frontend/src/main.tsx`

- [ ] Add evidence fields to the single-stock API response.
- [ ] Render factors with citation tags in the single-stock result instead of only dumping raw JSON.

### Task 4: Verification

**Commands:**
- `PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python -m unittest tests.test_signal_analysis tests.test_option_lab_macro_analysis -v`
- `PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v`
- `npm run build`

