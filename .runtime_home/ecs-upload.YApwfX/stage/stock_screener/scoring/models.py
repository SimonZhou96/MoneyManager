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
    explanation: str = ""
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
    data_sources: List[str] = field(default_factory=lambda: ["builtin"])


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

    # 各维度数据来源状态（available / missing / error / default）
    dimension_sources: Dict[str, str] = field(default_factory=dict)

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
                    "reason": result.explanation,
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
