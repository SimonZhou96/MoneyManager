#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import unittest

from db import InMemoryQuantRepository


class QuantDbTest(unittest.TestCase):
    def test_save_and_load_backtest_result(self):
        repo = InMemoryQuantRepository()
        repo.create_quant_backtest_run({"run_id": "r1", "user_id": 7, "status": "running", "request": {"market": "US"}})
        repo.finish_quant_backtest_run("r1", {"total_return": 0.12}, warnings=["ok"])

        row = repo.get_quant_backtest_run("r1")

        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row["run_id"], "r1")
        self.assertEqual(row["status"], "completed")
        self.assertEqual(row["metrics"]["total_return"], 0.12)
        self.assertEqual(row["warnings"], ["ok"])

    def test_update_backtest_progress_appends_server_logs(self):
        repo = InMemoryQuantRepository()
        repo.create_quant_backtest_run({"run_id": "r1", "user_id": 7, "status": "queued", "request": {"market": "US"}})

        repo.update_quant_backtest_progress("r1", status="running", progress_pct=35, current_stage="加载行情", log_message="正在读取K线")

        row = repo.get_quant_backtest_run("r1")
        assert row is not None
        self.assertEqual(row["status"], "running")
        self.assertEqual(row["progress_pct"], 35)
        self.assertEqual(row["current_stage"], "加载行情")
        self.assertEqual(row["progress_logs"][-1]["message"], "正在读取K线")


if __name__ == "__main__":
    unittest.main()
