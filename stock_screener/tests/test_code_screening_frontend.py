#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
MAIN_TSX = ROOT / "web_frontend" / "src" / "main.tsx"
STYLES_CSS = ROOT / "web_frontend" / "src" / "styles.css"


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

    def test_code_screening_form_prevents_rule_chain_checkbox_overlap(self):
        source = self.read_main()
        styles = STYLES_CSS.read_text(encoding="utf-8")

        self.assertIn('className="code-rule-field"', source)
        self.assertIn('className="code-screening-toggles"', source)
        self.assertIn(".compact-form-grid .code-rule-field", styles)
        self.assertIn(".code-screening-toggles", styles)
        self.assertIn("grid-column: span 2", styles)

    def test_code_screening_copy_uses_task_queue_terms_not_local_agent(self):
        source = self.read_main()

        self.assertNotIn("本地 Agent", source)
        self.assertIn("等待任务创建", source)
        self.assertIn("已进入执行队列，可在最近任务查看状态", source)

    def test_task_detail_uses_compact_summary_metrics(self):
        source = self.read_main()
        styles = STYLES_CSS.read_text(encoding="utf-8")

        self.assertIn('className="metric-grid task-summary-grid"', source)
        self.assertIn('className="task-summary-rule"', source)
        self.assertIn(".task-summary-grid", styles)
        self.assertIn(".task-summary-grid .metric strong", styles)

    def test_full_market_screening_form_prevents_rule_chain_toggle_overlap(self):
        source = self.read_main()
        styles = STYLES_CSS.read_text(encoding="utf-8")

        self.assertIn('className="form-grid screening-form-grid"', source)
        self.assertIn('className="screening-rule-field"', source)
        self.assertIn('className="screening-toggles"', source)
        self.assertIn('className="screening-actions"', source)
        self.assertIn(".screening-form-grid", styles)
        self.assertIn(".screening-rule-field", styles)
        self.assertIn(".screening-toggles", styles)
        self.assertIn(".screening-actions", styles)


if __name__ == "__main__":
    unittest.main()
