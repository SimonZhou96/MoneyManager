# 统一评分模型实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 MarketIntelMacroScore 和 EnterprisePotential 两套独立评分体系合并为以五因素模型为唯一出口的统一评分引擎，并精简 CSV 输出。

**Architecture:** 分两个阶段实现。阶段一（Task 1–10）完成评分模型增强 + MarketIntel 合并 + aggregate_rule_scores 重构，可独立上线验证。阶段二（Task 11–17）完成 CSV 列精简和报告结构升级。

**Tech Stack:** Python 3.11+, dataclasses, pytest, MySQL (screening_results 表)

---

## File Structure

```
stock_screener/
├── potential_analysis/
│   ├── scoring.py          # 增强: 置信度惩罚、LLM增强接口、市场差异化阈值
│   └── strategizer.py      # 增强: _inject_llm_enhancements()
├── macro_strategies.py     # 重构: MarketIntelMacroScoreStrategizer 降级
├── rule_engine.py          # 简化: 移除独立 market_intel 判断
├── api/
│   └── screen_service.py   # 简化: MarketIntel 注入、aggregate_rule_scores
├── market_intel/
│   └── macro_scoring.py    # aggregate_rule_scores 重构
├── signal_analysis/
│   ├── models.py           # 精简: to_csv_columns() 删/合并列
│   └── chain.py            # 精简: AI_CSV_COLUMNS
├── scheduled_daily_job.py  # 精简: MAIN_FORCE/ZUOYI CSV 列 + 五因素列
└── tests/
    ├── test_unified_scoring.py        # 新建: 评分模型测试
    ├── test_enterprise_potential_holding_period.py  # 已有, 扩展
    └── test_market_intel_reporting.py # 已有, 可能需更新
```

---

### Task 1: 缺失模块置信度惩罚

**Files:**
- Modify: `stock_screener/potential_analysis/scoring.py:_rule_score` (line ~496)

- [ ] **Step 1: 修改 `_rule_score` — 缺失模块不参与加权**

找到 `_rule_score` 方法（约 line 496），将缺失模块补齐 50 分的逻辑替换为：缺失模块不参与加权，confidence_score = 可用模块数/5×100，<2 模块时 skip。

定位到当前代码中这两段：

```python
# 旧代码 (~line 508-540)：缺失模块用 50 分补齐
for mod in ["macro", "industry", "company", "valuation", "trading"]:
    if mod not in scores:
        if mod in evidence.modules_missing:
            data_gaps.append(f"模块缺失: {mod}")
        scores[mod] = 50.0

# ...

# 旧代码：加权求和 — 所有权重参与
total = 0.0
weight_sum = 0.0
for mod, weight in self.weights.items():
    if mod in scores:
        total += scores[mod] * weight
        weight_sum += weight
result.total_score = round(total / weight_sum, 1) if weight_sum > 0 else 50.0

# 旧代码：置信度
result.confidence_score = round(len(evidence.modules_available) / 5 * 100, 1)
```

替换为：

```python
# 新代码：缺失模块不参与加权，仅标记缺口
weighted_modules = 0
for mod in ["macro", "industry", "company", "valuation", "trading"]:
    if mod not in scores:
        if mod in evidence.modules_missing:
            data_gaps.append(f"模块缺失: {mod}")
        # 不设默认分，不参与加权
    else:
        weighted_modules += 1

result.macro_score = scores.get("macro")
result.company_score = scores.get("company")
result.valuation_score = scores.get("valuation")
result.industry_score = scores.get("industry")
result.trading_score = scores.get("trading")

# 加权总分：仅可用模块参与
total = 0.0
weight_sum = 0.0
for mod, weight in self.weights.items():
    if mod in scores and scores[mod] is not None:
        total += scores[mod] * weight
        weight_sum += weight
result.total_score = round(total / weight_sum, 1) if weight_sum > 0 else 0.0

# 置信度 = 可用模块数 / 5
available_count = len(evidence.modules_available)
result.confidence_score = round(available_count / 5 * 100, 1)

# < 2 模块可用时 → skip，不参与决策
if available_count < 2:
    result.total_score = 0.0
    result.passed = False
    result.decision = "SKIP"
    result.summary = f"数据不足: 仅 {available_count}/5 模块可用，跳过深度评估"
    return result
```

- [ ] **Step 2: 将 `available_count` 传入 `_finalize_result` 的 summary**

在 `_finalize_result` 调用之前（约 line 568），追加置信度信息到 summary：

```python
# 在 _finalize_result 调用前，result.data_gaps 已设置
self._finalize_result(result, scores)
# 追加置信度标注
if result.decision != "SKIP":
    result.summary += f" | 置信度: {result.confidence_score:.0f}% ({available_count}/5)"
```

- [ ] **Step 3: 运行已有测试确保不破坏现有行为**

```bash
cd stock_screener && python -m pytest tests/test_enterprise_potential_holding_period.py -v
```

- [ ] **Step 4: Commit**

```bash
git add stock_screener/potential_analysis/scoring.py
git commit -m "feat: 缺失模块置信度惩罚替代50分中性补齐

缺失模块不再参与加权求和，confidence_score = 可用模块数/5。
< 2 模块可用时直接 skip，不输出总分。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: 市场差异化阈值

**Files:**
- Modify: `stock_screener/potential_analysis/scoring.py` (`EnterprisePotentialScorer.__init__`, `_rule_score`)
- Modify: `stock_screener/potential_analysis/strategizer.py` (`EnterprisePotentialAnalysisStrategizer.__init__`)

- [ ] **Step 1: 在 `EnterprisePotentialScorer` 支持 `market` 参数和差异化阈值**

当前 `__init__` 接受 `threshold: float = 70.0`，增加 `market: str = ""` 参数，按市场调整默认阈值：

```python
# 在 scoring.py 文件顶部（DEFAULT_WEIGHTS 之后）新增常量
MARKET_DEFAULT_THRESHOLDS = {
    "HK": {"buy": 75.0, "watch": 65.0},
    "US": {"buy": 75.0, "watch": 70.0},
    "A":  {"buy": 75.0, "watch": 70.0},
}
```

修改 `EnterprisePotentialScorer.__init__`：

```python
def __init__(self, weights=None, threshold=70.0, market="", llm_client=None):
    self.weights = weights or DEFAULT_WEIGHTS
    self.market = str(market or "").upper()
    # 市场差异化阈值：如果未显式传 threshold（使用默认70），则查表
    if threshold == 70.0 and self.market in MARKET_DEFAULT_THRESHOLDS:
        self.threshold = MARKET_DEFAULT_THRESHOLDS[self.market]["watch"]
    else:
        self.threshold = float(threshold)
    self._llm_client = llm_client
    self._scorers = {
        "macro": MacroScorer(), "company": CompanyScorer(),
        "valuation": ValuationScorer(), "industry": IndustryScorer(),
        "trading": TradingScorer(),
    }
