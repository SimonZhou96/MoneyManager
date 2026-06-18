# unified_bullish_top20 优化实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 拆分 entry_score / holding_score 双评分体系，新增 6 个持有层宏观规则，重写散户友好报告模板。

**Architecture:** 方案 B「评分层抽离」——新建 `scoring/` 模块负责分数聚合，RuleEngine 保持不动。5 个市场级规则通过 `MarketCache` 预计算缓存，1 个个股级规则（EarningsRevision）在 Top20 上逐只执行。报告层新建 `RetailReportRenderer` 替代旧的 markdown 拼接。

**Tech Stack:** Python 3.12+, pandas, numpy, yfinance, akshare, Futu OpenD API, Tavily API

## Global Constraints

- 核心链路不动：全部股票 → 过滤 → 技术入围 → Top20 → 后置宏观评估
- 三模式兼容：desktop app / web frontend / interactive shell
- 所有 yfinance 调用必须使用 `yf_ratelimit.py` 共享频控模块
- 当前阶段不新增外部数据源（FRED/Tushare 等 P2 预留接口即可）
- OOP 风格，遵循现有 `Strategizer(ABC)` / `StrategizerOutput` / `StrategizerChain` 模式
- 写入 CLAUDE.md Fix Log 记录所有修改

---

## File Structure

### 新建文件（7 个）

| 文件 | 职责 |
|---|---|
| `scoring/__init__.py` | 模块入口，导出主要类和函数 |
| `scoring/models.py` | `EntryScoreBreakdown`, `HoldingScoreBreakdown`, `MarketTemperature`, `CreditRiskResult`, `MarketBreadthResult`, `LiquidityNowcastResult`, `EarningsRevisionResult`, `PolicyEventResult`, `CommodityShockResult` |
| `scoring/constants.py` | `ENTRY_WEIGHTS`, `HOLDING_WEIGHTS`, `DECISION_MAP_ENTRY`, `DECISION_MAP_HOLDING`, `RULE_TO_MODULE_MAP` |
| `scoring/entry_scorer.py` | `EntryScorer` 类 —— 消费 filter_details 中 38 条技术规则 → 5 模块聚合 |
| `scoring/holding_scorer.py` | `HoldingScorer` 类 —— 消费 filter_details + MarketTemperature + LLM 结果 → 6 维度聚合 |
| `scoring/market_cache.py` | `MarketCache` 类 —— 5 个市场级规则预计算 + TTL 缓存 |
| `signal_analysis/renderers.py` | `RetailReportRenderer` 类 —— 新报告模板渲染 |

### 修改文件（14 个）

| 文件 | 变更 |
|---|---|
| `strategizers.py` | 新增 6 个 Strategizer 类 + 5 个 EntryScore 聚合器 |
| `strategy.py` | 新增 `MarketBreadthResult`, `CreditRiskResult`, `LiquidityNowcastResult` 等 dataclass；新增 `compute_market_breadth()`, `compute_credit_risk_proxy()` |
| `rule_engine.py` | `RuleRegistry.default()` 注册新规则；`evaluate_macro_rules_for_top20()` 注入市场级缓存 |
| `db.py` | `DEFAULT_RULE_METADATA` 新增/移除条目；新增 migration 方法 |
| `api/screen_service.py` | 集成 MarketCache + EntryScorer + HoldingScorer；Top20 排序改为 entry_score |
| `signal_analysis/chain.py` | 切换到 RetailReportRenderer；加载 MarketTemperature |
| `signal_analysis/hot_sectors.py` | 新增 `HotSectorClassifier` |
| `signal_analysis/models.py` | `UNIFIED_SCORE_WEIGHTS` 标记 deprecated |
| `market_intel/macro_scoring.py` | `aggregate_rule_scores` 标记 deprecated |
| `market_intel/reporting.py` | `render_multi_stock_report` 标记 deprecated |
| `tests/test_entry_scorer.py` | 新建 |
| `tests/test_holding_scorer.py` | 新建 |
| `tests/test_market_cache.py` | 新建 |
| `tests/test_new_strategizers.py` | 新建 |
| `tests/test_e2e_unified_bullish_top20_hk02685.py` | 更新规则计数 |
| `tests/test_e2e_unified_bullish_top20_scoring.py` | 适配新评分 |
| `tests/test_e2e_baseline_3stocks.py` | 验收测试 |

---

## 通用约定

所有 Python 文件头遵循现有注释风格：

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模块简要说明（中文）。
"""
```

双路径 import 模式（与 `strategizers.py` / `strategy.py` 一致）：

```python
if __package__:
    from .xxx import YYY
else:
    from xxx import YYY
```

---

## Phase 1: 基础设施（Task 1-9）

### Task 1: 创建 scoring 模块骨架与数据模型

**Files:**
- Create: `stock_screener/scoring/__init__.py`
- Create: `stock_screener/scoring/models.py`
- Create: `stock_screener/scoring/constants.py`

**Interfaces:**
- Produces: `EntryScoreBreakdown`, `HoldingScoreBreakdown`, `MarketTemperature`, `CreditRiskResult`, `MarketBreadthResult`, `LiquidityNowcastResult`, `EarningsRevisionResult`, `PolicyEventResult`, `CommodityShockResult` dataclasses
- Produces: `ENTRY_WEIGHTS`, `HOLDING_WEIGHTS`, `DECISION_MAP_ENTRY`, `DECISION_MAP_HOLDING`, `RULE_TO_MODULE_MAP`

- [ ] **Step 1: 创建 `scoring/__init__.py`**

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
评分模块：将规则引擎输出的 filter_details 聚合为 entry_score（入场信号分）
和 holding_score（持有价值分）。与 RuleEngine 解耦，只消费其输出。
"""

from .models import (
    EntryScoreBreakdown,
    HoldingScoreBreakdown,
    MarketTemperature,
    CreditRiskResult,
    MarketBreadthResult,
    LiquidityNowcastResult,
    EarningsRevisionResult,
    PolicyEventResult,
    CommodityShockResult,
)
from .constants import (
    ENTRY_WEIGHTS,
    HOLDING_WEIGHTS,
    DECISION_MAP_ENTRY,
    DECISION_MAP_HOLDING,
    RULE_TO_MODULE_MAP,
)
from .entry_scorer import EntryScorer
from .holding_scorer import HoldingScorer
from .market_cache import MarketCache

__all__ = [
    "EntryScoreBreakdown",
    "HoldingScoreBreakdown",
    "MarketTemperature",
    "CreditRiskResult",
    "MarketBreadthResult",
    "LiquidityNowcastResult",
    "EarningsRevisionResult",
    "PolicyEventResult",
    "CommodityShockResult",
    "ENTRY_WEIGHTS",
    "HOLDING_WEIGHTS",
    "DECISION_MAP_ENTRY",
    "DECISION_MAP_HOLDING",
    "RULE_TO_MODULE_MAP",
    "EntryScorer",
    "HoldingScorer",
    "MarketCache",
]
```

- [ ] **Step 2: 创建 `scoring/constants.py`**

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
评分常量：权重、决策映射表、规则到模块的映射。
"""

# ── EntryScore 5 模块权重 ──────────────────────────────────────
ENTRY_WEIGHTS = {
    "trend": 0.30,
    "momentum": 0.20,
    "volume": 0.20,
    "breakout": 0.20,
    "volatility_risk": 0.10,
}

# ── HoldingScore 6 维度权重 ────────────────────────────────────
HOLDING_WEIGHTS = {
    "enterprise": 0.35,
    "event": 0.20,
    "liquidity": 0.15,
    "macro_credit": 0.15,
    "earnings_revision": 0.10,
    "llm_review": 0.05,
}

# ── 决策映射 ────────────────────────────────────────────────────
DECISION_MAP_ENTRY = {
    (80, 100): "STRONG_BUY",
    (65, 80): "VALID_BUY",
    (50, 65): "WEAK_BUY",
    (0, 50): "NO_BUY",
}

DECISION_MAP_HOLDING = {
    (80, 100): "WORTH_HOLDING",
    (70, 80): "CAN_HOLD",
    (60, 70): "SHORT_TERM",
    (50, 60): "LIGHT_POSITION",
    (0, 50): "NOT_RECOMMENDED",
}

# ── 38 条技术规则 → 5 模块映射（key = rule_key，value = 模块内权重） ──
RULE_TO_MODULE_MAP = {
    # 趋势结构 (trend) — 权重 30%
    "zuoyi_bullish_signal": ("trend", 1.5),
    "ema_breakout": ("trend", 1.0),
    "sma_golden_cross": ("trend", 1.0),
    "ema_golden_cross": ("trend", 1.0),
    "price_above_ma50": ("trend", 0.5),
    "price_above_ma200": ("trend", 0.5),

    # 动量状态 (momentum) — 权重 20%
    "energy_phase_bullish": ("momentum", 1.5),
    "macd_bullish_cross": ("momentum", 1.0),
    "kdj_bullish_cross": ("momentum", 0.8),
    "rsi_bullish_rebound": ("momentum", 0.8),
    "daily_rise_4_45": ("momentum", 0.5),

    # 成交确认 (volume) — 权重 20%
    "volume_spike_prior3": ("volume", 1.5),
    "volume_price_breakout": ("volume", 1.0),
    "volume_ratio_high": ("volume", 0.8),

    # 突破质量 (breakout) — 权重 20%
    "zuoyi_bullish_signal": ("breakout", 1.0),  # 同时贡献两个模块（有意为之）
    "atr_breakout": ("breakout", 1.0),
    "bollinger_upper_breakout": ("breakout", 0.8),
    "new_high_breakout": ("breakout", 1.2),

    # 波动风险 (volatility_risk) — 权重 10%
    "atr_breakout": ("volatility_risk", 0.5),
    "bollinger_bandwidth_high": ("volatility_risk", 0.5),
}

# ── 波动风险维度：非规则条目（直接计算指标） ──
VOLATILITY_RISK_METRICS = {
    "max_drawdown_20d": 0.8,
    "intraday_amplitude": 0.3,
}

# ── 评分映射参数 ────────────────────────────────────────────────
# module_score = min(100, BASE_SCORE + raw_weighted * SCALE_FACTOR)
BASE_SCORE = 50.0
SCALE_FACTOR = 12.0  # 约 4 条 x1.0 规则命中 → 满分


def resolve_decision(score: float, decision_map: dict) -> str:
    """根据分数查找决策标签。"""
    for (lo, hi), decision in decision_map.items():
        if lo <= score < hi or (hi == 100 and lo <= score <= hi):
            return decision
    return "UNKNOWN"
```

