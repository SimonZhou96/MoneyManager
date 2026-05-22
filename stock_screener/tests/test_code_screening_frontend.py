#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
MAIN_TSX = ROOT / "web_frontend" / "src" / "main.tsx"


class CodeScreeningFrontendTest(unittest.TestCase):
    def read_main(self) -> str:
        return MAIN_TSX.read_text(encoding="utf-8")

    def test_navigation_replaces_single_stock_with_code_screening(self):
        source = self.read_main()

        self.assertIn("代码筛选", source)
        self.assertNotIn(">单股选股<", source)
        self.assertNotIn("page === 'single'", source)
        self.assertNotIn("setPage('single')", source)

    def test_code_screening_uses_custom_list_api_not_single_stock_api(self):
        source = self.read_main()

        self.assertIn("/api/screening/custom-list-tasks", source)
        self.assertIn("/api/screening/custom-list-tasks/${jobId}/results", source)
        self.assertNotIn("/api/screening/single-stock", source)

    def test_dashboard_does_not_link_to_single_stock_runs(self):
        source = self.read_main()

        self.assertNotIn("single_stock_runs", source)
        self.assertNotIn("SingleRunDetail", source)
        self.assertNotIn("openSingle", source)


if __name__ == "__main__":
    unittest.main()