```

- [ ] **Step 2: 在 `EnterprisePotentialAnalysisStrategizer.__init__` 中传递 market**

`strategizer.py` 的 `apply()` 方法中创建 scorer 时（约 line 145）：

```python
# 旧代码
self.scorer = EnterprisePotentialScorer(weights=self.weights, threshold=self.threshold)

# 新代码 — scorer 在 apply() 中按 market 动态创建，而非 __init__ 中固定
```

改为在 `apply()` 方法中延迟创建 scorer：

```python
def apply(self, stock: StockInfo, context: FilterContext) -> StrategizerOutput:
    market = stock.market or context.market
    code = stock.code
    name = stock.name

    # 按市场创建 scorer（阈值可能因市场不同）
    scorer = EnterprisePotentialScorer(
        weights=self.weights,
        threshold=self.threshold,
        market=market,
    )

    try:
        # ... 其余逻辑不变，使用 scorer 替代 self.scorer
```

同时在 `__init__` 中移除 `self.scorer = ...` 这一行。

- [ ] **Step 3: 运行测试**

```bash
cd stock_screener && python -m pytest tests/test_enterprise_potential_holding_period.py -v
```

- [ ] **Step 4: Commit**

```bash
git add stock_screener/potential_analysis/scoring.py stock_screener/potential_analysis/strategizer.py
git commit -m "feat: 五因素评分支持市场差异化阈值

HK WATCH=65, US/A WATCH=70, BUY 统一75。
阈值通过 params 可覆盖。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: MacroScorer LLM 增强接口

**Files:**
- Modify: `stock_screener/potential_analysis/scoring.py` (`MacroScorer.score`)

- [ ] **Step 1: 增加可选 `llm_enhancement` 参数**

修改 `MacroScorer.score` 签名和方法体：

```python
class MacroScorer(ModuleScorer):
    """宏观评分 — 基于宏观因子快照 + 可选 LLM 增强"""

    def score(self, macro: MacroSnapshot, llm_enhancement: Optional[dict] = None) -> float:
        score = 50.0  # 中性基准
        count = 0

        # ... 现有阈值打分逻辑不变 ...

        if count == 0:
            return 50.0

        score = round(max(0, min(100, score)), 1)

        # ── LLM 增强：MarketIntel 宏观评分作为 ±10 修正 ──
        if llm_enhancement and isinstance(llm_enhancement, dict):
            llm_macro = llm_enhancement.get("macro_score")
            if llm_macro is not None:
                try:
                    llm_val = float(llm_macro)
                    if llm_val >= 70:
                        score = min(100, score + 10)
                    elif llm_val < 40:
                        score = max(0, score - 10)
                except (TypeError, ValueError):
                    pass

        return score
```

- [ ] **Step 2: 修改 `_rule_score` 中 MacroScorer 的调用处**

当前 `_rule_score` 中 MacroScorer 调用（约 line 509）：

```python
if evidence.macro:
    try:
        scores["macro"] = self._scorers["macro"].score(evidence.macro)
    except Exception:
        data_gaps.append("macro 评分异常")
```

改为传入 LLM 增强数据（从 evidence 中读取 `_macro_llm_enhancement` 属性，该属性在 Task 6 中由 `_inject_llm_enhancements` 设置）：

```python
if evidence.macro:
    try:
        llm_enh = getattr(evidence, "_macro_llm_enhancement", None)
        scores["macro"] = self._scorers["macro"].score(evidence.macro, llm_enhancement=llm_enh)
    except Exception:
        data_gaps.append("macro 评分异常")
```

- [ ] **Step 3: Commit**

