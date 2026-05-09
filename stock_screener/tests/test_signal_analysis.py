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
from signal_analysis.factories import LLMProviderFactory, SearchProviderFactory
from signal_analysis.llm_providers import NullLLMProvider, OpenAICompatibleLLMProvider
from signal_analysis.models import (
    AnalysisSettings,
    CONFIDENCE_SCORE_CRITERIA,
    RELIABILITY_SCORE_CRITERIA,
    SIGNAL_BIAS_CRITERIA,
    SearchDocument,
    SignalAnalysisResult,
)
from signal_analysis.search_providers import NullSearchProvider, TavilySearchProvider


class FakeSearchProvider:
    name = "fake"
    is_available = True

    def __init__(self, should_fail=False):
        self.should_fail = should_fail

    def search(self, query, max_results):
        if self.should_fail:
            raise RuntimeError("search boom")
        return [SearchDocument(title="News", url="https://example.com/news", content="policy context")]


class FakeLLMProvider:
    name = "fake"
    is_available = True

    @property
    def model_name(self):
        return "fake-model"

    def analyze_batch(self, market, signals, market_documents, company_documents):
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
                model=self.model_name,
            )
            for row in signals
        ]


class ExplodingRepository:
    def save_results(self, rows):
        raise RuntimeError("db boom")


class SignalAnalysisTest(unittest.TestCase):
    def write_csv(self, directory):
        path = Path(directory) / "screening_result_2026-05-09_HK.csv"
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=["股票代码", "市场", "名称", "pe", "市值", "所属板块", "满足的条件"],
            )
            writer.writeheader()
            writer.writerow({
                "股票代码": "HK.00001",
                "市场": "港股",
                "名称": "Test HK",
                "pe": "10",
                "市值": "1000",
                "所属板块": "Finance",
                "满足的条件": "左一战法-看涨|EMA突破",
            })
        return str(path)

    def test_factories_return_null_providers_without_credentials(self):
        with patch.dict(os.environ, {}, clear=True):
            settings = AnalysisSettings()
            self.assertIsInstance(SearchProviderFactory.from_env(settings), NullSearchProvider)
            self.assertIsInstance(LLMProviderFactory.from_env(settings), NullLLMProvider)

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
            results = provider.analyze_batch("HK", signals, [], {})

        self.assertEqual(post.call_args.args[0], "https://llm.example.com/v1/chat/completions")
        self.assertEqual(results[0].code, "HK.00001")
        self.assertEqual(results[0].reliability_score, 88.0)
        self.assertEqual(results[0].market_hot_news, ["mh"])
        self.assertEqual(results[0].company_hot_news, ["ch"])
        self.assertEqual(results[0].news_impact, "利好")

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
            self.assertEqual(result.results_by_code["HK.00001"].reliability_score, 82.5)

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

    def test_signal_analysis_sql_exists(self):
        sql_path = Path(__file__).resolve().parents[1] / "sql" / "002_signal_analysis.sql"
        content = sql_path.read_text(encoding="utf-8")

        self.assertIn("CREATE TABLE IF NOT EXISTS screening_signal_analysis", content)
        self.assertIn("uk_signal_analysis_task_market_code", content)


if __name__ == "__main__":
    unittest.main()
