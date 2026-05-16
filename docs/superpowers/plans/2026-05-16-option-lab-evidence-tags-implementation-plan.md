# Option Lab Evidence Tags Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Option Lab macro analysis explain data gaps and attach concrete source tags to macro factors.

**Architecture:** Keep the existing option macro provider as the integration point. Expand source collection before LLM analysis, preserve structured evidence in `OptionMacroAnalysis`, propagate it to candidates/API/cache, and render citation tags in the React Option Lab page.

**Tech Stack:** Python dataclasses/unittest, FastAPI/Pydantic response payloads, existing `signal_analysis` Tavily/LLM chain, React/TypeScript.

---

### Task 1: Backend Evidence and Data Gap Model

**Files:**
- Modify: `stock_screener/option_lab/macro_analysis.py`
- Modify: `stock_screener/option_lab/models.py`
- Modify: `stock_screener/db.py`
- Test: `stock_screener/tests/test_option_lab_macro_analysis.py`
- Test: `stock_screener/tests/test_option_lab_models.py`

- [ ] Add failing tests for `OptionMacroAnalysis` serializing `数据缺失原因`, `引用来源`, and `因素引用`.
- [ ] Add failing tests proving `screening_row_from_snapshot()` propagates `主力流出风险`, `主力风险分`, `主力风险信号`, `主力风险说明`, and `资金与盘面观察` from `UnderlyingSnapshot.signal_summary`.
- [ ] Implement evidence link fields on macro analysis and strategy candidates.
- [ ] Extend DB macro JSON persistence keys so candidate history keeps the evidence fields.
- [ ] Run targeted tests.

### Task 2: Source Expansion and Factor Citation Binding

**Files:**
- Modify: `stock_screener/option_lab/macro_analysis.py`
- Test: `stock_screener/tests/test_option_lab_macro_analysis.py`

- [ ] Add failing tests for source expansion queries preferring official IR, HKEX/filings, annual reports, AI/robotics technical sources, and avoiding only generic quote/company pages.
- [ ] Add failing tests that an AI/robotics factor gets a citation tag when matching source documents mention `AI`, `机器人`, `MiMo`, `Robotics`, `VLA`, or official report text.
- [ ] Implement `expand_option_company_documents()` and evidence matching helpers.
- [ ] Add data-gap reasons for generic-only sources, missing company documents, missing main-force data, and uncited factors.
- [ ] Run targeted tests.

### Task 3: API and Frontend Rendering

**Files:**
- Modify: `stock_screener/web_frontend/src/main.tsx`
- Modify: `stock_screener/web_frontend/src/styles.css`
- Test: frontend build

- [ ] Render `数据缺失原因` in the macro analysis panel and selected candidate detail.
- [ ] Render each factor with small source tags from `因素引用`; source tags open in a new tab.
- [ ] Keep the old `信息来源` line as a fallback for flat source URLs.
- [ ] Run `npm run build`.

### Task 4: Verification

**Files:**
- Test only

- [ ] Run targeted option lab tests.
- [ ] Run full `stock_screener` unittest discovery.
- [ ] Rebuild frontend.
- [ ] Restart local backend/frontend if they are running.