```bash
git add stock_screener/potential_analysis/scoring.py
git commit -m "feat: MacroScorer 增加可选的 LLM 增强参数

MarketIntel LLM 宏观评分 ≥70 加10分，<40 减10分。
不可用时规则型评分正常工作（fail-open）。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: 评分模型单元测试

**Files:**
- Create: `stock_screener/tests/test_unified_scoring.py`

- [ ] **Step 1: 创建测试文件，覆盖置信度惩罚**

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""统一评分模型单元测试"""

import pytest
from potential_analysis.models import (
    CompanySnapshot,
    EnterprisePotentialEvidencePackage,
    MacroSnapshot,
    IndustrySnapshot,
    ValuationSnapshot,
    TradingSnapshot,
)
from potential_analysis.scoring import EnterprisePotentialScorer, MARKET_DEFAULT_THRESHOLDS


def _make_evidence(macro=True, industry=True, company=True, valuation=True, trading=True):
    """构建测试用证据包，按需包含模块"""
    return EnterprisePotentialEvidencePackage(
        market="HK",
        code="00001",
        name="测试股票",
        macro=MacroSnapshot(market="HK", lpr_1y=3.0, cpi_yoy=2.0, pmi_manufacturing=52,
                            m2_yoy=10, vix=18, dxy=102) if macro else None,
        industry=IndustrySnapshot(market="HK", sector="科技", hot_sector_tags=["AI"]) if industry else None,
        company=CompanySnapshot(market="HK", code="00001", roic=15, gross_margin=35,
                               net_margin=12, revenue_growth=15, debt_to_equity=60) if company else None,
        valuation=ValuationSnapshot(market="HK", code="00001", pe_trailing=15, pb=2.0,
                                   peg=1.2, ev_ebitda=10) if valuation else None,
        trading=TradingSnapshot(market="HK", code="00001", pct_change=2.0, beta=1.0,
                               trend_strength=60, price_vs_50ma=3, volatility_30d=25) if trading else None,
        modules_available=[m for m, v in
            [("macro", macro), ("industry", industry), ("company", company),
             ("valuation", valuation), ("trading", trading)] if v],
        modules_missing=[m for m, v in
            [("macro", macro), ("industry", industry), ("company", company),
             ("valuation", valuation), ("trading", trading)] if not v],
    )


class TestConfidencePenalty:

    def test_all_five_modules_confidence_100(self):
        """5/5 模块可用 → confidence=100%"""
        evidence = _make_evidence()
        scorer = EnterprisePotentialScorer(market="HK")
        result = scorer.score(evidence)
        assert result.confidence_score == 100.0
        assert result.total_score > 0

    def test_three_modules_confidence_60(self):
        """3/5 模块可用 → confidence=60%，仅 3 模块参与加权"""
        evidence = _make_evidence(valuation=False, industry=False)
        scorer = EnterprisePotentialScorer(market="HK")
        result = scorer.score(evidence)
        assert result.confidence_score == 60.0
        assert result.total_score > 0

    def test_one_module_skip(self):
        """仅 1/5 模块可用 → confidence=20% → skip"""
        evidence = _make_evidence(industry=False, company=False, valuation=False, trading=False)
        scorer = EnterprisePotentialScorer(market="HK")
        result = scorer.score(evidence)
        assert result.decision == "SKIP"
        assert "数据不足" in result.summary


class TestMarketThresholds:

    def test_hk_watch_threshold_65(self):
        """HK 市场 WATCH 阈值=65"""
        scorer = EnterprisePotentialScorer(market="HK")
        assert scorer.threshold == 65.0

    def test_us_watch_threshold_70(self):
        """US 市场 WATCH 阈值=70"""
        scorer = EnterprisePotentialScorer(market="US")
        assert scorer.threshold == 70.0

    def test_custom_threshold_overrides_market(self):
        """显式传 threshold 覆盖市场默认"""
        scorer = EnterprisePotentialScorer(market="HK", threshold=80.0)
        assert scorer.threshold == 80.0


class TestDecisionAndHolding:

    def test_buy_decision(self):
        """总分 ≥75 → BUY + 持有周期"""
        evidence = _make_evidence()
        scorer = EnterprisePotentialScorer(market="HK")
        result = scorer.score(evidence)
        # 这个 evidence 分可能不够75，不强制 assert BUY
        # 仅验证 decision 字段非空
        assert result.decision in ("BUY", "WATCH", "SKIP")
        if result.decision == "BUY":
            assert result.holding_period

    def test_skip_decision(self):
        """总分 < threshold → SKIP，无持有周期"""
        evidence = _make_evidence()
        scorer = EnterprisePotentialScorer(market="US", threshold=95.0)  # 极高阈值强制 SKIP
        result = scorer.score(evidence)
        assert result.decision == "SKIP"
        assert result.holding_period == ""
```

- [ ] **Step 2: 运行测试**

```bash
cd stock_screener && python -m pytest tests/test_unified_scoring.py -v
```

- [ ] **Step 3: Commit**

```bash
git add stock_screener/tests/test_unified_scoring.py
git commit -m "test: 统一评分模型单元测试

覆盖置信度惩罚、市场差异化阈值、决策逻辑。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: MarketIntelMacroScoreStrategizer 降级

**Files:**
- Modify: `stock_screener/macro_strategies.py`

- [ ] **Step 1: 重写 `MarketIntelMacroScoreStrategizer.apply()`**

将 `apply()` 从 "独立评分+返回 pass/fail" 改为 "收集证据包写入 FilterContext 缓存，始终返回 satisfied=True"。

定位当前 `apply()` 方法（约 line 188–280），替换为：

```python
class MarketIntelMacroScoreStrategizer(Strategizer):
    """降级为 LLM 增强数据源 — 不再独立评分，仅供 EnterprisePotential 消费"""

    def __init__(
        self,
        threshold=60,
        refresh_policy: str = "cache_or_refresh",
        technical_weight=DEFAULT_TECHNICAL_WEIGHT,
        macro_weight=DEFAULT_MACRO_WEIGHT,
        name: str = "MarketIntelMacroScoreStrategizer",
        enabled: bool = True,
    ):
        super().__init__(name=name, enabled=enabled)
        self.threshold = _validate_threshold(threshold)
        self.refresh_policy = _validate_refresh_policy(refresh_policy)
        self.technical_weight = _validate_weight("technical_weight", technical_weight)
        self.macro_weight = _validate_weight("macro_weight", macro_weight)

    def apply(self, stock: StockInfo, context: FilterContext) -> StrategizerOutput:
        """收集 MarketIntel 证据包，写入 FilterContext 缓存供 EnterprisePotential 消费。

        始终返回 satisfied=True — 不阻断规则链。实际通过/不通过由 EnterprisePotential 决定。
        """
        cache_key = f"market_intel:{stock.code}"

        service = context.get_cache(_MARKET_INTEL_SERVICE_KEY)
        if service is None:
            context.set_cache(cache_key, None)
            return StrategizerOutput(
                name=self.name,
                satisfied=True,  # 不阻断链
                result="skip",
                reason="缺少 market_intel_service，跳过 MarketIntel 增强",
                details={"code": stock.code, "market_intel_available": False},
            )

        scorer = context.get_cache(_MACRO_SCORE_SCORER_KEY)
        if scorer is None:
            context.set_cache(cache_key, None)
            return StrategizerOutput(
                name=self.name,
                satisfied=True,
                result="skip",
                reason="缺少 macro_score_scorer，跳过 MarketIntel 增强",
                details={"code": stock.code, "market_intel_available": False},
            )

        market = stock.market or context.market
        force_refresh = self.refresh_policy == "force_refresh"

        try:
            market_bundle = _get_cached_market_bundle(
                service, context, market=market,
                force_refresh=force_refresh, refresh_policy=self.refresh_policy,
            )
            pack = EvidencePackBuilder(service).build(
                market=market, code=stock.code,
                market_bundle=market_bundle, force_refresh=force_refresh,
            )
            package = MacroEvidencePreprocessor().build(pack)

            if not package.has_scoreable_evidence:
                context.set_cache(cache_key, None)
                return StrategizerOutput(
                    name=self.name,
                    satisfied=True,
                    result="skip",
                    reason="缺少可评分的宏观证据",
                    details={"code": stock.code, "market_intel_available": False},
                )

            # 执行 LLM 评分（结果供 EnterprisePotential 消费）
            score = scorer.score(package, threshold=self.threshold)
            enhancement = {
                "macro_score": score.macro_score,
                "passed": score.passed,
                "sub_scores": dict(score.sub_scores),
                "summary": score.summary,
                "risks": list(score.risks),
                "evidence_digest": package.evidence_digest(),
                "source_status": dict(getattr(package, "source_status", {}) or {}),
                "temporal_findings": _temporal_findings_details(package),
            }
            context.set_cache(cache_key, enhancement)

            return StrategizerOutput(
                name=self.name,
                satisfied=True,  # 不阻断链
                result="pass",
                reason=f"MarketIntel LLM 宏观评分: {score.macro_score:.0f}",
                details={
                    "code": stock.code,
                    "market_intel_available": True,
                    "market_intel_macro_score": score.macro_score,
                },
            )
        except Exception as exc:
            context.set_cache(cache_key, None)
            return StrategizerOutput(
                name=self.name,
                satisfied=True,  # 异常也不阻断链
                result="error",
                reason=f"MarketIntel 增强失败: {exc}",
                details={"code": stock.code, "market_intel_available": False, "error": True},
            )