- [ ] **Step 3: 创建 `scoring/models.py`**

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
评分数据模型：EntryScore、HoldingScore、MarketTemperature 及其子结构。
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# ═══════════════════════════════════════════════════════════════
# 市场级规则结果 dataclass
# ═══════════════════════════════════════════════════════════════

@dataclass
class CreditRiskResult:
    """信用风险评估结果。"""
    score: float                            # 0-100，越低风险越高
    level: str                              # "low" | "elevated" | "high"
    indicators: Dict[str, float] = field(default_factory=dict)
    reason: str = ""
    data_sources: List[str] = field(default_factory=lambda: ["YFinance"])


@dataclass
class MarketBreadthResult:
    """市场宽度评估结果。"""
    score: float                            # 0-100
    above_ma50_pct: float = 0.0
    above_ma200_pct: float = 0.0
    advance_decline_ratio: float = 1.0
    new_high_52w: int = 0
    new_low_52w: int = 0
    explanation: str = ""


@dataclass
class LiquidityNowcastResult:
    """资金流动性即期评估结果。"""
    score: float                            # 0-100
    fund_flow_direction: str = "neutral"    # "inflow" | "neutral" | "outflow"
    metrics: Dict[str, float] = field(default_factory=dict)
    explanation: str = ""


@dataclass
class EarningsRevisionResult:
    """盈利预期修正评估结果（个股级）。"""
    score: float                            # 0-100
    earnings_trend: str = "stable"          # "improving" | "stable" | "deteriorating"
    evidence: List[str] = field(default_factory=list)
    risks: List[str] = field(default_factory=list)
    confidence: float = 0.5


@dataclass
class PolicyEventResult:
    """政策事件风险评估结果。"""
    score: float                            # 0-100，越低政策风险越高
    direction: str = "neutral"              # "positive" | "neutral_positive" | "neutral" | "neutral_negative" | "negative"
    related_sectors: List[str] = field(default_factory=list)
    risk_events: List[str] = field(default_factory=list)
    explanation: str = ""


@dataclass
class CommodityShockResult:
    """大宗商品冲击评估结果。"""
    score: float                            # 0-100，多商品剧烈波动 → 低分
    shocks: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    explanation: str = ""


# ═══════════════════════════════════════════════════════════════
# 市场温度计
# ═══════════════════════════════════════════════════════════════

@dataclass
class MarketTemperature:
    """市场温度计 —— 每个市场一份，筛选开始时预计算。"""
    market: str

    credit_risk: Optional[CreditRiskResult] = None
    market_breadth: Optional[MarketBreadthResult] = None
    liquidity: Optional[LiquidityNowcastResult] = None
    policy_event: Optional[PolicyEventResult] = None
    commodity_shock: Optional[CommodityShockResult] = None

    computed_at: str = ""                   # ISO timestamp
    ttl_minutes: int = 60

    # 面向报告的摘要字段（由 MarketCache._build_summaries() 填充）
    temperature_summary: str = ""           # "偏强 / 中性 / 偏弱"
    money_making_summary: str = ""          # "好 / 一般 / 差"
    capital_env_summary: str = ""           # "流入 / 中性 / 流出"
    risk_appetite_summary: str = ""         # "高 / 中 / 低"
    hot_clarity_summary: str = ""           # "清晰 / 一般 / 混乱"

    def is_stale(self) -> bool:
        """检查缓存是否过期。"""
        if not self.computed_at:
            return True
        from datetime import datetime, timezone, timedelta
        try:
            computed = datetime.fromisoformat(self.computed_at)
            return (datetime.now(timezone.utc) - computed).total_seconds() > self.ttl_minutes * 60
        except (ValueError, TypeError):
            return True

    def to_filter_details(self) -> List[Dict[str, Any]]:
        """将 5 个市场级规则结果转为 filter_detail 条目，供注入 Top20 筛选结果。"""
        details = []
        for key, result, name in [
            ("credit_risk_regime", self.credit_risk, "信用风险环境"),
            ("market_breadth_regime", self.market_breadth, "市场宽度环境"),
            ("liquidity_nowcast", self.liquidity, "资金流动性即时报"),
            ("policy_event_risk", self.policy_event, "政策事件风险"),
            ("commodity_shock", self.commodity_shock, "大宗商品冲击"),
        ]:
            if result is None:
                details.append({
                    "rule_key": key,
                    "rule_name": name,
                    "rule_type": "strategy",
                    "strategy_category": "macro",
                    "result": "fail",
                    "reason": "数据不可用",
                    "details": {"error": "data_unavailable"},
                })
            else:
                details.append({
                    "rule_key": key,
                    "rule_name": name,
                    "rule_type": "strategy",
                    "strategy_category": "macro",
                    "result": "pass" if result.score >= 50 else "fail",
                    "reason": result.explanation or result.reason,
                    "details": {"score": result.score},
                })
        return details


# ═══════════════════════════════════════════════════════════════
# EntryScore / HoldingScore
# ═══════════════════════════════════════════════════════════════

@dataclass
class EntryScoreBreakdown:
    """入场信号分 —— 回答「现在有没有买点」。"""
    entry_score: float = 50.0               # 0-100
    entry_decision: str = "NO_BUY"

    trend_score: float = 50.0
    momentum_score: float = 50.0
    volume_score: float = 50.0
    breakout_score: float = 50.0
    volatility_risk_score: float = 50.0

    matched_rules: List[str] = field(default_factory=list)
    rule_details: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    formula: str = ""


@dataclass
class HoldingScoreBreakdown:
    """持有价值分 —— 回答「买了之后值不值得拿」。"""
    holding_score: float = 50.0             # 0-100
    holding_decision: str = "NOT_RECOMMENDED"

    enterprise_score: float = 50.0
    event_score: float = 50.0
    liquidity_score: float = 50.0
    macro_credit_score: float = 50.0
    earnings_revision_score: float = 50.0
    llm_review_score: float = 50.0

    holding_period: str = "短线"
    position_suggestion: str = "轻仓试探"
    risk_level: str = "medium"
    main_drivers: List[str] = field(default_factory=list)
    main_risks: List[str] = field(default_factory=list)
    data_gaps: List[str] = field(default_factory=list)
    formula: str = ""
```

- [ ] **Step 4: 验证模块可导入**

```bash
cd stock_screener && python3 -c "import sys; sys.path.insert(0,'.'); from scoring.models import EntryScoreBreakdown, HoldingScoreBreakdown, MarketTemperature; print('models OK'); from scoring.constants import ENTRY_WEIGHTS, RULE_TO_MODULE_MAP; print('constants OK')"
```

Expected: 无错误输出 "models OK" "constants OK"

- [ ] **Step 5: Commit**

```bash
git add stock_screener/scoring/__init__.py stock_screener/scoring/models.py stock_screener/scoring/constants.py
git commit -m "feat(scoring): add scoring module skeleton with models and constants

- EntryScoreBreakdown / HoldingScoreBreakdown dataclasses
- MarketTemperature with 5 sub-result types
- ENTRY_WEIGHTS / HOLDING_WEIGHTS / RULE_TO_MODULE_MAP constants
- Decision map for entry/holding score thresholds

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: 创建 MarketCache 框架 + CreditRiskRegime 实现

**Files:**
- Create: `stock_screener/scoring/market_cache.py`
- Test: `stock_screener/tests/test_market_cache.py`

**Interfaces:**
- Produces: `MarketCache` 类 —— `get_or_compute(market: str) -> MarketTemperature`
- Produces: `_compute_credit_risk(market, ...) -> CreditRiskResult`
- Produces: MarketTemperature 的 TTL 缓存逻辑 + `_build_summaries()` 摘要生成

- [ ] **Step 1: 写入测试 `tests/test_market_cache.py`**

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MarketCache 单元测试：TTL 过期、摘要生成、to_filter_details。"""

import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scoring.market_cache import MarketCache
from scoring.models import MarketTemperature, CreditRiskResult, MarketBreadthResult


class TestMarketCacheTTL(unittest.TestCase):
    """缓存 TTL 测试。"""

    def setUp(self):
        self.cache = MarketCache()

    def test_stale_when_empty(self):
        """无缓存时 is_stale() 返回 True。"""
        temp = MarketTemperature(market="HK")
        self.assertTrue(temp.is_stale())

    def test_not_stale_within_ttl(self):
        """TTL 内不 stale。"""
        temp = MarketTemperature(
            market="HK",
            computed_at=datetime.now(timezone.utc).isoformat(),
            ttl_minutes=60,
        )
        self.assertFalse(temp.is_stale())

    def test_stale_after_ttl(self):
        """超过 TTL 后 stale。"""
        temp = MarketTemperature(
            market="HK",
            computed_at=(datetime.now(timezone.utc) - timedelta(minutes=61)).isoformat(),
            ttl_minutes=60,
        )
        self.assertTrue(temp.is_stale())

    def test_cache_hit_within_ttl(self):
        """缓存命中：TTL 内直接返回，不重新计算。"""
        temp = MarketTemperature(
            market="A",
            computed_at=datetime.now(timezone.utc).isoformat(),
            credit_risk=CreditRiskResult(score=72, level="low"),
        )
        self.cache._cache["A"] = temp
        result = self.cache.get_or_compute("A")
        self.assertEqual(result.credit_risk.score, 72)

    @patch.object(MarketCache, '_compute_all', return_value=MarketTemperature(market="A"))
    def test_cache_miss_recomputes(self, mock_compute):
        """缓存过期/缺失 → 重新计算。"""
        stale = MarketTemperature(
            market="A",
            computed_at=(datetime.now(timezone.utc) - timedelta(minutes=61)).isoformat(),
            ttl_minutes=60,
        )
        self.cache._cache["A"] = stale
        self.cache.get_or_compute("A")
        mock_compute.assert_called_once_with("A")


class TestMarketTemperatureSummaries(unittest.TestCase):
    """摘要生成测试。"""

    def test_summaries_bullish(self):
        """全面偏强的市场温度。"""
        temp = MarketTemperature(
            market="A",
            credit_risk=CreditRiskResult(score=75, level="low"),
            market_breadth=MarketBreadthResult(score=68, above_ma50_pct=0.62),
        )
        temp.temperature_summary = "偏强"
        temp.money_making_summary = "好"
        temp.capital_env_summary = "流入"
        temp.risk_appetite_summary = "高"
        temp.hot_clarity_summary = "清晰"

        self.assertEqual(temp.temperature_summary, "偏强")

    def test_to_filter_details_all_available(self):
        """5 个市场级结果全部可用时，生成 5 条 filter_detail。"""
        temp = MarketTemperature(
            market="HK",
            credit_risk=CreditRiskResult(score=72, level="low", reason="信用环境稳定"),
        )
        details = temp.to_filter_details()
        self.assertEqual(len(details), 5)
        # credit_risk 有数据
        cr = [d for d in details if d["rule_key"] == "credit_risk_regime"][0]
        self.assertEqual(cr["result"], "pass")
        self.assertIn("score", cr["details"])
        # market_breadth 无数据
        mb = [d for d in details if d["rule_key"] == "market_breadth_regime"][0]
        self.assertEqual(mb["result"], "fail")


if __name__ == "__main__":
    unittest.main(verbosity=2)
```

- [ ] **Step 2: 验证测试失败**

```bash
cd stock_screener && python3 -m pytest tests/test_market_cache.py -v 2>&1 | head -5
```

Expected: ModuleNotFoundError (market_cache.py 尚未创建)

- [ ] **Step 3: 写入 `scoring/market_cache.py`**

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
市场缓存：在筛选开始时预计算 5 个市场级规则，结果缓存 60 分钟。
同一市场所有 Top20 股票共用一份 MarketTemperature，避免重复 API 调用。
"""

