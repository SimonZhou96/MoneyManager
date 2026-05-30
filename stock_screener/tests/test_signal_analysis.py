#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import csv
import io
import os
import tempfile
import unittest
from contextlib import redirect_stderr
from datetime import date
from pathlib import Path
from unittest.mock import patch

from signal_analysis.chain import (
    BuildSearchQueriesStep,
    MarketIntelEvidenceStep,
    MarketIntelFinalizeEvidencePacksStep,
    ApplyManualHotNewsStep,
    SearchContextStep,
    SignalAnalysisChain,
    SignalAnalysisContext,
    WriteArtifactsStep,
    write_analysis_columns_to_csv,
)
from signal_analysis.evidence import expand_company_documents
from signal_analysis.hot_news import ManualHotNewsConfig
from signal_analysis.hot_sectors import ManualHotSectorConfig
from signal_analysis.factories import LLMProviderFactory, SearchProviderFactory
from signal_analysis.llm_providers import (
    CodexResponsesLLMProvider,
    DeepSeekLLMProvider,
    FallbackLLMProvider,
    NullLLMProvider,
    OpenAICompatibleLLMProvider,
)
from signal_analysis.models import (
    AnalysisSettings,
    CONFIDENCE_SCORE_CRITERIA,
    HOT_SECTOR_MARK_CRITERIA,
    RELIABILITY_SCORE_CRITERIA,
    SIGNAL_BIAS_CRITERIA,
    ScreeningSignalRow,
    SearchDocument,
    SignalAnalysisResult,
)
from signal_analysis.search_providers import (
    FallbackSearchProvider,
    NullSearchProvider,
    TavilySearchProvider,
    ZhipuWebSearchProvider,
    _build_company_batch_query,
)
import signal_analysis.service as signal_service
from signal_analysis.service import run_signal_analysis_for_market


class FakeSearchProvider:
    name = "fake"
    is_available = True

    def __init__(self, should_fail=False):
        self.should_fail = should_fail
        self.search_queries = []
        self.company_batch_calls = []

    def search(self, query, max_results):
        self.search_queries.append(query)
        if self.should_fail:
            raise RuntimeError("search boom")
        return [SearchDocument(title="News", url="https://example.com/news", content="policy context")]

    def search_companies_batch(self, market, rows, max_results):
        self.company_batch_calls.append([row.code for row in rows])
        if self.should_fail:
            raise RuntimeError("search boom")
        return {
            row.code: [
                SearchDocument(
                    title=f"{row.code} company news",
                    url=f"https://example.com/{row.code}",
                    content=f"{row.name} company context",
                )
            ]
            for row in rows
        }


class FakeLLMProvider:
    name = "fake"
    is_available = True

    @property
    def model_name(self):
        return "fake-model"

    def analyze_batch(self, market, signals, market_documents, sector_documents, hot_sectors, company_documents):
        return [
            SignalAnalysisResult(
                code=row.code,
                name=row.name,
                reliability_score=82.5,
                confidence_score=76.0,
                signal_bias="bullish",
                summary="信号与事件共振",
                positive_factors=["技术信号通过"],
                risk_factors=["宏观波动"],
                macro_factors=["政策稳定"],
                company_events=["近期新闻"],
                market_hot_news=["港股市场热点"],
                company_hot_news=["公司热点"],
                news_impact="利好",
                news_sources=["https://example.com/news"],
                source_urls=["https://example.com/news"],
                hot_sectors=hot_sectors or ["Finance"],
                hot_sector_mark="重点",
                matched_hot_sectors=["Finance"],
                hot_sector_relevance="100",
                hot_sector_reason="所属板块直接匹配热点板块",
                hot_sector_sources=["manual_config"],
                model=self.model_name,
            )
            for row in signals
        ]


class RecordingLLMProvider:
    is_available = True

    def __init__(self, name, model, should_fail=False, reliability_score=80.0):
        self.name = name
        self.model = model
        self.should_fail = should_fail
        self.reliability_score = reliability_score
        self.calls = 0

    @property
    def model_name(self):
        return self.model

    def analyze_batch(self, market, signals, market_documents, sector_documents, hot_sectors, company_documents):
        self.calls += 1
        if self.should_fail:
            raise RuntimeError(f"{self.name} boom")
        return [
            SignalAnalysisResult(
                code=row.code,
                name=row.name,
                reliability_score=self.reliability_score,
                confidence_score=70.0,
                signal_bias="neutral",
                summary=f"{self.name} result",
                model=self.model,
            )
            for row in signals
        ]


class ExplodingRepository:
    def save_results(self, rows):
        raise RuntimeError("db boom")


class CacheAwareRepository:
    def __init__(self, cached_rows=None):
        self.cached_rows = cached_rows or {}
        self.saved_rows = []

    def save_results(self, rows):
        self.saved_rows.extend(rows)

    def get_signal_analysis_cache(self, market, code, timeframe, analysis_profile, trade_date):
        return self.cached_rows.get((market, code, timeframe, analysis_profile, str(trade_date)))


class CompanyNewsCacheRepository(CacheAwareRepository):
    def __init__(self, cached_rows=None, company_news_rows=None):
        super().__init__(cached_rows)
        self.company_news_rows = company_news_rows or {}

    def get_company_news_cache(self, market, code, timeframe, analysis_profile, trade_date, provider):
        return self.company_news_rows.get((
            market,
            code,
            timeframe,
            analysis_profile,
            str(trade_date),
            provider,
        ))


class FakeMarketIntelService:
    def __init__(self):
        self.market_digest_calls = []
        self.stock_intel_calls = []

    def get_market_digest(self, market, force_refresh=False):
        self.market_digest_calls.append((market, force_refresh))
        return {
            "scope_type": "market",
            "market": market,
            "code": "",
            "groups": {
                "market_news": [{
                    "scope_type": "market",
                    "market": market,
                    "code": "",
                    "source": "fixture",
                    "provider": "fixture",
                    "item_type": "market_news",
                    "title": "Market intel headline",
                    "summary": "Market intel summary",
                    "url": "https://example.com/market",
                    "fetched_at": "2026-05-25T09:00:00",
                    "expires_at": "2026-05-25T10:00:00",
                    "dedupe_key": "market-intel",
                }],
            },
            "freshness_status": "fresh",
            "source_status": {"market-provider": {"status": "success", "item_count": 1}},
        }

    def get_stock_intel(self, market, code, force_refresh=False):
        self.stock_intel_calls.append((market, code, force_refresh))
        return {
            "scope_type": "stock",
            "market": market,
            "code": code,
            "groups": {
                "market_news": [{
                    "scope_type": "stock",
                    "market": market,
                    "code": code,
                    "source": "fixture",
                    "provider": "fixture",
                    "item_type": "market_news",
                    "title": f"{code} stock intel headline",
                    "summary": f"{code} stock intel summary",
                    "url": f"https://example.com/{code}",
                    "fetched_at": "2026-05-25T09:00:00",
                    "expires_at": "2026-05-25T10:00:00",
                    "dedupe_key": f"{code}-stock-intel",
                }],
            },
            "freshness_status": "fresh",
            "source_status": {f"{code}-provider": {"status": "success", "item_count": 1}},
        }


class MarketIntelSignalAnalysisIntegrationTest(unittest.TestCase):
    def test_market_intel_evidence_step_fetches_once_per_scope_before_search(self):
        rows = [
            ScreeningSignalRow(
                index=0,
                code="US.AAPL",
                market="US",
                market_label="美股",
                name="Apple",
                pe_ratio="",
                market_cap="",
                sector="Technology",
                conditions_met="左一战法-看涨",
            ),
            ScreeningSignalRow(
                index=1,
                code="US.MSFT",
                market="US",
                market_label="美股",
                name="Microsoft",
                pe_ratio="",
                market_cap="",
                sector="Technology",
                conditions_met="左一战法-看涨",
            ),
        ]
        service = FakeMarketIntelService()
        context = SignalAnalysisContext(
            task_id="task-US",
            market="US",
            csv_path="unused.csv",
            check_date=date(2026, 5, 25),
            settings=AnalysisSettings(search_max_results=2),
            search_provider=NullSearchProvider(),
            llm_provider=NullLLMProvider(),
            rows=rows,
            all_rows=list(rows),
        )
        context.market_intel_service = service

        BuildSearchQueriesStep().run(context)
        MarketIntelEvidenceStep().run(context)
        SearchContextStep().run(context)

        self.assertEqual(service.market_digest_calls, [("US", False)])
        self.assertEqual(
            service.stock_intel_calls,
            [("US", "US.AAPL", False), ("US", "US.MSFT", False)],
        )
        self.assertEqual(
            sum("未配置搜索 provider" in warning for warning in context.warnings),
            1,
        )
        self.assertEqual(len(context.market_documents), 1)
        self.assertEqual(len(context.company_documents["US.AAPL"]), 1)
        self.assertEqual(len(context.company_documents["US.MSFT"]), 1)

    def test_finalized_market_intel_pack_includes_search_and_manual_documents_without_extra_search(self):
        row = ScreeningSignalRow(
            index=0,
            code="US.AAPL",
            market="US",
            market_label="美股",
            name="Apple",
            pe_ratio="",
            market_cap="",
            sector="Technology",
            conditions_met="左一战法-看涨",
        )
        service = FakeMarketIntelService()
        context = SignalAnalysisContext(
            task_id="task-US",
            market="US",
            csv_path="unused.csv",
            check_date=date(2026, 5, 25),
            settings=AnalysisSettings(search_max_results=2),
            search_provider=NullSearchProvider(),
            llm_provider=NullLLMProvider(),
            rows=[row],
            all_rows=[row],
            market_documents=[
                SearchDocument(
                    title="Existing market search doc",
                    url="https://example.com/existing-market",
                    content="Existing market search context.",
                    query="market",
                )
            ],
            company_documents={
                "US.AAPL": [
                    SearchDocument(
                        title="Existing company search doc",
                        url="https://example.com/existing-company",
                        content="Existing company search context.",
                        query="company",
                    )
                ]
            },
            manual_hot_news=ManualHotNewsConfig(
                market_hot_news=["Manual market hot news"],
                company_hot_news_by_code={"US.AAPL": ["Manual company hot news"]},
                market_news_sources=["https://example.com/manual-market"],
                company_news_sources_by_code={"US.AAPL": ["https://example.com/manual-company"]},
            ),
        )
        context.market_intel_service = service

        BuildSearchQueriesStep().run(context)
        MarketIntelEvidenceStep().run(context)
        SearchContextStep().run(context)
        ApplyManualHotNewsStep().run(context)
        MarketIntelFinalizeEvidencePacksStep().run(context)

        self.assertEqual(service.market_digest_calls, [("US", False)])
        self.assertEqual(service.stock_intel_calls, [("US", "US.AAPL", False)])
        self.assertEqual(
            sum("未配置搜索 provider" in warning for warning in context.warnings),
            1,
        )
        payload = context.evidence_packs["US.AAPL"]
        self.assertEqual(
            payload["stock_context"]["items"][0]["title"],
            "US.AAPL stock intel headline",
        )
        self.assertTrue(any(item["title"] == "Existing market search doc" for item in payload["search_documents"]))
        self.assertTrue(any(item["title"] == "Existing company search doc" for item in payload["search_documents"]))
        self.assertTrue(any("Manual market hot news" in item["summary"] for item in payload["manual_items"]))
        self.assertTrue(any("Manual company hot news" in item["summary"] for item in payload["manual_items"]))

    def test_chain_result_exposes_market_intel_evidence_packs(self):
        row = ScreeningSignalRow(
            index=0,
            code="US.AAPL",
            market="US",
            market_label="美股",
            name="Apple",
            pe_ratio="",
            market_cap="",
            sector="Technology",
            conditions_met="左一战法-看涨",
        )
        context = SignalAnalysisContext(
            task_id="task-US",
            market="US",
            csv_path="unused.csv",
            check_date=date(2026, 5, 25),
            settings=AnalysisSettings(search_max_results=2),
            search_provider=NullSearchProvider(),
            llm_provider=NullLLMProvider(),
            rows=[row],
            all_rows=[row],
            market_intel_service=FakeMarketIntelService(),
        )

        result = SignalAnalysisChain(steps=[
            BuildSearchQueriesStep(),
            MarketIntelEvidenceStep(),
            SearchContextStep(),
            ApplyManualHotNewsStep(),
            MarketIntelFinalizeEvidencePacksStep(),
        ]).run(context)

        self.assertIn("US.AAPL", result.evidence_packs)
        self.assertEqual(
            result.evidence_packs["US.AAPL"]["stock_context"]["items"][0]["title"],
            "US.AAPL stock intel headline",
        )

    def test_write_artifacts_uses_market_intel_report_when_evidence_packs_exist(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            csv_path = Path(tmp_dir) / "screening_result_2026-05-25_A.csv"
            with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=["股票代码", "市场", "名称", "标的类型"])
                writer.writeheader()
                writer.writerow({"股票代码": "600519", "市场": "A股", "名称": "贵州茅台", "标的类型": "股票"})

            row = ScreeningSignalRow(
                index=0,
                code="600519",
                market="A",
                market_label="A股",
                name="贵州茅台",
                pe_ratio="",
                market_cap="",
                sector="白酒",
                conditions_met="左一战法",
            )
            context = SignalAnalysisContext(
                task_id="task-A",
                market="A",
                csv_path=str(csv_path),
                check_date=date(2026, 5, 25),
                settings=AnalysisSettings(),
                search_provider=NullSearchProvider(),
                llm_provider=NullLLMProvider(),
                rows=[row],
                all_rows=[row],
                evidence_packs={
                    "600519": {
                        "market": "A",
                        "code": "600519",
                        "stock_context": {"items": [{"title": "贵州茅台经营稳健", "summary": "渠道反馈稳定"}]},
                        "data_gaps": [],
                    }
                },
                results_by_code={
                    "600519": SignalAnalysisResult(
                        code="600519",
                        name="贵州茅台",
                        reliability_score=86,
                        confidence_score=74,
                        signal_bias="bullish",
                        summary="信号较强，但仍需要观察资金连续性。",
                    )
                },
            )

            WriteArtifactsStep().run(context)

            report_text = Path(context.artifact_paths[0]).read_text(encoding="utf-8")
            self.assertIn("# 多股票市场情报与AI复核报告", report_text)
            self.assertIn("饼图：股票评级分布", report_text)
            self.assertIn("## 10. 数据缺失与来源说明", report_text)