```

关键变更：
1. `apply()` 始终返回 `satisfied=True`（`result="pass"` 或 `"skip"`）— 不阻断规则链
2. LLM 评分结果（`enhancement` dict）写入 `FilterContext` 缓存键 `market_intel:{code}`
3. `CompanyEventHotSectorStrategizer` 和 `CompanyEventHotNewsStrategizer` **保持不变**，它们仍然是独立的策略器

- [ ] **Step 2: 运行已有测试确保不破坏**

```bash
cd stock_screener && python -m pytest tests/test_market_intel_reporting.py -v
```

- [ ] **Step 3: Commit**

```bash
git add stock_screener/macro_strategies.py
git commit -m "refactor: MarketIntelMacroScoreStrategizer 降级为 LLM 增强数据源

不再独立输出 pass/fail。apply() 始终返回 satisfied=True 不阻断链。
LLM 评分结果写入 FilterContext 缓存键 market_intel:{code}，
供 EnterprisePotentialAnalysisStrategizer 消费。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 6: EnterprisePotential 读取 MarketIntel 缓存

**Files:**
- Modify: `stock_screener/potential_analysis/strategizer.py` (`_inject_signal_analysis` → `_inject_llm_enhancements`)

- [ ] **Step 1: 重命名并扩展注入方法**

将 `_inject_signal_analysis` 重命名为 `_inject_llm_enhancements`，增加从 `FilterContext` 读取 `market_intel:{code}` 缓存的逻辑：

```python
def _inject_llm_enhancements(self, evidence: Any, context: FilterContext) -> None:
    """从 FilterContext 读取 SignalAnalysis LLM 和 MarketIntel 结果，注入证据包"""
    code = evidence.code

    # ── 1. SignalAnalysis LLM 增强（热点/事件/新闻）──
    analysis = context.get_cache(f"signal_analysis:{code}")
    if analysis is None:
        loader = context.get_cache("signal_analysis_loader")
        if callable(loader):
            try:
                from filters import StockInfo
                analysis = loader(StockInfo(
                    market=evidence.market, code=code, name=evidence.name))
            except Exception:
                pass

    if analysis is not None:
        # 注入行业热点标签
        if evidence.industry is not None:
            hot_sectors = list(getattr(analysis, "hot_sectors", []) or [])
            matched = list(getattr(analysis, "matched_hot_sectors", []) or [])
            all_hot = list(dict.fromkeys(hot_sectors + matched))
            if all_hot:
                evidence.industry.hot_sector_tags = all_hot
            mark = getattr(analysis, "hot_sector_mark", "")
            if mark in ("重点",):
                evidence.industry.policy_support = "strong"

        # 注入公司事件影响方向
        if evidence.company is not None:
            impact = getattr(analysis, "news_impact", "")
            if impact:
                evidence.company.event_impact = str(impact)
            company_news = getattr(analysis, "company_hot_news", []) or []
            if company_news:
                evidence.company.news_validation = True

    # ── 2. MarketIntel LLM 宏观增强 ──
    market_intel = context.get_cache(f"market_intel:{code}")
    if market_intel and isinstance(market_intel, dict):
        # 将 MarketIntel 增强数据附加到 evidence 上，供 MacroScorer 和 CompanyScorer 使用
        evidence._macro_llm_enhancement = market_intel

        # 如果 MarketIntel 识别到风险，追加到 evidence 的 data_gaps
        risks = market_intel.get("risks", [])
        if risks:
            for risk in risks:
                if risk not in (evidence.data_gaps or []):
                    evidence.data_gaps.append(f"[MarketIntel] {risk}")
```

- [ ] **Step 2: 更新 `apply()` 中的调用点**

将 `apply()` 方法中的 `self._inject_signal_analysis(evidence, context)` 改为 `self._inject_llm_enhancements(evidence, context)`。

- [ ] **Step 3: Commit**

```bash
git add stock_screener/potential_analysis/strategizer.py
git commit -m "feat: EnterprisePotential 读取 MarketIntel 缓存增强评分

_inject_signal_analysis → _inject_llm_enhancements，
新增 market_intel:{code} 缓存读取，LLM 宏观评分注入 MacroScorer。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 7: 简化 RuleEngine

**Files:**
- Modify: `stock_screener/rule_engine.py`

- [ ] **Step 1: 移除独立的 MarketIntel 判断逻辑**

当前 `RuleEngine` 中 `SIGNAL_ANALYSIS_IMPLEMENTATIONS` 和 `MARKET_INTEL_MACRO_IMPLEMENTATIONS` 是分开的两组常量。`requires_market_intel_macro_score()` 和 `requires_signal_analysis()` 也是独立的两个方法。

`requires_signal_analysis()` 仍然需要保留 — 它判断是否需要运行 SignalAnalysis LLM（时事分析），而 MarketIntel 的独立判断可以合并。

修改 `requires_macro_analysis()` 方法（约 line 571），移除对 `requires_market_intel_macro_score()` 的单独调用，改为统一由 `requires_enterprise_potential()` 触发：

```python
# 旧代码
def requires_macro_analysis(self) -> bool:
    return (self.requires_signal_analysis()
            or self.requires_market_intel_macro_score()
            or self.requires_enterprise_potential())

