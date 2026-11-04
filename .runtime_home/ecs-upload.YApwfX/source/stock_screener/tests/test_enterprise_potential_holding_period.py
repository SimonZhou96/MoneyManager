#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""企业潜力分析 — 建议持有周期单测"""

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from potential_analysis.holding_period import (
    HORIZON_BANDS_BY_MODULE,
    decision_from_total_score,
    dominant_module,
    format_band,
    normalize_holding_period,
    suggest_holding_period,
)
from potential_analysis.models import EnterprisePotentialEvidencePackage
from potential_analysis.scoring import DEFAULT_WEIGHTS, EnterprisePotentialScorer


class DecisionTests(unittest.TestCase):
    def test_decision_thresholds(self):
        self.assertEqual(decision_from_total_score(80, 70), "BUY")
        self.assertEqual(decision_from_total_score(75, 70), "BUY")
        self.assertEqual(decision_from_total_score(72, 70), "WATCH")
        self.assertEqual(decision_from_total_score(70, 70), "WATCH")
        self.assertEqual(decision_from_total_score(65, 70), "SKIP")


class DominantModuleTests(unittest.TestCase):
    def test_trading_dominant_short(self):
        scores = {"macro": 50, "industry": 50, "company": 50,
                  "valuation": 50, "trading": 95}
        self.assertEqual(dominant_module(scores, DEFAULT_WEIGHTS), "trading")

    def test_company_dominant_long(self):
        scores = {"macro": 50, "industry": 50, "company": 95,
                  "valuation": 50, "trading": 50}
        self.assertEqual(dominant_module(scores, DEFAULT_WEIGHTS), "company")

    def test_no_positive_returns_none(self):
        scores = {m: 50 for m in HORIZON_BANDS_BY_MODULE}
        self.assertIsNone(dominant_module(scores, DEFAULT_WEIGHTS))


class SuggestHoldingPeriodTests(unittest.TestCase):
    def test_trading_buy_short_band(self):
        scores = {"macro": 50, "industry": 50, "company": 50,
                  "valuation": 50, "trading": 95}
        period = suggest_holding_period("BUY", scores, DEFAULT_WEIGHTS, 80, 70)
        self.assertEqual(period, "1-3个月")

    def test_valuation_buy_longest_band(self):
        scores = {"macro": 50, "industry": 50, "company": 50,
                  "valuation": 95, "trading": 50}
        period = suggest_holding_period("BUY", scores, DEFAULT_WEIGHTS, 80, 70)
        self.assertEqual(period, "12-36个月")

    def test_watch_is_shorter_than_buy(self):
        scores = {"macro": 95, "industry": 50, "company": 50,
                  "valuation": 50, "trading": 50}
        buy = suggest_holding_period("BUY", scores, DEFAULT_WEIGHTS, 80, 70)
        watch = suggest_holding_period("WATCH", scores, DEFAULT_WEIGHTS, 72, 70)
        self.assertEqual(buy, "6-12个月")
        self.assertEqual(watch, "3-6个月")

    def test_skip_returns_empty(self):
        scores = {"macro": 95, "industry": 50, "company": 50,
                  "valuation": 50, "trading": 50}
        self.assertEqual(
            suggest_holding_period("SKIP", scores, DEFAULT_WEIGHTS, 60, 70), "")

    def test_fallback_band_when_no_dominant(self):
        scores = {m: 50 for m in HORIZON_BANDS_BY_MODULE}
        self.assertEqual(
            suggest_holding_period("BUY", scores, DEFAULT_WEIGHTS, 80, 70), "6-12个月")
        self.assertEqual(
            suggest_holding_period("WATCH", scores, DEFAULT_WEIGHTS, 72, 70), "3-6个月")


class FormatAndNormalizeTests(unittest.TestCase):
    def test_format_band(self):
        self.assertEqual(format_band(6, 12), "6-12个月")
        self.assertEqual(format_band(3, 3), "3个月")
        self.assertEqual(format_band(0, 2), "1-2个月")

    def test_normalize_english(self):
        self.assertEqual(normalize_holding_period("6-18 months"), "6-18个月")
        self.assertEqual(normalize_holding_period("3-6months"), "3-6个月")
        self.assertEqual(normalize_holding_period("9 months"), "9个月")
        self.assertEqual(normalize_holding_period(""), "")
        self.assertEqual(normalize_holding_period("6-12个月"), "6-12个月")


class RuleScoreEndToEndTests(unittest.TestCase):
    def test_rule_score_outputs_holding_period(self):
        scorer = EnterprisePotentialScorer(threshold=70.0)
        evidence = EnterprisePotentialEvidencePackage(
            market="HK", code="01810", name="测试",
            as_of="2026-06-01T00:00:00+00:00",
        )
        result = scorer.score(evidence)
        self.assertIn(result.decision, {"BUY", "WATCH", "SKIP"})
        details = result.to_details()
        self.assertIn("holding_period", details)
        if result.decision in {"BUY", "WATCH"}:
            self.assertTrue(result.holding_period)
            self.assertIn("个月", result.holding_period)
            self.assertIn("建议持有周期", result.summary)
        else:
            self.assertEqual(result.holding_period, "")


if __name__ == "__main__":
    unittest.main()