class SignalAnalysisTest(unittest.TestCase):
    def test_build_market_intel_service_requires_env_unless_explicitly_enabled(self):
        class FakeDB:
            instances = []

            def __init__(self, config):
                self.config = config
                self.schema_initialized = False
                self.closed = False
                self.__class__.instances.append(self)

            def init_market_intel_schema(self):
                self.schema_initialized = True

            def close(self):
                self.closed = True

        FakeDB.instances = []
        with patch.dict(os.environ, {"SIGNAL_ENABLE_MARKET_INTEL": "0"}), \
                patch.object(signal_service, "MarketDatabase", FakeDB), \
                patch.object(signal_service, "MySqlMarketIntelRepository", lambda db: {"db": db}), \
                patch.object(signal_service, "build_market_intel_providers", return_value=["provider"]), \
                patch.object(
                    signal_service,
                    "MarketIntelService",
                    lambda repository, providers: {"repository": repository, "providers": providers},
                ):
            self.assertIsNone(signal_service.build_market_intel_service(object()))
            service = signal_service.build_market_intel_service(object(), enabled=True)

        self.assertEqual(len(FakeDB.instances), 1)
        self.assertTrue(FakeDB.instances[0].schema_initialized)
        self.assertEqual(service["providers"], ["provider"])

    def signal_rows(self):
        return [
            ScreeningSignalRow(
                index=0,
                code="HK.00001",
                market="HK",
                market_label="港股",
                name="Test HK",
                pe_ratio="",
                market_cap="",
                sector="Finance",
                conditions_met="左一战法-看涨|EMA突破",
            )
        ]

    def write_csv(self, directory, rows=None):
        path = Path(directory) / "screening_result_2026-05-09_HK.csv"
        rows = rows or [{
            "股票代码": "HK.00001",
            "市场": "港股",
            "名称": "Test HK",
            "标的类型": "股票",
            "pe": "10",
            "市值": "1000",
            "所属板块": "Finance",
            "满足的条件": "左一战法-看涨|EMA突破",
            "主力流出风险": "中",
            "主力风险分": "42.00",
            "主力风险信号": "放量下跌；盘口卖盘压制",
            "主力风险说明": "放量下跌且盘口卖盘压力偏高",
            "资金与盘面观察": "资金流向: 整体资金净流入5.71万，超大单净流入69.55万，大单净流出30.02万；盘口: 卖一量2.82万股，买一量2.66万股，卖一约为买一1.1倍；成交量分布: 现价90.00，近120日成交量加权价101.55，低于11.4%；最大成交量区间100.00~101.00，占比21.3%；样本80日，总成交量8.25万股",
            "资金流向数据": "数据不足:未启用",
            "盘口数据": "数据不足:未启用",
            "龙虎榜数据": "不适用",
            "成交量分布数据": "可用:K线成交量分布",
        }]
        fieldnames = [
            "股票代码", "市场", "名称", "标的类型", "pe", "市值", "所属板块", "满足的条件",
            "主力流出风险", "主力风险分", "主力风险信号", "主力风险说明",
            "资金与盘面观察", "资金流向数据", "盘口数据", "龙虎榜数据", "成交量分布数据",
        ]
        for row in rows:
            for key in row:
                if key not in fieldnames:
                    fieldnames.append(key)
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=fieldnames,
            )
            writer.writeheader()
            writer.writerows(rows)
        return str(path)

    def test_factories_return_null_providers_without_credentials(self):
        with patch.dict(os.environ, {}, clear=True):
            settings = AnalysisSettings()
            self.assertIsInstance(SearchProviderFactory.from_env(settings), NullSearchProvider)
            self.assertIsInstance(LLMProviderFactory.from_env(settings), NullLLMProvider)

    def test_search_factory_builds_tavily_then_zhipu_fallback(self):
        with patch.dict(
            os.environ,
            {
                "TAVILY_API_KEY": "tavily-key",
                "ZHIPUAI_API_KEY": "zhipu-key",
            },
            clear=True,
        ):
            provider = SearchProviderFactory.from_env(AnalysisSettings(timeout_sec=9))

        self.assertIsInstance(provider, FallbackSearchProvider)
        self.assertEqual([item.name for item in provider.providers], ["tavily", "zhipuai"])
        self.assertIsInstance(provider.providers[0], TavilySearchProvider)
        self.assertIsInstance(provider.providers[1], ZhipuWebSearchProvider)
        self.assertEqual(provider.providers[0].timeout_sec, 9)
        self.assertEqual(provider.providers[1].timeout_sec, 9)

    def test_search_factory_uses_bigmodel_key_for_zhipu_fallback(self):
        with patch.dict(
            os.environ,
            {
                "SIGNAL_SEARCH_PROVIDER_ORDER": "zhipuai",
                "BIGMODEL_API_KEY": "bigmodel-key",
            },
            clear=True,
        ):
            provider = SearchProviderFactory.from_env(AnalysisSettings(timeout_sec=11))

        self.assertIsInstance(provider, ZhipuWebSearchProvider)
        self.assertEqual(provider.api_key, "bigmodel-key")
        self.assertEqual(provider.timeout_sec, 11)

    def test_search_factory_uses_zhipu_api_key_alias_for_zhipu_fallback(self):
        with patch.dict(
            os.environ,
            {
                "SIGNAL_SEARCH_PROVIDER_ORDER": "zhipuai",
                "ZHIPU_API_KEY": "zhipu-alias-key",
            },
            clear=True,
        ):
            provider = SearchProviderFactory.from_env(AnalysisSettings(timeout_sec=11))

        self.assertIsInstance(provider, ZhipuWebSearchProvider)
        self.assertEqual(provider.api_key, "zhipu-alias-key")
        self.assertEqual(provider.timeout_sec, 11)

    def test_run_signal_analysis_uses_shared_cache_before_llm(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            csv_path = self.write_csv(tmp_dir)
            repo = CacheAwareRepository({
                ("HK", "HK.00001", "1d", "default", "2026-05-19"): {
                    "code": "HK.00001",
                    "name": "Test HK",
                    "analysis_status": "success",
                    "summary": "cached",
                    "reliability_score": 88,
                    "confidence_score": 75,
                    "signal_bias": "bullish",
                    "company_events": ["缓存公司事件"],
                    "company_hot_news": ["缓存公司新闻"],
                    "market_hot_news": ["缓存市场新闻"],
                    "news_impact": "利好",
                    "news_sources": ["https://example.com/cache"],
                    "hot_sectors": ["机器人"],
                    "hot_sector_mark": "重点",
                    "matched_hot_sectors": ["机器人"],
                    "hot_sector_reason": "cache",
                    "source_urls": ["https://example.com/cache"],
                    "data_gaps": [],
                    "evidence_links": [],
                    "factor_citations": {},
                    "model": "cache",
                }
            })
            llm = RecordingLLMProvider("unused", "unused-model")
            search = FakeSearchProvider()
            result = run_signal_analysis_for_market(
                mysql_config=None,
                task_id="task-cache",
                market="HK",
                csv_path=csv_path,
                check_date=date(2026, 5, 19),
                timeframe="1d",
                enabled=True,
                repository_override=repo,
                search_provider_override=search,
                llm_provider_override=llm,
            )

            self.assertEqual(llm.calls, 0)
            self.assertEqual(search.search_queries, [])
            self.assertEqual(search.company_batch_calls, [])
            self.assertEqual(result.results_by_code["HK.00001"].summary, "cached")

    def test_run_signal_analysis_recomputes_when_cached_company_events_below_threshold(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            csv_path = self.write_csv(tmp_dir)
            repo = CacheAwareRepository({
                ("HK", "HK.00001", "1d", "default", "2026-05-19"): {
                    "code": "HK.00001",
                    "name": "Test HK",
                    "analysis_status": "success",
                    "summary": "cached without events",
                    "reliability_score": 55,
                    "confidence_score": 45,
                    "signal_bias": "neutral",
                    "company_events": [],
                    "company_hot_news": ["缓存公司新闻"],
                    "market_hot_news": ["缓存市场新闻"],
                    "news_impact": "中性",
                    "news_sources": ["https://example.com/cache"],
                    "hot_sectors": ["机器人"],
                    "hot_sector_mark": "观察",
                    "matched_hot_sectors": [],
                    "hot_sector_reason": "cache",
                    "source_urls": ["https://example.com/cache"],
                    "data_gaps": [],
                    "evidence_links": [],
                    "factor_citations": {},
                    "model": "cache",
                }
            })
            llm = RecordingLLMProvider("recompute", "recompute-model", reliability_score=92.0)
            search = FakeSearchProvider()
            with patch.dict(os.environ, {"SIGNAL_CACHE_MIN_COMPANY_EVENTS": "1"}):
                result = run_signal_analysis_for_market(
                    mysql_config=None,
                    task_id="task-cache-threshold",
                    market="HK",
                    csv_path=csv_path,
                    check_date=date(2026, 5, 19),
                    timeframe="1d",
                    enabled=True,
                    repository_override=repo,
                    search_provider_override=search,
                    llm_provider_override=llm,
                )

            self.assertEqual(llm.calls, 1)
            self.assertEqual(search.company_batch_calls, [["HK.00001"]])
            self.assertEqual(result.results_by_code["HK.00001"].model, "recompute-model")
            self.assertTrue(any("缓存重算触发" in warning for warning in result.warnings))

    def test_run_signal_analysis_uses_provider_specific_company_news_cache(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            csv_path = self.write_csv(tmp_dir)
            base_cache = {
                ("HK", "HK.00001", "1d", "default", "2026-05-19"): {
                    "code": "HK.00001",
                    "name": "Test HK",
                    "analysis_status": "success",
                    "summary": "cached summary",
                    "reliability_score": 88,
                    "confidence_score": 75,
                    "signal_bias": "bullish",
                    "company_events": [],
                    "company_hot_news": [],
                    "market_hot_news": ["缓存市场新闻"],
                    "news_impact": "信息不足",
                    "news_sources": [],
                    "hot_sectors": ["机器人"],
                    "hot_sector_mark": "重点",
                    "matched_hot_sectors": ["机器人"],
                    "hot_sector_reason": "cache",
                    "source_urls": [],
                    "data_gaps": [],
                    "evidence_links": [],
                    "factor_citations": {},
                    "model": "cache",
                }
            }
            news_cache = {
                ("HK", "HK.00001", "1d", "default", "2026-05-19", "fake"): {
                    "company_news_provider": "fake",
                    "company_events": ["独立缓存公司事件"],
                    "company_hot_news": ["独立缓存公司新闻"],
                    "news_impact": "利好",
                    "news_sources": ["https://example.com/news"],
                    "source_urls": ["https://example.com/news"],
                }
            }
            repo = CompanyNewsCacheRepository(base_cache, news_cache)
            llm = RecordingLLMProvider("unused", "unused-model")
            search = FakeSearchProvider()

            result = run_signal_analysis_for_market(
                mysql_config=None,
                task_id="task-company-news-cache",
                market="HK",
                csv_path=csv_path,
                check_date=date(2026, 5, 19),
                timeframe="1d",
                enabled=True,
                repository_override=repo,
                search_provider_override=search,
                llm_provider_override=llm,
            )

            self.assertEqual(llm.calls, 0)
            self.assertEqual(search.company_batch_calls, [])
            analysis = result.results_by_code["HK.00001"]
            self.assertEqual(analysis.company_events, ["独立缓存公司事件"])
            self.assertEqual(analysis.company_hot_news, ["独立缓存公司新闻"])
            self.assertEqual(analysis.news_impact, "利好")

    def test_run_signal_analysis_recomputes_when_company_news_cache_missing(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            csv_path = self.write_csv(tmp_dir)
            repo = CompanyNewsCacheRepository({
                ("HK", "HK.00001", "1d", "default", "2026-05-19"): {
                    "code": "HK.00001",
                    "name": "Test HK",
                    "analysis_status": "success",
                    "summary": "old cache",
                    "reliability_score": 55,
                    "confidence_score": 45,
                    "signal_bias": "neutral",
                    "company_events": [],
                    "company_hot_news": [],
                    "market_hot_news": ["缓存市场新闻"],
                    "news_impact": "信息不足",
                    "news_sources": [],
                    "hot_sectors": [],
                    "hot_sector_mark": "未知",
                    "matched_hot_sectors": [],
                    "hot_sector_reason": "",
                    "source_urls": [],
                    "data_gaps": [],
                    "evidence_links": [],
                    "factor_citations": {},
                    "model": "cache",
                }
            })
            llm = RecordingLLMProvider("recompute", "recompute-model", reliability_score=92.0)
            search = FakeSearchProvider()

            result = run_signal_analysis_for_market(
                mysql_config=None,
                task_id="task-company-news-cache-miss",
                market="HK",
                csv_path=csv_path,
                check_date=date(2026, 5, 19),
                timeframe="1d",
                enabled=True,
                repository_override=repo,
                search_provider_override=search,
                llm_provider_override=llm,
            )

            self.assertEqual(llm.calls, 1)
            self.assertEqual(search.company_batch_calls, [["HK.00001"]])
            self.assertEqual(result.results_by_code["HK.00001"].model, "recompute-model")
            self.assertTrue(any("公司时事缓存缺失" in warning for warning in result.warnings))

    def test_company_news_cache_key_keeps_providers_separate(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            csv_path = self.write_csv(tmp_dir)
            base_cache = {
                ("HK", "HK.00001", "1d", "default", "2026-05-19"): {
                    "code": "HK.00001",
                    "name": "Test HK",
                    "analysis_status": "success",
                    "summary": "cached summary",
                    "reliability_score": 88,
                    "confidence_score": 75,
                    "signal_bias": "bullish",
                    "company_events": [],
                    "company_hot_news": [],
                    "market_hot_news": ["缓存市场新闻"],
                    "news_impact": "信息不足",
                    "news_sources": [],
                    "hot_sectors": [],
                    "hot_sector_mark": "未知",
                    "matched_hot_sectors": [],
                    "hot_sector_reason": "",
                    "source_urls": [],
                    "data_gaps": [],
                    "evidence_links": [],
                    "factor_citations": {},
                    "model": "cache",
                }
            }
            news_cache = {
                ("HK", "HK.00001", "1d", "default", "2026-05-19", "tavily"): {
                    "company_news_provider": "tavily",
                    "company_events": ["Tavily 公司事件"],
                    "company_hot_news": [],
                    "news_impact": "中性",
                    "news_sources": [],
                    "source_urls": [],
                }
            }
            repo = CompanyNewsCacheRepository(base_cache, news_cache)
            llm = RecordingLLMProvider("recompute", "recompute-model")
            search = FakeSearchProvider()
            search.name = "zhipuai"

            result = run_signal_analysis_for_market(
                mysql_config=None,
                task_id="task-company-news-provider-separation",
                market="HK",
                csv_path=csv_path,
                check_date=date(2026, 5, 19),
                timeframe="1d",
                enabled=True,
                repository_override=repo,
                search_provider_override=search,
                llm_provider_override=llm,
            )

            self.assertEqual(llm.calls, 1)
            self.assertEqual(result.results_by_code["HK.00001"].model, "recompute-model")
            self.assertTrue(any("providers=zhipuai" in warning for warning in result.warnings))

    def test_expand_company_documents_adds_market_specific_authoritative_queries(self):
        provider = FakeSearchProvider()
        rows = [
            ScreeningSignalRow(0, "HK.01810", "HK", "港股", "小米集团-W", "", "", "消费电子", "左一战法"),
            ScreeningSignalRow(1, "US.AAPL", "US", "美股", "Apple Inc.", "", "", "Technology", "左一战法"),
            ScreeningSignalRow(2, "SH.600519", "A", "A股", "贵州茅台", "", "", "白酒", "左一战法"),
        ]

        for row in rows:
            expand_company_documents(provider, row.market, row, 2)

        joined_queries = "\n".join(provider.search_queries).lower()
        self.assertIn("hkexnews.hk", joined_queries)
        self.assertIn("sec.gov", joined_queries)
        self.assertIn("cninfo.com.cn", joined_queries)
        self.assertIn("sse.com.cn", joined_queries)
        self.assertIn("szse.cn", joined_queries)

    def test_company_batch_query_includes_market_authoritative_source_terms_without_extra_calls(self):
        hk_query = _build_company_batch_query("HK", [self.signal_rows()[0]], max_chars=390)
        us_query = _build_company_batch_query(
            "US",
            [ScreeningSignalRow(0, "US.AAPL", "US", "美股", "Apple", "", "", "Technology", "左一战法")],
            max_chars=390,
        )
        a_query = _build_company_batch_query(
            "A",
            [ScreeningSignalRow(0, "SH.600519", "A", "A股", "贵州茅台", "", "", "白酒", "左一战法")],
            max_chars=390,
        )

        self.assertIn("HKEX announcement", hk_query)
        self.assertIn("SEC filing", us_query)
        self.assertIn("巨潮资讯", a_query)

    def test_factory_selects_default_openai_compatible_provider(self):
        with patch.dict(os.environ, {"LLM_API_KEY": "key", "LLM_MODEL": "model-x"}, clear=True):
            provider = LLMProviderFactory.from_env(AnalysisSettings())

        self.assertIsInstance(provider, FallbackLLMProvider)
        self.assertEqual(provider.provider_names, ["openai_compatible", "codex_responses", "deepseek"])
        self.assertIsInstance(provider.providers[0], OpenAICompatibleLLMProvider)
        self.assertEqual(provider.providers[0].model_name, "model-x")

    def test_factory_builds_fallback_provider_from_provider_order(self):
        with patch.dict(
            os.environ,
            {
                "LLM_PROVIDER_ORDER": "openai_compatible,codex_responses,deepseek",
                "LLM_API_KEY": "openai-key",
                "LLM_MODEL": "model-x",
                "CODEX_API_KEY": "codex-key",
                "CODEX_LLM_MODEL": "gpt-5.2-codex",
                "DEEPSEEK_API_KEY": "deepseek-key",
                "DEEPSEEK_LLM_MODEL": "deepseek-v4-flash",
            },
            clear=True,
        ):
            provider = LLMProviderFactory.from_env(AnalysisSettings())

        self.assertIsInstance(provider, FallbackLLMProvider)
        self.assertEqual(provider.provider_names, ["openai_compatible", "codex_responses", "deepseek"])

    def test_factory_uses_legacy_llm_provider_as_first_fallback_choice(self):
        with patch.dict(
            os.environ,
            {
                "LLM_PROVIDER": "deepseek",
                "LLM_API_KEY": "openai-key",
                "LLM_MODEL": "model-x",
                "CODEX_API_KEY": "codex-key",
                "CODEX_LLM_MODEL": "gpt-5.2-codex",
                "DEEPSEEK_API_KEY": "deepseek-key",
                "DEEPSEEK_LLM_MODEL": "deepseek-v4-flash",
            },
            clear=True,
        ):
            provider = LLMProviderFactory.from_env(AnalysisSettings())

        self.assertIsInstance(provider, FallbackLLMProvider)
        self.assertEqual(provider.provider_names, ["deepseek", "openai_compatible", "codex_responses"])

    def test_factory_selects_codex_responses_provider_with_llm_key_fallback(self):
        with patch.dict(
            os.environ,
            {
                "LLM_PROVIDER": "codex_responses",
                "LLM_API_KEY": "fallback-key",
                "CODEX_LLM_MODEL": "gpt-5.2-codex",
                "CODEX_REASONING_EFFORT": "high",
            },
            clear=True,
        ):
            provider = LLMProviderFactory.from_env(AnalysisSettings(timeout_sec=7))

        self.assertIsInstance(provider, FallbackLLMProvider)
        self.assertEqual(provider.provider_names, ["codex_responses", "deepseek"])
        self.assertIsInstance(provider.providers[0], CodexResponsesLLMProvider)
        self.assertEqual(provider.providers[0].model_name, "gpt-5.2-codex")
        self.assertEqual(provider.providers[0].reasoning_effort, "high")

    def test_factory_selects_deepseek_provider_with_llm_key_fallback(self):
        with patch.dict(
            os.environ,
            {
                "LLM_PROVIDER_ORDER": "deepseek",
                "LLM_API_KEY": "fallback-key",
                "DEEPSEEK_LLM_MODEL": "deepseek-v4-pro",
            },
            clear=True,
        ):
            provider = LLMProviderFactory.from_env(AnalysisSettings(timeout_sec=7))

        self.assertIsInstance(provider, DeepSeekLLMProvider)
        self.assertEqual(provider.model_name, "deepseek-v4-pro")
        self.assertEqual(provider.timeout_sec, 7)

    def test_factory_skips_unconfigured_providers_in_order(self):
        with patch.dict(
            os.environ,
            {
                "LLM_PROVIDER_ORDER": "openai_compatible,codex_responses,deepseek",
                "DEEPSEEK_API_KEY": "deepseek-key",
                "DEEPSEEK_LLM_MODEL": "deepseek-v4-flash",
            },
            clear=True,
        ):
            provider = LLMProviderFactory.from_env(AnalysisSettings())

        self.assertIsInstance(provider, DeepSeekLLMProvider)
        self.assertEqual(provider.model_name, "deepseek-v4-flash")

    def test_tavily_provider_parses_search_results(self):
        class Response:
            status_code = 200
            text = ""

            def json(self):
                return {
                    "results": [
                        {
                            "title": "Policy",
                            "url": "https://example.com/policy",
                            "content": "market policy",
                            "score": 0.91,
                        }
                    ]
                }

        with patch("signal_analysis.search_providers.requests.post", return_value=Response()) as post:
            provider = TavilySearchProvider(api_key="key", timeout_sec=5)
            docs = provider.search("HK market", 3)

        self.assertEqual(len(docs), 1)
        self.assertEqual(docs[0].url, "https://example.com/policy")
        self.assertEqual(docs[0].score, 0.91)
        self.assertEqual(post.call_args.kwargs["json"]["max_results"], 3)

    def test_zhipu_provider_parses_search_results_and_uses_bearer_auth(self):
        class Response:
            status_code = 200
            text = ""

            def json(self):
                return {
                    "search_result": [
                        {
                            "title": "小米汽车交付进展",
                            "link": "https://example.com/xiaomi",
                            "content": "小米汽车交付量继续增长",
                            "media": "示例媒体",
                            "publish_date": "2026-05-24",
                        }
                    ]
                }

        with patch("signal_analysis.search_providers.requests.post", return_value=Response()) as post:
            provider = ZhipuWebSearchProvider(api_key="zhipu-key", timeout_sec=5)
            docs = provider.search("HK.01810 小米集团 最新公告 新闻 机器人 AI", 3)

        self.assertEqual(len(docs), 1)
        self.assertEqual(docs[0].title, "小米汽车交付进展")
        self.assertEqual(docs[0].url, "https://example.com/xiaomi")
        self.assertIn("示例媒体", docs[0].content)
        self.assertIn("2026-05-24", docs[0].content)
        self.assertEqual(post.call_args.kwargs["headers"]["Authorization"], "Bearer zhipu-key")
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["count"], 3)
        self.assertLessEqual(len(payload["search_query"]), 70)

    def test_zhipu_company_batch_query_stays_under_provider_limit(self):
        class Response:
            status_code = 200
            text = ""

            def json(self):
                return {"search_result": []}

        rows = [
            ScreeningSignalRow(index, code, "HK", "港股", name, "", "", "", "")
            for index, (code, name) in enumerate([
                ("HK.01810", "小米集团-W"),
                ("HK.00700", "腾讯控股"),
                ("HK.03690", "美团-W"),
            ])
        ]

        with patch("signal_analysis.search_providers.requests.post", return_value=Response()) as post:
            provider = ZhipuWebSearchProvider(api_key="zhipu-key", timeout_sec=5)
            grouped = provider.search_companies_batch("HK", rows, 5)

        self.assertGreaterEqual(post.call_count, 1)
        for call in post.call_args_list:
            self.assertLessEqual(len(call.kwargs["json"]["search_query"]), 70)
        self.assertEqual(set(grouped.keys()), {"HK.01810", "HK.00700", "HK.03690"})

    def test_fallback_search_provider_uses_tavily_without_zhipu_when_tavily_succeeds(self):
        class Provider:
            is_available = True

            def __init__(self, name, docs=None, should_fail=False):
                self.name = name
                self.docs = docs or []
                self.should_fail = should_fail
                self.search_calls = 0

            def search(self, query, max_results):
                self.search_calls += 1
                if self.should_fail:
                    raise RuntimeError(f"{self.name} boom")
                return self.docs

            def search_companies_batch(self, market, rows, max_results):
                return {row.code: [] for row in rows}

        tavily_doc = SearchDocument(title="Tavily", url="https://example.com/tavily", content="ok")
        tavily = Provider("tavily", [tavily_doc])
        zhipu = Provider("zhipuai", [SearchDocument(title="Zhipu", url="", content="unused")])

        docs = FallbackSearchProvider([tavily, zhipu]).search("query", 2)

        self.assertEqual(docs, [tavily_doc])
        self.assertEqual(tavily.search_calls, 1)
        self.assertEqual(zhipu.search_calls, 0)

    def test_fallback_search_provider_uses_zhipu_when_tavily_batch_fails(self):
        class Provider:
            is_available = True

            def __init__(self, name, should_fail=False):
                self.name = name
                self.should_fail = should_fail
                self.batch_calls = 0

            def search(self, query, max_results):
                return []

            def search_companies_batch(self, market, rows, max_results):
                self.batch_calls += 1
                if self.should_fail:
                    raise RuntimeError(f"{self.name} boom")
                return {
                    row.code: [
                        SearchDocument(
                            title=f"{self.name} {row.code}",
                            url=f"https://example.com/{row.code}",
                            content=row.name,
                        )
                    ]
                    for row in rows
                }

        rows = [ScreeningSignalRow(0, "HK.01810", "HK", "港股", "小米集团-W", "", "", "", "")]
        tavily = Provider("tavily", should_fail=True)
        zhipu = Provider("zhipuai")

        provider = FallbackSearchProvider([tavily, zhipu])
        grouped = provider.search_companies_batch("HK", rows, 3)

        self.assertEqual(tavily.batch_calls, 1)
        self.assertEqual(zhipu.batch_calls, 1)
        self.assertEqual(grouped["HK.01810"][0].title, "zhipuai HK.01810")
        self.assertEqual(provider.last_success_provider, "zhipuai")

    def test_fallback_search_provider_returns_empty_when_all_providers_fail(self):
        class Provider:
            is_available = True

            def __init__(self, name):
                self.name = name

            def search(self, query, max_results):
                raise RuntimeError(f"{self.name} boom")

            def search_companies_batch(self, market, rows, max_results):
                raise RuntimeError(f"{self.name} boom")

        rows = [
            ScreeningSignalRow(0, "US.AAPL", "US", "美股", "Apple Inc", "", "", "", ""),
            ScreeningSignalRow(1, "HK.01810", "HK", "港股", "小米集团-W", "", "", "", ""),
        ]
        provider = FallbackSearchProvider([Provider("tavily"), Provider("zhipuai")])

        self.assertEqual(provider.search("query", 3), [])
        self.assertEqual(provider.search_companies_batch("HK", rows, 3), {"US.AAPL": [], "HK.01810": []})

    def test_tavily_provider_assigns_batch_documents_to_matching_stocks(self):
        class Response:
            status_code = 200
            text = ""

            def json(self):
                return {
                    "results": [
                        {
                            "title": "HK.00001 earnings update",
                            "url": "https://example.com/hk-00001",
                            "content": "Company event for HK.00001",
                        },
                        {
                            "title": "AAPL stock news",
                            "url": "https://example.com/aapl",
                            "content": "Apple Inc product launch",
                        },
                        {
                            "title": "Textile Manufacturing rebound",
                            "url": "https://example.com/bros",
                            "content": "Bros Eastern Co Ltd latest company event",
                        },
                    ]
                }

        rows = [
            ScreeningSignalRow(
                index=0,
                code="HK.00001",
                market="HK",
                market_label="港股",
                name="Test HK",
                pe_ratio="",
                market_cap="",
                sector="Finance",
                conditions_met="",
            ),
            ScreeningSignalRow(
                index=1,
                code="US.AAPL",
                market="US",
                market_label="美股",
                name="Apple Inc",
                pe_ratio="",
                market_cap="",
                sector="Technology",
                conditions_met="",
            ),
            ScreeningSignalRow(
                index=2,
                code="SH.601339",
                market="A",
                market_label="A股",
                name="Bros Eastern Co Ltd",
                pe_ratio="",
                market_cap="",
                sector="Consumer Cyclical",
                conditions_met="",
            ),
        ]

        with patch("signal_analysis.search_providers.requests.post", return_value=Response()) as post:
            provider = TavilySearchProvider(api_key="key", timeout_sec=5)
            grouped = provider.search_companies_batch("US", rows, 5)

        self.assertEqual(post.call_count, 1)
        self.assertEqual(len(grouped["HK.00001"]), 1)
        self.assertEqual(len(grouped["US.AAPL"]), 1)
        self.assertEqual(len(grouped["SH.601339"]), 1)
        self.assertIn("HK.00001", post.call_args.kwargs["json"]["query"])
        self.assertIn("US.AAPL", post.call_args.kwargs["json"]["query"])

    def test_tavily_company_batch_query_stays_under_provider_limit_with_name_fragments(self):
        class Response:
            status_code = 200
            text = ""

            def json(self):
                return {"results": []}

        fixtures = [
            ("HK.00006", "POWER ASSETS HOLDINGS"),
            ("HK.01038", "CK INFRASTRUCTURE HOLDINGS"),
            ("HK.02460", "JIDU INC"),
            ("HK.02543", "AUTOHOME INC"),
            ("HK.02587", "WEIMOB INC"),
            ("HK.02656", "BOSS ZHIPIN"),
            ("HK.02659", "S.F. HOLDING"),
            ("HK.03476", "CSOP MSCI HK TECH ETF"),
            ("HK.09151", "PREMIA CHINA STAR50 ETF"),
        ]
        rows = [
            ScreeningSignalRow(
                index=index,
                code=code,
                market="HK",
                market_label="港股",
                name=name,
                pe_ratio="",
                market_cap="",
                sector="",
                conditions_met="",
            )
            for index, (code, name) in enumerate(fixtures)
        ]

        with patch("signal_analysis.search_providers.requests.post", return_value=Response()) as post:
            provider = TavilySearchProvider(api_key="key", timeout_sec=5)
            grouped = provider.search_companies_batch("HK", rows, 5)

        self.assertEqual(post.call_count, 1)
        query = post.call_args.kwargs["json"]["query"]
        self.assertLessEqual(len(query), 400)
        for row in rows:
            self.assertIn(row.code, query)
            self.assertIn(row.name.split()[0], query)
            self.assertIn(row.code, grouped)

    def test_tavily_company_batch_debug_outputs_cross_market_matching_context(self):
        class Response:
            status_code = 200
            text = ""

            def json(self):
                return {
                    "results": [
                        {
                            "title": "Apple Inc latest product launch",
                            "url": "https://example.com/aapl",
                            "content": "AAPL company event",
                        },
                        {
                            "title": "贵州茅台 600519 最新公告",
                            "url": "https://example.com/600519",
                            "content": "贵州茅台 公司事件",
                        },
                    ]
                }

        rows = [
            ScreeningSignalRow(
                index=0,
                code="US.AAPL",
                market="US",
                market_label="美股",
                name="Apple Inc",
                pe_ratio="",
                market_cap="",
                sector="Technology",
                conditions_met="",
            ),
            ScreeningSignalRow(
                index=1,
                code="SH.600519",
                market="A",
                market_label="A股",
                name="贵州茅台",
                pe_ratio="",
                market_cap="",
                sector="白酒",
                conditions_met="",
            ),
            ScreeningSignalRow(
                index=2,
                code="HK.00148",
                market="HK",
                market_label="港股",
                name="KINGBOARD HLDG",
                pe_ratio="",
                market_cap="",
                sector="Industrials",
                conditions_met="",
            ),
        ]

        stderr = io.StringIO()
        with patch.dict(os.environ, {"SIGNAL_COMPANY_SEARCH_DEBUG": "1"}):
            with patch("signal_analysis.search_providers.requests.post", return_value=Response()):
                with redirect_stderr(stderr):
                    provider = TavilySearchProvider(api_key="key", timeout_sec=5)
                    grouped = provider.search_companies_batch("HK", rows, 5)

        output = stderr.getvalue()
        self.assertIn("market=HK codes=US.AAPL,SH.600519,HK.00148", output)
        self.assertIn("max_results=5 returned=2", output)
        self.assertIn("identity[US.AAPL]=US.AAPL|AAPL|AAPL.US|AAPL US|Apple Inc", output)
        self.assertIn("identity[SH.600519]=SH.600519|600519|600519.SH|SH600519|贵州茅台", output)
        self.assertIn("identity[HK.00148]=HK.00148|00148|00148.HK|00148 HK|00148-HK|0148.HK", output)
        self.assertIn("result#1 matched=US.AAPL", output)
        self.assertIn("result#2 matched=SH.600519", output)
        self.assertIn("missing=HK.00148", output)
        self.assertEqual(len(grouped["US.AAPL"]), 1)
        self.assertEqual(len(grouped["SH.600519"]), 1)
        self.assertEqual(grouped["HK.00148"], [])

    def test_tavily_company_batch_diagnoses_missing_matches_by_default(self):
        class Response:
            status_code = 200
            text = ""

            def json(self):
                return {"results": []}

        rows = [
            ScreeningSignalRow(
                index=0,
                code="HK.00148",
                market="HK",
                market_label="港股",
                name="KINGBOARD HLDG",
                pe_ratio="",
                market_cap="",
                sector="Industrials",
                conditions_met="",
            )
        ]

        stderr = io.StringIO()
        with patch.dict(os.environ, {}, clear=True):
            with patch("signal_analysis.search_providers.requests.post", return_value=Response()):
                with redirect_stderr(stderr):
                    TavilySearchProvider(api_key="key", timeout_sec=5).search_companies_batch("HK", rows, 5)

        output = stderr.getvalue()
        self.assertIn("[公司事件搜索诊断]", output)
        self.assertIn("missing=HK.00148", output)

    def test_tavily_company_batch_does_not_diagnose_successful_matches_by_default(self):
        class Response:
            status_code = 200
            text = ""

            def json(self):
                return {
                    "results": [
                        {
                            "title": "Apple Inc latest product launch",
                            "url": "https://example.com/aapl",
                            "content": "AAPL company event",
                        }
                    ]
                }

        rows = [
            ScreeningSignalRow(
                index=0,
                code="US.AAPL",
                market="US",
                market_label="美股",
                name="Apple Inc",
                pe_ratio="",
                market_cap="",
                sector="Technology",
                conditions_met="",
            )
        ]

        stderr = io.StringIO()
        with patch.dict(os.environ, {}, clear=True):
            with patch("signal_analysis.search_providers.requests.post", return_value=Response()):
                with redirect_stderr(stderr):
                    TavilySearchProvider(api_key="key", timeout_sec=5).search_companies_batch("US", rows, 5)

        self.assertEqual(stderr.getvalue(), "")

    def test_tavily_company_batch_requests_at_least_one_result_per_stock(self):
        class Response:
            status_code = 200
            text = ""

            def json(self):
                return {"results": []}

        rows = [
            ScreeningSignalRow(
                index=index,
                code=f"HK.00{index + 100:03d}",
                market="HK",
                market_label="港股",
                name=f"CO{index}",
                pe_ratio="",
                market_cap="",
                sector="",
                conditions_met="",
            )
            for index in range(8)
        ]

        with patch("signal_analysis.search_providers.requests.post", return_value=Response()) as post:
            provider = TavilySearchProvider(api_key="key", timeout_sec=5)
            provider.search_companies_batch("HK", rows, 5)

        self.assertEqual(post.call_count, 1)
        self.assertEqual(post.call_args.kwargs["json"]["max_results"], 8)

    def test_tavily_company_batch_matches_hk_us_and_a_share_aliases(self):
        class Response:
            status_code = 200
            text = ""

            def json(self):
                return {
                    "results": [
                        {
                            "title": "Kingboard Holdings Limited (0148.HK) latest announcement",
                            "url": "https://example.com/0148.HK",
                            "content": "Kingboard Holdings reported new company updates.",
                        },
                        {
                            "title": "Berkshire Hathaway BRK.B shareholder meeting",
                            "url": "https://example.com/brk.b",
                            "content": "Berkshire Hathaway class B updates.",
                        },
                        {
                            "title": "贵州茅台 600519.SH 最新公告",
                            "url": "https://example.com/600519.SH",
                            "content": "贵州茅台 公司事件",
                        },
                    ]
                }

        rows = [
            ScreeningSignalRow(
                index=0,
                code="HK.00148",
                market="HK",
                market_label="港股",
                name="KINGBOARD HLDG",
                pe_ratio="",
                market_cap="",
                sector="Industrials",
                conditions_met="",
            ),
            ScreeningSignalRow(
                index=1,
                code="US.BRK-B",
                market="US",
                market_label="美股",
                name="Berkshire Hathaway",
                pe_ratio="",
                market_cap="",
                sector="Financials",
                conditions_met="",
            ),
            ScreeningSignalRow(
                index=2,
                code="SH.600519",
                market="A",
                market_label="A股",
                name="贵州茅台",
                pe_ratio="",
                market_cap="",
                sector="白酒",
                conditions_met="",
            ),
        ]

        with patch("signal_analysis.search_providers.requests.post", return_value=Response()) as post:
            provider = TavilySearchProvider(api_key="key", timeout_sec=5)
            grouped = provider.search_companies_batch("HK", rows, 5)

        query = post.call_args.kwargs["json"]["query"]
        self.assertIn("0148.HK", query)
        self.assertIn("BRK.B", query)
        self.assertIn("600519.SH", query)
        self.assertEqual(len(grouped["HK.00148"]), 1)
        self.assertEqual(len(grouped["US.BRK-B"]), 1)
        self.assertEqual(len(grouped["SH.600519"]), 1)

    def test_tavily_company_batch_splits_when_name_preserving_query_is_too_long(self):
        class Response:
            status_code = 200
            text = ""

            def json(self):
                return {"results": []}

        rows = [
            ScreeningSignalRow(
                index=index,
                code=f"US.LONGTICK{index:02d}",
                market="US",
                market_label="美股",
                name=f"Very Long Company Name {index}",
                pe_ratio="",
                market_cap="",
                sector="",
                conditions_met="",
            )
            for index in range(18)
        ]

        with patch.dict(os.environ, {"SIGNAL_COMPANY_SEARCH_QUERY_MAX_CHARS": "120"}):
            with patch("signal_analysis.search_providers.requests.post", return_value=Response()) as post:
                provider = TavilySearchProvider(api_key="key", timeout_sec=5)
                grouped = provider.search_companies_batch("US", rows, 5)

        self.assertGreater(post.call_count, 1)
        self.assertEqual(set(grouped), {row.code for row in rows})
        for call in post.call_args_list:
            query = call.kwargs["json"]["query"]
            self.assertLessEqual(len(query), 120)
            self.assertIn("Very", query)

    def test_tavily_company_batch_falls_back_to_single_safe_queries_for_extreme_batches(self):
        class Response:
            status_code = 200
            text = ""

            def json(self):
                return {"results": []}

        rows = [
            ScreeningSignalRow(
                index=index,
                code=f"US.EXTREMELYLONGTICKER000000000{index}",
                market="US",
                market_label="美股",
                name=f"Very Long Company Name With Many Words {index}",
                pe_ratio="",
                market_cap="",
                sector="",
                conditions_met="",
            )
            for index in range(3)
        ]

        with patch.dict(os.environ, {"SIGNAL_COMPANY_SEARCH_QUERY_MAX_CHARS": "120"}):
            with patch("signal_analysis.search_providers.requests.post", return_value=Response()) as post:
                provider = TavilySearchProvider(api_key="key", timeout_sec=5)
                grouped = provider.search_companies_batch("US", rows, 5)

        self.assertEqual(post.call_count, len(rows))
        self.assertEqual(set(grouped), {row.code for row in rows})
        for call in post.call_args_list:
            query = call.kwargs["json"]["query"]
            self.assertLessEqual(len(query), 120)
            self.assertIn("Very", query)

    def test_null_search_provider_batch_returns_empty_documents_by_code(self):
        rows = [
            ScreeningSignalRow(
                index=0,
                code="HK.00001",
                market="HK",
                market_label="港股",
                name="Test HK",
                pe_ratio="",
                market_cap="",
                sector="Finance",
                conditions_met="",
            )
        ]

        result = NullSearchProvider().search_companies_batch("HK", rows, 3)

        self.assertEqual(result, {"HK.00001": []})

    def test_openai_compatible_provider_parses_items(self):
        class Response:
            status_code = 200
            text = ""

            def json(self):
                return {
                    "choices": [{
                        "message": {
                            "content": (
                                '{"items":[{"code":"HK.00001","name":"Test HK",'
                                '"analysis_status":"success","reliability_score":88,'
                                '"confidence_score":79,"signal_bias":"bullish",'
                                '"summary":"ok","positive_factors":["p"],'
                                '"risk_factors":["r"],"macro_factors":["m"],'
                                '"company_events":["e"],"market_hot_news":["mh"],'
                                '"company_hot_news":["ch"],"news_impact":"利好",'
                                '"news_sources":["https://example.com/news"],'
                                '"hot_sectors":["Finance"],"hot_sector_mark":"重点",'
                                '"matched_hot_sectors":["Finance"],"hot_sector_relevance":"100",'
                                '"hot_sector_reason":"直接匹配","hot_sector_sources":["api"],'
                                '"source_urls":["https://example.com"]}]}'
                            )
                        }
                    }]
                }

        with tempfile.TemporaryDirectory() as tmp_dir:
            csv_path = self.write_csv(tmp_dir)
            with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
                row = next(csv.DictReader(f))

        from signal_analysis.models import ScreeningSignalRow
        signals = [ScreeningSignalRow.from_csv_row(row, 0, "HK")]

        with patch("signal_analysis.llm_providers.requests.post", return_value=Response()) as post:
            provider = OpenAICompatibleLLMProvider(
                api_key="key",
                model="model-x",
                api_base="https://llm.example.com/v1",
                timeout_sec=10,
            )
            results = provider.analyze_batch("HK", signals, [], [], ["Finance"], {})

        self.assertEqual(post.call_args.args[0], "https://llm.example.com/v1/chat/completions")
        self.assertEqual(results[0].code, "HK.00001")
        self.assertEqual(results[0].reliability_score, 88.0)
        self.assertEqual(results[0].market_hot_news, ["mh"])
        self.assertEqual(results[0].company_hot_news, ["ch"])
        self.assertEqual(results[0].news_impact, "利好")

    def test_deepseek_provider_parses_chat_completions_json(self):
        class Response:
            status_code = 200
            text = ""

            def json(self):
                return {
                    "choices": [{
                        "message": {
                            "content": (
                                '{"items":[{"code":"HK.00001","name":"Test HK",'
                                '"analysis_status":"success","reliability_score":86,'
                                '"confidence_score":75,"signal_bias":"bullish",'
                                '"summary":"ok","positive_factors":["p"],'
                                '"risk_factors":["r"],"macro_factors":["m"],'
                                '"company_events":["e"],"market_hot_news":["mh"],'
                                '"company_hot_news":["ch"],"news_impact":"利好",'
                                '"news_sources":["https://example.com/news"],'
                                '"hot_sectors":["Finance"],"hot_sector_mark":"重点",'
                                '"matched_hot_sectors":["Finance"],"hot_sector_relevance":"100",'
                                '"hot_sector_reason":"直接匹配","hot_sector_sources":["api"],'
                                '"source_urls":["https://example.com"]}]}'
                            )
                        }
                    }]
                }

        with tempfile.TemporaryDirectory() as tmp_dir:
            csv_path = self.write_csv(tmp_dir)
            with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
                row = next(csv.DictReader(f))

        signals = [ScreeningSignalRow.from_csv_row(row, 0, "HK")]

        with patch("signal_analysis.llm_providers.requests.post", return_value=Response()) as post:
            provider = DeepSeekLLMProvider(
                api_key="key",
                model="deepseek-v4-flash",
                api_base="https://api.deepseek.com",
                timeout_sec=10,
            )
            results = provider.analyze_batch("HK", signals, [], [], ["Finance"], {})

        self.assertEqual(post.call_args.args[0], "https://api.deepseek.com/chat/completions")
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["model"], "deepseek-v4-flash")
        self.assertEqual(payload["response_format"], {"type": "json_object"})
        self.assertIn("JSON object", payload["messages"][0]["content"])
        self.assertEqual(results[0].code, "HK.00001")
        self.assertEqual(results[0].reliability_score, 86.0)

    def test_fallback_provider_stops_after_first_success(self):
        first = RecordingLLMProvider("first", "model-a", reliability_score=81.0)
        second = RecordingLLMProvider("second", "model-b", reliability_score=92.0)
        provider = FallbackLLMProvider([first, second])

        results = provider.analyze_batch("HK", self.signal_rows(), [], [], ["Finance"], {})

        self.assertEqual(first.calls, 1)
        self.assertEqual(second.calls, 0)
        self.assertEqual(results[0].reliability_score, 81.0)
        self.assertEqual(provider.drain_warnings(), [])

    def test_fallback_provider_tries_next_after_failure(self):
        first = RecordingLLMProvider("first", "model-a", should_fail=True)
        second = RecordingLLMProvider("second", "model-b", reliability_score=92.0)
        provider = FallbackLLMProvider([first, second])

        results = provider.analyze_batch("HK", self.signal_rows(), [], [], ["Finance"], {})
        warnings = provider.drain_warnings()

        self.assertEqual(first.calls, 1)
        self.assertEqual(second.calls, 1)
        self.assertEqual(results[0].reliability_score, 92.0)
        self.assertTrue(any("first:model-a 失败" in warning for warning in warnings))
        self.assertTrue(any("fallback 使用 second:model-b 成功返回" in warning for warning in warnings))

    def test_fallback_provider_raises_after_all_providers_fail(self):
        first = RecordingLLMProvider("first", "model-a", should_fail=True)
        second = RecordingLLMProvider("second", "model-b", should_fail=True)
        provider = FallbackLLMProvider([first, second])

        with self.assertRaisesRegex(RuntimeError, "所有 LLM provider 均失败"):
            provider.analyze_batch("HK", self.signal_rows(), [], [], ["Finance"], {})

        warnings = provider.drain_warnings()
        self.assertEqual(first.calls, 1)
        self.assertEqual(second.calls, 1)
        self.assertTrue(any("first:model-a 失败" in warning for warning in warnings))
        self.assertTrue(any("second:model-b 失败" in warning for warning in warnings))

    def test_codex_responses_provider_parses_output_text_json(self):
        class Response:
            status_code = 200
            text = ""

            def json(self):
                return {
                    "output_text": (
                        '{"items":[{"code":"HK.00001","name":"Test HK",'
                        '"analysis_status":"success","reliability_score":88,'
                        '"confidence_score":79,"signal_bias":"bullish",'
                        '"summary":"ok","positive_factors":["p"],'
                        '"risk_factors":["r"],"macro_factors":["m"],'
                        '"company_events":["e"],"market_hot_news":["mh"],'
                        '"company_hot_news":["ch"],"news_impact":"利好",'
                        '"news_sources":["https://example.com/news"],'
                        '"hot_sectors":["Finance"],"hot_sector_mark":"重点",'
                        '"matched_hot_sectors":["Finance"],"hot_sector_relevance":"100",'
                        '"hot_sector_reason":"直接匹配","hot_sector_sources":["api"],'
                        '"source_urls":["https://example.com"]}]}'
                    )
                }

        with tempfile.TemporaryDirectory() as tmp_dir:
            csv_path = self.write_csv(tmp_dir)
            with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
                row = next(csv.DictReader(f))
        signals = [ScreeningSignalRow.from_csv_row(row, 0, "HK")]

        with patch("signal_analysis.llm_providers.requests.post", return_value=Response()) as post:
            provider = CodexResponsesLLMProvider(
                api_key="key",
                model="gpt-5.2-codex",
                api_base="https://api.openai.com/v1",
                timeout_sec=10,
                reasoning_effort="high",
            )
            results = provider.analyze_batch("HK", signals, [], [], ["Finance"], {})

        self.assertEqual(post.call_args.args[0], "https://api.openai.com/v1/responses")
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["reasoning"], {"effort": "high"})
        self.assertEqual(payload["text"]["format"]["type"], "json_schema")
        self.assertEqual(results[0].code, "HK.00001")
        self.assertEqual(results[0].reliability_score, 88.0)

    def test_codex_responses_provider_parses_output_content_text(self):
        class Response:
            status_code = 200
            text = ""

            def json(self):
                return {
                    "output": [{
                        "type": "message",
                        "content": [{
                            "type": "output_text",
                            "text": (
                                '{"items":[{"code":"HK.00001","name":"Test HK",'
                                '"analysis_status":"success","reliability_score":77,'
                                '"confidence_score":66,"signal_bias":"neutral",'
                                '"summary":"ok","positive_factors":[],"risk_factors":[],'
                                '"macro_factors":[],"company_events":[],"market_hot_news":[],'
                                '"company_hot_news":[],"news_impact":"中性","news_sources":[],'
                                '"hot_sectors":[],"hot_sector_mark":"观察","matched_hot_sectors":[],'
                                '"hot_sector_relevance":"30","hot_sector_reason":"观察",'
                                '"hot_sector_sources":[],"source_urls":[]}]}'
                            ),
                        }],
                    }]
                }

        with tempfile.TemporaryDirectory() as tmp_dir:
            csv_path = self.write_csv(tmp_dir)
            with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
                row = next(csv.DictReader(f))
        signals = [ScreeningSignalRow.from_csv_row(row, 0, "HK")]

        with patch("signal_analysis.llm_providers.requests.post", return_value=Response()):
            provider = CodexResponsesLLMProvider(api_key="key")
            results = provider.analyze_batch("HK", signals, [], [], [], {})

        self.assertEqual(results[0].code, "HK.00001")
        self.assertEqual(results[0].reliability_score, 77.0)

    def test_codex_responses_provider_retries_json_schema_as_json_object(self):
        class BadSchemaResponse:
            status_code = 400
            text = "unsupported json_schema in text.format"

            def json(self):
                return {}

        class OkResponse:
            status_code = 200
            text = ""

            def json(self):
                return {
                    "output_text": (
                        '{"items":[{"code":"HK.00001","name":"Test HK",'
                        '"analysis_status":"success","reliability_score":70,'
                        '"confidence_score":61,"signal_bias":"neutral",'
                        '"summary":"ok","positive_factors":[],"risk_factors":[],'
                        '"macro_factors":[],"company_events":[],"market_hot_news":[],'
                        '"company_hot_news":[],"news_impact":"中性","news_sources":[],'
                        '"hot_sectors":[],"hot_sector_mark":"观察","matched_hot_sectors":[],'
                        '"hot_sector_relevance":"30","hot_sector_reason":"观察",'
                        '"hot_sector_sources":[],"source_urls":[]}]}'
                    )
                }

        with tempfile.TemporaryDirectory() as tmp_dir:
            csv_path = self.write_csv(tmp_dir)
            with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
                row = next(csv.DictReader(f))
        signals = [ScreeningSignalRow.from_csv_row(row, 0, "HK")]

        with patch("signal_analysis.llm_providers.requests.post", side_effect=[BadSchemaResponse(), OkResponse()]) as post:
            provider = CodexResponsesLLMProvider(api_key="key")
            results = provider.analyze_batch("HK", signals, [], [], [], {})

        self.assertEqual(post.call_count, 2)
        self.assertEqual(post.call_args_list[0].kwargs["json"]["text"]["format"]["type"], "json_schema")
        self.assertEqual(post.call_args_list[1].kwargs["json"]["text"]["format"]["type"], "json_object")
        self.assertEqual(results[0].reliability_score, 70.0)

    def test_codex_responses_provider_raises_on_rate_limit_or_malformed_json(self):
        class RateLimitResponse:
            status_code = 429
            text = "rate limit"

            def json(self):
                return {}

        with tempfile.TemporaryDirectory() as tmp_dir:
            csv_path = self.write_csv(tmp_dir)
            with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
                row = next(csv.DictReader(f))
        signals = [ScreeningSignalRow.from_csv_row(row, 0, "HK")]

        with patch("signal_analysis.llm_providers.requests.post", return_value=RateLimitResponse()):
            provider = CodexResponsesLLMProvider(api_key="key")
            with self.assertRaisesRegex(RuntimeError, "HTTP 429"):
                provider.analyze_batch("HK", signals, [], [], [], {})

    def test_chain_writes_artifacts_and_keeps_going_when_search_and_db_fail(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            csv_path = self.write_csv(tmp_dir)
            context = SignalAnalysisContext(
                task_id="task-HK",
                market="HK",
                csv_path=csv_path,
                check_date=date(2026, 5, 9),
                settings=AnalysisSettings(batch_size=20, search_max_results=2),
                search_provider=FakeSearchProvider(should_fail=True),
                llm_provider=FakeLLMProvider(),
                repository=ExplodingRepository(),
            )

            result = SignalAnalysisChain().run(context)

            self.assertTrue(result.success)
            self.assertEqual(len(result.artifact_paths), 1)
            for artifact in result.artifact_paths:
                self.assertTrue(Path(artifact).exists())
            self.assertFalse(Path(csv_path.replace(".csv", "_ai.csv")).exists())
            self.assertTrue(any("搜索失败" in warning for warning in result.warnings))
            self.assertTrue(any("落库失败" in warning for warning in result.warnings))
            self.assertEqual(context.search_provider.company_batch_calls, [["HK.00001"]])

            with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
                rows = list(csv.DictReader(f))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["股票代码"], "HK.00001")
            self.assertEqual(rows[0]["信号可靠性评分"], "82.50")
            self.assertEqual(rows[0]["信号可靠性评分口径"], RELIABILITY_SCORE_CRITERIA)
            self.assertEqual(rows[0]["辅助方向判断"], "bullish")
            self.assertEqual(rows[0]["辅助方向判断口径"], SIGNAL_BIAS_CRITERIA)
            self.assertEqual(rows[0]["市场热点新闻"], "港股市场热点")
            self.assertEqual(rows[0]["公司热点新闻"], "公司热点")
            self.assertEqual(rows[0]["新闻影响判断"], "利好")
            self.assertEqual(rows[0]["新闻来源"], "https://example.com/news")
            self.assertEqual(rows[0]["热点板块标记"], "重点")
            self.assertEqual(rows[0]["热点板块标记口径"], HOT_SECTOR_MARK_CRITERIA)
            self.assertEqual(result.results_by_code["HK.00001"].reliability_score, 82.5)
            report = Path(result.artifact_paths[0]).read_text(encoding="utf-8")
            self.assertIn("资金与盘面观察", report)
            self.assertIn("整体资金净流入5.71万", report)
            self.assertIn("卖一量2.82万股", report)
            self.assertNotIn("外部数据状态", report)
            self.assertNotIn("数据覆盖", report)

    def test_chain_batches_company_search_without_per_stock_search_calls(self):
        rows = []
        for index in range(23):
            rows.append({
                "股票代码": f"HK.{index + 1:05d}",
                "市场": "港股",
                "名称": f"Company {index + 1}",
                "pe": "10",
                "市值": "1000",
                "所属板块": "Finance",
                "满足的条件": "左一战法-看涨|EMA突破",
            })

        with tempfile.TemporaryDirectory() as tmp_dir:
            csv_path = self.write_csv(tmp_dir, rows=rows)
            provider = FakeSearchProvider()
            context = SignalAnalysisContext(
                task_id="task-HK",
                market="HK",
                csv_path=csv_path,
                check_date=date(2026, 5, 9),
                settings=AnalysisSettings(batch_size=50, search_max_results=2),
                search_provider=provider,
                llm_provider=FakeLLMProvider(),
            )

            with patch.dict(os.environ, {"SIGNAL_COMPANY_SEARCH_BATCH_SIZE": "10", "SIGNAL_ENABLE_API_HOT_SECTORS": "0"}):
                result = SignalAnalysisChain().run(context)

        self.assertTrue(result.success)
        self.assertEqual(len(provider.search_queries), 2)
        self.assertEqual([len(batch) for batch in provider.company_batch_calls], [10, 10, 3])
        self.assertEqual(len(result.results_by_code), 23)
        self.assertFalse(any("公司事件批量搜索未匹配" in warning for warning in result.warnings))

    def test_chain_searches_company_events_only_for_stocks_and_reports_etf_theme(self):
        rows = [
            {
                "股票代码": "HK.00001",
                "市场": "港股",
                "名称": "Company One",
                "标的类型": "股票",
                "pe": "10",
                "市值": "1000",
                "所属板块": "Finance",
                "满足的条件": "左一战法-看涨|EMA突破",
            },
            {
                "股票代码": "HK.03033",
                "市场": "港股",
                "名称": "Hang Seng Tech ETF",
                "标的类型": "ETF",
                "pe": "",
                "市值": "",
                "所属板块": "",
                "满足的条件": "左一战法-看涨|EMA突破",
            },
        ]

        with tempfile.TemporaryDirectory() as tmp_dir:
            csv_path = self.write_csv(tmp_dir, rows=rows)
            provider = FakeSearchProvider()
            context = SignalAnalysisContext(
                task_id="task-HK",
                market="HK",
                csv_path=csv_path,
                check_date=date(2026, 5, 9),
                settings=AnalysisSettings(batch_size=20, search_max_results=2),
                search_provider=provider,
                llm_provider=FakeLLMProvider(),
            )

            with patch.dict(os.environ, {"SIGNAL_COMPANY_SEARCH_BATCH_SIZE": "10", "SIGNAL_ENABLE_API_HOT_SECTORS": "0"}):
                result = SignalAnalysisChain().run(context)

            report = Path(result.artifact_paths[0]).read_text(encoding="utf-8")

        self.assertTrue(result.success)
        self.assertEqual(provider.company_batch_calls, [["HK.00001"]])
        self.assertTrue(any("ETF 基金 跟踪指数" in query for query in provider.search_queries))
        self.assertNotIn("执行警告", report)
        self.assertNotIn("所属方向", report)
        self.assertNotIn("未补齐", report)
        self.assertIn("## 三、主力流出风险观察", report)
        self.assertIn("## 六、个股观察", report)
        self.assertIn("## 七、ETF/基金观察", report)
        self.assertIn("不适用，按基金/ETF主题观察", report)
        self.assertIn("主题资料暂缺", report)
        self.assertFalse(any("HK.03033" in warning and "公司事件批量搜索" in warning for warning in result.warnings))

    def test_chain_batch_search_failure_does_not_fallback_to_per_stock_search(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            csv_path = self.write_csv(tmp_dir)
            provider = FakeSearchProvider(should_fail=True)
            context = SignalAnalysisContext(
                task_id="task-HK",
                market="HK",
                csv_path=csv_path,
                check_date=date(2026, 5, 9),
                settings=AnalysisSettings(batch_size=20, search_max_results=2),
                search_provider=provider,
                llm_provider=FakeLLMProvider(),
            )

            with patch.dict(os.environ, {"SIGNAL_COMPANY_SEARCH_BATCH_SIZE": "10", "SIGNAL_ENABLE_API_HOT_SECTORS": "0"}):
                result = SignalAnalysisChain().run(context)

        self.assertTrue(result.success)
        self.assertEqual(len(provider.search_queries), 2)
        self.assertEqual(provider.company_batch_calls, [["HK.00001"]])
        self.assertEqual(context.company_documents["HK.00001"], [])
        self.assertTrue(any("公司事件批量搜索失败" in warning for warning in result.warnings))

    def test_chain_records_warning_when_all_fallback_llm_providers_fail(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            csv_path = self.write_csv(tmp_dir)
            llm_provider = FallbackLLMProvider([
                RecordingLLMProvider("first", "model-a", should_fail=True),
                RecordingLLMProvider("second", "model-b", should_fail=True),
            ])
            context = SignalAnalysisContext(
                task_id="task-HK",
                market="HK",
                csv_path=csv_path,
                check_date=date(2026, 5, 9),
                settings=AnalysisSettings(batch_size=20, search_max_results=2),
                search_provider=FakeSearchProvider(),
                llm_provider=llm_provider,
            )

            with patch.dict(os.environ, {"SIGNAL_ENABLE_API_HOT_SECTORS": "0"}):
                result = SignalAnalysisChain().run(context)

        self.assertFalse(result.success)
        self.assertEqual(result.artifact_paths, [])
        self.assertTrue(any("LLM provider first:model-a 失败" in warning for warning in result.warnings))
        self.assertTrue(any("LLM provider second:model-b 失败" in warning for warning in result.warnings))
        self.assertTrue(any("模型批量分析失败" in warning for warning in result.warnings))
        self.assertTrue(any("模型分析没有成功返回任何股票结果" in warning for warning in result.warnings))

    def test_chain_stops_after_llm_quota_error_without_repeating_batches(self):
        class QuotaLLMProvider:
            name = "codex_responses"
            is_available = True

            def __init__(self):
                self.calls = 0

            @property
            def model_name(self):
                return "gpt-5.2-codex"

            def analyze_batch(self, market, signals, market_documents, sector_documents, hot_sectors, company_documents):
                self.calls += 1
                raise RuntimeError(
                    'Codex Responses analysis failed: HTTP 429 {"error":{"type":"insufficient_quota"}}'
                )

        rows = [
            {
                "股票代码": f"SH.6000{index}",
                "市场": "A股",
                "名称": f"Test A {index}",
                "标的类型": "股票",
                "满足的条件": "测试规则",
            }
            for index in range(3)
        ]
        with tempfile.TemporaryDirectory() as tmp_dir:
            csv_path = self.write_csv(tmp_dir, rows=rows)
            llm_provider = QuotaLLMProvider()
            context = SignalAnalysisContext(
                task_id="task-A",
                market="A",
                csv_path=csv_path,
                check_date=date(2026, 5, 20),
                settings=AnalysisSettings(batch_size=1, search_max_results=2),
                search_provider=NullSearchProvider(),
                llm_provider=llm_provider,
            )

            with patch.dict(os.environ, {"SIGNAL_ENABLE_API_HOT_SECTORS": "0"}):
                result = SignalAnalysisChain().run(context)

        self.assertFalse(result.success)
        self.assertEqual(llm_provider.calls, 1)
        self.assertEqual(result.skipped_reason, "LLM provider 额度不足，跳过 AI 辅助分析")
        self.assertTrue(any("insufficient_quota" in warning for warning in result.warnings))

    def test_markdown_report_explains_information_gap_reasons(self):
        class GapLLMProvider(FakeLLMProvider):
            def analyze_batch(self, market, signals, market_documents, sector_documents, hot_sectors, company_documents):
                return [
                    SignalAnalysisResult(
                        code=row.code,
                        name=row.name,
                        reliability_score=20.0,
                        confidence_score=18.0,
                        signal_bias="unknown",
                        summary="搜索信息不足，无法形成明确判断",
                        positive_factors=[],
                        risk_factors=["规则链未形成强确认"],
                        macro_factors=["市场波动"],
                        company_events=[],
                        market_hot_news=["市场宏观新闻"],
                        company_hot_news=[],
                        news_impact="信息不足",
                        news_sources=[],
                        hot_sectors=hot_sectors or ["创新药"],
                        hot_sector_mark="无明确关联",
                        matched_hot_sectors=[],
                        hot_sector_relevance="0",
                        hot_sector_reason="所属板块与热点板块无直接匹配",
                        model=self.model_name,
                    )
                    for row in signals
                ]

        with tempfile.TemporaryDirectory() as tmp_dir:
            csv_path = self.write_csv(tmp_dir)
            context = SignalAnalysisContext(
                task_id="task-HK",
                market="HK",
                csv_path=csv_path,
                check_date=date(2026, 5, 9),
                settings=AnalysisSettings(batch_size=20, search_max_results=2),
                search_provider=FakeSearchProvider(),
                llm_provider=GapLLMProvider(),
                manual_hot_sectors=ManualHotSectorConfig(hot_sectors=["创新药"], sources=["manual_config"]),
            )

            with patch.dict(os.environ, {"SIGNAL_ENABLE_API_HOT_SECTORS": "0"}):
                result = SignalAnalysisChain().run(context)

            report_path = result.artifact_paths[0]
            report = Path(report_path).read_text(encoding="utf-8")

        self.assertIn("## 一、核心结论", report)
        self.assertIn("## 三、主力流出风险观察", report)
        self.assertIn("## 六、个股观察", report)
        self.assertIn("主力流出风险", report)
        self.assertIn("成交量分布", report)
        self.assertIn("最大成交量区间", report)
        self.assertIn("信息缺口", report)
        self.assertIn("缺少明确公司事件", report)
        self.assertIn("目前信息不足，无法判断新闻方向", report)
        self.assertIn("热点板块未直接匹配", report)
        self.assertIn("判断信心偏低", report)
        self.assertNotIn("规则链", report)

    def test_write_analysis_columns_refreshes_existing_ai_columns_in_place(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            csv_path = self.write_csv(tmp_dir)
            write_analysis_columns_to_csv(
                csv_path,
                {
                    "HK.00001": SignalAnalysisResult(
                        code="HK.00001",
                        name="Test HK",
                        analysis_status="success",
                        reliability_score=70.0,
                        confidence_score=60.0,
                        signal_bias="neutral",
                        market_hot_news=["旧市场新闻"],
                        company_hot_news=["旧公司新闻"],
                        news_impact="中性",
                        news_sources=["https://example.com/old"],
                        hot_sectors=["旧热点"],
                        hot_sector_mark="观察",
                        matched_hot_sectors=[],
                        hot_sector_relevance="30",
                        hot_sector_reason="旧理由",
                        hot_sector_sources=["old_source"],
                    )
                },
            )
            write_analysis_columns_to_csv(
                csv_path,
                {
                    "HK.00001": SignalAnalysisResult(
                        code="HK.00001",
                        name="Test HK",
                        analysis_status="success",
                        reliability_score=91.0,
                        confidence_score=88.0,
                        signal_bias="bullish",
                        market_hot_news=["新市场新闻"],
                        company_hot_news=["新公司新闻"],
                        news_impact="利好",
                        news_sources=["https://example.com/new"],
                        hot_sectors=["新热点"],
                        hot_sector_mark="重点",
                        matched_hot_sectors=["新热点"],
                        hot_sector_relevance="100",
                        hot_sector_reason="新理由",
                        hot_sector_sources=["new_source"],
                    )
                },
            )

            with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
                rows = list(csv.DictReader(f))

            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["股票代码"], "HK.00001")
            self.assertEqual(rows[0]["信号可靠性评分"], "91.00")
            self.assertEqual(rows[0]["信号可靠性评分口径"], RELIABILITY_SCORE_CRITERIA)
            self.assertEqual(rows[0]["模型置信度"], "88.00")
            self.assertEqual(rows[0]["模型置信度口径"], CONFIDENCE_SCORE_CRITERIA)
            self.assertEqual(rows[0]["辅助方向判断"], "bullish")
            self.assertEqual(rows[0]["辅助方向判断口径"], SIGNAL_BIAS_CRITERIA)
            self.assertEqual(rows[0]["市场热点新闻"], "新市场新闻")
            self.assertEqual(rows[0]["公司热点新闻"], "新公司新闻")
            self.assertEqual(rows[0]["新闻影响判断"], "利好")
            self.assertEqual(rows[0]["新闻来源"], "https://example.com/new")
            self.assertEqual(rows[0]["AI识别热点板块"], "新热点")
            self.assertEqual(rows[0]["热点板块标记"], "重点")
            self.assertEqual(rows[0]["匹配热点板块"], "新热点")
            self.assertEqual(rows[0]["热点板块关联度"], "100")
            self.assertEqual(rows[0]["热点板块匹配理由"], "新理由")
            self.assertEqual(rows[0]["热点板块来源"], "new_source")

    def test_write_analysis_columns_includes_evidence_gap_and_factor_citation_fields(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            csv_path = self.write_csv(tmp_dir)
            write_analysis_columns_to_csv(
                csv_path,
                {
                    "HK.00001": SignalAnalysisResult(
                        code="HK.00001",
                        name="Test HK",
                        analysis_status="success",
                        reliability_score=80.0,
                        confidence_score=70.0,
                        signal_bias="bullish",
                        positive_factors=["AI 机器人业务推进"],
                        data_gaps=["部分宏观因素缺少可追溯来源"],
                        evidence_links=[{
                            "label": "公司IR",
                            "url": "https://example.com/ir",
                            "title": "AI Robotics",
                            "domain": "example.com",
                            "source_type": "官方",
                        }],
                        factor_citations={
                            "AI 机器人业务推进": [{
                                "label": "公司IR",
                                "url": "https://example.com/ir",
                                "title": "AI Robotics",
                                "domain": "example.com",
                                "source_type": "官方",
                            }]
                        },
                    )
                },
            )

            with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
                rows = list(csv.DictReader(f))

            self.assertEqual(rows[0]["数据缺失原因"], "部分宏观因素缺少可追溯来源")
            self.assertIn("https://example.com/ir", rows[0]["引用来源"])
            self.assertIn("AI 机器人业务推进", rows[0]["因素引用"])

    def test_chain_populates_evidence_fields_from_search_documents(self):
        class EvidenceSearchProvider(FakeSearchProvider):
            def search(self, query, max_results):
                self.search_queries.append(query)
                return [
                    SearchDocument(
                        title="Company IR AI Robotics Strategy",
                        url="https://ir.example.com/annual-report",
                        content="AI robotics strategy and policy stability support this company",
                        query=query,
                    )
                ]

            def search_companies_batch(self, market, rows, max_results):
                self.company_batch_calls.append([row.code for row in rows])
                return {
                    row.code: [
                        SearchDocument(
                            title="Company IR AI Robotics Strategy",
                            url="https://ir.example.com/annual-report",
                            content="AI robotics strategy and policy stability support this company",
                        )
                    ]
                    for row in rows
                }

        class EvidenceLLMProvider(FakeLLMProvider):
            def analyze_batch(self, market, signals, market_documents, sector_documents, hot_sectors, company_documents):
                return [
                    SignalAnalysisResult(
                        code=row.code,
                        name=row.name,
                        reliability_score=82.5,
                        confidence_score=76.0,
                        signal_bias="bullish",
                        summary="信号与事件共振",
                        positive_factors=["AI robotics strategy"],
                        risk_factors=[],
                        macro_factors=["policy stability"],
                        news_impact="利好",
                        source_urls=[],
                        model=self.model_name,
                    )
                    for row in signals
                ]

        with tempfile.TemporaryDirectory() as tmp_dir:
            csv_path = self.write_csv(tmp_dir)
            context = SignalAnalysisContext(
                task_id="task-HK",
                market="HK",
                csv_path=csv_path,
                check_date=date(2026, 5, 9),
                settings=AnalysisSettings(batch_size=20, search_max_results=2),
                search_provider=EvidenceSearchProvider(),
                llm_provider=EvidenceLLMProvider(),
            )

            with patch.dict(os.environ, {"SIGNAL_ENABLE_API_HOT_SECTORS": "0"}):
                result = SignalAnalysisChain().run(context)

            analysis = result.results_by_code["HK.00001"]
            self.assertTrue(analysis.evidence_links)
            self.assertIn("AI robotics strategy", analysis.factor_citations)
            self.assertIn("policy stability", analysis.factor_citations)

    def test_chain_filters_source_labels_from_factors_and_dedupes_same_source_tags(self):
        class HkexSearchProvider(FakeSearchProvider):
            def search(self, query, max_results):
                self.search_queries.append(query)
                return [
                    SearchDocument(
                        title="HKEX announcement market",
                        url="https://www1.hkexnews.hk/listedco/listconews/sehk/2026/market.pdf",
                        content="AI robotics strategy and policy stability",
                        query=query,
                    )
                ]

            def search_companies_batch(self, market, rows, max_results):
                self.company_batch_calls.append([row.code for row in rows])
                return {
                    row.code: [
                        SearchDocument(
                            title="HKEX announcement one",
                            url="https://www1.hkexnews.hk/listedco/listconews/sehk/2026/one.pdf",
                            content="AI robotics strategy",
                        ),
                        SearchDocument(
                            title="HKEX announcement two",
                            url="https://www1.hkexnews.hk/listedco/listconews/sehk/2026/two.pdf",
                            content="AI robotics strategy",
                        ),
                    ]
                    for row in rows
                }

        class SourceLabelLLMProvider(FakeLLMProvider):
            def analyze_batch(self, market, signals, market_documents, sector_documents, hot_sectors, company_documents):
                return [
                    SignalAnalysisResult(
                        code=row.code,
                        name=row.name,
                        reliability_score=70,
                        confidence_score=60,
                        signal_bias="bullish",
                        summary="信号偏多",
                        positive_factors=["AI robotics strategy", "HKEX公告", "HKEX公告"],
                        risk_factors=[],
                        macro_factors=[],
                        news_impact="中性",
                        model=self.model_name,
                    )
                    for row in signals
                ]

        with tempfile.TemporaryDirectory() as tmp_dir:
            csv_path = self.write_csv(tmp_dir)
            context = SignalAnalysisContext(
                task_id="task-HK",
                market="HK",
                csv_path=csv_path,
                check_date=date(2026, 5, 9),
                settings=AnalysisSettings(batch_size=20, search_max_results=2),
                search_provider=HkexSearchProvider(),
                llm_provider=SourceLabelLLMProvider(),
            )

            with patch.dict(os.environ, {"SIGNAL_ENABLE_API_HOT_SECTORS": "0"}):
                result = SignalAnalysisChain().run(context)

            analysis = result.results_by_code["HK.00001"]
            self.assertEqual(analysis.positive_factors, ["AI robotics strategy"])
            self.assertEqual(len(analysis.factor_citations["AI robotics strategy"]), 1)
            self.assertEqual(analysis.factor_citations["AI robotics strategy"][0]["label"], "HKEX公告")

    def test_chain_skips_artifacts_when_llm_is_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            csv_path = self.write_csv(tmp_dir)
            context = SignalAnalysisContext(
                task_id="task-HK",
                market="HK",
                csv_path=csv_path,
                check_date=date(2026, 5, 9),
                settings=AnalysisSettings(),
                search_provider=NullSearchProvider(),
                llm_provider=NullLLMProvider(),
            )

            result = SignalAnalysisChain().run(context)

            self.assertFalse(result.success)
            self.assertEqual(result.artifact_paths, [])
            self.assertIn("未配置 LLM provider", result.skipped_reason)

    def test_manual_hot_news_overrides_model_and_search_news_columns(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            csv_path = self.write_csv(tmp_dir)
            context = SignalAnalysisContext(
                task_id="task-HK",
                market="HK",
                csv_path=csv_path,
                check_date=date(2026, 5, 9),
                settings=AnalysisSettings(batch_size=20, search_max_results=2),
                search_provider=FakeSearchProvider(),
                llm_provider=FakeLLMProvider(),
                manual_hot_news=ManualHotNewsConfig(
                    market_hot_news=["手动市场热点"],
                    company_hot_news_by_code={"HK.00001": ["手动公司热点"]},
                    market_news_sources=["https://manual.example.com/market"],
                    company_news_sources_by_code={"HK.00001": ["https://manual.example.com/company"]},
                ),
            )

            result = SignalAnalysisChain().run(context)

            self.assertTrue(result.success)
            with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
                rows = list(csv.DictReader(f))

            self.assertEqual(rows[0]["市场热点新闻"], "手动市场热点")
            self.assertEqual(rows[0]["公司热点新闻"], "手动公司热点")
            self.assertEqual(
                rows[0]["新闻来源"],
                "https://manual.example.com/market；https://manual.example.com/company",
            )
            self.assertTrue(any("手动配置" in warning for warning in result.warnings))

    def test_manual_hot_sectors_override_search_and_are_written_to_csv(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            csv_path = self.write_csv(tmp_dir)
            context = SignalAnalysisContext(
                task_id="task-HK",
                market="HK",
                csv_path=csv_path,
                check_date=date(2026, 5, 9),
                settings=AnalysisSettings(batch_size=20, search_max_results=2),
                search_provider=FakeSearchProvider(),
                llm_provider=FakeLLMProvider(),
                manual_hot_sectors=ManualHotSectorConfig(
                    hot_sectors=["Finance"],
                    sources=["https://manual.example.com/sector"],
                ),
            )

            result = SignalAnalysisChain().run(context)

            self.assertTrue(result.success)
            with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
                rows = list(csv.DictReader(f))

            self.assertEqual(rows[0]["AI识别热点板块"], "Finance")
            self.assertEqual(rows[0]["热点板块标记"], "重点")
            self.assertEqual(rows[0]["匹配热点板块"], "Finance")
            self.assertEqual(rows[0]["热点板块来源"], "manual_config")
            self.assertTrue(any("热点板块" in warning for warning in result.warnings))

    def test_signal_analysis_sql_exists(self):
        sql_path = Path(__file__).resolve().parents[1] / "sql" / "002_signal_analysis.sql"
        content = sql_path.read_text(encoding="utf-8")

        self.assertIn("CREATE TABLE IF NOT EXISTS screening_signal_analysis", content)
        self.assertIn("uk_signal_analysis_task_market_code", content)
        self.assertIn("hot_sector_mark", content)


if __name__ == "__main__":
    unittest.main()