from datetime import datetime, timezone
from typing import Dict, Optional

from .models import (
    MarketTemperature,
    CreditRiskResult,
    MarketBreadthResult,
    LiquidityNowcastResult,
    PolicyEventResult,
    CommodityShockResult,
)


class MarketCache:
    """市场级规则缓存。"""

    def __init__(self):
        self._cache: Dict[str, MarketTemperature] = {}

    def get_or_compute(self, market: str) -> MarketTemperature:
        """获取市场温度计。如缓存未过期则直接返回；否则重新计算全部 5 个维度。"""
        cached = self._cache.get(market)
        if cached is not None and not cached.is_stale():
            return cached
        result = self._compute_all(market)
        result.computed_at = datetime.now(timezone.utc).isoformat()
        self._cache[market] = result
        return result

    def invalidate(self, market: str) -> None:
        """强制使指定市场的缓存失效。"""
        self._cache.pop(market, None)

    # ── 内部计算 ───────────────────────────────────────────────

    def _compute_all(self, market: str) -> MarketTemperature:
        """顺序计算 5 个维度。每个维度失败不阻塞其他维度。"""
        temp = MarketTemperature(market=market)

        temp.credit_risk = self._compute_credit_risk(market)
        temp.market_breadth = self._compute_market_breadth(market)
        temp.liquidity = self._compute_liquidity(market)
        temp.policy_event = self._compute_policy_event(market)
        temp.commodity_shock = self._compute_commodity_shock(market)

        self._build_summaries(temp)
        return temp

    def _compute_credit_risk(self, market: str) -> Optional[CreditRiskResult]:
        """用 YFinance ETF 代理指标计算信用风险。

        HK/US: HYG / LQD / JNK / IEF / VIX / DXY
        A: AKShare 信用利差（Phase 2 接入，Phase 1 用 YFinance 代理）
        """
        try:
            import yfinance as yf
            from ..yf_ratelimit import yf_sleep

            if market in ("HK", "US"):
                tickers = yf.Tickers("HYG LQD JNK IEF ^VIX DX-Y.NYB")
                yf_sleep()

                def _pct_change(hist, days=20):
                    if hist is None or hist.empty or len(hist) < days:
                        return 0.0
                    closes = hist["Close"]
                    return float((closes.iloc[-1] - closes.iloc[-days]) / closes.iloc[-days])

                def _max_drawdown(hist, days=20):
                    if hist is None or hist.empty or len(hist) < days:
                        return 0.0
                    closes = hist["Close"].tail(days)
                    peak = closes.cummax()
                    return float(((closes - peak) / peak).min())

                hyg_ret = _pct_change(tickers.tickers.get("HYG", MagicMock()).history(period="1mo"))
                lqd_ret = _pct_change(tickers.tickers.get("LQD", MagicMock()).history(period="1mo"))
                hyg_dd = _max_drawdown(tickers.tickers.get("HYG", MagicMock()).history(period="1mo"))

                score = 50.0
                if hyg_ret < -0.02: score -= 20
                if hyg_ret - lqd_ret < -0.01: score -= 15
                if hyg_dd > 0.03: score -= 10
                if hyg_ret > 0.02: score += 15

                vix_hist = tickers.tickers.get("^VIX", MagicMock()).history(period="5d")
                vix = float(vix_hist["Close"].iloc[-1]) if not vix_hist.empty else 20.0
                if vix > 25: score -= 10
                if vix < 15: score += 10

                score = max(0.0, min(100.0, score))
                level = "low" if score >= 70 else ("elevated" if score >= 50 else "high")
                return CreditRiskResult(
                    score=score, level=level,
                    indicators={"hyg_ret": round(hyg_ret, 4), "lqd_ret": round(lqd_ret, 4), "vix": vix},
                    reason=f"HYG近20日{'上涨' if hyg_ret>0 else '下跌'}{abs(hyg_ret)*100:.1f}%，"
                           f"VIX={vix:.1f}，信用环境{'稳定' if score>=50 else '承压'}",
                )

            elif market == "A":
                # Phase 1: A 股信用风险用 Shibor + 信用债收益率代理
                # 简化实现：返回中性
                return CreditRiskResult(
                    score=50.0, level="low",
                    reason="A股信用风险暂用中性（Phase 2 接入 AKShare 信用利差）",
                )

            return None

        except Exception as e:
            return CreditRiskResult(
                score=50.0, level="low",
                reason=f"信用风险评估异常: {e}",
            )

    def _compute_market_breadth(self, market: str) -> Optional[MarketBreadthResult]:
        """市场宽度计算（Phase 2 实现，Phase 1 返回桩）。"""
        return MarketBreadthResult(score=50.0, explanation="市场宽度暂未计算（Phase 2 实现）")

    def _compute_liquidity(self, market: str) -> Optional[LiquidityNowcastResult]:
        """流动性即时报（Phase 2 实现，Phase 1 返回桩）。"""
        return LiquidityNowcastResult(score=50.0, explanation="流动性暂未计算（Phase 2 实现）")

    def _compute_policy_event(self, market: str) -> Optional[PolicyEventResult]:
        """政策事件风险（Phase 3 实现，Phase 1 返回桩）。"""
        return PolicyEventResult(score=50.0, explanation="政策事件暂未评估（Phase 3 实现）")

    def _compute_commodity_shock(self, market: str) -> Optional[CommodityShockResult]:
        """大宗商品冲击（Phase 2 实现，Phase 1 返回桩）。"""
        return CommodityShockResult(score=50.0, explanation="商品冲击暂未计算（Phase 2 实现）")

    # ── 摘要生成 ───────────────────────────────────────────────

    def _build_summaries(self, temp: MarketTemperature) -> None:
        """根据 5 个维度结果生成面向报告的摘要文本。"""
        breadth = temp.market_breadth
        credit = temp.credit_risk
        liquidity = temp.liquidity

        # 市场环境
        if breadth is not None and breadth.score >= 60:
            temp.temperature_summary = "偏强"
        elif breadth is not None and breadth.score >= 40:
            temp.temperature_summary = "中性"
        else:
            temp.temperature_summary = "偏弱"

        # 赚钱效应
        if breadth is not None and breadth.above_ma50_pct > 0.55:
            temp.money_making_summary = "好"
        elif breadth is not None and breadth.above_ma50_pct > 0.35:
            temp.money_making_summary = "一般"
        else:
            temp.money_making_summary = "差"

        # 资金环境
        if liquidity is not None:
            if liquidity.fund_flow_direction == "inflow":
                temp.capital_env_summary = "流入"
            elif liquidity.fund_flow_direction == "outflow":
                temp.capital_env_summary = "流出"
            else:
                temp.capital_env_summary = "中性"
        else:
            temp.capital_env_summary = "中性"

        # 风险偏好
        if credit is not None:
            if credit.level == "low":
                temp.risk_appetite_summary = "高"
            elif credit.level == "elevated":
                temp.risk_appetite_summary = "中"
            else:
                temp.risk_appetite_summary = "低"
        else:
            temp.risk_appetite_summary = "中"

        # 热点清晰度（Phase 3 接入 HotSectorClassifier 后更新）
        temp.hot_clarity_summary = "一般"


