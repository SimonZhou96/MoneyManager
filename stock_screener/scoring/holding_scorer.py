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
            period, position, risk = "短线", "轻仓试探", "medium"
        else:
            period, position, risk = "短线", "不宜追高", "high"

        # 用 liquidity 微调
        if liquidity >= 70:
            risk = "low" if risk == "medium" else risk
        elif liquidity < 40:
            risk = "high"

        return period, position, risk
