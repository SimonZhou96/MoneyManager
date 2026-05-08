#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import contextlib
import io
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import scheduled_daily_job as job


class FakeMarketDatabase:
    pools_by_market = {}
    etfs_by_market = {}
    instances = []

    def __init__(self, config):
        self.config = config
        self.closed = False
        self.__class__.instances.append(self)

    def init_stock_pool_schema(self):
        return None

    def close(self):
        self.closed = True

    def get_stock_pool(self, market, pool_type, limit=None):
        if pool_type == "etf":
            return self.etfs_by_market.get(market, [])
        return self.pools_by_market.get(market, []) if pool_type == "best" else []


class ScheduledDailyJobParallelTest(unittest.TestCase):
    def setUp(self):
        FakeMarketDatabase.pools_by_market = {
            "HK": [{"code": "HK.00001", "name": "HK One"}],
            "A": [{"code": "A.000001", "name": "A One"}],
            "US": [{"code": "US.AAPL", "name": "Apple"}],
        }
        FakeMarketDatabase.etfs_by_market = {}
        FakeMarketDatabase.instances = []

    def run_main(self, tmp_dir, markets="HK,A,US", workers=3):
        argv = [
            "scheduled_daily_job.py",
            "--no-fetch",
            "--no-feishu",
            "--markets",
            markets,
            "--market-workers",
            str(workers),
            "--csv",
            str(Path(tmp_dir) / "screening_result.csv"),
        ]
        with patch.object(sys, "argv", argv), \
                patch.object(job, "MarketDatabase", FakeMarketDatabase), \
                patch.object(job, "get_db_config", return_value=object()):
            with contextlib.redirect_stdout(io.StringIO()):
                return job.main()

    def test_workers_start_concurrently_when_market_workers_is_three(self):
        barrier = threading.Barrier(3, timeout=2)
        started = []
        started_lock = threading.Lock()

        def fake_run_screening(mysql_config, market, timeframe, default_params, verbose=False):
            with started_lock:
                started.append(market)
            barrier.wait()
            return f"task-{market}", []

        with tempfile.TemporaryDirectory() as tmp_dir:
            with patch.object(job, "run_screening_for_market", side_effect=fake_run_screening):
                exit_code = self.run_main(tmp_dir, workers=3)

        self.assertEqual(exit_code, 0)
        self.assertCountEqual(started, ["HK", "A", "US"])
        self.assertGreaterEqual(len(FakeMarketDatabase.instances), 4)

    def test_failure_in_one_market_does_not_block_success_in_another(self):
        def fake_run_screening(mysql_config, market, timeframe, default_params, verbose=False):
            if market == "A":
                raise RuntimeError("boom")
            return f"task-{market}", [{
                "code": "HK.00001",
                "name": "HK One",
                "market": market,
                "sector": "Finance",
                "industry": "Banking",
                "market_cap": 100.0,
                "pe_ratio": 10.0,
                "conditions_met": "EMA突破",
            }]

        with tempfile.TemporaryDirectory() as tmp_dir:
            with patch.object(job, "run_screening_for_market", side_effect=fake_run_screening):
                exit_code = self.run_main(tmp_dir, markets="HK,A", workers=2)
            hk_csv = Path(tmp_dir) / f"screening_result_{job.date.today():%Y-%m-%d}_HK.csv"
            a_csv = Path(tmp_dir) / f"screening_result_{job.date.today():%Y-%m-%d}_A.csv"

            self.assertEqual(exit_code, 0)
            self.assertTrue(hk_csv.exists())
            self.assertFalse(a_csv.exists())

    def test_market_workers_one_preserves_market_order(self):
        run_order = []

        def fake_run_screening(mysql_config, market, timeframe, default_params, verbose=False):
            run_order.append(market)
            return f"task-{market}", []

        with tempfile.TemporaryDirectory() as tmp_dir:
            with patch.object(job, "run_screening_for_market", side_effect=fake_run_screening):
                exit_code = self.run_main(tmp_dir, workers=1)

        self.assertEqual(exit_code, 0)
        self.assertEqual(run_order, ["HK", "A", "US"])

    def test_csv_paths_are_market_specific(self):
        FakeMarketDatabase.pools_by_market = {
            "US": [{"code": "US.TEST", "name": "Test Inc"}],
        }
        FakeMarketDatabase.etfs_by_market = {
            "US": [{"code": "US.ETF", "name": "ETF Fund"}],
        }
        passed = [
            {
                "code": "US.TEST",
                "name": "Test Inc",
                "market": "US",
                "sector": "Tech",
                "industry": "Software",
                "market_cap": 200.0,
                "pe_ratio": 20.0,
                "conditions_met": "EMA突破",
            },
            {
                "code": "US.ETF",
                "name": "ETF Fund",
                "market": "US",
                "sector": "ETF",
                "industry": "ETF",
                "market_cap": 300.0,
                "pe_ratio": 30.0,
                "conditions_met": "RSI超卖",
            },
        ]

        def fake_run_screening(mysql_config, market, timeframe, default_params, verbose=False):
            return "task-US", passed

        with tempfile.TemporaryDirectory() as tmp_dir:
            csv_base = str(Path(tmp_dir) / "screening_result")
            with patch.object(job, "MarketDatabase", FakeMarketDatabase), \
                    patch.object(job, "run_screening_for_market", side_effect=fake_run_screening):
                with contextlib.redirect_stdout(io.StringIO()):
                    result = job.run_market_screening_worker(
                        mysql_config=object(),
                        market="US",
                        timeframe="1d",
                        default_params={},
                        csv_base=csv_base,
                        today_str="2026-05-08",
                    )

            expected_paths = [
                str(Path(tmp_dir) / "screening_result_2026-05-08_US.csv"),
                str(Path(tmp_dir) / "screening_result_2026-05-08_US_no_etf.csv"),
                str(Path(tmp_dir) / "screening_result_2026-05-08_US_etf_only.csv"),
            ]
            self.assertFalse(result.skipped)
            self.assertEqual(result.csv_paths, expected_paths)
            for path in expected_paths:
                self.assertTrue(Path(path).exists())


if __name__ == "__main__":
    unittest.main()
