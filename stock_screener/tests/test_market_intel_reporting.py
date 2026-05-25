#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import unittest
from datetime import date

from market_intel.reporting import render_multi_stock_report, render_single_stock_report


class MarketIntelReportingTest(unittest.TestCase):
    def test_single_stock_report_is_chinese_plain_language_with_chart_ready_sections(self):
        pack = {
            "market": "A",
            "code": "600519",
            "stock_context": {
                "items": [
                    {
                        "title": "贵州茅台披露经营数据",
                        "summary": "公司收入保持稳健，渠道库存处于可控区间。",
                        "url": "https://example.com/moutai",
                    }
                ]
            },
            "market_context": {
                "items": [
                    {
                        "title": "白酒板块资金关注度回升",
                        "summary": "消费板块出现阶段性轮动。",
                        "url": "https://example.com/market",
                    }
                ]
            },
            "data_gaps": ["未获取到近 N 日逐日资金流明细"],
        }
        result = {
            "code": "600519",
            "name": "贵州茅台",
            "reliability_score": 86,
            "confidence_score": 74,
            "signal_bias": "bullish",
            "summary": "信号较强，但仍需要观察资金连续性。",
            "positive_factors": ["品牌护城河稳固", "板块关注度回升"],
            "risk_factors": ["估值不低", "资金流数据不完整"],
            "source_urls": ["https://example.com/moutai"],
        }

        report = render_single_stock_report(pack, result, report_date=date(2026, 5, 25))

        self.assertIn("# 贵州茅台（600519）市场情报与AI复核报告", report)
        self.assertIn("## 2. 核心评分", report)
        self.assertIn("饼图：综合评分来源占比", report)
        self.assertIn("| 来源 | 分值 | 占比 |", report)
        self.assertIn("柱状图：近 N 日资金流入/流出", report)
        self.assertIn("| 日期 | 资金流入 | 资金流出 | 净流入 |", report)
        self.assertIn("本报告仅用于辅助观察和复盘，不构成投资建议", report)
        self.assertNotIn("<script", report)

    def test_multi_stock_report_has_chinese_sections_and_chart_ready_tables(self):
        packs = [
            {
                "market": "A",
                "code": "600519",
                "data_gaps": [],
                "citations": [{"label": "公告", "url": "https://example.com/600519"}],
            },
            {
                "market": "A",
                "code": "000858",
                "data_gaps": ["未获取到公司新闻"],
                "citations": [],
            },
        ]
        results = [
            {
                "code": "600519",
                "name": "贵州茅台",
                "reliability_score": 86,
                "confidence_score": 74,
                "signal_bias": "bullish",
                "summary": "信号较强，可重点跟踪。",
                "positive_factors": ["盈利质量较稳"],
                "risk_factors": ["估值不低"],
            },
            {
                "code": "000858",
                "name": "五粮液",
                "reliability_score": 58,
                "confidence_score": 61,
                "signal_bias": "neutral",
                "summary": "需要等待更多信息确认。",
                "positive_factors": [],
                "risk_factors": ["信息不足"],
            },
        ]

        report = render_multi_stock_report(packs, results, report_date=date(2026, 5, 25))

        self.assertIn("# 多股票市场情报与AI复核报告", report)
        self.assertIn("饼图：股票评级分布", report)
        self.assertIn("| 评级 | 股票数量 | 占比 |", report)
        self.assertIn("柱状图：综合评分 Top 10", report)
        self.assertIn("| 排名 | 股票代码 | 股票名称 | 综合评分 |", report)
        self.assertIn("## 6. 重点关注股票", report)
        self.assertIn("## 10. 数据缺失与来源说明", report)
        self.assertIn("贵州茅台", report)
        self.assertIn("五粮液", report)
        self.assertIn("本报告仅用于辅助观察和复盘，不构成投资建议", report)


if __name__ == "__main__":
    unittest.main()
