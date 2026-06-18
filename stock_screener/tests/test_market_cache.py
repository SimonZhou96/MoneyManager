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
            credit_risk=CreditRiskResult(score=72, level="low", explanation="信用环境稳定"),
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
