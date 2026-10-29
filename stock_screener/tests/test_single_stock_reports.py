import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

from filters import StockInfo
from signal_analysis.models import AnalysisRunResult, SignalAnalysisResult
from test_task_reports import ReportDatabase, ReportIndex
from web.auth import CurrentUser, require_user
from web.main import app, get_single_stock
from web.reports import report_repository
from web.single_stock import SingleStockRequest, run_single_stock_analysis


class SingleRunDatabase(ReportDatabase):
    def __init__(self):
        super().__init__()
        self.run = {}
        self.states = []

    def init_web_schema(self):
        pass

    def create_single_stock_run(self, item):
        self.run = dict(item)
        self.states.append(item['status'])

    def insert_single_stock_rule_details(self, *args):
        pass

    def finish_single_stock_run(self, run_id, passed, status, data_source, **kwargs):
        self.run.update(status=status, passed=passed)
        self.states.append(status)

    def close(self):
        pass

    def claim_report_retry(self, task_type, task_id, expected_status, status='queued'):
        if self.run['status'] != expected_status:
            return False
        self.run['status'] = status
        self.states.append(status)
        return True

    def finish_report_retry(self, task_type, task_id, status, error_message=None):
        self.run['status'] = status
        self.states.append(status)


class SingleStockReportTests(unittest.TestCase):
    def setUp(self):
        self.db = SingleRunDatabase()
        self.index = ReportIndex()
        self.engine = SimpleNamespace(
            requires_market_intel_macro_score=lambda: False,
            requires_signal_analysis=lambda: False,
            evaluate_stock=lambda *args, **kwargs: SimpleNamespace(passed=True, filter_outputs=[]),
            metadata=[], chain_config=SimpleNamespace(chain_key='technical', timeframe='1d', chain_name='技术'),
        )
        self.addCleanup(patch.stopall)
        patch('web.single_stock.MarketDatabase', return_value=self.db).start()
        patch('web.single_stock._load_stock_info', return_value=StockInfo(market='US', code='US.AAPL', name='Apple')).start()
        patch('web.single_stock._fetch_single_kline', return_value=(None, 'fixture')).start()
        patch('web.single_stock.create_rule_engine_from_db', return_value=self.engine).start()
        patch('task_reports.ElasticsearchReportRepository.from_env', return_value=self.index).start()
        patch('task_reports.run_signal_analysis_for_market', side_effect=self.analyze).start()

    def analyze(self, **kwargs):
        self.assertEqual(self.db.run['status'], 'running')
        return AnalysisRunResult(success=True, report_markdown='# Apple\n宏观政策、公司信息及技术风险',
                                 results_by_code={'US.AAPL': SignalAnalysisResult(code='US.AAPL', name='Apple')})

    def run_single(self):
        return run_single_stock_analysis(None, SingleStockRequest('US', 'AAPL', '1d', run_id='single-one'))

    def test_single_task_finishes_only_after_report_is_queryable(self):
        result = self.run_single()
        self.assertEqual(self.db.states, ['running', 'completed'])
        self.assertEqual(result.get('report'), {'status': 'stored', 'report_id': 'single_stock-single-one'})
        app.dependency_overrides[require_user] = lambda: CurrentUser(id=1, username='tester', role='admin')
        app.dependency_overrides[report_repository] = lambda: self.index
        try:
            response = TestClient(app).get('/api/reports/single_stock-single-one')
        finally:
            app.dependency_overrides.pop(require_user, None)
            app.dependency_overrides.pop(report_repository, None)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['task_type'], 'single_stock')
        self.assertEqual(response.json()['codes'], ['AAPL', 'US.AAPL'])
        self.assertIn('技术风险', response.json()['content_markdown'])

    def test_single_es_failure_keeps_draft_and_never_completes(self):
        self.index.fail = True
        with self.assertRaisesRegex(RuntimeError, 'ES unavailable'):
            self.run_single()
        self.assertEqual(self.db.states, ['running', 'failed'])
        self.assertIn('draft', self.db.checkpoint)
        self.assertEqual(self.db.checkpoint['report_id'], 'single_stock-single-one')
        self.assertFalse(self.index.documents)

    def test_single_es_retry_saves_same_report_without_rescreening_or_model(self):
        from web.jobs import retry_task_report
        self.index.fail = True
        with self.assertRaises(RuntimeError):
            self.run_single()
        self.index.fail = False
        self.db.run['status'] = 'queued'
        with patch('web.jobs.MarketDatabase', return_value=self.db), \
                patch('task_reports.run_signal_analysis_for_market', side_effect=AssertionError('No repeat analysis')):
            retry_task_report(None, 'single_stock', 'single-one')
        self.assertEqual(self.db.run['status'], 'completed')
        self.assertTrue(self.db.run['passed'])
        self.assertEqual(list(self.index.documents), ['single_stock-single-one'])

    def test_single_rejected_stock_finishes_without_report_or_model(self):
        self.engine.evaluate_stock = lambda *args, **kwargs: SimpleNamespace(passed=False, filter_outputs=[])
        with patch('task_reports.run_signal_analysis_for_market', side_effect=AssertionError('No report LLM')):
            result = self.run_single()
        self.assertFalse(result['passed'])
        self.assertEqual((result.get('report') or {}).get('status'), 'not_required')
        self.assertFalse(self.index.documents)

    def test_single_task_detail_exposes_report_status_not_unfinished_draft(self):
        from unittest.mock import Mock
        checkpoint = {'status': 'saving', 'report_id': 'single_stock-single-one',
                      'draft': {'content_markdown': 'unpublished'}, 'records_by_market': {'US': []}}
        db = Mock()
        db.get_single_stock_run.return_value = {
            'run_id': 'single-one', 'status': 'failed', 'report': checkpoint,
            'result_json': {'report': checkpoint, 'code': 'US.AAPL'},
        }
        result = get_single_stock('single-one', db=db)
        self.assertEqual(result['report'], {'status': 'saving', 'report_id': 'single_stock-single-one'})
        self.assertNotIn('draft', result['result_json']['report'])
        self.assertEqual(result['result_json']['code'], 'US.AAPL')
