#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import csv
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from network_preflight import check_host_resolution, extract_host, format_resolution_failures
from signal_analysis.llm_providers import FallbackLLMProvider
from signal_analysis.models import AnalysisSettings
from signal_analysis.service import _prepare_analysis_providers, run_signal_analysis_for_market
from signal_analysis.search_providers import FallbackSearchProvider, NullSearchProvider


class NoopRepository:
    def save_results(self, rows):
        pass


class NetworkPreflightTest(unittest.TestCase):
    def test_extract_host_supports_urls_and_raw_hostnames(self):
        self.assertEqual(extract_host("https://api.openai.com/v1/chat/completions"), "api.openai.com")
        self.assertEqual(extract_host("api.tavily.com"), "api.tavily.com")
        self.assertEqual(extract_host(""), "")

    def test_format_resolution_failures_formats_dns_errors(self):
        with patch("network_preflight.socket.getaddrinfo", side_effect=OSError("dns down")):
            messages = format_resolution_failures("preflight", ["https://api.tavily.com"], attempts=1)

        self.assertEqual(len(messages), 1)
        self.assertIn("api.tavily.com", messages[0])
        self.assertIn("dns down", messages[0])

    def test_check_host_resolution_retries_transient_dns_errors(self):
        with patch(
            "network_preflight.socket.getaddrinfo",
            side_effect=[OSError("dns down"), [object()]],
        ) as getaddrinfo, patch("network_preflight.time.sleep") as sleep:
            failures = check_host_resolution(["https://api.tavily.com"], attempts=2, retry_delay_sec=0.01)

        self.assertEqual(failures, [])
        self.assertEqual(getaddrinfo.call_count, 2)
        sleep.assert_called_once()

    def test_search_dns_failure_downgrades_to_no_search_without_disabling_llm(self):
        fake_llm = type("FakeLLM", (), {"is_available": True, "api_base": "https://api.deepseek.com", "name": "deepseek"})()
        fake_search = type("FakeSearch", (), {"is_available": True, "endpoint": "https://api.tavily.com/search"})()

        def fake_resolution(hosts, **kwargs):
            text = ",".join(hosts)
            if "api.tavily.com" in text:
                return [("api.tavily.com", "gaierror: dns down")]
            return []

        with patch("signal_analysis.service.check_host_resolution", side_effect=fake_resolution):
            search_provider, llm_provider, warnings = _prepare_analysis_providers(fake_search, fake_llm)

        self.assertIsInstance(search_provider, NullSearchProvider)
        self.assertIs(llm_provider, fake_llm)
        self.assertTrue(any("联网检索" in warning for warning in warnings))

    def test_search_preflight_filters_only_failed_fallback_provider(self):
        fake_llm = type("FakeLLM", (), {"is_available": True, "api_base": "https://api.deepseek.com", "name": "deepseek"})()
        tavily = type(
            "FakeTavilySearch",
            (),
            {"is_available": True, "endpoint": "https://api.tavily.com/search", "name": "tavily"},
        )()
        zhipu = type(
            "FakeZhipuSearch",
            (),
            {"is_available": True, "endpoint": "https://open.bigmodel.cn/api/paas/v4/web_search", "name": "zhipuai"},
        )()
        fallback = FallbackSearchProvider([tavily, zhipu])

        def fake_resolution(hosts, **kwargs):
            text = ",".join(hosts)
            if "api.tavily.com" in text:
                return [("api.tavily.com", "gaierror: dns down")]
            return []

        with patch("signal_analysis.service.check_host_resolution", side_effect=fake_resolution):
            search_provider, llm_provider, warnings = _prepare_analysis_providers(fallback, fake_llm)

        self.assertIs(search_provider, zhipu)
        self.assertIs(llm_provider, fake_llm)
        self.assertTrue(any("tavily" in warning and "联网检索" in warning for warning in warnings))

    def test_search_preflight_downgrades_when_all_fallback_providers_fail(self):
        fake_llm = type("FakeLLM", (), {"is_available": True, "api_base": "https://api.deepseek.com", "name": "deepseek"})()
        tavily = type(
            "FakeTavilySearch",
            (),
            {"is_available": True, "endpoint": "https://api.tavily.com/search", "name": "tavily"},
        )()
        zhipu = type(
            "FakeZhipuSearch",
            (),
            {"is_available": True, "endpoint": "https://open.bigmodel.cn/api/paas/v4/web_search", "name": "zhipuai"},
        )()
        fallback = FallbackSearchProvider([tavily, zhipu])

        def fake_resolution(hosts, **kwargs):
            text = ",".join(hosts)
            if "api.tavily.com" in text:
                return [("api.tavily.com", "gaierror: dns down")]
            if "open.bigmodel.cn" in text:
                return [("open.bigmodel.cn", "gaierror: dns down")]
            return []

        with patch("signal_analysis.service.check_host_resolution", side_effect=fake_resolution):
            search_provider, llm_provider, warnings = _prepare_analysis_providers(fallback, fake_llm)

        self.assertIsInstance(search_provider, NullSearchProvider)
        self.assertIs(llm_provider, fake_llm)
        self.assertTrue(any("tavily" in warning for warning in warnings))
        self.assertTrue(any("zhipuai" in warning for warning in warnings))

    def test_llm_dns_failure_filters_only_failed_fallback_provider(self):
        bad = type("FakeDeepSeek", (), {"is_available": True, "api_base": "https://api.deepseek.com", "name": "deepseek"})()
        good = type("FakeOpenAI", (), {"is_available": True, "api_base": "https://api.openai.com", "name": "openai"})()
        fallback = FallbackLLMProvider([bad, good])

        def fake_resolution(hosts, **kwargs):
            text = ",".join(hosts)
            if "api.deepseek.com" in text:
                return [("api.deepseek.com", "gaierror: dns down")]
            return []

        with patch("signal_analysis.service.check_host_resolution", side_effect=fake_resolution):
            _, llm_provider, warnings = _prepare_analysis_providers(NullSearchProvider(), fallback)

        self.assertIs(llm_provider, good)
        self.assertTrue(any("deepseek" in warning for warning in warnings))

    def test_signal_analysis_returns_clear_skip_when_all_llm_dns_preflight_fails(self):
        fake_llm = type("FakeLLM", (), {"is_available": True, "api_base": "https://api.deepseek.com"})()
        fake_search = type("FakeSearch", (), {"is_available": True, "endpoint": "https://api.tavily.com/search"})()

        with tempfile.TemporaryDirectory() as tmp_dir:
            csv_path = Path(tmp_dir) / "screening_result.csv"
            with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
                writer = csv.DictWriter(
                    f,
                    fieldnames=["股票代码", "市场", "名称", "标的类型", "满足的条件"],
                )
                writer.writeheader()
                writer.writerow({
                    "股票代码": "US.TEST",
                    "市场": "美股",
                    "名称": "Test US",
                    "标的类型": "股票",
                    "满足的条件": "测试规则",
                })

            with patch.dict("os.environ", {"SIGNAL_ENABLE_API_HOT_SECTORS": "0"}), \
                    patch("signal_analysis.service.LLMProviderFactory.from_env", return_value=fake_llm), \
                    patch("signal_analysis.service.SearchProviderFactory.from_env", return_value=fake_search), \
                    patch(
                        "signal_analysis.service.check_host_resolution",
                        return_value=[("api.deepseek.com", "gaierror: dns down")],
                    ):
                result = run_signal_analysis_for_market(
                    mysql_config=object(),
                    task_id="task-A",
                    market="A",
                    csv_path=str(csv_path),
                    repository_override=NoopRepository(),
                )

        self.assertFalse(result.success)
        self.assertEqual(result.skipped_reason, "LLM provider 网络预检失败，跳过 AI 辅助分析")
        self.assertTrue(any("api.deepseek.com" in warning for warning in result.warnings))
        self.assertFalse(any("未配置搜索 provider" in warning for warning in result.warnings))
        self.assertFalse(any("未配置可用 LLM provider" in warning for warning in result.warnings))


if __name__ == "__main__":
    unittest.main()
