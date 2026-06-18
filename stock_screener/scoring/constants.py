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
