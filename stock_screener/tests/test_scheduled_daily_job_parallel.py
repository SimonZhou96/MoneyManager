#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import contextlib
import csv
import io
import sys
import tempfile
import threading
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

import scheduled_daily_job as job
from signal_analysis.models import SignalAnalysisResult


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
            "--no-ai-analysis",
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

    def test_default_screening_params_use_zuoyi_window_15(self):
        params = job.get_default_screening_params()

        self.assertTrue(params["use_zuoyi_strategy"])
        self.assertEqual(params["zuoyi_signal_window"], 15)

    def test_extract_zuoyi_csv_fields_requires_passed_signals(self):
        failed_detail = {
            "filter_name": "ZuoYiStrategizer",
            "result": "fail",
            "details": {"signals": [{
                "direction": "bullish",
                "left_one_high": 10.0,
                "left_one_low": 8.0,
            }]},
        }
        other_detail = {
            "filter_name": "EMABreakoutStrategizer",
            "result": "pass",
            "details": {"signals": [{
                "direction": "bullish",
                "left_one_high": 10.0,
                "left_one_low": 8.0,
            }]},
        }

        self.assertEqual(job._extract_zuoyi_csv_fields(failed_detail), {})
        self.assertEqual(job._extract_zuoyi_csv_fields(other_detail), {})

    def test_extract_zuoyi_csv_fields_formats_multiple_signals(self):
        detail = {
            "filter_name": "ZuoYiStrategizer",
            "result": "pass",
            "details": {
                "signals": [
                    {
                        "direction": "bullish",
                        "left_one_date": "2026-05-01",
                        "left_one_high": 10.0,
                        "left_one_low": 8.0,
                        "median_date": "2026-05-03",
                        "breakout_date": "2026-05-10",
                        "bars_to_breakout": 7,
                    },
                    {
                        "direction": "bearish",
                        "left_one_date": "2026-05-02",
                        "left_one_high": 14.0,
                        "left_one_low": 12.0,
                        "median_date": "2026-05-04",
                        "breakout_date": "2026-05-11",
                        "bars_to_breakout": 7,
                    },
                ],
            },
        }

        fields = job._extract_zuoyi_csv_fields(detail)

        self.assertEqual(fields["zuoyi_direction"], "看涨；看跌")
        self.assertEqual(fields["zuoyi_left_one_high"], "10；14")
        self.assertEqual(fields["zuoyi_left_one_low"], "8；12")
        self.assertEqual(fields["zuoyi_support_zone"], "8~10；12~14")
        self.assertEqual(fields["zuoyi_breakout_date"], "2026-05-10；2026-05-11")

    def test_write_screening_csv_adds_zuoyi_columns_only_when_present(self):
        base_record = {
            "code": "HK.00001",
            "name": "HK One",
            "market": "HK",
            "sector": "Finance",
            "market_cap": 100.0,
            "pe_ratio": 10.0,
            "conditions_met": "EMA突破",
        }

        with tempfile.TemporaryDirectory() as tmp_dir:
            plain_csv = Path(tmp_dir) / "plain.csv"
            zuoyi_csv = Path(tmp_dir) / "zuoyi.csv"

            job.write_screening_csv([base_record], str(plain_csv))
            with open(plain_csv, "r", encoding="utf-8-sig", newline="") as f:
                plain_rows = list(csv.DictReader(f))

            zuoyi_record = dict(base_record)
            zuoyi_record.update({
                "conditions_met": "左一战法-看涨|EMA突破",
                "zuoyi_direction": "看涨",
                "zuoyi_left_one_date": "2026-05-01",
                "zuoyi_left_one_high": "10",
                "zuoyi_left_one_low": "8",
                "zuoyi_support_zone": "8~10",
                "zuoyi_median_date": "2026-05-03",
                "zuoyi_breakout_date": "2026-05-10",
                "zuoyi_bars_to_breakout": "7",
            })
            job.write_screening_csv([zuoyi_record], str(zuoyi_csv))
            with open(zuoyi_csv, "r", encoding="utf-8-sig", newline="") as f:
                zuoyi_rows = list(csv.DictReader(f))

        self.assertNotIn("左一顶", plain_rows[0])
        self.assertNotIn("左一支撑区间", plain_rows[0])
        self.assertEqual(zuoyi_rows[0]["左一方向"], "看涨")
        self.assertEqual(zuoyi_rows[0]["左一顶"], "10")
        self.assertEqual(zuoyi_rows[0]["左一底"], "8")
        self.assertEqual(zuoyi_rows[0]["左一支撑区间"], "8~10")

    def test_split_screening_csvs_inherit_zuoyi_columns_per_file(self):
        records = [
            {
                "code": "US.TEST",
                "name": "Test Inc",
                "market": "US",
                "sector": "Tech",
                "market_cap": 200.0,
                "pe_ratio": 20.0,
                "conditions_met": "左一战法-看涨|EMA突破",
                "zuoyi_direction": "看涨",
                "zuoyi_left_one_date": "2026-05-01",
                "zuoyi_left_one_high": "10",
                "zuoyi_left_one_low": "8",
                "zuoyi_support_zone": "8~10",
                "zuoyi_median_date": "2026-05-03",
                "zuoyi_breakout_date": "2026-05-10",
                "zuoyi_bars_to_breakout": "7",
            },
            {
                "code": "US.ETF",
                "name": "ETF Fund",
                "market": "US",
                "sector": "ETF",
                "market_cap": 300.0,
                "pe_ratio": 30.0,
                "conditions_met": "左一战法-看跌|RSI超买",
                "zuoyi_direction": "看跌",
                "zuoyi_left_one_date": "2026-05-02",
                "zuoyi_left_one_high": "14",
                "zuoyi_left_one_low": "12",
                "zuoyi_support_zone": "12~14",
                "zuoyi_median_date": "2026-05-04",
                "zuoyi_breakout_date": "2026-05-11",
                "zuoyi_bars_to_breakout": "7",
            },
        ]

        with tempfile.TemporaryDirectory() as tmp_dir:
            no_etf_path, etf_only_path = job.write_split_screening_csvs(
                records,
                str(Path(tmp_dir) / "screening_result.csv"),
                {"US.ETF"},
            )
            with open(no_etf_path, "r", encoding="utf-8-sig", newline="") as f:
                no_etf_rows = list(csv.DictReader(f))
            with open(etf_only_path, "r", encoding="utf-8-sig", newline="") as f:
                etf_rows = list(csv.DictReader(f))

        self.assertEqual(no_etf_rows[0]["左一支撑区间"], "8~10")
        self.assertEqual(etf_rows[0]["左一支撑区间"], "12~14")

    def test_ai_analysis_success_appends_artifact_paths(self):
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
                "conditions_met": "左一战法-看涨|EMA突破",
            },
            {
                "code": "US.ETF",
                "name": "ETF Fund",
                "market": "US",
                "sector": "ETF",
                "industry": "ETF",
                "market_cap": 300.0,
                "pe_ratio": 30.0,
                "conditions_met": "左一战法-看跌|RSI超买",
            },
        ]

        def fake_run_screening(mysql_config, market, timeframe, default_params, verbose=False):
            return "task-US", passed

        with tempfile.TemporaryDirectory() as tmp_dir:
            csv_base = str(Path(tmp_dir) / "screening_result")
            report_path = str(Path(tmp_dir) / "screening_result_2026-05-08_US_ai_report.md")
            results_by_code = {
                "US.TEST": SignalAnalysisResult(
                    code="US.TEST",
                    name="Test Inc",
                    reliability_score=82.0,
                    confidence_score=76.0,
                    signal_bias="bullish",
                    market_hot_news=["美股市场热点"],
                    company_hot_news=["Test Inc 新闻"],
                    news_impact="利好",
                    news_sources=["https://example.com/us-test"],
                ),
                "US.ETF": SignalAnalysisResult(
                    code="US.ETF",
                    name="ETF Fund",
                    reliability_score=55.0,
                    confidence_score=66.0,
                    signal_bias="bearish",
                    market_hot_news=["美股市场热点"],
                    company_hot_news=["ETF 新闻"],
                    news_impact="利空",
                    news_sources=["https://example.com/us-etf"],
                ),
            }
            with patch.object(job, "MarketDatabase", FakeMarketDatabase), \
                    patch.object(job, "run_screening_for_market", side_effect=fake_run_screening), \
                    patch.object(
                        job,
                        "run_signal_analysis_for_market",
                        return_value=SimpleNamespace(
                            warnings=[],
                            artifact_paths=[report_path],
                            results_by_code=results_by_code,
                            skipped_reason="",
                        ),
                    ) as analyze:
                with contextlib.redirect_stdout(io.StringIO()):
                    result = job.run_market_screening_worker(
                        mysql_config=object(),
                        market="US",
                        timeframe="1d",
                        default_params={},
                        csv_base=csv_base,
                        today_str="2026-05-08",
                        enable_ai_analysis=True,
                    )

            self.assertFalse(result.skipped)
            self.assertEqual(result.csv_paths, [
                str(Path(tmp_dir) / "screening_result_2026-05-08_US.csv"),
                str(Path(tmp_dir) / "screening_result_2026-05-08_US_no_etf.csv"),
                str(Path(tmp_dir) / "screening_result_2026-05-08_US_etf_only.csv"),
                report_path,
            ])
            self.assertFalse(Path(tmp_dir, "screening_result_2026-05-08_US_ai.csv").exists())
            with open(Path(tmp_dir) / "screening_result_2026-05-08_US_no_etf.csv", "r", encoding="utf-8-sig", newline="") as f:
                no_etf_rows = list(csv.DictReader(f))
            with open(Path(tmp_dir) / "screening_result_2026-05-08_US_etf_only.csv", "r", encoding="utf-8-sig", newline="") as f:
                etf_rows = list(csv.DictReader(f))
            self.assertEqual(no_etf_rows[0]["股票代码"], "US.TEST")
            self.assertEqual(no_etf_rows[0]["信号可靠性评分"], "82.00")
            self.assertIn("80-100", no_etf_rows[0]["信号可靠性评分口径"])
            self.assertEqual(no_etf_rows[0]["市场热点新闻"], "美股市场热点")
            self.assertEqual(no_etf_rows[0]["公司热点新闻"], "Test Inc 新闻")
            self.assertEqual(no_etf_rows[0]["新闻影响判断"], "利好")
            self.assertEqual(no_etf_rows[0]["新闻来源"], "https://example.com/us-test")
            self.assertEqual(etf_rows[0]["股票代码"], "US.ETF")
            self.assertEqual(etf_rows[0]["辅助方向判断"], "bearish")
            self.assertIn("bearish偏看跌", etf_rows[0]["辅助方向判断口径"])
            self.assertEqual(etf_rows[0]["新闻影响判断"], "利空")
            analyze.assert_called_once()

    def test_ai_analysis_failure_keeps_original_csv_paths(self):
        FakeMarketDatabase.pools_by_market = {
            "US": [{"code": "US.TEST", "name": "Test Inc"}],
        }
        passed = [{
            "code": "US.TEST",
            "name": "Test Inc",
            "market": "US",
            "sector": "Tech",
            "industry": "Software",
            "market_cap": 200.0,
            "pe_ratio": 20.0,
            "conditions_met": "左一战法-看涨|EMA突破",
        }]

        def fake_run_screening(mysql_config, market, timeframe, default_params, verbose=False):
            return "task-US", passed

        with tempfile.TemporaryDirectory() as tmp_dir:
            csv_base = str(Path(tmp_dir) / "screening_result")
            with patch.object(job, "MarketDatabase", FakeMarketDatabase), \
                    patch.object(job, "run_screening_for_market", side_effect=fake_run_screening), \
                    patch.object(job, "run_signal_analysis_for_market", side_effect=RuntimeError("model down")):
                with contextlib.redirect_stdout(io.StringIO()):
                    result = job.run_market_screening_worker(
                        mysql_config=object(),
                        market="US",
                        timeframe="1d",
                        default_params={},
                        csv_base=csv_base,
                        today_str="2026-05-08",
                        enable_ai_analysis=True,
                    )

            expected_paths = [
                str(Path(tmp_dir) / "screening_result_2026-05-08_US.csv"),
                str(Path(tmp_dir) / "screening_result_2026-05-08_US_no_etf.csv"),
                str(Path(tmp_dir) / "screening_result_2026-05-08_US_etf_only.csv"),
            ]
            self.assertIsNone(result.error)
            self.assertEqual(result.csv_paths, expected_paths)


if __name__ == "__main__":
    unittest.main()
