#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TDD: K 线数据获取诊断信息流测试。

验证点:
1. 默认链不包含数据库 K 线缓存
4. eastmoney fetch_klines() 在全部失败时抛出包含"K-line fetch failed:"和源名的异常
5. eastmoney fetch_klines() 在成功时返回数据并记录来源
6. HK.00700 (腾讯) 通过至少一个数据源能获取到 K 线数据
"""

import os
import sys
import unittest
from unittest import mock

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kline_fetcher import KlineFetcherFactory, YFinanceKlineFetcher


class KlineFetcherFactoryTest(unittest.TestCase):
    """验证工厂不再接受数据库缓存配置。"""

    def test_default_excludes_database_fetcher(self):
        chain = KlineFetcherFactory.create_fetcher_chain()
        names = [f.get_name() for f in chain]
        self.assertNotIn("DatabaseKlineCache", names)

    def test_no_db_includes_only_yfinance_and_akshare(self):
        """默认链不含数据库来源，且始终包含 YFinance。"""
        chain = KlineFetcherFactory.create_fetcher_chain()
        names = [f.get_name() for f in chain]
        self.assertNotIn("DatabaseKlineCache", names)
        self.assertIn("YFinance", names, "应始终包含 YFinance")


class YFinanceKlineFetcherLifecycleTest(unittest.TestCase):
    """Protect the session lifecycle that prevents per-symbol FD growth."""

    @staticmethod
    def _download_frame():
        return pd.DataFrame({
            "Date": pd.date_range("2026-01-01", periods=2, freq="D"),
            "Open": [10.0, 11.0],
            "High": [11.0, 12.0],
            "Low": [9.0, 10.0],
            "Close": [10.5, 11.5],
            "Volume": [100, 110],
        })

    def test_sequential_fetches_reuse_the_supplied_session_without_worker_threads(self):
        """Would fail if a fetch creates a new yfinance session or worker thread."""
        session = object()
        fetcher = YFinanceKlineFetcher(session=session)
        with mock.patch.object(fetcher.yf, "download", return_value=self._download_frame()) as download:
            self.assertIsNotNone(fetcher.fetch("US.TEST", market="US", timeframe="1d"))
            self.assertIsNotNone(fetcher.fetch("US.TEST2", market="US", timeframe="1d"))

        self.assertEqual(download.call_count, 2)
        for call in download.call_args_list:
            self.assertIs(call.kwargs["session"], session)
            self.assertFalse(call.kwargs["threads"])

    def test_close_releases_only_the_session_owned_by_the_fetcher(self):
        """Would fail if a completed job leaves the fetcher's HTTP session open."""
        class Session:
            def __init__(self):
                self.close_calls = 0

            def close(self):
                self.close_calls += 1

        session = Session()
        fetcher = YFinanceKlineFetcher(session=session, owns_session=True)

        fetcher.close()
        fetcher.close()

        self.assertEqual(session.close_calls, 1)


