#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import csv
import os
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from signal_analysis.chain import SignalAnalysisChain, SignalAnalysisContext, write_analysis_columns_to_csv
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
from signal_analysis.search_providers import NullSearchProvider, TavilySearchProvider


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


class SignalAnalysisTest(unittest.TestCase):
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
            "pe": "10",
            "市值": "1000",
            "所属板块": "Finance",
            "满足的条件": "左一战法-看涨|EMA突破",
        }]
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=["股票代码", "市场", "名称", "pe", "市值", "所属板块", "满足的条件"],
            )
            writer.writeheader()
            writer.writerows(rows)
        return str(path)

    def test_factories_return_null_providers_without_credentials(self):
        with patch.dict(os.environ, {}, clear=True):
            settings = AnalysisSettings()
            self.assertIsInstance(SearchProviderFactory.from_env(settings), NullSearchProvider)
            self.assertIsInstance(LLMProviderFactory.from_env(settings), NullLLMProvider)

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