# 新代码 — MarketIntel 不再独立触发，统一由 EnterprisePotential 覆盖
def requires_macro_analysis(self) -> bool:
    return (self.requires_signal_analysis()
            or self.requires_enterprise_potential())
```

移除 `MARKET_INTEL_MACRO_IMPLEMENTATIONS` 常量（约 line 538），以及 `requires_market_intel_macro_score()` 方法（约 line 565）。

**注意：** `SIGNAL_ANALYSIS_IMPLEMENTATIONS` 保持不变 — `CompanyEventHotSectorStrategizer` 和 `CompanyEventHotNewsStrategizer` 仍然是独立的时事分析策略器，需要触发 SignalAnalysis LLM。

- [ ] **Step 2: Commit**

```bash
git add stock_screener/rule_engine.py
git commit -m "refactor: 移除 RuleEngine 中独立的 MarketIntel 判断

requires_market_intel_macro_score() 已删除。
MarketIntel 触发统一由 requires_enterprise_potential() 覆盖。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 8: 简化 screen_service.py 的 MarketIntel 注入

**Files:**
- Modify: `stock_screener/api/screen_service.py`

- [ ] **Step 1: 移除独立的 MarketIntel Service 注入**

当前 `run_screening_task()` 中（约 line 457–461）有独立的 MarketIntel 服务注入：

```python
# 旧代码
if rule_engine is not None and rule_engine.requires_market_intel_macro_score():
    market_intel_service = build_market_intel_service(mysql_config, enabled=True)
    macro_score_scorer = build_macro_score_scorer()
    context.set_cache("market_intel_service", market_intel_service)
    context.set_cache("macro_score_scorer", macro_score_scorer)
```

将此逻辑合并到 EnterprisePotential 的预取逻辑中（约 line 463–473）：

```python
# 新代码 — 将 MarketIntel service 注入合并到 enterprise potential 的设置中
if rule_engine is not None and rule_engine.requires_enterprise_potential():
    # MarketIntel service + scorer 注入（供 MarketIntelMacroScoreStrategizer 使用）
    market_intel_service = build_market_intel_service(mysql_config, enabled=True)
    macro_score_scorer = build_macro_score_scorer()
    context.set_cache("market_intel_service", market_intel_service)
    context.set_cache("macro_score_scorer", macro_score_scorer)

    # EnterprisePotential 批量预取
    from potential_analysis.service import EnterprisePotentialService
    service = EnterprisePotentialService()
    codes = [s.code for s in stock_infos]
    if verbose:
        print(f"📊 批量预取企业潜力数据: {len(codes)} 只股票 ({market})")
    report = service.prefetch_batch(market, codes, context)
    if verbose:
        print(f"   完成: {report.ok_count} 成功, {report.fail_count} 失败, "
              f"{report.duration_ms}ms")
    context.set_cache("enterprise_service", service)
```

- [ ] **Step 2: 运行已有测试确保不破坏**

```bash
cd stock_screener && python -m pytest tests/ -v -k "not test_macro" --timeout=30 2>&1 | head -50
```

- [ ] **Step 3: Commit**

```bash
git add stock_screener/api/screen_service.py
git commit -m "refactor: MarketIntel Service 注入合并到 EnterprisePotential 设置

移除独立的 requires_market_intel_macro_score() 分支。
MarketIntel service+scorer 注入合并到 enterprise potential 预取逻辑。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 9: 重构 aggregate_rule_scores

**Files:**
- Modify: `stock_screener/market_intel/macro_scoring.py` (`aggregate_rule_scores`)

- [ ] **Step 1: 重写 `aggregate_rule_scores` — 优先读五因素，fallback 旧逻辑**

将当前 `aggregate_rule_scores` 函数（约 line 86–135）替换为：

```python
def aggregate_rule_scores(
    rows: Iterable[Dict[str, Any]],
    technical_weight: float = DEFAULT_TECHNICAL_WEIGHT,
    macro_weight: float = DEFAULT_MACRO_WEIGHT,
) -> Dict[str, Any]:
    """聚合规则评分。优先读取 EnterprisePotential 五因素分数，检测不到时 fallback 旧逻辑。"""

    # ── 优先查找五因素分数（EnterprisePotential 输出）──
    for row in rows:
        if not isinstance(row, dict):
            continue
        details = row.get("details")
        if not isinstance(details, dict):
            continue
        if "total_score" in details:
            return {
                "technical_score": details.get("trading_score"),
                "macro_score": details.get("macro_score"),
                "final_score": details["total_score"],
                "module_scores": {
                    "macro": details.get("macro_score"),
                    "industry": details.get("industry_score"),
                    "company": details.get("company_score"),
                    "valuation": details.get("valuation_score"),
                    "trading": details.get("trading_score"),
                },
                "decision": details.get("decision"),
                "holding_period": details.get("holding_period"),
                "confidence": details.get("confidence_score"),
            }

    # ── Fallback: 旧逻辑（不启用 B 体系的规则链）──
    technical_values: List[float] = []
    macro_values: List[float] = []

    for row in rows:
        if not isinstance(row, dict):
            continue
        rule_type = str(row.get("rule_type") or "").lower()
        strategy_category = str(row.get("strategy_category") or "").lower()
        details = row.get("details")
        details = details if isinstance(details, dict) else {}
        result = str(row.get("result") or "").lower()
        if result not in {"pass", "fail"}:
            continue

        if strategy_category == "macro":
            if rule_type != "strategy":
                continue
            if "macro_score" in details:
                macro_score = _coerce_score(details.get("macro_score"))
                if macro_score is not None:
                    macro_values.append(_clamp_score(macro_score))
            continue

        if rule_type == "strategy":
            technical_values.append(100.0 if result == "pass" else 0.0)

    technical_score = _average(technical_values)
    macro_score = _average(macro_values)
    technical_weight_value = _to_float(technical_weight, DEFAULT_TECHNICAL_WEIGHT)
    macro_weight_value = _to_float(macro_weight, DEFAULT_MACRO_WEIGHT)
    final_score = _aggregate_scores(
        technical_score, macro_score,
        technical_weight=technical_weight_value,
        macro_weight=macro_weight_value,
    )

    return {
        "technical_score": technical_score,
        "macro_score": macro_score,
        "final_score": final_score,
        "technical_weight": technical_weight_value,
        "macro_weight": macro_weight_value,
    }
