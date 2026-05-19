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


if __name__ == "__main__":
    unittest.main()
