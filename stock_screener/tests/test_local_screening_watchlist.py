import unittest
from datetime import date
from unittest.mock import patch

from filters import FilterContext, FilterResult, StockInfo
from rule_engine import RuleEngine
from test_rule_engine import chain, metadata, registry


class LocalScreeningWatchlistTests(unittest.TestCase):
    def make_engine(self, category='technical', enabled=True):
        return RuleEngine(
            [metadata('technical_signal', 'strategy', 'StaticStrategizer', enabled=enabled,
                      strategy_category=category, params={'satisfied': False})],
            chain({'ref': 'technical_signal'}), registry(),
        )

    def test_only_listed_stock_passes_and_normal_execution_is_restored(self):
        from scripts.run_local_screening_test import local_screening_test
        engine = self.make_engine()
        context = FilterContext(date(2026, 10, 29), 'HK')
        with local_screening_test(['HK.00700']):
            result = engine.evaluate_stock(StockInfo('HK', 'HK.00700'), context)
            other = engine.evaluate_stock(StockInfo('HK', 'HK.09988'), context)
        self.assertTrue(result.passed)
        self.assertEqual(result.filter_outputs[0].result, FilterResult.PASS)
        self.assertTrue(result.filter_outputs[0].details['local_test'])
        self.assertIn('模拟通过', result.filter_outputs[0].reason)
        self.assertFalse(other.passed)
        self.assertFalse(engine.evaluate_stock(StockInfo('HK', 'HK.00700'), context).passed)

    def test_non_technical_and_disabled_rules_cannot_be_bypassed(self):
        from scripts.run_local_screening_test import local_screening_test
        context = FilterContext(date(2026, 10, 29), 'HK')
        with local_screening_test(['HK.00700']):
            for category, enabled in [('macro', True), ('market', True), ('technical', False)]:
                with self.subTest(category=category, enabled=enabled):
                    self.assertFalse(self.make_engine(category, enabled).evaluate_stock(
                        StockInfo('HK', 'HK.00700'), context).passed)

    def test_report_is_visibly_marked_but_normal_stock_report_is_unchanged(self):
        from scripts.run_local_screening_test import local_screening_test
        from task_reports import ElasticsearchReportRepository
        documents = []
        # Capture the real storage boundary, not the report generation pipeline.
        with patch.object(ElasticsearchReportRepository, 'save', lambda store, report: documents.append(dict(report))):
            with local_screening_test(['HK.00700']):
                store = ElasticsearchReportRepository(None)
                store.save({'codes': ['HK.00700'], 'title': '腾讯', 'summary': '摘要',
                            'content_markdown': '# 分析', 'content_text': '分析'})
                store.save({'codes': ['US.AAPL'], 'title': 'Apple', 'summary': '摘要',
                            'content_markdown': '# 分析', 'content_text': '分析'})
        self.assertIn('本地测试', documents[0]['title'])
        self.assertIn('技术条件模拟通过', documents[0]['content_markdown'])
        self.assertIn('本地测试', documents[0]['content_text'])
        self.assertEqual(documents[1]['title'], 'Apple')

    def test_report_model_receives_explicit_simulation_notice(self):
        from scripts.run_local_screening_test import local_screening_test
        from signal_analysis.models import ScreeningSignalRow
        import task_reports
        row = ScreeningSignalRow.from_record({'code': 'HK.00700', 'name': '腾讯'}, 0, 'HK')
        observed = []
        def analyze(**kwargs):
            observed.append(kwargs['rows'][0].conditions_met)
        with patch.object(task_reports, 'run_signal_analysis_for_market', side_effect=analyze):
            with local_screening_test(['HK.00700']):
                task_reports.run_signal_analysis_for_market(rows=[row])
        self.assertIn('技术条件模拟通过', observed[0])
