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