# 延迟导入避免循环
from unittest.mock import MagicMock
```

- [ ] **Step 4: 运行测试**

```bash
cd stock_screener && python3 -m pytest tests/test_market_cache.py -v
```

Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add stock_screener/scoring/market_cache.py stock_screener/tests/test_market_cache.py
git commit -m "feat(scoring): add MarketCache with CreditRiskRegime and TTL caching

- MarketCache.get_or_compute(market) with 60-min TTL
- CreditRiskRegime computed from YFinance HYG/LQD/VIX ETF proxies
- 4 remaining dimensions as stubs (Phase 2-3 implementation)
- MarketTemperature.to_filter_details() for injection

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: EntryScorer 实现

**Files:**
- Create: `stock_screener/scoring/entry_scorer.py`
- Test: `stock_screener/tests/test_entry_scorer.py`

**Interfaces:**
- Consumes: `filter_details: List[dict]`（38 条技术规则的 pass/fail）
- Produces: `EntryScoreBreakdown`

- [ ] **Step 1: 写入测试 `tests/test_entry_scorer.py`**

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""EntryScorer 单元测试：5 模块聚合、权重计算、决策映射。"""

import unittest
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scoring.entry_scorer import EntryScorer
from scoring.models import EntryScoreBreakdown


def _fd(rule_key, result="pass", strategy_category="technical", details=None):
    """快捷构造一条 filter_detail。"""
    return {
        "rule_key": rule_key,
        "rule_name": rule_key,
        "rule_type": "strategy",
        "strategy_category": strategy_category,
        "result": result,
        "reason": "",
        "details": details or {},
    }


class TestEntryScorer(unittest.TestCase):
    """EntryScorer 核心逻辑。"""

    def setUp(self):
        self.scorer = EntryScorer()

    def test_no_rules_returns_50(self):
        """无规则命中 → 所有模块 50 分，entry_score=50。"""
        result = self.scorer.compute([])
        self.assertEqual(result.trend_score, 50.0)
        self.assertEqual(result.entry_score, 50.0)
        self.assertEqual(result.entry_decision, "WEAK_BUY")

    def test_single_strong_bullish_signal(self):
        """zuoyi_bullish_signal 命中 → trend + breakout 两个模块均加分。"""
        result = self.scorer.compute([_fd("zuoyi_bullish_signal")])
        self.assertGreater(result.trend_score, 50.0)
        self.assertGreater(result.breakout_score, 50.0)
        self.assertEqual(result.momentum_score, 50.0)  # 未命中动量规则

    def test_all_five_modules_scoring(self):
        """5 个模块各命中一条 x1.0 规则 → 各模块约 62 分。"""
        filter_details = [
            _fd("ema_breakout"),           # trend x1.0
            _fd("macd_bullish_cross"),     # momentum x1.0
            _fd("volume_price_breakout"),  # volume x1.0
            _fd("atr_breakout"),           # breakout x1.0 + volatility_risk x0.5
        ]
        result = self.scorer.compute(filter_details)
        self.assertGreater(result.trend_score, 55.0)
        self.assertGreater(result.momentum_score, 55.0)
        self.assertGreater(result.volume_score, 55.0)
        self.assertGreater(result.breakout_score, 55.0)
        self.assertGreater(result.entry_score, 55.0)

    def test_energy_phase_is_high_weight(self):
        """energy_phase_bullish 是动量模块的 x1.5 高权重规则。"""
        result = self.scorer.compute([_fd("energy_phase_bullish")])
        # x1.5 权重 → 比 x1.0 多 50%
        self.assertGreater(result.momentum_score, 60.0)

    def test_volume_spike_is_high_weight(self):
        """volume_spike_prior3 是成交模块的 x1.5 高权重规则。"""
        result = self.scorer.compute([_fd("volume_spike_prior3")])
        self.assertGreater(result.volume_score, 60.0)

    def test_bearish_rules_excluded(self):
        """非 bullish 方向的规则不应出现在 RULE_TO_MODULE_MAP 中。"""
        result = self.scorer.compute([_fd("rsi_overbought")])  # 已不在映射中
        # 所有模块应保持 50 不变
        self.assertEqual(result.trend_score, 50.0)
        self.assertEqual(result.momentum_score, 50.0)

    def test_strong_entry_decision(self):
        """多规则命中 → STRONG_BUY。"""
        rules = [
            _fd("zuoyi_bullish_signal"), _fd("ema_breakout"),
            _fd("energy_phase_bullish"), _fd("macd_bullish_cross"),
            _fd("volume_spike_prior3"), _fd("atr_breakout"),
            _fd("new_high_breakout"),
        ]
        result = self.scorer.compute(rules)
        self.assertGreater(result.entry_score, 75.0)
        self.assertEqual(result.entry_decision, "STRONG_BUY")

    def test_score_clamped_to_100(self):
        """大量规则命中 → entry_score 不超过 100。"""
        rules = [_fd(k) for k in [
            "zuoyi_bullish_signal", "ema_breakout", "sma_golden_cross",
            "ema_golden_cross", "price_above_ma50", "price_above_ma200",
            "energy_phase_bullish", "macd_bullish_cross", "kdj_bullish_cross",
            "rsi_bullish_rebound", "daily_rise_4_45",
            "volume_spike_prior3", "volume_price_breakout", "volume_ratio_high",
            "atr_breakout", "bollinger_upper_breakout", "new_high_breakout",
        ]]
        result = self.scorer.compute(rules)
        self.assertLessEqual(result.entry_score, 100.0)

    def test_formula_includes_all_modules(self):
        """formula 字符串包含 5 个模块分和最终分。"""
        result = self.scorer.compute([_fd("ema_breakout")])
        self.assertIn("趋势", result.formula)
        self.assertIn("动量", result.formula)
        self.assertIn("=", result.formula)
        self.assertIn("entry_score", result.formula.lower() or "entry" in result.formula)

    def test_macro_rules_ignored(self):
        """macro 类规则不参与 entry_score 计算。"""
        result = self.scorer.compute([
            _fd("company_event_hot_sector_link", strategy_category="macro"),
        ])
        self.assertEqual(result.entry_score, 50.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
```

- [ ] **Step 2: 验证测试失败**

```bash
cd stock_screener && python3 -m pytest tests/test_entry_scorer.py -v 2>&1 | head -5
```

Expected: ModuleNotFoundError for entry_scorer

- [ ] **Step 3: 写入 `scoring/entry_scorer.py`**

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
入场信号评分器：消费 filter_details 中技术规则的 pass/fail 结果，
按 5 个模块（趋势/动量/成交/突破/波动）聚合为 entry_score。
"""

from typing import Any, Dict, List, Optional

from .constants import (
    ENTRY_WEIGHTS,
    RULE_TO_MODULE_MAP,
    VOLATILITY_RISK_METRICS,
    BASE_SCORE,
    SCALE_FACTOR,
    DECISION_MAP_ENTRY,
    resolve_decision,
)
from .models import EntryScoreBreakdown


class EntryScorer:
    """入场信号评分器。

    输入：一条股票的 filter_details（技术规则 pass/fail + 详情）
    输出：EntryScoreBreakdown（entry_score + 5 模块子分 + 决策 + 公式）
    """

    def compute(self, filter_details: List[Dict[str, Any]]) -> EntryScoreBreakdown:
        """从 filter_details 计算 entry_score。"""
        # 梳理已通过的技术规则
        passed = {
            d["rule_key"]
            for d in filter_details
            if d.get("rule_type") == "strategy"
            and d.get("strategy_category") == "technical"
            and d.get("result") == "pass"
        }

        # 按 5 模块聚合原始加权分
        raw = {"trend": 0.0, "momentum": 0.0, "volume": 0.0, "breakout": 0.0, "volatility_risk": 0.0}

        for rule_key in passed:
            if rule_key in RULE_TO_MODULE_MAP:
                module, weight = RULE_TO_MODULE_MAP[rule_key]
                raw[module] += weight

        # 波动风险：部分来自规则命中（atr_breakout/bollinger_bandwidth_high），
        # 部分来自直接计算的指标（max_drawdown_20d/intraday_amplitude）。
        # 指标值在 Phase 2 中从 filter_details 的 details 子字段提取。
        for rule_key, d in ((d.get("rule_key"), d) for d in filter_details):
            if rule_key in VOLATILITY_RISK_METRICS and d.get("result") == "pass":
                raw["volatility_risk"] += VOLATILITY_RISK_METRICS[rule_key]

        # 模块原始分 → 0-100
        module_scores = {}
        for module in ENTRY_WEIGHTS:
            module_scores[module] = min(100.0, BASE_SCORE + raw[module] * SCALE_FACTOR)

        # 加权计算 entry_score
        entry_score = sum(
            module_scores[m] * ENTRY_WEIGHTS[m]
            for m in ENTRY_WEIGHTS
        )
        entry_score = round(max(0.0, min(100.0, entry_score)), 1)

        # 决策映射
        entry_decision = resolve_decision(entry_score, DECISION_MAP_ENTRY)

        # 构建公式字符串
        formula = (
            f"趋势{module_scores['trend']:.1f}*{ENTRY_WEIGHTS['trend']*100:.0f}%"
            f" + 动量{module_scores['momentum']:.1f}*{ENTRY_WEIGHTS['momentum']*100:.0f}%"
            f" + 成交{module_scores['volume']:.1f}*{ENTRY_WEIGHTS['volume']*100:.0f}%"
            f" + 突破{module_scores['breakout']:.1f}*{ENTRY_WEIGHTS['breakout']*100:.0f}%"
            f" + 波动{module_scores['volatility_risk']:.1f}*{ENTRY_WEIGHTS['volatility_risk']*100:.0f}%"
            f" = {entry_score:.1f}"
        )

        # 构建 rule_details
        rule_details = {
            d["rule_key"]: {
                "satisfied": d.get("result") == "pass",
                "reason": d.get("reason", ""),
            }
            for d in filter_details
            if d.get("rule_type") == "strategy"
        }

        return EntryScoreBreakdown(
            entry_score=entry_score,
            entry_decision=entry_decision,
            trend_score=round(module_scores["trend"], 1),
            momentum_score=round(module_scores["momentum"], 1),
            volume_score=round(module_scores["volume"], 1),
            breakout_score=round(module_scores["breakout"], 1),
            volatility_risk_score=round(module_scores["volatility_risk"], 1),
            matched_rules=sorted(passed),
            rule_details=rule_details,
            formula=formula,
        )
```

- [ ] **Step 4: 运行测试**

```bash
cd stock_screener && python3 -m pytest tests/test_entry_scorer.py -v
```

Expected: 9 passed

- [ ] **Step 5: Commit**

```bash
git add stock_screener/scoring/entry_scorer.py stock_screener/tests/test_entry_scorer.py
git commit -m "feat(scoring): add EntryScorer with 5-module weighted aggregation

- Consumes filter_details of 38 technical rules
- 5 modules: trend(30%), momentum(20%), volume(20%), breakout(20%), volatility_risk(10%)
- module_score = min(100, 50 + raw_weighted * 12)
- Outputs EntryScoreBreakdown with decision and formula string

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: HoldingScorer 实现

**Files:**
- Create: `stock_screener/scoring/holding_scorer.py`
- Test: `stock_screener/tests/test_holding_scorer.py`

**Interfaces:**
- Consumes: `filter_details` + `MarketTemperature` + `Optional[LLM result dict]`
- Produces: `HoldingScoreBreakdown`

- [ ] **Step 1: 写入测试 `tests/test_holding_scorer.py`**

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""HoldingScorer 单元测试：6 维度聚合、数据缺失回退。"""

import unittest
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scoring.holding_scorer import HoldingScorer
from scoring.models import (
    HoldingScoreBreakdown, MarketTemperature,
    CreditRiskResult, LiquidityNowcastResult, EarningsRevisionResult,
)


def _fd(rule_key, result="pass", strategy_category="macro", details=None):
    """快捷构造一条 filter_detail。"""
    return {
        "rule_key": rule_key, "rule_name": rule_key,
        "rule_type": "strategy", "strategy_category": strategy_category,
        "result": result, "reason": "", "details": details or {},
    }


