#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import unittest
from unittest.mock import patch

from network_preflight import check_host_resolution, extract_host, format_resolution_failures
from signal_analysis.models import AnalysisSettings
from signal_analysis.service import run_signal_analysis_for_market


class NetworkPreflightTest(unittest.TestCase):
    def test_extract_host_supports_urls_and_raw_hostnames(self):
        self.assertEqual(extract_host("https://api.openai.com/v1/chat/completions"), "api.openai.com")
        self.assertEqual(extract_host("api.tavily.com"), "api.tavily.com")
        self.assertEqual(extract_host(""), "")

    def test_format_resolution_failures_formats_dns_errors(self):
        with patch("network_preflight.socket.getaddrinfo", side_effect=OSError("dns down")):
            messages = format_resolution_failures("preflight", ["https://api.tavily.com"])

        self.assertEqual(len(messages), 1)
        self.assertIn("api.tavily.com", messages[0])
        self.assertIn("dns down", messages[0])

    def test_signal_analysis_returns_clear_skip_when_dns_preflight_fails(self):
        fake_llm = type("FakeLLM", (), {"is_available": True, "api_base": "https://api.deepseek.com"})()
        fake_search = type("FakeSearch", (), {"is_available": True, "endpoint": "https://api.tavily.com/search"})()

        with patch("signal_analysis.service.LLMProviderFactory.from_env", return_value=fake_llm), \
                patch("signal_analysis.service.SearchProviderFactory.from_env", return_value=fake_search), \
                patch(
                    "signal_analysis.service.format_resolution_failures",
                    return_value=["[AI分析] 网络预检失败: 域名解析失败 `api.tavily.com` | OSError: dns down"],
                ):
            result = run_signal_analysis_for_market(
                mysql_config=object(),
                task_id="task-A",
                market="A",
                csv_path="/tmp/not_used.csv",
            )

        self.assertFalse(result.success)
        self.assertEqual(result.skipped_reason, "网络预检失败，跳过 AI 辅助分析")
        self.assertTrue(any("api.tavily.com" in warning for warning in result.warnings))


if __name__ == "__main__":
    unittest.main()