```

- [ ] **Step 2: 更新 `_score_weights_from_filter_details`，适配五因素输出**

在 `screen_service.py` 中，`_score_weights_from_filter_details`（约 line 275）当前从 `macro_score` 的 details 中提取 `technical_weight` 和 `macro_weight`。五因素模式下这些权重不再使用，但需要保留兼容。

无需修改 — `aggregate_rule_scores` 的五因素路径不使用 `_score_weights_from_filter_details` 的返回值，旧逻辑路径仍然使用。

- [ ] **Step 3: Commit**

```bash
git add stock_screener/market_intel/macro_scoring.py
git commit -m "refactor: aggregate_rule_scores 优先读取五因素分数

新增五因素优先路径: 从 filter_details 中检测 total_score，
直接返回 module_scores + decision + holding_period。
检测不到时 fallback 旧 0.6/0.4 加权逻辑。
向下兼容不启用 B 体系的规则链。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 10: 端到端集成验证

**Files:**
- 已有: `stock_screener/tests/test_unified_scoring.py` (扩展)

- [ ] **Step 1: 添加集成测试 — 模拟完整规则链执行**

在 `test_unified_scoring.py` 末尾追加：

```python
class TestIntegration:

    def test_aggregate_scores_five_factor_path(self):
        """aggregate_rule_scores 应能识别五因素输出"""
        from market_intel.macro_scoring import aggregate_rule_scores

        rows = [
            {"rule_type": "strategy", "strategy_category": "potential",
             "result": "pass",
             "details": {
                 "total_score": 72.5,
                 "macro_score": 68.0, "industry_score": 85.0,
                 "company_score": 72.0, "valuation_score": 55.0,
                 "trading_score": 60.0,
                 "decision": "WATCH", "holding_period": "3-9个月",
                 "confidence_score": 100.0,
             }},
        ]
        result = aggregate_rule_scores(rows)
        assert result["final_score"] == 72.5
        assert result["decision"] == "WATCH"
        assert result["holding_period"] == "3-9个月"
        assert result["module_scores"]["industry"] == 85.0

    def test_aggregate_scores_fallback(self):
        """无五因素数据时 fallback 旧逻辑"""
        from market_intel.macro_scoring import aggregate_rule_scores

        rows = [
            {"rule_type": "strategy", "strategy_category": "",
             "result": "pass", "details": {}},
            {"rule_type": "strategy", "strategy_category": "",
             "result": "fail", "details": {}},
        ]
        result = aggregate_rule_scores(rows)
        assert result["technical_score"] == 50.0  # 1 pass + 1 fail = 50
        assert result["macro_score"] is None

    def test_market_intel_strategizer_always_satisfied(self):
        """MarketIntelMacroScoreStrategizer 降级后始终 satisfied=True"""
        from filters import FilterContext, StockInfo
        from datetime import date
        from macro_strategies import MarketIntelMacroScoreStrategizer

        stock = StockInfo(market="HK", code="00001", name="测试")
        ctx = FilterContext(check_date=date.today(), market="HK")
        strategizer = MarketIntelMacroScoreStrategizer()

        output = strategizer.apply(stock, ctx)
        # 即使没有 market_intel_service，也应 satisfied=True（不阻断链）
        assert output.satisfied is True
```

- [ ] **Step 2: 运行全部相关测试**

```bash
cd stock_screener && python -m pytest tests/test_unified_scoring.py tests/test_enterprise_potential_holding_period.py -v
```

- [ ] **Step 3: Commit**

```bash
git add stock_screener/tests/test_unified_scoring.py
git commit -m "test: 集成测试 — 五因素 aggregate + MarketIntel 降级

覆盖 aggregate_rule_scores 五因素路径/fallback，
以及 MarketIntelMacroScoreStrategizer 降级后行为。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 11: 精简 SignalAnalysisResult.to_csv_columns()

**Files:**
- Modify: `stock_screener/signal_analysis/models.py` (`to_csv_columns`)

- [ ] **Step 1: 删除 4 个口径列、合并新闻/热点列、删除 JSON blob 列**

在 `SignalAnalysisResult.to_csv_columns()`（约 line 281–309）中：

```python
def to_csv_columns(self) -> Dict[str, str]:
    # 合并新闻: 市场热点新闻 + 公司热点新闻
    all_news = []
    for n in (self.market_hot_news or []):
        all_news.append(f"[市场]{n}")
    for n in (self.company_hot_news or []):
        all_news.append(f"[公司]{n}")

    # 合并热点板块: AI识别 + 匹配成功打★
    all_sectors = []
    matched = set(self.matched_hot_sectors or [])
    for s in (self.hot_sectors or []):
        all_sectors.append(f"★{s}" if s in matched else s)
    for s in (self.matched_hot_sectors or []):
        if s not in set(self.hot_sectors or []):
            all_sectors.append(f"★{s}")

    return {
        # 保留的核心 AI 分析列
        "AI分析状态": self.analysis_status,
        "信号可靠性评分": "" if self.reliability_score is None else f"{self.reliability_score:.2f}",
        "模型置信度": "" if self.confidence_score is None else f"{self.confidence_score:.2f}",
        "辅助方向判断": self.signal_bias,
        "关键利好因素": "；".join(self.positive_factors),
        "关键风险因素": "；".join(self.risk_factors),
        "宏观/政策因素": "；".join(self.macro_factors),
        "公司事件": "；".join(self.company_events),
        # 合并后的列
        "相关新闻": "；".join(all_news),
        "新闻影响判断": self.news_impact,
        "热点板块": "；".join(all_sectors),
        "热点板块标记": self.hot_sector_mark,
        # 保留的源信息列
        "信息来源": "；".join(self.source_urls),
        "数据缺失原因": "；".join(self.data_gaps),
    }