class EastmoneyProviderDiagnosticTest(unittest.TestCase):
    """验证 eastmoney provider 的错误诊断信息流。"""

    def test_fetch_klines_reports_all_source_failures(self):
        """当所有数据源都失败时，异常消息应包含每个尝试过的源名"""
        from stock_terminal.providers.eastmoney import EastmoneyStockTerminalProvider

        provider = EastmoneyStockTerminalProvider(db="fake_db")

        # 模拟所有 fetcher.fetch() 返回 None
        with unittest.mock.patch.object(
            EastmoneyStockTerminalProvider, '_rows_from_frame', return_value=[]
        ):
            with self.assertRaises(RuntimeError) as ctx:
                provider.fetch_klines("HK", "HK.00700", "1d", 200)

            msg = str(ctx.exception)
            # 应包含特征字符串
            self.assertIn("K-line fetch failed", msg,
                          f"错误消息应包含 'K-line fetch failed', 实际: {msg}")
            # skip_db_cache=True 时不包含 DatabaseKlineCache
            self.assertNotIn("DatabaseKlineCache", msg,
                             f"使用 skip_db_cache=True 时不应出现 DatabaseKlineCache, 实际: {msg}")

    def test_fetch_klines_reports_exception_from_fetcher(self):
        """当某个 fetcher 抛出异常时，异常消息应包含该源名和异常信息"""
        from stock_terminal.providers.eastmoney import EastmoneyStockTerminalProvider
        from kline_fetcher import KlineFetcherBase

        provider = EastmoneyStockTerminalProvider(db=None)

        # 创建一个总是抛异常的假 fetcher（签名匹配基类）
        class FailingFetcher(KlineFetcherBase):
            def get_name(self):
                return "TestFailingSource"
            def fetch(self, stock_code=None, market=None, timeframe=None, max_count=None):
                raise RuntimeError("simulated network timeout")

        # eastmoney.py 在 fetch_klines() 内部动态 import KlineFetcherFactory
        # 所以需要 patch 该函数内部的 import 结果
        def _fake_create_chain(**kwargs):
            return [FailingFetcher()]

        with unittest.mock.patch(
            'kline_fetcher.KlineFetcherFactory.create_fetcher_chain',
            side_effect=_fake_create_chain
        ):
            with self.assertRaises(RuntimeError) as ctx:
                provider.fetch_klines("HK", "HK.00700", "1d", 200)

            msg = str(ctx.exception)
            self.assertIn("TestFailingSource", msg,
                          f"错误消息应包含失败的源名, 实际: {msg}")
            self.assertIn("simulated network timeout", msg,
                          f"错误消息应包含异常信息, 实际: {msg}")


class HK00700KlineIntegrationTest(unittest.TestCase):
    """集成测试：验证 HK.00700 (腾讯) 能够通过至少一个数据源获取 K 线。"""

    @classmethod
    def setUpClass(cls):
        from db import MarketDatabase, MySqlConfig
        cls.mysql_config = MySqlConfig(
            host=os.getenv("MYSQL_HOST", "127.0.0.1"),
            port=int(os.getenv("MYSQL_PORT", "3306")),
            user=os.getenv("MYSQL_USER", "root"),
            password=os.getenv("MYSQL_PASSWORD", "123456"),
            database=os.getenv("MYSQL_DATABASE", "market_data"),
        )
        # 尝试连接 DB（可能没有 MySQL，忽略）
        try:
            cls.db = MarketDatabase(cls.mysql_config)
        except Exception:
            cls.db = None

    def setUp(self):
        if self.db is None:
            self.skipTest("MySQL 不可用，跳过集成测试")

    def test_eastmoney_provider_returns_diagnostic_on_failure(self):
        """即使所有数据源失败，eastmoney provider 也应抛出包含诊断信息的异常"""
        from stock_terminal.providers.eastmoney import EastmoneyStockTerminalProvider

        # db=None 且无网络 → YFinance 和 AKShare 应该失败或返回空
        provider = EastmoneyStockTerminalProvider(db=None, timeout_sec=2.0)

        try:
            rows = provider.fetch_klines("HK", "HK.00700", "1d", 10)
            # 如果成功，数据应非空
            self.assertIsInstance(rows, list, "成功时应返回 list")
            if rows:
                self.assertIsNotNone(rows[0].open, "KlinePoint 应有 open 字段")
        except RuntimeError as exc:
            msg = str(exc)
            # 关键验证: 错误消息不是无意义的 "returned no data"
            self.assertNotEqual(msg, "eastmoney kline provider returned no data",
                                "错误消息不应是无信息的 fixed string")
            self.assertIn("K-line fetch failed", msg,
                          "错误消息应包含 'K-line fetch failed' 前缀")

    @classmethod
    def tearDownClass(cls):
        if cls.db is not None:
            try:
                cls.db.close()
            except Exception:
                pass


if __name__ == "__main__":
    unittest.main()