class TestHoldingScorer(unittest.TestCase):
    """HoldingScorer 核心逻辑。"""

    def setUp(self):
        self.scorer = HoldingScorer()

    def test_empty_returns_50(self):
        """无输入 → 所有维度 50，holding_score=50。"""
        result = self.scorer.compute([], None, None)
        self.assertEqual(result.holding_score, 50.0)
        self.assertEqual(result.holding_decision, "LIGHT_POSITION")

    def test_enterprise_score_from_filter_details(self):
        """enterprise_score 从 enterprise_potential_analysis 的 total_score 读取。"""
        details = [_fd("enterprise_potential_analysis", details={"total_score": 85.0})]
        result = self.scorer.compute(details, None, None)
        self.assertEqual(result.enterprise_score, 85.0)

    def test_enterprise_score_missing_defaults_50(self):
        """enterprise_potential_analysis 缺失 → enterprise_score=50。"""
        result = self.scorer.compute([], None, None)
        self.assertEqual(result.enterprise_score, 50.0)

    def test_liquidity_from_market_temperature(self):
        """市场级 liquidity 分数从 MarketTemperature 读取。"""
        mt = MarketTemperature(
            market="A",
            liquidity=LiquidityNowcastResult(score=75.0, fund_flow_direction="inflow"),
        )
        result = self.scorer.compute([], mt, None)
        self.assertEqual(result.liquidity_score, 75.0)

    def test_macro_credit_from_market_temperature(self):
        """macro_credit 综合信用风险 + 宏观评分。"""
        mt = MarketTemperature(
            market="HK",
            credit_risk=CreditRiskResult(score=72.0, level="low"),
        )
        result = self.scorer.compute([], mt, None)
        self.assertGreater(result.macro_credit_score, 60.0)

    def test_holding_decision_worth_holding(self):
        """高 enterprise + 高 liquidity → WORTH_HOLDING。"""
        details = [_fd("enterprise_potential_analysis", details={"total_score": 90.0})]
        mt = MarketTemperature(
            market="A",
            liquidity=LiquidityNowcastResult(score=80.0),
            credit_risk=CreditRiskResult(score=80.0, level="low"),
        )
        result = self.scorer.compute(details, mt, None)
        self.assertGreater(result.holding_score, 70.0)

    def test_risk_level_from_scores(self):
        """低分 → risk_level=high。"""
        result = self.scorer.compute([], None, None)
        self.assertEqual(result.risk_level, "medium")

    def test_formula_includes_all_dimensions(self):
        """formula 字符串包含所有 6 个维度。"""
        result = self.scorer.compute([], None, None)
        self.assertIn("企业", result.formula)
        self.assertIn("事件", result.formula)

    def test_earnings_revision_from_stock_level(self):
        """个股级盈利预期修正参与计算。"""
        mt = MarketTemperature(market="A")
        result = self.scorer.compute(
            [_fd("earnings_revision_momentum", details={"score": 82.0})],
            mt, None,
        )
        self.assertEqual(result.earnings_revision_score, 82.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
```

- [ ] **Step 2: 验证测试失败**

```bash
cd stock_screener && python3 -m pytest tests/test_holding_scorer.py -v 2>&1 | head -5
```

Expected: ModuleNotFoundError

- [ ] **Step 3: 写入 `scoring/holding_scorer.py`**

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
持有价值评分器：消费 filter_details（宏观规则部分）+ MarketTemperature
+ 个股事件/LLM 结果，按 6 个维度聚合为 holding_score。
"""

from typing import Any, Dict, List, Optional

from .constants import HOLDING_WEIGHTS, DECISION_MAP_HOLDING, resolve_decision
from .models import HoldingScoreBreakdown, MarketTemperature


class HoldingScorer:
    """持有价值评分器。

    输入：
      - filter_details: 宏观规则/事件规则输出
      - market_temp: 市场温度计（5 个市场级规则预计算结果）
      - llm_result: 可选的 LLM 分析结果 dict

    输出：HoldingScoreBreakdown
    """

    def compute(
        self,
        filter_details: List[Dict[str, Any]],
        market_temp: Optional[MarketTemperature],
        llm_result: Optional[Dict[str, Any]],
    ) -> HoldingScoreBreakdown:
        """计算 holding_score。"""

        # ── 企业潜力分 (35%) ──
        enterprise_score = self._extract_enterprise_score(filter_details)

        # ── 事件热点分 (20%) ──
        event_score = self._extract_event_score(filter_details)

        # ── 资金与流动性 (15%) ──
        liquidity_score = self._extract_liquidity_score(market_temp)

        # ── 宏观与信用环境 (15%) ──
        macro_credit_score = self._extract_macro_credit_score(filter_details, market_temp)

        # ── 盈利预期修正 (10%) ──
        earnings_revision_score = self._extract_earnings_revision_score(filter_details)

        # ── 模型复核 (5%) ──
        llm_review_score = self._extract_llm_review_score(llm_result)

        # 加权
        holding_score = round(sum([
            enterprise_score * HOLDING_WEIGHTS["enterprise"],
            event_score * HOLDING_WEIGHTS["event"],
            liquidity_score * HOLDING_WEIGHTS["liquidity"],
            macro_credit_score * HOLDING_WEIGHTS["macro_credit"],
            earnings_revision_score * HOLDING_WEIGHTS["earnings_revision"],
            llm_review_score * HOLDING_WEIGHTS["llm_review"],
        ]), 1)
        holding_score = max(0.0, min(100.0, holding_score))

        holding_decision = resolve_decision(holding_score, DECISION_MAP_HOLDING)

        # 决策辅助
        holding_period, position_suggestion, risk_level = self._derive_advice(
            enterprise_score, liquidity_score, holding_score
        )

        formula = (
            f"企业{enterprise_score:.1f}*{HOLDING_WEIGHTS['enterprise']*100:.0f}%"
            f" + 事件{event_score:.1f}*{HOLDING_WEIGHTS['event']*100:.0f}%"
            f" + 流动性{liquidity_score:.1f}*{HOLDING_WEIGHTS['liquidity']*100:.0f}%"
            f" + 宏观信用{macro_credit_score:.1f}*{HOLDING_WEIGHTS['macro_credit']*100:.0f}%"
            f" + 盈利修正{earnings_revision_score:.1f}*{HOLDING_WEIGHTS['earnings_revision']*100:.0f}%"
            f" + LLM{llm_review_score:.1f}*{HOLDING_WEIGHTS['llm_review']*100:.0f}%"
            f" = {holding_score:.1f}"
        )

        return HoldingScoreBreakdown(
            holding_score=holding_score,
            holding_decision=holding_decision,
            enterprise_score=enterprise_score,
            event_score=event_score,
            liquidity_score=liquidity_score,
            macro_credit_score=macro_credit_score,
            earnings_revision_score=earnings_revision_score,
            llm_review_score=llm_review_score,
            holding_period=holding_period,
            position_suggestion=position_suggestion,
            risk_level=risk_level,
            formula=formula,
        )

    # ── 内部分数提取 ───────────────────────────────────────────

    def _extract_enterprise_score(self, details: List[Dict]) -> float:
        for d in details:
            if d.get("rule_key") == "enterprise_potential_analysis":
                total = (d.get("details") or {}).get("total_score")
                if total is not None:
                    return float(total)
        return 50.0

    def _extract_event_score(self, details: List[Dict]) -> float:
        """从 company_event_hot_sector_link / company_event_hot_news_link 综合。"""
        sector_hit = any(
            d.get("rule_key") == "company_event_hot_sector_link" and d.get("result") == "pass"
            for d in details
        )
        news_hit = any(
            d.get("rule_key") == "company_event_hot_news_link" and d.get("result") == "pass"
            for d in details
        )
        score = 50.0
        if sector_hit:
            score += 20.0
        if news_hit:
            score += 15.0
        return min(100.0, score)

    def _extract_liquidity_score(self, market_temp: Optional[MarketTemperature]) -> float:
        if market_temp is not None and market_temp.liquidity is not None:
            return market_temp.liquidity.score
        return 50.0

    def _extract_macro_credit_score(
        self, details: List[Dict], market_temp: Optional[MarketTemperature]
    ) -> float:
        """综合 CreditRiskRegime + MarketIntelMacroScore。"""
        credit_score = 50.0
        macro_score = 50.0

        if market_temp is not None and market_temp.credit_risk is not None:
            credit_score = market_temp.credit_risk.score

        for d in details:
            if d.get("rule_key") == "market_intel_macro_score_link" and d.get("result") == "pass":
                ms = (d.get("details") or {}).get("macro_score")
                if ms is not None:
                    macro_score = float(ms)

        return round(credit_score * 0.6 + macro_score * 0.4, 1)

    def _extract_earnings_revision_score(self, details: List[Dict]) -> float:
        for d in details:
            if d.get("rule_key") == "earnings_revision_momentum":
                score = (d.get("details") or {}).get("score")
                if score is not None:
                    return float(score)
        return 50.0

    def _extract_llm_review_score(self, llm_result: Optional[Dict]) -> float:
        if llm_result is None:
            return 50.0
        reliability = float(llm_result.get("reliability_score", 50))
        confidence = float(llm_result.get("confidence_score", 50))
        return round(reliability * 0.8 + confidence * 0.2, 1)

    def _derive_advice(
        self, enterprise: float, liquidity: float, holding: float
    ) -> tuple:
        """推导持有周期、仓位建议、风险等级。"""
        if holding >= 80:
            period, position, risk = "中线", "正常仓位", "low"
        elif holding >= 70:
            period, position, risk = "中线", "正常仓位", "medium"
        elif holding >= 60:
            period, position, risk = "短线", "轻仓试探", "medium"
        elif holding >= 50:
            period, position, risk = "短线", "轻仓试探", "high"
        else:
            period, position, risk = "短线", "不宜追高", "high"

        # 用 liquidity 微调
        if liquidity >= 70:
            risk = "low" if risk == "medium" else risk
        elif liquidity < 40:
            risk = "high"

        return period, position, risk
```

- [ ] **Step 4: 运行测试**

```bash
cd stock_screener && python3 -m pytest tests/test_holding_scorer.py -v
```

Expected: 9 passed

- [ ] **Step 5: Commit**

```bash
git add stock_screener/scoring/holding_scorer.py stock_screener/tests/test_holding_scorer.py
git commit -m "feat(scoring): add HoldingScorer with 6-dimension weighted aggregation

- enterprise(35%) from EnterprisePotentialAnalysis total_score
- event(20%) from sector/news link rules
- liquidity(15%) from MarketTemperature
- macro_credit(15%) from CreditRisk + MarketIntelMacroScore
- earnings_revision(10%) from EarningsRevisionMomentum
- llm_review(5%) from LLM reliability + confidence

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: 新增 6 个持有层 Strategizer 类

**Files:**
- Modify: `stock_screener/strategizers.py` — 末尾追加 6 个新类

**Interfaces:**
- Produces: `CreditRiskRegimeStrategizer(Strategizer)`, `MarketBreadthRegimeStrategizer(Strategizer)`, `LiquidityNowcastStrategizer(Strategizer)`, `EarningsRevisionMomentumStrategizer(Strategizer)`, `PolicyEventRiskStrategizer(Strategizer)`, `CommodityShockStrategizer(Strategizer)`
- 每个类的 `apply()` 方法遵循现有模式：`apply(stock: StockInfo, context: FilterContext) -> StrategizerOutput`

- [ ] **Step 1: 在 `strategizers.py` 末尾追加新类**

在现有文件末尾（`TodayVolumeExceedsPrior3MaxStrategizer` 等类之后）追加以下代码：

```python
# =============================================================================
# 持有层宏观规则 Strategizer（6 个）
# =============================================================================


class CreditRiskRegimeStrategizer(Strategizer):
    """信用风险环境策略器（市场级）—— 评估信用风险是否上升。

    数据源：YFinance（HK/US: HYG/LQD/JNK/IEF/VIX; A: AKShare 信用债）。
    Phase 1 实现 YFinance ETF 代理方案。
    """

    def __init__(
        self,
        name: str = "CreditRiskRegimeStrategizer",
        enabled: bool = True,
    ):
        super().__init__(name=name, enabled=enabled)

    def apply(self, stock: StockInfo, context: FilterContext) -> StrategizerOutput:
        """委托给 MarketCache，此 Strategizer 仅作为 RuleRegistry 注册占位。
        实际计算在 MarketCache._compute_credit_risk() 中。"""
        # MarketCache 的结果会通过 MarketTemperature.to_filter_details() 注入
        return StrategizerOutput(
            name=self.name,
            satisfied=False,
            reason="CreditRiskRegime 由 MarketCache 统一计算，不逐只股票调用",
            details={"delegated": True},
        )


class MarketBreadthRegimeStrategizer(Strategizer):
    """市场宽度策略器（市场级）—— 判断指数上涨是否健康扩散。

    Phase 2 实现。数据源：Futu OpenD 全市场个股日 K + AKShare 行业涨跌。
    """

    def __init__(
        self,
        name: str = "MarketBreadthRegimeStrategizer",
        enabled: bool = True,
    ):
        super().__init__(name=name, enabled=enabled)

    def apply(self, stock: StockInfo, context: FilterContext) -> StrategizerOutput:
        return StrategizerOutput(
            name=self.name,
            satisfied=False,
            reason="MarketBreadthRegime 由 MarketCache 统一计算，不逐只股票调用",
            details={"delegated": True},
        )


class LiquidityNowcastStrategizer(Strategizer):
    """资金流动性即报策略器（市场级）—— 判断资金环境是否支持继续持有。

    Phase 2 实现。数据源：AKShare（A股融资融券/北向资金/ETF资金）+ Futu OpenD（成交额）。
    """

    def __init__(
        self,
        name: str = "LiquidityNowcastStrategizer",
        enabled: bool = True,
    ):
        super().__init__(name=name, enabled=enabled)

    def apply(self, stock: StockInfo, context: FilterContext) -> StrategizerOutput:
        return StrategizerOutput(
            name=self.name,
            satisfied=False,
            reason="LiquidityNowcast 由 MarketCache 统一计算，不逐只股票调用",
            details={"delegated": True},
        )


class EarningsRevisionMomentumStrategizer(Strategizer):
    """盈利预期修正策略器（个股级）—— 判断公司未来盈利预期是否改善。

    数据源：Tavily（搜索业绩预告/财报/券商观点）+ LLM 结构化。
    Phase 3 实现 LLM 调用，Phase 1 返回中性桩。
    """

    def __init__(
        self,
        name: str = "EarningsRevisionMomentumStrategizer",
        enabled: bool = True,
    ):
        super().__init__(name=name, enabled=enabled)

    def apply(self, stock: StockInfo, context: FilterContext) -> StrategizerOutput:
        # Phase 1 桩实现
        return StrategizerOutput(
            name=self.name,
            satisfied=False,
            reason="EarningsRevision 暂未实现（Phase 3）",
            details={"score": 50.0, "earnings_trend": "stable", "confidence": 0.0},
        )


class PolicyEventRiskStrategizer(Strategizer):
    """政策事件风险策略器（市场级）—— 识别政策/监管/地缘事件影响。

    Phase 3 实现。数据源：Tavily + LLM。
    """

    def __init__(
        self,
        name: str = "PolicyEventRiskStrategizer",
        enabled: bool = True,
    ):
        super().__init__(name=name, enabled=enabled)

    def apply(self, stock: StockInfo, context: FilterContext) -> StrategizerOutput:
        return StrategizerOutput(
            name=self.name,
            satisfied=False,
            reason="PolicyEventRisk 由 MarketCache 统一计算，不逐只股票调用",
            details={"delegated": True},
        )


class CommodityShockStrategizer(Strategizer):
    """大宗商品冲击策略器（市场级）—— 识别商品价格变化对不同行业的影响。

    Phase 2 实现。数据源：YFinance（WTI原油/铜/黄金/天然气/白银期货）。
    """

    def __init__(
        self,
        name: str = "CommodityShockStrategizer",
        enabled: bool = True,
    ):
        super().__init__(name=name, enabled=enabled)

    def apply(self, stock: StockInfo, context: FilterContext) -> StrategizerOutput:
        return StrategizerOutput(
            name=self.name,
            satisfied=False,
            reason="CommodityShock 由 MarketCache 统一计算，不逐只股票调用",
            details={"delegated": True},
        )
```

- [ ] **Step 2: 验证导入**

```bash
cd stock_screener && python3 -c "
import sys; sys.path.insert(0,'.')
from strategizers import (
    CreditRiskRegimeStrategizer,
    MarketBreadthRegimeStrategizer,
    LiquidityNowcastStrategizer,
    EarningsRevisionMomentumStrategizer,
    PolicyEventRiskStrategizer,
    CommodityShockStrategizer,
)
print('All 6 new strategizers imported OK')
"
```

- [ ] **Step 3: Commit**

```bash
git add stock_screener/strategizers.py
git commit -m "feat(strategizers): add 6 holding-layer macro strategizer stubs

- CreditRiskRegimeStrategizer (market-level, delegated to MarketCache)
- MarketBreadthRegimeStrategizer (market-level, Phase 2)
- LiquidityNowcastStrategizer (market-level, Phase 2)
- EarningsRevisionMomentumStrategizer (stock-level, Phase 3)
- PolicyEventRiskStrategizer (market-level, Phase 3)
- CommodityShockStrategizer (market-level, Phase 2)

All market-level strategizers delegate actual computation to MarketCache.
Only EarningsRevision runs per-stock (Phase 3 implementation).

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 6: 更新 RuleRegistry 注册新规则

**Files:**
- Modify: `stock_screener/rule_engine.py`

**Interfaces:**
- Modify: `RuleRegistry.default()` — 注册 11 个新规则（6 个持有层 + 5 个 EntryScore 聚合器）
- Modify: `evaluate_macro_rules_for_top20()` — 注入市场级缓存

- [ ] **Step 1: 在 `RuleRegistry.default()` 中注册新规则**

在 `rule_engine.py` 的 `RuleRegistry.default()` 方法末尾（return 之前），追加以下注册：

```python
        # ── 持有层宏观规则（6 个新增） ──────────────────────
        registry.register(RuleMetadata(
            market="*", rule_key="credit_risk_regime",
            rule_name="信用风险环境", rule_type=RULE_TYPE_STRATEGY,
            implementation="CreditRiskRegimeStrategizer",
            strategy_category="macro",
            params={}, enabled=True, display_order=200,
            description="判断市场信用风险是否上升（HYG/LQD/VIX ETF代理）",
        ))
        registry.register(RuleMetadata(
            market="*", rule_key="market_breadth_regime",
            rule_name="市场宽度环境", rule_type=RULE_TYPE_STRATEGY,
            implementation="MarketBreadthRegimeStrategizer",
            strategy_category="macro",
            params={}, enabled=True, display_order=210,
            description="判断指数上涨是否健康扩散到多数股票",
        ))
        registry.register(RuleMetadata(
            market="*", rule_key="liquidity_nowcast",
            rule_name="资金流动性即时报", rule_type=RULE_TYPE_STRATEGY,
            implementation="LiquidityNowcastStrategizer",
            strategy_category="macro",
            params={}, enabled=True, display_order=220,
            description="判断资金环境是否支持继续持有",
        ))
        registry.register(RuleMetadata(
            market="*", rule_key="earnings_revision_momentum",
            rule_name="盈利预期修正", rule_type=RULE_TYPE_STRATEGY,
            implementation="EarningsRevisionMomentumStrategizer",
            strategy_category="macro",
            params={}, enabled=True, display_order=230,
            description="判断公司未来盈利预期是否改善（Tavily+LLM代理）",
        ))
        registry.register(RuleMetadata(
            market="*", rule_key="policy_event_risk",
            rule_name="政策事件风险", rule_type=RULE_TYPE_STRATEGY,
            implementation="PolicyEventRiskStrategizer",
            strategy_category="macro",
            params={}, enabled=True, display_order=240,
            description="识别宏观政策/行业政策/监管事件/地缘事件影响",
        ))
        registry.register(RuleMetadata(
            market="*", rule_key="commodity_shock",
            rule_name="大宗商品冲击", rule_type=RULE_TYPE_STRATEGY,
            implementation="CommodityShockStrategizer",
            strategy_category="macro",
            params={}, enabled=True, display_order=250,
            description="识别大宗商品价格变化对资源/周期/制造/消费行业影响",
        ))

        # ── EntryScore 聚合器（5 个评分层 Strategy） ─────────
        registry.register(RuleMetadata(
            market="*", rule_key="trend_structure",
            rule_name="趋势结构", rule_type=RULE_TYPE_STRATEGY,
            implementation="TrendStructureStrategizer",
            strategy_category="scoring",
            params={}, enabled=True, display_order=300,
            description="[评分聚合] 趋势结构模块（EMA排列/左一/MA位置）",
        ))
        registry.register(RuleMetadata(
            market="*", rule_key="momentum_state",
            rule_name="动量状态", rule_type=RULE_TYPE_STRATEGY,
            implementation="MomentumStateStrategizer",
            strategy_category="scoring",
            params={}, enabled=True, display_order=310,
            description="[评分聚合] 动量状态模块（RSI/MACD/KDJ/能量相位）",
        ))
        registry.register(RuleMetadata(
            market="*", rule_key="volume_confirmation",
            rule_name="成交确认", rule_type=RULE_TYPE_STRATEGY,
            implementation="VolumeConfirmationStrategizer",
            strategy_category="scoring",
            params={}, enabled=True, display_order=320,
            description="[评分聚合] 成交确认模块（放量/量价配合）",
        ))
        registry.register(RuleMetadata(
            market="*", rule_key="breakout_quality",
            rule_name="突破质量", rule_type=RULE_TYPE_STRATEGY,
            implementation="BreakoutQualityStrategizer",
            strategy_category="scoring",
            params={}, enabled=True, display_order=330,
            description="[评分聚合] 突破质量模块（ATR突破/新高/形态有效性）",
        ))
        registry.register(RuleMetadata(
            market="*", rule_key="volatility_risk",
            rule_name="波动风险", rule_type=RULE_TYPE_STRATEGY,
            implementation="VolatilityRiskStrategizer",
            strategy_category="scoring",
            params={}, enabled=True, display_order=340,
            description="[评分聚合] 波动风险模块（布林带宽/回撤/日内振幅）",
        ))
```

同时移除 `rsi_oversold` 的注册（不再作为独立条目），或者保留但标记 `enabled=False`。移除 `rsi_overbought` 和 `daily_drop_6_65` 注册（如果它们存在）。

- [ ] **Step 2: 修改 `evaluate_macro_rules_for_top20()`**

在 `evaluate_macro_rules_for_top20()` 方法中，增加从 MarketCache 注入市场级规则的逻辑。

```python
def evaluate_macro_rules_for_top20(self, si, context, market_cache=None):
    # ... 现有的 4 条 macro 规则逐只评估 ...
    
    # 新增：从 MarketCache 注入 5 条市场级规则（不逐只调用）
    if market_cache is not None:
        market_temp = market_cache.get_or_compute(context.market)
        extra_details = market_temp.to_filter_details()
        # earnings_revision 不在这里注入（它是个股级，需要逐只评估）
        # 只注入 4 条市场级规则
        for d in extra_details:
            if d["rule_key"] != "earnings_revision_momentum":
                filter_details.append(d)
    
    # ... 评估 earnings_revision（个股级，Phase 3 实现）...
    
    return filter_details
```

- [ ] **Step 3: 验证 Registry 包含新规则**

```bash
cd stock_screener && python3 -c "
from rule_engine import RuleRegistry
r = RuleRegistry.default()
keys = [e.rule_key for e in r.list_entries()]
for k in ['credit_risk_regime', 'market_breadth_regime', 'liquidity_nowcast', 'earnings_revision_momentum', 'policy_event_risk', 'commodity_shock', 'trend_structure', 'momentum_state', 'volume_confirmation', 'breakout_quality', 'volatility_risk']:
    assert k in keys, f'{k} not in registry'
print('All 11 new rules registered OK')
"
```

- [ ] **Step 4: Commit**

```bash
git add stock_screener/rule_engine.py
git commit -m "feat(rule_engine): register 11 new rules (6 holding + 5 scoring)

- 6 holding-layer macro rules: credit_risk, market_breadth, liquidity,
  earnings_revision, policy_event, commodity_shock
- 5 scoring-layer aggregators: trend, momentum, volume, breakout, volatility
- evaluate_macro_rules_for_top20 accepts optional market_cache for injection
- Remove rsi_overbought and daily_drop_6_65 from registry

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 7: 更新 DEFAULT_RULE_METADATA

**Files:**
- Modify: `stock_screener/db.py`

**Interfaces:**
- Modify: `DEFAULT_RULE_METADATA` — 新增 11 条 + 移除 3 条

- [ ] **Step 1: 修改 `DEFAULT_RULE_METADATA`**

在 `db.py` 的 `DEFAULT_RULE_METADATA` 元组中：
1. 移除 `rsi_overbought`、`daily_drop_6_65`、`rsi_oversold` 条目
2. 追加 11 条新规则条目

```python
# 在 DEFAULT_RULE_METADATA 元组末尾追加：
    # ── 持有层宏观规则 ──
    ("credit_risk_regime", "信用风险环境", "strategy", "macro",
     "CreditRiskRegimeStrategizer", "N/A", {}, True, 200, None,
     "判断市场信用风险是否上升（HYG/LQD/VIX ETF代理）"),
    ("market_breadth_regime", "市场宽度环境", "strategy", "macro",
     "MarketBreadthRegimeStrategizer", "N/A", {}, True, 210, None,
     "判断指数上涨是否健康扩散到多数股票"),
    ("liquidity_nowcast", "资金流动性即时报", "strategy", "macro",
     "LiquidityNowcastStrategizer", "N/A", {}, True, 220, None,
     "判断资金环境是否支持继续持有"),
    ("earnings_revision_momentum", "盈利预期修正", "strategy", "macro",
     "EarningsRevisionMomentumStrategizer", "N/A", {}, True, 230, None,
     "判断公司盈利预期是否改善（Tavily+LLM代理）"),
    ("policy_event_risk", "政策事件风险", "strategy", "macro",
     "PolicyEventRiskStrategizer", "N/A", {}, True, 240, None,
     "识别政策/监管/地缘事件影响"),
    ("commodity_shock", "大宗商品冲击", "strategy", "macro",
     "CommodityShockStrategizer", "N/A", {}, True, 250, None,
     "识别大宗商品价格变化对行业影响"),

    # ── EntryScore 聚合器（scoring 类别，不参与 technical 筛选） ──
    ("trend_structure", "趋势结构", "strategy", "scoring",
     "TrendStructureStrategizer", "N/A", {}, True, 300, None,
     "[评分聚合] 趋势结构（EMA排列/左一/MA位置）"),
    ("momentum_state", "动量状态", "strategy", "scoring",
     "MomentumStateStrategizer", "N/A", {}, True, 310, None,
     "[评分聚合] 动量状态（RSI/MACD/KDJ/能量相位）"),
    ("volume_confirmation", "成交确认", "strategy", "scoring",
     "VolumeConfirmationStrategizer", "N/A", {}, True, 320, None,
     "[评分聚合] 成交确认（放量/量价配合）"),
    ("breakout_quality", "突破质量", "strategy", "scoring",
     "BreakoutQualityStrategizer", "N/A", {}, True, 330, None,
     "[评分聚合] 突破质量（ATR突破/新高/形态有效性）"),
    ("volatility_risk", "波动风险", "strategy", "scoring",
     "VolatilityRiskStrategizer", "N/A", {}, True, 340, None,
     "[评分聚合] 波动风险（布林带宽/回撤/日内振幅）"),
```

- [ ] **Step 2: 验证导入**

```bash
cd stock_screener && python3 -c "
from db import DEFAULT_RULE_METADATA
keys = [r[0] for r in DEFAULT_RULE_METADATA]
new_keys = ['credit_risk_regime', 'market_breadth_regime', 'liquidity_nowcast', 'earnings_revision_momentum', 'policy_event_risk', 'commodity_shock', 'trend_structure', 'momentum_state', 'volume_confirmation', 'breakout_quality', 'volatility_risk']
for k in new_keys:
    assert k in keys, f'{k} not in DEFAULT_RULE_METADATA'
print('All 11 new rules in DEFAULT_RULE_METADATA OK')
# 验证移除的 3 条不再存在
removed = ['rsi_overbought', 'daily_drop_6_65']
for k in removed:
    assert k not in keys, f'{k} should have been removed'
print('Removed rules confirmed absent OK')
"
```

- [ ] **Step 3: 添加 DB migration 方法**

在 `db.py` 的 `init_schema()` 或相关初始化方法中，添加 migration：

```python
def _migrate_add_scoring_columns(cursor):
    """新增 entry_score / holding_score 等字段到 screening_results 表。"""
    columns = [
        ("entry_score", "DECIMAL(5,2) DEFAULT NULL"),
        ("entry_decision", "VARCHAR(30) DEFAULT NULL"),
        ("holding_score", "DECIMAL(5,2) DEFAULT NULL"),
        ("holding_decision", "VARCHAR(30) DEFAULT NULL"),
        ("holding_period", "VARCHAR(20) DEFAULT NULL"),
        ("position_suggestion", "VARCHAR(30) DEFAULT NULL"),
        ("risk_level", "VARCHAR(20) DEFAULT NULL"),
        ("score_formula", "TEXT DEFAULT NULL"),
    ]
    for col_name, col_def in columns:
        try:
            cursor.execute(f"ALTER TABLE screening_results ADD COLUMN {col_name} {col_def}")
        except Exception as e:
            if "Duplicate column" in str(e) or "already exists" in str(e):
                pass  # 列已存在，跳过
            else:
                raise
```

- [ ] **Step 4: Commit**

```bash
git add stock_screener/db.py
git commit -m "feat(db): update DEFAULT_RULE_METADATA + add scoring columns migration

- Add 11 new rule entries (6 holding + 5 scoring)
- Remove rsi_overbought, daily_drop_6_65
- _migrate_add_scoring_columns: 8 new columns on screening_results
  (entry_score, entry_decision, holding_score, holding_decision,
   holding_period, position_suggestion, risk_level, score_formula)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 8: 集成到 screen_service.py

**Files:**
- Modify: `stock_screener/api/screen_service.py`

**Interfaces:**
- Consumes: `MarketCache`, `EntryScorer`, `HoldingScorer`
- Modify: `run_screening_task()` — 预计算 MarketTemperature → 计算 EntryScore → 按 entry_score 排序 Top20 → 计算 HoldingScore → 写 DB

- [ ] **Step 1: 修改 `run_screening_task()`**

在 `run_screening_task()` 中：

1. 在筛选开始前初始化 `MarketCache` 并预计算 `MarketTemperature`：
```python
from scoring.market_cache import MarketCache
from scoring.entry_scorer import EntryScorer
from scoring.holding_scorer import HoldingScorer

market_cache = MarketCache()
market_temp = market_cache.get_or_compute(market)
entry_scorer = EntryScorer()
holding_scorer = HoldingScorer()
```

2. 在每只股票通过技术规则评估后，计算 entry_score：
```python
# 在收集 stock 的 filter_details 之后
entry_bd = entry_scorer.compute(stock_filter_details)
stock.entry_score = entry_bd.entry_score
stock.entry_decision = entry_bd.entry_decision
```

3. Top20 排序改为 entry_score：
```python
# 旧: candidates.sort(key=lambda c: c.total_match_count, reverse=True)
candidates.sort(key=lambda c: getattr(c, 'entry_score', 0), reverse=True)
```

4. Top20 后置宏观评估时，注入 MarketCache：
```python
# 调用 evaluate_macro_rules_for_top20 时传入 market_cache
macro_details = engine.evaluate_macro_rules_for_top20(si, context, market_cache=market_cache)
```

5. 计算 holding_score 并写入 DB：
```python
# 在 filter_details 收集完成后
holding_bd = holding_scorer.compute(
    stock.filter_details,
    market_temp,
    llm_result,  # 如果有 LLM 分析结果
)
stock.holding_score = holding_bd.holding_score
stock.holding_decision = holding_bd.holding_decision
stock.holding_period = holding_bd.holding_period
stock.position_suggestion = holding_bd.position_suggestion
stock.risk_level = holding_bd.risk_level
```

- [ ] **Step 2: 验证导入不报错**

```bash
cd stock_screener && python3 -c "from api.screen_service import run_screening_task; print('screen_service imports OK')"
```

- [ ] **Step 3: Commit**

```bash
git add stock_screener/api/screen_service.py
git commit -m "feat(screen_service): integrate MarketCache + EntryScorer + HoldingScorer

- Pre-compute MarketTemperature before screening
- EntryScorer on each stock after technical evaluation
- Top20 sorted by entry_score (was total_match_count)
- MarketCache injected into evaluate_macro_rules_for_top20
- HoldingScorer on Top20 stocks after macro evaluation
- New score fields written to DB

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 9: 更新现有测试

**Files:**
- Modify: `stock_screener/tests/test_e2e_unified_bullish_top20_hk02685.py`
- Modify: `stock_screener/tests/test_e2e_unified_bullish_top20_scoring.py`

- [ ] **Step 1: 更新规则计数**

在 `test_e2e_unified_bullish_top20_hk02685.py` 中：
- 技术规则计数从 21（或当前值）更新为移除后值（20 条 bullish technical？这取决于 `bullish_technical_rule_keys()` 的行为——它自动发现 direction=bullish, strategy_category=technical 的规则）

```python
# 移除 rsi_oversold 后，bullish 技术规则减少 1 条
self.assertEqual(len(bullish_keys), 20)  # 原 21 → 20
```

在 `test_e2e_unified_bullish_top20_scoring.py` 中同样更新。

- [ ] **Step 2: 运行现有测试确认不回归**

```bash
cd stock_screener && python3 -m pytest tests/test_e2e_unified_bullish_top20_hk02685.py -v --tb=short 2>&1 | tail -30
```

Expected: 现有通过的测试仍然通过，仅规则计数测试需更新

- [ ] **Step 3: Commit**

```bash
git add stock_screener/tests/test_e2e_unified_bullish_top20_hk02685.py stock_screener/tests/test_e2e_unified_bullish_top20_scoring.py
git commit -m "test: update bullish technical rule count (21→20, removed rsi_oversold)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Phase 2: 评分上线（Task 10-14）

### Task 10: MarketBreadth 实现

**Files:**
- Modify: `stock_screener/scoring/market_cache.py` — 实现 `_compute_market_breadth()`

在 `_compute_market_breadth()` 桩实现的位置，替换为实际计算。数据来源：Futu OpenD 全市场个股日 K（仅使用已缓存的 `stock_kline_cache` 表数据，不实时拉取）。

- [ ] 从 `stock_kline_cache` 表查询全市场个股最近一日收盘价和 MA50/MA200
- [ ] 计算 advance_decline_ratio, above_ma50_pct, above_ma200_pct, new_high/low_52w
- [ ] 加减分模型映射到 0-100
- [ ] 单元测试

### Task 11: LiquidityNowcast 实现

**Files:**
- Modify: `stock_screener/scoring/market_cache.py` — 实现 `_compute_liquidity()`

- [ ] A 股：AKShare 融资融券、北向资金、板块资金流、ETF 资金流
- [ ] 港股：南向资金、港股成交额
- [ ] 美股：YFinance ETF 资金流代理
- [ ] 单元测试

### Task 12: CommodityShock 实现

**Files:**
- Modify: `stock_screener/scoring/market_cache.py` — 实现 `_compute_commodity_shock()`

- [ ] YFinance 获取 5 种商品期货近 1 月价格
- [ ] 5 日涨跌幅 > 5% → 记录冲击
- [ ] 多商品同时剧烈波动 → 扣分
- [ ] 单元测试

### Task 13: 5 个 EntryScore 聚合 Strategizer

**Files:**
- Modify: `stock_screener/strategizers.py` — 实现 5 个聚合器类

每个聚合器遵循现有模式，但它们是「只读消费者」——不产生新的信号判断，只消费 filter_details 中已有规则的 pass/fail：

```python
class TrendStructureStrategizer(Strategizer):
    """趋势结构聚合器 —— 消费 zuoyi/EMA/SMA/MA 相关规则的输出。"""
    def apply(self, stock, context):
        # 读取 context.filter_details 中趋势相关规则的比分
        ...
```

- [ ] `TrendStructureStrategizer`
- [ ] `MomentumStateStrategizer`
- [ ] `VolumeConfirmationStrategizer`
- [ ] `BreakoutQualityStrategizer`
- [ ] `VolatilityRiskStrategizer`

### Task 14: 端到端验证（Phase 2 里程碑）

- [ ] 运行 `test_e2e_unified_bullish_top20_scoring.py`
- [ ] 手动对比新旧评分——新系统 entry_score 应该比旧 total_match_count 更合理
- [ ] Feature flag 验证：`USE_NEW_SCORING=0` 回退

---

## Phase 3: 报告上线（Task 15-18）

### Task 15: RetailReportRenderer

**Files:**
- Create: `stock_screener/signal_analysis/renderers.py`

实现 10-section 报告渲染器：

- [ ] `_render_disclaimer()` — 重要提示
- [ ] `_render_one_line_conclusion()` — 一句话结论
- [ ] `_render_market_thermometer()` — 市场温度计表格
- [ ] `_render_hot_directions()` — 热点方向三分类
- [ ] `_render_scoring_guide()` — entry/holding 双分解释
- [ ] `_render_top20_table()` — 简化 7 列表格
- [ ] `_render_stock_cards()` — 精选个股卡片
- [ ] `_render_category_advice()` — 短线/中线/不追高分类
- [ ] `_render_risk_warnings()` — 5 层风险提示
- [ ] `_render_data_freshness()` — 数据新鲜度表

### Task 16: HotSectorClassifier

**Files:**
- Modify: `stock_screener/signal_analysis/hot_sectors.py`

- [ ] `HotSectorClassifier` 类
- [ ] 行业热点（40+ 英文板块 → 中文行业映射）
- [ ] 主题热点（AI/机器人/算力/新能源等关键词匹配）
- [ ] 地域热点（仅限有政策催化的地域）
- [ ] 单元测试 `tests/test_hot_sector_classifier.py`

### Task 17: PolicyEvent + EarningsRevision

**Files:**
- Modify: `stock_screener/scoring/market_cache.py` — 实现 `_compute_policy_event()`
- Modify: `stock_screener/strategizers.py` — 实现 `EarningsRevisionMomentumStrategizer.apply()`

- [ ] PolicyEvent: Tavily 搜索 + LLM 结构化 → 5 个政策类别搜索
- [ ] EarningsRevision: Tavily 搜索 + LLM 关键词评分（个股级，Top20 逐只执行）
- [ ] 单元测试

### Task 18: 切换到新渲染器

**Files:**
- Modify: `stock_screener/signal_analysis/chain.py`

- [ ] `_render_artifact_report()` 切换到 `RetailReportRenderer`
- [ ] MarketTemperature 注入到信号分析链
- [ ] 旧渲染器标记 deprecated
- [ ] Feature flag: `USE_NEW_RENDERER`

---

## Phase 4: 清理 + P2 预留（Task 19-20）

### Task 19: Deprecated 标记 + P2 接口

**Files:**
- Modify: `stock_screener/market_intel/macro_scoring.py`
- Modify: `stock_screener/market_intel/reporting.py`
- Modify: `stock_screener/signal_analysis/models.py`
- Create: `stock_screener/market_intel/providers/base.py`（P2 抽象接口）

- [ ] `aggregate_rule_scores` → 标记 deprecated（保留兼容调用）
- [ ] `compute_unified_score` → 标记 deprecated
- [ ] `_render_markdown_report` / `render_multi_stock_report` → 标记 deprecated
- [ ] `UNIFIED_SCORE_WEIGHTS` → 标记 deprecated
- [ ] 写入 P2 抽象接口 `MacroDataProvider` ABC
- [ ] Feature flag 清理：默认使用新系统，`USE_NEW_SCORING=0` 可回退

### Task 20: 验收测试 + CLAUDE.md 更新

**Files:**
- Modify: `stock_screener/tests/test_e2e_baseline_3stocks.py`
- Modify: `CLAUDE.md`

- [ ] 用 SH.600999 / US.AXP / HK.00128 跑全流程验收
- [ ] 验收标准：
  1. 三只股票全部 is_passed=True
  2. 每只有完整 filter_details（技术 + 宏观 + 市场级规则）
  3. 每只有有效的 entry_score + holding_score
  4. 结果写入 DB
  5. 报告正确展示市场温度计、热点、卡片、风险
- [ ] 写入 CLAUDE.md Fix Log 记录本次修改

---

## 附录 A: 测试数据工厂

所有测试文件中共享的 DataFrame 构造方法，沿用现有 `test_energy_phase_classifier.py` 的风格：

```python
def _df(rows):
    """Build a DataFrame from list of (open, high, low, close, volume) tuples."""
    start = date(2025, 6, 1)
    data = []
    for idx, row in enumerate(rows):
        item = {
            "date": start + timedelta(days=idx),
            "open": row[0], "high": row[1], "low": row[2],
            "close": row[3], "volume": row[4] if len(row) > 4 else 1000 + idx * 10,
        }
        data.append(item)
    return pd.DataFrame(data)
```

## 附录 B: 回滚 Feature Flag

```python
# 环境变量控制，Phase 4 默认使用新系统
USE_NEW_SCORING = os.getenv("USE_NEW_SCORING", "1") == "1"
USE_NEW_RENDERER = os.getenv("USE_NEW_RENDERER", "1") == "1"
```

## 附录 C: 预估时间

| Phase | 任务数 | 预估时间 |
|---|---|---|
| Phase 1 | 9 | 1-2 天 |
| Phase 2 | 5 | 2-3 天 |
| Phase 3 | 4 | 2-3 天 |
| Phase 4 | 2 | 1 天 |
| **合计** | **20** | **6-9 天** |
