# Option Lab Macro Analysis Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add optional macro-level scoring to Option Lab, controlled by frontend and shell switches, without changing the existing option strategy score.

**Architecture:** Add a focused `option_lab.macro_analysis` module that adapts existing `signal_analysis` search/LLM providers into a symbol-level macro result. `OptionLabService` applies that one macro result to every candidate for the symbol, computes `综合评分 = 期权评分 * 0.7 + 宏观分析评分 * 0.3`, and persists/returns macro fields only when enabled.

**Tech Stack:** Python 3.14, unittest, FastAPI/Pydantic, PyMySQL-style repository methods, React + TypeScript + Vite.

---

### Task 1: Backend Macro Analysis Core

**Files:**
- Create: `stock_screener/option_lab/macro_analysis.py`
- Modify: `stock_screener/option_lab/models.py`
- Modify: `stock_screener/option_lab/service.py`
- Test: `stock_screener/tests/test_option_lab_macro_analysis.py`
- Test: `stock_screener/tests/test_option_lab_service.py`

- [ ] Write failing tests for disabled macro analysis, enabled macro analysis, composite score calculation, LLM unavailable fallback, and cache hit behavior.
- [ ] Add `OptionMacroAnalysis`, `MacroAnalysisProvider`, and deterministic fake/null providers for tests.
- [ ] Extend `StrategyCandidate` with optional macro fields while keeping existing `评分` equal to `期权评分`.
- [ ] Extend `OptionLabService.evaluate_single()` with `enable_macro_analysis`, `force_macro_refresh`, and `macro_cache_ttl_minutes`.
- [ ] Verify macro provider is called once per symbol and never once per candidate.

### Task 2: Persistence and API Contract

**Files:**
- Modify: `stock_screener/db.py`
- Modify: `stock_screener/sql/013_option_lab.sql`
- Modify: `stock_screener/web/options.py`
- Test: `stock_screener/tests/test_option_lab_db.py`
- Test: `stock_screener/tests/test_option_lab_api.py`

- [ ] Add `option_macro_analysis_cache` schema and repository methods.
- [ ] Add request fields `enable_macro_analysis`, `force_macro_refresh`, and `macro_cache_ttl_minutes`.
- [ ] Return top-level `macro_analysis` only when requested.
- [ ] Include candidate macro fields only when macro analysis succeeds.
- [ ] Preserve batch response/history candidate shape and current owner scoping behavior.

### Task 3: Frontend Option Lab Controls and Display

**Files:**
- Modify: `stock_screener/web_frontend/src/main.tsx`
- Modify: `stock_screener/web_frontend/src/styles.css`

- [ ] Add `开启宏观层面分析` switch default off.
- [ ] Add disabled-by-default `强制重新分析` switch that is enabled only when macro analysis is on.
- [ ] Send macro request flags for single and batch evaluation.
- [ ] Hide macro columns and macro detail when disabled.
- [ ] Show `期权评分`, `宏观分析评分`, `综合评分`, `宏观方向`, `新闻影响`, `热点匹配`, and `主力资金风险` when enabled.
- [ ] Keep default sorting/display compatible with existing candidate selection and save-plan flow.

### Task 4: Interactive Shell Controls and Output

**Files:**
- Modify: `stock_screener/interactive_option_lab.py`
- Test: `stock_screener/tests/test_interactive_option_lab.py`

- [ ] Prompt `是否开启宏观层面分析 [y/N]`.
- [ ] Prompt `是否强制重新分析 [y/N]` only when macro analysis is enabled.
- [ ] Pass macro flags into `OptionLabService.evaluate_single()`.
- [ ] Print macro fields only when macro analysis exists.
- [ ] Keep shell mode commands and one-shot CLI behavior intact.

### Task 5: Verification

**Files:**
- Test suite and frontend build only.

- [ ] Run Option Lab backend/API/CLI tests.
- [ ] Run full Python unittest suite from `stock_screener`.
- [ ] Run `npm run build` from `stock_screener/web_frontend`.
- [ ] Restore tracked `__pycache__` generated files if Python tests modify them.
