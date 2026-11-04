import copy
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from signal_analysis.models import AnalysisRunResult, SignalAnalysisResult


class ReportDatabase:
    def __init__(self):
        self.checkpoint = {}
        self.active = True

    def get_task_report_checkpoint(self, task_type, task_id):
        return copy.deepcopy(self.checkpoint)

    def save_task_report_checkpoint(self, task_type, task_id, checkpoint, lock_token=None):
        if not self.active:
            return False
        self.checkpoint = copy.deepcopy(checkpoint)
        return True


class ReportIndex:
    def __init__(self):
        self.documents = {}
        self.fail = False
        self.writes = 0

    def get(self, report_id):
        return copy.deepcopy(self.documents.get(report_id))

    def save(self, report):
        if self.fail:
            raise RuntimeError('ES unavailable')
        self.writes += 1
        self.documents[report['report_id']] = copy.deepcopy(report)

    def close(self):
        pass


class TaskReportTests(unittest.TestCase):
    def setUp(self):
        from task_reports import publish_task_report
        self.publish = publish_task_report
        self.db = ReportDatabase()
        self.index = ReportIndex()
        self.records = {'HK': [dict(code='HK.00700', name='腾讯')], 'US': [dict(code='US.AAPL', name='Apple')]}

    def analyze(self, **kwargs):
        results = {row.code: SignalAnalysisResult(code=row.code, name=row.name, summary='政策与公司信息') for row in kwargs['rows']}
        if kwargs.get('on_results'):
            kwargs['on_results'](results)
        return AnalysisRunResult(success=True, report_markdown='# ' + kwargs['market'] + '\n政策与技术分析', results_by_code=results)

    def run_report(self):
        with patch('task_reports.ElasticsearchReportRepository.from_env', return_value=self.index):
            return self.publish(None, self.db, 'market', 'job-one', ['HK', 'US'], '1d', self.records, 'chain')

    def test_multiple_markets_publish_exactly_one_task_document(self):
        with patch('task_reports.run_signal_analysis_for_market', side_effect=self.analyze):
            result = self.run_report()
        self.assertEqual(len(self.index.documents), 1)
        self.assertEqual(result['report_id'], 'market-job-one')
        self.assertEqual(result['markets'], ['HK', 'US'])
        self.assertIn('HK', result['content_markdown'])
        self.assertIn('US', result['content_markdown'])
        self.assertNotIn('draft', self.db.checkpoint)
        self.assertEqual(self.db.checkpoint['status'], 'stored')

    def test_es_failure_retries_draft_without_any_provider_calls(self):
        self.index.fail = True
        with patch('task_reports.run_signal_analysis_for_market', side_effect=self.analyze):
            with self.assertRaises(RuntimeError):
                self.run_report()
        self.assertIn('draft', self.db.checkpoint)
        self.index.fail = False
        with patch('task_reports.run_signal_analysis_for_market', side_effect=AssertionError('No repeat AI')):
            result = self.run_report()
        self.assertEqual(result['report_id'], 'market-job-one')
        self.assertEqual(self.index.writes, 1)

    def test_existing_es_document_reconciles_lost_database_acknowledgement(self):
        with patch('task_reports.run_signal_analysis_for_market', side_effect=self.analyze):
            result = self.run_report()
        self.db.checkpoint = {'status': 'saving', 'draft': result}
        with patch('task_reports.run_signal_analysis_for_market', side_effect=AssertionError('No repeat AI')):
            self.run_report()
        self.assertEqual(self.index.writes, 1)
        self.assertEqual(self.db.checkpoint['status'], 'stored')

    def test_partial_ai_failure_keeps_successful_results_for_resume(self):
        def analyze(**kwargs):
            if kwargs['market'] == 'US':
                return AnalysisRunResult(success=False, skipped_reason='LLM quota')
            return self.analyze(**kwargs)
        with patch('task_reports.run_signal_analysis_for_market', side_effect=analyze):
            with self.assertRaises(RuntimeError):
                self.run_report()
        self.assertIn('HK', self.db.checkpoint['markdown_by_market'])
        self.assertFalse(self.index.documents)
        with patch('task_reports.run_signal_analysis_for_market', side_effect=self.analyze) as analyze_again:
            self.run_report()
        self.assertEqual([call.kwargs['market'] for call in analyze_again.call_args_list], ['US'])

    def test_empty_candidates_do_not_call_es_or_model(self):
        self.records = {'HK': [], 'US': []}
        with patch('task_reports.run_signal_analysis_for_market', side_effect=AssertionError('No AI')):
            result = self.run_report()
        self.assertIsNone(result)
        self.assertEqual(self.db.checkpoint['status'], 'not_required')
        self.assertFalse(self.index.documents)

    def test_cancelled_attempt_cannot_publish(self):
        self.db.active = False
        with patch('task_reports.run_signal_analysis_for_market', side_effect=self.analyze):
            with self.assertRaises(RuntimeError):
                self.run_report()
        self.assertFalse(self.index.documents)
