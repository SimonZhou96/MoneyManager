#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
入场信号评分器：消费 filter_details 中技术规则的 pass/fail 结果，
按 5 个模块（趋势/动量/成交/突破/波动）聚合为 entry_score。
"""

from typing import Any, Dict, List

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

        # RULE_TO_MODULE_MAP 是 [(rule_key, module, weight), ...] 列表
        # 同一 rule_key 可映射到多个模块（zuoyi_bullish_signal → trend + breakout）
        for rule_key in passed:
            for rk, module, weight in RULE_TO_MODULE_MAP:
                if rk == rule_key:
                    raw[module] += weight

        # 波动风险：部分来自规则命中（atr_breakout/bollinger_bandwidth_high），
        # 部分来自直接计算的指标（max_drawdown_20d/intraday_amplitude）。
        # 指标值在 Phase 2 中从 filter_details 的 details 子字段提取。
        for d in filter_details:
            rk = d.get("rule_key")
            if rk in VOLATILITY_RISK_METRICS and d.get("result") == "pass":
                raw["volatility_risk"] += VOLATILITY_RISK_METRICS[rk]

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
            f"entry_score = 趋势{module_scores['trend']:.1f}*{ENTRY_WEIGHTS['trend']*100:.0f}%"
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
