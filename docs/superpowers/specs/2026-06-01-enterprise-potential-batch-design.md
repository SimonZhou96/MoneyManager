# 企业潜力分析批量编排设计

> 日期：2026-06-01
> 关联：`2026-05-28-enterprise-potential-analysis-rule-design.md` 第 13 节

## 目标

在 `potential_analysis/` 模块中增加 `EnterprisePotentialService` 批量编排层，实现 batch-first 数据预取 + 批量 LLM 评分。

## 类结构

```
EnterprisePotentialService
  ├── BatchPrefetcher (门面)
  │     ├── BatchCompanyFetcher   (yf.Tickers info/financials)
  │     ├── BatchValuationFetcher (yf.Tickers info)
  │     ├── BatchTradingFetcher   (yf.Tickers history)
  │     └── BatchIndustryFetcher  (yf.Tickers info.sector)
  │
  ├── 所有快照 → FilterContext._cache
  │     cache key: "enterprise:company:{code}", etc.
  │
  └── EnterprisePotentialLLMScorer
        ├── EnterprisePotentialPromptBuilder
        ├── LLM client (复用 signal_analysis)
        └── EnterprisePotentialParser
```

## 数据流

```
screen_service.py:
  1. service = EnterprisePotentialService(llm_client)
  2. service.prefetch_batch(market, codes, context)   ← 批量预取
  3. for stock in stocks: rule_engine.evaluate_stock() ← 策略器从 cache 读
  4. (可选) service.score_batch(...)                   ← 批量 LLM
```

## 改动文件

| 文件 | 类型 |
|------|------|
| potential_analysis/service.py | 新增 |
| potential_analysis/prompting.py | 新增 |
| potential_analysis/scoring.py | 修改（加 LLMScorer） |
| potential_analysis/strategizer.py | 修改（改读 cache） |
| api/screen_service.py | 修改（1 行） |