```

删除的列（不出现）：
- `信号可靠性评分口径`、`模型置信度口径`、`辅助方向判断口径`、`热点板块标记口径`
- `市场热点新闻`、`公司热点新闻` → 合并为 `相关新闻`
- `AI识别热点板块`、`匹配热点板块` → 合并为 `热点板块`
- `热点板块匹配理由`、`热点板块来源`、`热点板块关联度`
- `引用来源`、`因素引用`
- `新闻来源` → 合并到 `信息来源`

- [ ] **Step 2: Commit**

```bash
git add stock_screener/signal_analysis/models.py
git commit -m "refactor: 精简 SignalAnalysisResult.to_csv_columns()

删除4个口径常数列、6个冗余/长文本列、2个JSON blob列。
合并新闻(2→1)、热点板块(2→1)、来源(2→1)。
输出从26列减少到16列。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 12: 更新 AI_CSV_COLUMNS

**Files:**
- Modify: `stock_screener/signal_analysis/chain.py`

- [ ] **Step 1: 更新常量匹配 Task 11 的新列**

将 `AI_CSV_COLUMNS`（约 line 39–66）替换为：

```python
AI_CSV_COLUMNS = [
    "AI分析状态",
    "信号可靠性评分",
    "模型置信度",
    "辅助方向判断",
    "关键利好因素",
    "关键风险因素",
    "宏观/政策因素",
    "公司事件",
    "相关新闻",
    "新闻影响判断",
    "热点板块",
    "热点板块标记",
    "信息来源",
    "数据缺失原因",
]
```

- [ ] **Step 2: Commit**

```bash
git add stock_screener/signal_analysis/chain.py
git commit -m "refactor: 精简 AI_CSV_COLUMNS 匹配新 to_csv_columns 输出

26→14 列，与 SignalAnalysisResult.to_csv_columns() 对齐。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 13: 精简主力资金和左一 CSV 列

**Files:**
- Modify: `stock_screener/scheduled_daily_job.py`

- [ ] **Step 1: 精简 `MAIN_FORCE_CSV_COLUMNS`**

将 9 列精简为 4 列，删除 5 个原始明细长文本列：

```python
MAIN_FORCE_CSV_COLUMNS = [
    ("主力流出风险", "main_force_risk_level_text"),
    ("主力风险分", "main_force_risk_score_text"),
    ("主力风险信号", "main_force_risk_signals_text"),
    ("主力风险说明", "main_force_risk_summary"),
]
```

- [ ] **Step 2: 精简 `ZUOYI_CSV_COLUMNS`**

删除「左一中位线日期」（可从支撑区间推算）：

```python
ZUOYI_CSV_COLUMNS = [
    ("左一方向", "zuoyi_direction"),
    ("左一日期", "zuoyi_left_one_date"),
    ("左一顶", "zuoyi_left_one_high"),
    ("左一底", "zuoyi_left_one_low"),
    ("左一支撑区间", "zuoyi_support_zone"),
    ("左一突破日期", "zuoyi_breakout_date"),
    ("左一突破用时", "zuoyi_bars_to_breakout"),
]
```

- [ ] **Step 3: 同步删除 `_extract_zuoyi_csv_fields` 中的中位线提取**

在 `_extract_zuoyi_csv_fields` 函数（约 line 633）中，删除 `median_date` 相关逻辑：

```python
# 删除 extracted.append 中的 "median_date": str(signal.get("median_date") or "")
# 删除返回 dict 中的 "zuoyi_median_date": ...
```

- [ ] **Step 4: Commit**

```bash
git add stock_screener/scheduled_daily_job.py
git commit -m "refactor: 精简主力资金(9→4列)和左一(8→7列) CSV 输出

删除5个主力资金原始明细长文本列 + 左一中位线日期。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 14: CSV 追加五因素列

**Files:**
- Modify: `stock_screener/scheduled_daily_job.py` (`write_screening_csv`)

- [ ] **Step 1: 在 `write_screening_csv` 中追加五因素列**

在 `write_screening_csv` 函数末尾（约 line 724 之前），追加五因素列定义：

```python
FIVE_FACTOR_CSV_COLUMNS = [
    ("五因素总分", "five_factor_total"),
    ("五因素明细", "five_factor_detail"),
    ("决策", "five_factor_decision"),
    ("建议持有周期", "five_factor_holding"),
    ("评分置信度", "five_factor_confidence"),
]
```

在 `write_screening_csv` 的 columns 构建中（约 line 703 之后），追加：

```python
# 追加五因素列（如果 records 中存在相关数据）
if any(r.get("five_factor_total") for r in records):
    columns.extend(FIVE_FACTOR_CSV_COLUMNS)
```

- [ ] **Step 2: 在 `run_screening_task` 的 db_record 中填充五因素字段**

在 `screen_service.py` 的 `run_screening_task` 中（`db_record` 构建处，约 line 676），追加五因素字段：

```python
# 在 score_summary = aggregate_rule_scores(...) 之后
db_record = {
    # ... 现有字段不变 ...
    "five_factor_total": score_summary.get("final_score"),
    "five_factor_detail": _format_five_factor_detail(score_summary.get("module_scores")),
    "five_factor_decision": score_summary.get("decision"),
    "five_factor_holding": score_summary.get("holding_period"),
    "five_factor_confidence": _format_confidence(score_summary.get("confidence")),
}
```

并在 `screen_service.py` 顶部添加辅助函数：

```python
def _format_five_factor_detail(module_scores: Optional[dict]) -> str:
    """将模块分格式化为 M68·I85·C72·V55·T60 形式"""
    if not module_scores:
        return ""
    labels = {"macro": "M", "industry": "I", "company": "C", "valuation": "V", "trading": "T"}
    parts = []
    for key, label in labels.items():
        score = module_scores.get(key)
        if score is not None:
            parts.append(f"{label}{score:.0f}")
    return "·".join(parts)


def _format_confidence(confidence: Optional[float]) -> str:
    """格式化置信度"""
    if confidence is None:
        return ""
    return f"{confidence:.0f}%"
```

- [ ] **Step 3: 在 `load_passed_screening_records` 中传递五因素字段到 records**

确保 `load_passed_screening_records`（在 `scheduled_daily_job.py` 中）从 DB 的 `score_details` JSON 中提取五因素字段并放入返回的 record dict 中。

- [ ] **Step 4: Commit**

