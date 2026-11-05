import unittest
from datetime import datetime, timezone, date
from unittest.mock import Mock, patch
import json
import os
from pathlib import Path
import tempfile

import pandas as pd
import requests

from worker.names import choose_names, simplified
from worker.providers import parse_a, parse_hk, parse_us
from worker.stock_catalog import today, due, load_configuration, poll_delay, run_child, Settings
from worker.transport import Transport, BudgetExceeded
from worker.repository import lock_name


class NamesTests(unittest.TestCase):
    def test_hk_prefers_simplified_and_retains_english_alias(self):
        result = choose_names("HK", "HK.00700", "騰訊控股", "TENCENT")
        self.assertEqual(result, {"name": "腾讯控股", "name_zh": "腾讯控股", "name_en": "TENCENT"})
        self.assertEqual(simplified("騰訊"), "腾讯")

    def test_fallbacks_and_placeholders(self):
        for empty in (None, "", "--", "nan", float("nan"), "N/A"):
            self.assertEqual(choose_names("HK", "HK.00700", empty, "TENCENT")["name"], "TENCENT")
            self.assertEqual(choose_names("HK", "HK.00700", empty, empty)["name"], "HK.00700")

    def test_failed_chinese_source_preserves_cached_name(self):
        result = choose_names("HK", "HK.00700", None, "TENCENT", {"name": "騰訊控股"})
        self.assertEqual(result["name"], "腾讯控股")

    def test_us_uses_only_english_or_code(self):
        self.assertEqual(choose_names("US", "US.AAPL", "苹果", "Apple Inc.")["name"], "Apple Inc.")
        self.assertEqual(choose_names("US", "US.AAPL", "苹果", None)["name"], "US.AAPL")


class ProviderParsingTests(unittest.TestCase):
    def test_a_exchange_provenance_and_etf_prefix(self):
        self.assertEqual(parse_a(pd.DataFrame({"证券代码": ["920001"], "证券简称": ["样本"]}), "BJ")[0]["code"], "BJ.920001")
        records = parse_a(pd.DataFrame({"代码": ["sh510300", "sz159919"], "名称": ["沪深300", "300ETF"]}), etf=True)
        self.assertEqual([r["code"] for r in records], ["SH.510300", "SZ.159919"])
        self.assertTrue(all(r["instrument_type"] == "ETF" for r in records))

    def test_bad_schema_and_unknown_code_are_rejected(self):
        with self.assertRaises(ValueError):
            parse_a(pd.DataFrame({"wrong": ["bad"]}))
        with self.assertRaises(ValueError):
            parse_a(pd.DataFrame({"代码": ["unexpected"], "名称": ["bad"]}), "SH")

    def test_hk_bilingual_join_filters_derivatives(self):
        en = pd.DataFrame({"Stock Code": ["00700", "02800", "07500", "10001"],
            "Name of Securities": ["TENCENT", "TRACKER FUND", "INVERSE FUND", "WARRANT"],
            "Category": ["Equity", "Exchange Traded Products", "Exchange Traded Products", "Derivative Warrants"],
            "Sub-Category": ["Equity Securities (Main Board)", "Exchange Traded Funds", "Leveraged and Inverse", "Warrants"]})
        zh = pd.DataFrame({"股份代號": ["00700", "02800"], "股份名稱": ["騰訊控股", "盈富基金"]})
        records = parse_hk(en, zh)
        self.assertEqual([r["code"] for r in records], ["HK.00700", "HK.02800", "HK.07500"])
        self.assertEqual(records[1]["instrument_type"], "ETF")
        self.assertEqual(records[2]["instrument_type"], "ETF")
        self.assertEqual(records[0]["name_zh"], "騰訊控股")
        self.assertIsNone(parse_hk(en)[0]["name_zh"])

    def test_nasdaq_footer_special_symbols_and_exclusions(self):
        text = "Symbol|Security Name|ETF|Test Issue|Listing Exchange\n"
        text += "AAPL|Apple Inc. - Common Stock|N|N|Q\nBRK.B|Berkshire Hathaway Class B|N|N|N\n"
        text += "SPY|SPDR S&P 500 ETF|Y|N|P\nNAN|Nuveen New York Quality Municipal Income Fund|N|N|N\nBAD|Example - Warrants|N|N|Q\n"
        text += "TEST|Test Company|N|Y|Q\nFile Creation Time: 110420261800||||\n"
        records = parse_us(text)
        self.assertEqual([r["code"] for r in records], ["US.AAPL", "US.BRK.B", "US.SPY", "US.NAN"])
        with self.assertRaises(ValueError):
            parse_us(text.split("File Creation Time:")[0])


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.elapsed = 0
        self.count = 0

    def sleep(self, delay):
        self.elapsed += delay

    def count_attempt(self):
        self.count += 1

    def response(self, status=200, retry_after=None):
        response = requests.Response()
        response.status_code = status
        response.url = "https://provider.test/list"
        if retry_after:
            response.headers["Retry-After"] = retry_after
        return response

    def transport(self, responses, deadline=900):
        session = Mock()
        session.request.side_effect = responses
        return Transport(self.count_attempt, deadline=deadline, clock=lambda: self.elapsed, sleep=self.sleep, session=session)

    def test_same_host_throttle_counts_actual_requests(self):
        client = self.transport([self.response(), self.response()])
        client.request("GET", "https://provider.test/one")
        client.request("GET", "https://provider.test/two")
        self.assertEqual(self.count, 2)
        self.assertEqual(self.elapsed, 1)

    def test_timeouts_and_500_have_only_two_retries(self):
        client = self.transport([requests.Timeout(), self.response(500), self.response()])
        client.request("GET", "https://provider.test/list")
        self.assertEqual(self.count, 3)
        self.assertEqual(self.elapsed, 25)

    def test_retry_after_is_obeyed(self):
        client = self.transport([self.response(429, "30"), self.response()])
        client.request("GET", "https://provider.test/list")
        self.assertEqual((self.count, self.elapsed), (2, 30))

    def test_cooldown_cannot_exceed_deadline(self):
        client = self.transport([self.response(429, "1000")], deadline=100)
        with self.assertRaises(BudgetExceeded):
            client.request("GET", "https://provider.test/list")
        self.assertEqual(self.count, 1)

    def test_daily_budget_prevents_an_actual_network_call(self):
        client = self.transport([self.response()])
        client.count_attempt = Mock(side_effect=BudgetExceeded("limit"))
        with self.assertRaises(BudgetExceeded):
            client.request("GET", "https://provider.test/list")
        client.session.request.assert_not_called()

    def test_permanent_403_is_not_retried(self):
        client = self.transport([self.response(403)])
        with self.assertRaises(requests.HTTPError):
            client.request("GET", "https://provider.test/list")
        self.assertEqual(self.count, 1)

    def test_redirects_are_counted_and_rate_limited(self):
        redirect = self.response(302)
        redirect.headers["Location"] = "/new-list"
        client = self.transport([redirect, self.response()])
        client.request("GET", "https://provider.test/list")
        self.assertEqual((self.count, self.elapsed), (2, 1))

    def test_repeated_429_opens_a_circuit_for_that_host(self):
        client = self.transport([self.response(429, "1") for _ in range(3)])
        with self.assertRaises(requests.HTTPError):
            client.request("GET", "https://provider.test/list")
        with self.assertRaisesRegex(RuntimeError, "circuit open"):
            client.request("GET", "https://provider.test/another-source")
        self.assertEqual(self.count, 3)


