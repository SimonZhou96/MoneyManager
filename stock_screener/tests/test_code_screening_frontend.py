#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
MAIN_TSX = ROOT / "web_frontend" / "src" / "main.tsx"
STYLES_CSS = ROOT / "web_frontend" / "src" / "styles.css"
STOCK_TERMINAL_API = ROOT / "web_frontend" / "src" / "features" / "stockTerminal" / "api.ts"


class CodeScreeningFrontendTest(unittest.TestCase):
    def read_main(self) -> str:
        return MAIN_TSX.read_text(encoding="utf-8")

    def read_stock_terminal_api(self) -> str:
        return STOCK_TERMINAL_API.read_text(encoding="utf-8")

    def test_navigation_replaces_single_stock_with_code_screening(self):
        source = self.read_main()

        self.assertIn('Header title="个股筛选器"', source)
        self.assertNotIn(">单股选股<", source)
        self.assertNotIn("page === 'single'", source)
        self.assertNotIn("setPage('single')", source)

    def test_code_screening_uses_tasks_api(self):
        source = self.read_main()

        self.assertIn("/api/screening/tasks", source)
        self.assertIn("/api/screening/tasks/${taskId}/results", source)

    def test_dashboard_does_not_link_to_single_stock_runs(self):
        source = self.read_main()

        self.assertNotIn("single_stock_runs", source)
        self.assertNotIn("SingleRunDetail", source)
        self.assertNotIn("openSingle", source)

    def test_code_screening_form_prevents_rule_chain_checkbox_overlap(self):
        source = self.read_main()
        styles = STYLES_CSS.read_text(encoding="utf-8")

        self.assertIn('className="compact-form-grid"', source)
        self.assertIn('className="form-row2"', source)
        self.assertIn(".compact-form-grid .form-row2", styles)
        self.assertIn(".compact-form-grid .field", styles)
        self.assertIn("grid-column: span 2", styles)

    def test_code_screening_copy_uses_task_queue_terms_not_local_agent(self):
        source = self.read_main()

        self.assertNotIn("本地 Agent", source)
        self.assertIn("排队中", source)
        self.assertIn("等待任务创建", source)

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

        self.assertIn('className="metric-grid"', source)
        self.assertIn(".compact-form-grid .form-row2", styles)
        self.assertIn(".code-screening-layout .metric-grid", styles)
        self.assertIn("grid-column: span 2", styles)

    def test_code_screening_is_merged_stock_terminal_entry(self):
        source = self.read_main()

        self.assertIn("个股筛选器", source)
        self.assertNotIn(">市场情报<", source)
        self.assertNotIn("page === 'marketIntel'", source)
        self.assertIn("StockTerminalPanel", source)

    def test_terminal_loading_is_row_selection_driven(self):
        source = self.read_main()
        api_source = self.read_stock_terminal_api()

        self.assertIn("selectedResult", source)
        self.assertIn("setSelectedResult", source)
        self.assertIn("normalizeCodeScreeningTerminalRow", source)
        self.assertIn("terminalMarket", source)
        self.assertIn("/api/stock-terminal", api_source)

    def test_task_detail_score_panel_requires_result_selection(self):
        source = self.read_main()

        self.assertIn("选择结果行查看评分明细", source)
        self.assertNotIn("setSelectedResult(results[0])", source)

    def test_task_detail_stops_polling_terminal_status(self):
        source = self.read_main()

        self.assertIn("isTerminalTaskStatus(task?.status)", source)
        self.assertIn("task?.status", source)

    def test_frontend_renders_macro_score_details_and_temporal_summary(self):
        source = self.read_main()

        self.assertIn("MacroScoreDetails", source)
        self.assertIn("temporal_summary", source)
        self.assertIn("sub_scores", source)
        self.assertIn("final_score", source)

    def test_mobile_touch_targets_use_accessible_minimums(self):
        source = self.read_main()
        styles = STYLES_CSS.read_text(encoding="utf-8")

        self.assertIn('className="secondary-button" onClick={startNewChain}', source)
        self.assertIn('className="secondary-button" disabled={saving || !editor.chain_key}', source)
        self.assertIn(".check input", styles)
        self.assertIn("width: 18px", styles)
        self.assertIn("accent-color: #c9a84c", styles)
        self.assertIn("min-height: 36px", styles)

    def test_code_screening_renders_backend_report_sections(self):
        source = self.read_main()
        styles = STYLES_CSS.read_text(encoding="utf-8")

        self.assertIn("report_sections?: ReportSection[]", source)
        self.assertIn("selectedResult", source)
        self.assertIn("CodeScreeningReportPanel", source)
        self.assertIn("report_sections", source)
        self.assertIn(".code-report", styles)
        self.assertIn(".code-report-section", styles)


if __name__ == "__main__":
    unittest.main()