```bash
git add stock_screener/scheduled_daily_job.py stock_screener/api/screen_service.py
git commit -m "feat: CSV 追加五因素评分列

新增5列: 五因素总分、五因素明细(M·I·C·V·T)、决策、建议持有周期、评分置信度。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 15: 测试更新 — to_csv_columns 和 CSV 导出

**Files:**
- Modify: `stock_screener/tests/test_unified_scoring.py` (追加)

- [ ] **Step 1: 添加 CSV 列相关的测试**

```python
class TestCsvColumns:

    def test_to_csv_columns_no_redundant(self):
        """to_csv_columns 不应包含口径列"""
        from signal_analysis.models import SignalAnalysisResult

        result = SignalAnalysisResult(code="00001", name="测试")
        columns = result.to_csv_columns()
        # 口径列不应存在
        assert "信号可靠性评分口径" not in columns
        assert "模型置信度口径" not in columns
        assert "辅助方向判断口径" not in columns
        assert "热点板块标记口径" not in columns
        # JSON blob 列不应存在
        assert "引用来源" not in columns
        assert "因素引用" not in columns

    def test_to_csv_columns_merged_news(self):
        """相关新闻应合并市场和公司新闻"""
        from signal_analysis.models import SignalAnalysisResult

        result = SignalAnalysisResult(
            code="00001", name="测试",
            market_hot_news=["全球AI大会召开"],
            company_hot_news=["发布季度财报"],
        )
        columns = result.to_csv_columns()
        news = columns["相关新闻"]
        assert "[市场]全球AI大会召开" in news
        assert "[公司]发布季度财报" in news

    def test_to_csv_columns_merged_sectors(self):
        """热点板块应合并并给匹配成功的打★"""
        from signal_analysis.models import SignalAnalysisResult

        result = SignalAnalysisResult(
            code="00001", name="测试",
            hot_sectors=["AI", "机器人", "消费"],
            matched_hot_sectors=["AI"],
        )
        columns = result.to_csv_columns()
        sectors = columns["热点板块"]
        assert "★AI" in sectors
        assert "机器人" in sectors

    def test_format_five_factor_detail(self):
        """五因素明细格式化"""
        from api.screen_service import _format_five_factor_detail

        scores = {"macro": 68.0, "industry": 85.0, "company": 72.0,
                  "valuation": 55.0, "trading": 60.0}
        detail = _format_five_factor_detail(scores)
        assert detail == "M68·I85·C72·V55·T60"
```

- [ ] **Step 2: 运行全部测试**

```bash
cd stock_screener && python -m pytest tests/test_unified_scoring.py -v
```

- [ ] **Step 3: Commit**

```bash
git add stock_screener/tests/test_unified_scoring.py
git commit -m "test: CSV 列精简和五因素格式化测试

覆盖 to_csv_columns 去口径/合并逻辑，以及五因素明细格式化。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 16: 报告模板更新

**Files:**
- Modify: 报告生成模板（`signal_analysis/chain.py` 中的 `_render_artifact_report` 或独立模板文件）

- [ ] **Step 1: 定位报告渲染逻辑并增加五因素章节**

报告渲染当前在 `_render_artifact_report()`（`chain.py` 约 line 1058）中。需要在该函数中：

1. 总览表增加 3 列（五因素、决策、持有建议）
2. 新增第三节「五因素评分明细」
3. 核心结论增加五因素概览

由于报告模板逻辑较复杂且可能依赖 LLM 生成，此任务的具体实现需查看完整的 `_render_artifact_report` 和相关 prompt 模板。

**本任务的实现细节留到执行时根据实际模板代码确定。** 核心原则：
- 表头增加 `五因素 | 决策 | 持有建议` 三列
- 仅对 BUY/WATCH 股票渲染「五因素评分明细」子节
- 核心结论追加五因素平均分和最弱维度

- [ ] **Step 2: Commit**

```bash
git add stock_screener/signal_analysis/chain.py
git commit -m "feat: 报告增加五因素评分章节

总览表5→8列。新增五因素评分明细（仅BUY/WATCH展开）。
核心结论增加五因素概览。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 17: 最终验证 & 清理

- [ ] **Step 1: 运行全部测试套件**

```bash
cd stock_screener && python -m pytest tests/ -v --timeout=60 2>&1 | tail -30
```

- [ ] **Step 2: 检查无 import 错误**

```bash
cd stock_screener && python -c "
from potential_analysis.scoring import EnterprisePotentialScorer, MARKET_DEFAULT_THRESHOLDS
from potential_analysis.strategizer import EnterprisePotentialAnalysisStrategizer
from macro_strategies import MarketIntelMacroScoreStrategizer
from market_intel.macro_scoring import aggregate_rule_scores
from signal_analysis.models import SignalAnalysisResult
from api.screen_service import _format_five_factor_detail
print('All imports OK')
"
```

- [ ] **Step 3: 运行已有集成测试确保无回归**

```bash
cd stock_screener && python -m pytest tests/test_enterprise_potential_holding_period.py tests/test_market_intel_reporting.py -v
```

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "chore: 最终验证通过，统一评分模型实现完成

阶段一(Task 1-10): 评分增强 + MarketIntel合并 + aggregate重构
阶段二(Task 11-16): CSV精简(51→38列) + 报告升级

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## 实现顺序依赖

```
Task 1 (置信度惩罚) ──┐
Task 2 (市场阈值)   ──┤
Task 3 (LLM增强接口)──┤
                      ├──→ Task 4 (单元测试) ──→ Task 5 (MarketIntel降级)
                      │                              │
                      │                              ├──→ Task 6 (读取缓存)
                      │                              │        │
                      │                              │        ├──→ Task 7 (RuleEngine)
                      │                              │        ├──→ Task 8 (screen_service)
                      │                              │        └──→ Task 9 (aggregate)
                      │                              │              │
                      │                              └──────────────┤
                      │                                             │
                      └──→ Task 10 (集成测试) ←────────────────────┘
                               │
                               ├──→ Task 11 (to_csv_columns) ──→ Task 12 (AI_CSV_COLUMNS)
                               ├──→ Task 13 (主力/左一列精简)
                               ├──→ Task 14 (五因素列追加)
                               ├──→ Task 15 (CSV测试)
                               └──→ Task 16 (报告模板)
                                        │
                                        └──→ Task 17 (最终验证)
```

Task 1-3 可并行。Task 5 依赖 Task 1-3 完成。Task 7-9 可并行。Task 11-14 可并行。
