import os
import unittest
from datetime import date
from unittest.mock import patch

from signal_analysis.chain import SignalAnalysisChain, SignalAnalysisContext
from signal_analysis.models import AnalysisSettings, ScreeningSignalRow, SignalAnalysisResult
from signal_analysis.search_providers import NullSearchProvider
from signal_analysis.llm_providers import NullLLMProvider


class TestLLM(NullLLMProvider):
    is_available = True
    calls = 0
    partial = False

    def analyze_batch(self, market, signals, **kwargs):
        self.calls += 1
        return [SignalAnalysisResult(code=row.code, name=row.name, summary='政策与技术分析',
                                     company_events=['公告'], company_hot_news=['新闻'])
                for row in (signals[:1] if self.partial else signals)]


class StructuredSignalReportTests(unittest.TestCase):
    def setUp(self):
        self.patches = [patch.dict(os.environ, {'SIGNAL_ENABLE_API_HOT_SECTORS': '0'}),
                        patch('signal_analysis.chain._fetch_stock_snapshots', return_value={}),
                        patch('signal_analysis.chain.USE_NEW_RENDERER', False)]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    def rows(self):
        return [ScreeningSignalRow.from_record(dict(code=code, name=name, conditions_met='EMA突破'), index=i, default_market='HK')
                for i, (code, name) in enumerate([('HK.00700', '腾讯'), ('HK.09988', '阿里')])]

    def context(self, rows=None, llm=None):
        return SignalAnalysisContext(task_id='task-one', market='HK', rows=self.rows() if rows is None else rows,
                                     check_date=date(2026, 10, 29), settings=AnalysisSettings(),
                                     search_provider=NullSearchProvider(), llm_provider=llm or TestLLM())

    def test_report_uses_structured_rows_without_opening_any_file(self):
        context = self.context()
        with patch('builtins.open', side_effect=AssertionError('No report CSV or MD files allowed')):
            result = SignalAnalysisChain().run(context)
        self.assertTrue(result.success, result.warnings)
        self.assertIn('腾讯', result.report_markdown)
        self.assertIn('阿里', result.report_markdown)
        self.assertEqual(result.artifact_paths, [])

    def test_empty_input_never_calls_model(self):
        llm = TestLLM()
        result = SignalAnalysisChain().run(self.context(rows=[], llm=llm))
        self.assertFalse(result.success)
        self.assertEqual(llm.calls, 0)
        self.assertEqual(result.report_markdown, '')

    def test_partial_model_output_does_not_publish_placeholder_report(self):
        llm = TestLLM()
        llm.partial = True
        result = SignalAnalysisChain().run(self.context(llm=llm))
        self.assertFalse(result.success)
        self.assertIn('HK.00700', result.results_by_code)
        self.assertEqual(result.report_markdown, '')

    def test_missing_model_cannot_report_success(self):
        result = SignalAnalysisChain().run(self.context(llm=NullLLMProvider()))
        self.assertFalse(result.success)

    def test_single_row_analysis_does_not_create_temporary_csv(self):
        from signal_analysis.service import run_signal_analysis_for_row
        row = self.rows()[0]
        with patch('signal_analysis.service.LLMProviderFactory.from_env', return_value=TestLLM()), \
             patch('signal_analysis.service.SearchProviderFactory.from_env', return_value=NullSearchProvider()), \
             patch('signal_analysis.service._prepare_analysis_providers', side_effect=lambda search, llm: (search, llm, [])), \
             patch('signal_analysis.service.build_market_intel_service', return_value=None), \
             patch('signal_analysis.service.MySqlSignalAnalysisRepository', return_value=None), \
             patch('tempfile.TemporaryDirectory', side_effect=AssertionError('No temporary CSV')):
            result, warnings = run_signal_analysis_for_row(None, task_id='single', row=row, market='HK')
        self.assertEqual(result.code, row.code, warnings)
