import unittest

from option_lab.macro_analysis import (
    OptionMacroAnalysis,
    apply_macro_analysis_to_candidates,
    cache_key_for_macro_analysis,
    expand_option_company_documents,
    macro_analysis_from_signal_result,
    screening_row_from_snapshot,
)
from option_lab.market_data import FakeOptionMarketDataProvider
from option_lab.models import DataQuality, MarketSnapshot, RiskProfile, UnderlyingSnapshot
from option_lab.service import InMemoryOptionLabRepository, OptionLabService
from signal_analysis.models import SearchDocument, SignalAnalysisResult


class RecordingMacroProvider:
    def __init__(self, macro_score=80):
        self.calls = []
        self.macro_score = macro_score

    def analyze(self, *, market, code, snapshot, ttl_minutes):
        self.calls.append({
            "market": market,
            "code": code,
            "snapshot_id": snapshot.snapshot_id,
            "ttl_minutes": ttl_minutes,
        })
        return OptionMacroAnalysis(
            macro_score=self.macro_score,
            macro_direction="偏多",
            news_impact="利好",
            hot_sector_mark="重点",
            main_force_risk_level="低",
            summary="宏观环境支持观察",
            positive_factors=["热点资金关注"],
            risk_factors=["估值波动"],
            macro_factors=["政策环境中性偏多"],
            source_urls=["https://example.com/news"],
            warnings=[],
            cached=False,
            provider="recording",
            analyzed_at="2026-05-16T10:00:00",
            expires_at="2026-05-16T11:00:00",
        )


class FailingMacroProvider:
    def __init__(self):
        self.calls = 0

    def analyze(self, **kwargs):
        self.calls += 1
        raise RuntimeError("model down")


class RecordingSearchProvider:
    name = "recording"
    is_available = True

    def __init__(self):
        self.queries = []

    def search(self, query, max_results):
        self.queries.append(query)
        return [
            SearchDocument(
                title="Xiaomi Annual Report AI Robotics",
                url="https://ir.mi.com/annual-report",
                content="Xiaomi MiMo large foundation model and Xiaomi-Robotics-0 VLA model for robotics",
                query=query,
            )
        ]

    def search_companies_batch(self, market, rows, max_results):
        return {}