class SchedulingTests(unittest.TestCase):
    def test_child_keeps_its_run_date_even_if_execution_crosses_midnight(self):
        process = Mock()
        process.wait.return_value = 0
        with patch("worker.stock_catalog.today", return_value=date(2026, 11, 4)), patch("worker.stock_catalog.subprocess.Popen", return_value=process) as popen:
            self.assertEqual(run_child(Settings()), 0)
        command = popen.call_args.args[0]
        self.assertEqual(command[-2:], ["--run-date", "2026-11-04"])
    def test_lock_is_database_scoped_and_within_mysql_length_limit(self):
        first, second = Mock(), Mock()
        first.config.database, second.config.database = "database_a", "database_b"
        self.assertNotEqual(lock_name(first), lock_name(second))
        first.config.database = "x" * 64
        self.assertLessEqual(len(lock_name(first)), 64)
    def test_runtime_config_preserves_password_and_explicit_env_override(self):
        password = 'special$"\\password'
        with tempfile.TemporaryDirectory() as temp:
            config = Path(temp) / "config.json"
            config.write_text(json.dumps({"MYSQL_PASSWORD": password, "MYSQL_HOST": "snapshot-host"}))
            with patch.dict(os.environ, {"STOCK_CATALOG_CONFIG_FILE": str(config), "MYSQL_HOST": "override-host"}, clear=True), patch("worker.stock_catalog.load_dotenv"):
                load_configuration()
                self.assertEqual(os.environ["MYSQL_PASSWORD"], password)
                self.assertEqual(os.environ["MYSQL_HOST"], "override-host")
    def test_midnight_uses_beijing_date(self):
        self.assertEqual(today(datetime(2026, 11, 4, 15, 59, tzinfo=timezone.utc)), date(2026, 11, 4))
        self.assertEqual(today(datetime(2026, 11, 4, 16, 0, tzinfo=timezone.utc)), date(2026, 11, 5))

    def test_startup_wake_and_restart_skip_finished_dates(self):
        for status in ("success", "partial", "failed"):
            self.assertFalse(due(status))
        for status in (None, "running"):
            self.assertTrue(due(status))

    def test_polling_aligns_with_midnight_instead_of_drifting_past_it(self):
        self.assertEqual(poll_delay(datetime(2026, 11, 4, 15, 59, 55, tzinfo=timezone.utc)), 5)
        self.assertEqual(poll_delay(datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc)), 60)
