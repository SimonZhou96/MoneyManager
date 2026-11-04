#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Tests for new strategizers: EarningsRevisionMomentumStrategizer.

Covers:
- Tavily-less fallback (returns neutral score=50, stable trend)
- Positive keyword scoring when Tavily returns bullish earnings news
- Negative keyword scoring when Tavily returns bearish earnings news
- Edge cases (empty stock name, exception resilience)
"""

from datetime import date
from unittest.mock import MagicMock, patch
import unittest

from filters import FilterContext, StockInfo
from strategizers import EarningsRevisionMomentumStrategizer


def _make_stock(name: str = "贵州茅台", code: str = "SH.600519") -> StockInfo:
    return StockInfo(
        market="A",
        code=code,
        name=name,
        pe_ratio=30.0,
        market_cap=20000.0,
    )


class TestEarningsRevisionNoTavily(unittest.TestCase):
    """EarningsRevision tests when Tavily is not configured."""

    def setUp(self):
        self.s = EarningsRevisionMomentumStrategizer()
        self.stock = _make_stock()
        self.ctx = FilterContext(check_date=date(2026, 6, 18), market="A")

    def test_returns_output_object(self):
        """apply() always returns a StrategizerOutput, never crashes."""
        result = self.s.apply(self.stock, self.ctx)
        self.assertIsNotNone(result)
        self.assertEqual(result.name, "EarningsRevisionMomentumStrategizer")

    def test_no_tavily_returns_neutral(self):
        """未配置 Tavily → score=50, earnings_trend=stable."""
        result = self.s.apply(self.stock, self.ctx)
        self.assertEqual(result.details["score"], 50.0)
        self.assertEqual(result.details["earnings_trend"], "stable")
        self.assertEqual(result.details["confidence"], 0.0)

    def test_no_tavily_not_satisfied(self):
        """未配置 Tavily → score=50, satisfied=True（中性信号视为满足）。"""
        result = self.s.apply(self.stock, self.ctx)
        # score=50 属于中性，视为满足（score >= 50）
        self.assertTrue(result.satisfied)
        self.assertEqual(result.details["score"], 50.0)

    def test_empty_stock_name_and_code(self):
        """股票名称和代码均为空 → 返回中性，不崩溃。"""
        stock = _make_stock(name="", code="")
        result = self.s.apply(stock, self.ctx)
        self.assertEqual(result.details["score"], 50.0)
        self.assertEqual(result.details["earnings_trend"], "stable")

    def test_exception_resilience(self):
        """内部异常不传播，返回中性结果。"""
        # Patch the inner Tavily import to raise; inner except catches it → 中性
        with patch("os.getenv", side_effect=RuntimeError("unexpected")):
            result = self.s.apply(self.stock, self.ctx)
        self.assertEqual(result.details["score"], 50.0)
        self.assertEqual(result.details["earnings_trend"], "stable")


class TestEarningsRevisionScoring(unittest.TestCase):
    """EarningsRevision scoring tests with mocked Tavily results."""

    def setUp(self):
        self.s = EarningsRevisionMomentumStrategizer()
        self.ctx = FilterContext(check_date=date(2026, 6, 18), market="A")

    def _mock_search(self, docs):
        """Patch TavilySearchProvider.search() to return given docs."""
        mock_client = MagicMock()
        mock_client.search.return_value = docs
        patcher = patch(
            "signal_analysis.search_providers.TavilySearchProvider",
            return_value=mock_client,
        )
        patcher_env = patch("os.getenv", return_value="mock_key")
        patcher.start()
        patcher_env.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(patcher_env.stop)

    def test_positive_keywords_increase_score(self):
        """Tavily 返回含正面关键词 → score > 50, trend=improving。"""
        from signal_analysis.models import SearchDocument

        docs = [
            SearchDocument(
                title="贵州茅台业绩超预期，净利润增长20%",
                url="http://example.com/1",
                content="贵州茅台发布业绩预告，超预期增长，毛利率改善至92%",
                score=1.0,
                query="贵州茅台 业绩 预告 2026",
            ),
        ]
        self._mock_search(docs)
        stock = _make_stock("贵州茅台", "SH.600519")
        result = self.s.apply(stock, self.ctx)

        # "超预期" + "毛利率改善" = 2 positive kws
        self.assertGreater(result.details["score"], 50.0)
        self.assertEqual(result.details["earnings_trend"], "improving")
        self.assertGreater(result.details["confidence"], 0.5)
        self.assertTrue(result.satisfied)

    def test_negative_keywords_decrease_score(self):
        """Tavily 返回含负面关键词 → score < 50, trend=deteriorating。"""
        from signal_analysis.models import SearchDocument

        docs = [
            SearchDocument(
                title="贵州茅台下修业绩预期，毛利率承压",
                url="http://example.com/2",
                content="公司公告下修2026年业绩预测，毛利率承压且亏损扩大",
                score=1.0,
                query="贵州茅台 业绩 预告 2026",
            ),
        ]
        self._mock_search(docs)
        stock = _make_stock("贵州茅台", "SH.600519")
        result = self.s.apply(stock, self.ctx)

        # "下修" + "毛利率承压" + "亏损扩大" = 3 negative kws
        self.assertLess(result.details["score"], 50.0)
        self.assertEqual(result.details["earnings_trend"], "deteriorating")
        self.assertGreater(result.details["confidence"], 0.5)
        self.assertFalse(result.satisfied)

    def test_positive_outweighs_negative(self):
        """正面关键词多于负面 → trend=improving, score=55~70。"""
        from signal_analysis.models import SearchDocument

        # 2 positive, 1 negative
        docs = [
            SearchDocument(
                title="贵州茅台业绩超预期，订单增长",
                url="http://example.com/3",
                content="公司订单增长30%，产能释放顺利，价格上涨趋势明显。下修风险可控",
                score=1.0,
                query="贵州茅台 业绩 预告 2026",
            ),
        ]
        self._mock_search(docs)
        stock = _make_stock("贵州茅台", "SH.600519")
        result = self.s.apply(stock, self.ctx)

        # "超预期" + "订单增长" + "产能释放" + "价格上涨" = 4 positive
        # "下修" = 1 negative
        # pos(4) > neg(1) → improving, score between 55-70
        self.assertEqual(result.details["earnings_trend"], "improving")
        self.assertGreaterEqual(result.details["score"], 55.0)
        self.assertLessEqual(result.details["score"], 70.0)
        self.assertTrue(result.satisfied)


if __name__ == "__main__":
    unittest.main(verbosity=2)