class OptionLabMacroAnalysisTests(unittest.TestCase):
    def test_cache_key_is_symbol_level_not_candidate_level(self):
        key = cache_key_for_macro_analysis("HK", "HK.01810", "default", "2026-05-16")

        self.assertEqual(key, "HK:HK.01810:2026-05-16:default")

    def test_apply_macro_analysis_preserves_option_score_and_adds_composite_score(self):
        repo = InMemoryOptionLabRepository()
        service = OptionLabService(repository=repo, market_data_provider=FakeOptionMarketDataProvider())
        result = service.evaluate_single(market="US", code="US.AAPL", risk_profile=RiskProfile.BALANCED)
        candidate = result.candidates[0]

        enriched = apply_macro_analysis_to_candidates([candidate], RecordingMacroProvider(80).analyze(
            market="US",
            code="US.AAPL",
            snapshot=FakeOptionMarketDataProvider().fetch_snapshot("US", "US.AAPL"),
            ttl_minutes=60,
        ))

        self.assertEqual(enriched[0].score, candidate.score)
        self.assertEqual(enriched[0].option_score, candidate.score)
        self.assertEqual(enriched[0].macro_score, 80)
        self.assertEqual(enriched[0].composite_score, round(candidate.score * 0.7 + 80 * 0.3, 2))
        row = enriched[0].to_dict()
        self.assertEqual(row["评分"], candidate.score)
        self.assertEqual(row["期权评分"], candidate.score)
        self.assertEqual(row["宏观分析评分"], 80)
        self.assertEqual(row["综合评分"], enriched[0].composite_score)

    def test_disabled_macro_analysis_does_not_call_provider(self):
        provider = RecordingMacroProvider(90)
        repo = InMemoryOptionLabRepository()
        service = OptionLabService(
            repository=repo,
            market_data_provider=FakeOptionMarketDataProvider(),
            macro_analysis_provider=provider,
        )

        result = service.evaluate_single(
            market="US",
            code="US.AAPL",
            risk_profile=RiskProfile.BALANCED,
            enable_macro_analysis=False,
        )

        self.assertEqual(provider.calls, [])
        self.assertIsNone(result.macro_analysis)
        self.assertNotIn("宏观分析评分", result.candidates[0].to_dict())

    def test_enabled_macro_analysis_calls_provider_once_and_enriches_all_candidates(self):
        provider = RecordingMacroProvider(82)
        repo = InMemoryOptionLabRepository()
        service = OptionLabService(
            repository=repo,
            market_data_provider=FakeOptionMarketDataProvider(),
            macro_analysis_provider=provider,
        )

        result = service.evaluate_single(
            market="US",
            code="US.AAPL",
            risk_profile=RiskProfile.AGGRESSIVE,
            enable_macro_analysis=True,
            macro_cache_ttl_minutes=45,
        )

        self.assertEqual(len(provider.calls), 1)
        self.assertGreater(len(result.candidates), 1)
        self.assertIsNotNone(result.macro_analysis)
        self.assertTrue(all(candidate.macro_score == 82 for candidate in result.candidates))
        self.assertTrue(all(candidate.composite_score is not None for candidate in result.candidates))
        self.assertEqual(provider.calls[0]["ttl_minutes"], 45)

    def test_macro_cache_hit_avoids_provider_call(self):
        provider = RecordingMacroProvider(60)
        repo = InMemoryOptionLabRepository()
        cached = OptionMacroAnalysis(
            macro_score=77,
            macro_direction="中性",
            news_impact="中性",
            hot_sector_mark="观察",
            main_force_risk_level="数据不足",
            summary="缓存宏观分析",
            positive_factors=[],
            risk_factors=[],
            macro_factors=[],
            source_urls=[],
            warnings=[],
            cached=False,
            provider="cache",
            analyzed_at="2026-05-16T10:00:00",
            expires_at="2999-01-01T00:00:00",
        )
        repo.save_option_macro_analysis_cache(cache_key_for_macro_analysis("US", "US.AAPL"), cached.to_cache_row("US", "US.AAPL"))
        service = OptionLabService(
            repository=repo,
            market_data_provider=FakeOptionMarketDataProvider(),
            macro_analysis_provider=provider,
        )

        result = service.evaluate_single(
            market="US",
            code="US.AAPL",
            risk_profile=RiskProfile.BALANCED,
            enable_macro_analysis=True,
        )

        self.assertEqual(provider.calls, [])
        self.assertEqual(result.macro_analysis.macro_score, 77)
        self.assertTrue(result.macro_analysis.cached)

    def test_macro_failure_does_not_block_option_evaluation(self):
        provider = FailingMacroProvider()
        repo = InMemoryOptionLabRepository()
        service = OptionLabService(
            repository=repo,
            market_data_provider=FakeOptionMarketDataProvider(),
            macro_analysis_provider=provider,
        )

        result = service.evaluate_single(
            market="US",
            code="US.AAPL",
            risk_profile=RiskProfile.BALANCED,
            enable_macro_analysis=True,
        )

        self.assertEqual(provider.calls, 1)
        self.assertGreater(len(result.candidates), 0)
        self.assertIsNone(result.macro_analysis.macro_score)
        self.assertTrue(any("宏观分析失败" in warning for warning in result.warnings))

    def test_screening_row_includes_main_force_fields_from_snapshot(self):
        snapshot = MarketSnapshot(
            snapshot_id="snap-1",
            provider="fake",
            underlying=UnderlyingSnapshot(
                market="HK",
                code="HK.01810",
                name="小米集团",
                signal_summary={
                    "所属板块": "AI硬件",
                    "主力流出风险": "中",
                    "主力风险分": "42.00",
                    "主力风险信号": "放量下跌；盘口卖盘压制",
                    "主力风险说明": "卖压偏高",
                    "资金与盘面观察": "资金流向: 整体资金净流出1000",
                },
            ),
            option_quotes=[],
            data_quality=DataQuality(status="ok"),
        )

        row = screening_row_from_snapshot("HK", "HK.01810", snapshot)

        self.assertEqual(row.sector, "AI硬件")
        self.assertEqual(row.main_force_risk_level, "中")
        self.assertEqual(row.main_force_risk_score, "42.00")
        self.assertIn("盘口卖盘压制", row.main_force_risk_signals)
        self.assertEqual(row.main_force_risk_summary, "卖压偏高")
        self.assertIn("整体资金净流出1000", row.main_force_market_data_observation)

    def test_macro_analysis_explains_data_gaps_and_binds_factor_sources(self):
        row = screening_row_from_snapshot(
            "HK",
            "HK.01810",
            FakeOptionMarketDataProvider().fetch_snapshot("HK", "HK.01810"),
        )
        result = SignalAnalysisResult(
            code="HK.01810",
            name="小米集团",
            reliability_score=70,
            signal_bias="bullish",
            summary="技术信号偏多，但公司事件信息不足",
            positive_factors=["小米在AI、机器人领域有业务布局"],
            risk_factors=["公司新闻及事件信息不足"],
            macro_factors=["港股IPO新政可能提振市场情绪"],
            news_impact="信息不足",
            hot_sector_mark="观察",
            source_urls=["https://www.reuters.com/markets/companies/1810.HK/"],
        )
        docs = [
            SearchDocument(
                title="Xiaomi 2025 Annual Report",
                url="https://ir.mi.com/annual-report",
                content="Xiaomi MiMo large foundation model and Xiaomi-Robotics-0 robotics VLA model",
            ),
            SearchDocument(
                title="Reuters 1810.HK company profile",
                url="https://www.reuters.com/markets/companies/1810.HK/",
                content="Xiaomi company quote page",
            ),
        ]

        analysis = macro_analysis_from_signal_result(
            result=result,
            warnings=[],
            provider="fake-model",
            ttl_minutes=60,
            row=row,
            source_documents=docs,
        )
        payload = analysis.to_dict()

        self.assertIn("主力资金/盘口数据未接入期权实验室", payload["数据缺失原因"])
        self.assertIn("引用来源", payload)
        self.assertEqual(payload["因素引用"]["小米在AI、机器人领域有业务布局"][0]["label"], "小米年报")
        self.assertEqual(payload["因素引用"]["小米在AI、机器人领域有业务布局"][0]["url"], "https://ir.mi.com/annual-report")

    def test_expand_option_company_documents_adds_official_and_filing_queries(self):
        row = screening_row_from_snapshot(
            "HK",
            "HK.01810",
            FakeOptionMarketDataProvider().fetch_snapshot("HK", "HK.01810"),
        )
        row = type(row)(
            **{
                **row.__dict__,
                "name": "小米集团",
            }
        )
        search_provider = RecordingSearchProvider()

        docs = expand_option_company_documents(
            search_provider=search_provider,
            market="HK",
            row=row,
            max_results=2,
        )

        joined_queries = "\n".join(search_provider.queries).lower()
        self.assertIn("official investor relations", joined_queries)
        self.assertIn("annual report", joined_queries)
        self.assertIn("hkexnews", joined_queries)
        self.assertIn("1810.hk", joined_queries)
        self.assertIn("ai robotics", joined_queries)
        self.assertGreaterEqual(len(docs), 1)


if __name__ == "__main__":
    unittest.main()
