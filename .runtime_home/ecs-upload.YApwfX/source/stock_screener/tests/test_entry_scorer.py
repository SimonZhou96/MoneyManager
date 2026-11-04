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
            _fd("sma_golden_cross"),  # trend x1.0, pushes entry over 80
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
        # formula starts with "entry_score = ..."
        self.assertIn("entry_score", result.formula)

    def test_macro_rules_ignored(self):
        """macro 类规则不参与 entry_score 计算。"""
        result = self.scorer.compute([
            _fd("company_event_hot_sector_link", strategy_category="macro"),
        ])
        self.assertEqual(result.entry_score, 50.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
