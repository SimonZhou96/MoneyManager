import copy
import json
import unittest
from datetime import date
from decimal import Decimal
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
    """In-memory repository fake for publisher and API tests."""

    def __init__(self, database=None, analyzer=None):
        self.documents = {}
        self.fail = False
        self.writes = 0

    def get(self, report_id):
        return copy.deepcopy(self.documents.get(report_id))

    def save(self, report):
        if self.fail:
            raise RuntimeError('MySQL unavailable')
        self.writes += 1
        self.documents[report['report_id']] = copy.deepcopy(report)

    def search(self, q='', page=1, page_size=20):
        reports = list(self.documents.values())
        if q:
            reports = [report for report in reports if q.lower() in str(report).lower()]
        start = (page - 1) * page_size
        return {'reports': reports[start:start + page_size], 'total': len(reports), 'page': page, 'page_size': page_size}


class TaskReportTests(unittest.TestCase):
    def setUp(self):
        from task_reports import publish_task_report
        self.publish = publish_task_report
        self.db = ReportDatabase()
        self.store = ReportIndex()
        self.records = {'HK': [dict(code='HK.00700', name='腾讯')], 'US': [dict(code='US.AAPL', name='Apple')]}

    def analyze(self, **kwargs):
        results = {
            row.code: SignalAnalysisResult(code=row.code, name=row.name, summary='政策与公司信息',
                                           positive_factors=['业务增长'])
            for row in kwargs['rows']
        }
        if kwargs.get('on_results'):
            kwargs['on_results'](results)
        market_content = {'market': kwargs['market'], 'stocks': [
            {'code': row.code, 'name': row.name, 'analysis': {'summary': '政策与技术分析'}}
            for row in kwargs['rows']
        ]}
        return AnalysisRunResult(success=True, report_content=market_content, results_by_code=results)

    def run_report(self):
        with patch('task_reports.MySqlReportRepository', return_value=self.store):
            return self.publish(None, self.db, 'market', 'job-one', ['HK', 'US'], '1d', self.records, 'chain')

    def test_multiple_markets_publish_one_structured_task_document(self):
        with patch('task_reports.run_signal_analysis_for_market', side_effect=self.analyze):
            result = self.run_report()
        self.assertEqual(len(self.store.documents), 1)
        self.assertEqual(result['report_id'], 'market-job-one')
        self.assertEqual(result['markets'], ['HK', 'US'])
        self.assertEqual(result['content']['schema_version'], 1)
        self.assertEqual([item['market'] for item in result['content']['markets']], ['HK', 'US'])
        self.assertEqual(result['content']['markets'][0]['stocks'][0]['code'], 'HK.00700')
        self.assertNotIn('content_markdown', result)
        self.assertNotIn('draft', self.db.checkpoint)
        self.assertEqual(self.db.checkpoint['status'], 'stored')

    def test_mysql_failure_retries_saved_structured_draft_without_reanalysis(self):
        self.store.fail = True
        with patch('task_reports.run_signal_analysis_for_market', side_effect=self.analyze):
            with self.assertRaisesRegex(Exception, 'MySQL unavailable'):
                self.run_report()
        self.assertIn('content', self.db.checkpoint['draft'])
        self.store.fail = False
        with patch('task_reports.run_signal_analysis_for_market', side_effect=AssertionError('No repeat AI')):
            result = self.run_report()
        self.assertEqual(result['report_id'], 'market-job-one')
        self.assertEqual(self.store.writes, 1)

    def test_existing_mysql_report_reconciles_lost_checkpoint_acknowledgement(self):
        with patch('task_reports.run_signal_analysis_for_market', side_effect=self.analyze):
            result = self.run_report()
        self.db.checkpoint = {'status': 'saving', 'draft': result, 'records_by_market': self.records,
                              'task_date': '2026-10-29', 'markets': ['HK', 'US'], 'timeframe': '1d', 'chain_key': 'chain'}
        with patch('task_reports.run_signal_analysis_for_market', side_effect=AssertionError('No repeat analysis')):
            self.run_report()
        self.assertEqual(self.store.writes, 1)
        self.assertEqual(self.db.checkpoint['status'], 'stored')

    def test_partial_ai_failure_keeps_successful_market_content_for_resume(self):
        def analyze(**kwargs):
            if kwargs['market'] == 'US':
                return AnalysisRunResult(success=False, skipped_reason='LLM quota')
            return self.analyze(**kwargs)
        with patch('task_reports.run_signal_analysis_for_market', side_effect=analyze):
            with self.assertRaisesRegex(RuntimeError, 'LLM quota'):
                self.run_report()
        self.assertIn('HK', self.db.checkpoint['content_by_market'])
        self.assertFalse(self.store.documents)
        with patch('task_reports.run_signal_analysis_for_market', side_effect=self.analyze) as analyze_again:
            self.run_report()
        self.assertEqual([call.kwargs['market'] for call in analyze_again.call_args_list], ['US'])

    def test_empty_candidates_do_not_call_model_or_store(self):
        self.records = {'HK': [], 'US': []}
        with patch('task_reports.run_signal_analysis_for_market', side_effect=AssertionError('No AI')):
            result = self.run_report()
        self.assertIsNone(result)
        self.assertEqual(self.db.checkpoint['status'], 'not_required')
        self.assertFalse(self.store.documents)

    def test_cancelled_attempt_cannot_publish(self):
        self.db.active = False
        with patch('task_reports.run_signal_analysis_for_market', side_effect=self.analyze):
            with self.assertRaisesRegex(RuntimeError, '任务已终止'):
                self.run_report()
        self.assertFalse(self.store.documents)

    def test_legacy_markdown_checkpoint_is_rebuilt_from_saved_analysis(self):
        self.db.checkpoint = {
            'status': 'saving', 'report_id': 'market-job-one', 'records_by_market': self.records,
            'task_date': '2026-10-29', 'markets': ['HK', 'US'], 'timeframe': '1d', 'chain_key': 'chain',
            'markdown_by_market': {'HK': '# old'}, 'results_by_market': {
                market: {row['code']: {'code': row['code'], 'name': row['name'], 'analysis_status': 'success',
                                       'summary': '旧检查点结果'} for row in rows}
                for market, rows in self.records.items()
            },
            'draft': {'content_markdown': '# legacy'},
        }
        with patch('task_reports.MySqlReportRepository', return_value=self.store), \
                patch('task_reports.run_signal_analysis_for_market', side_effect=self.analyze):
            result = self.publish(None, self.db, 'market', 'job-one', ['HK', 'US'], '1d', self.records, 'chain')
        self.assertIn('content', result)
        self.assertNotIn('content_markdown', result)


class ReportStoreTests(unittest.TestCase):
    def test_analyzer_tokenizes_chinese_and_english_and_highlighting_returns_plain_segments(self):
        from report_store import ReportTextAnalyzer, _highlight_segments, _json
        analyzer = ReportTextAnalyzer()
        tokens = analyzer.tokenize('腾讯 Apple earnings')
        self.assertTrue(any('腾讯' in token or token in {'腾讯'} for token in tokens))
        self.assertIn('apple', tokens)
        self.assertIn('earnings', tokens)
        parts = _highlight_segments('<img src=x>腾讯 Apple', tokens)
        self.assertTrue(any(part['matched'] and part['text'] == '腾讯' for part in parts))
        self.assertTrue(any(part['matched'] and part['text'] == 'Apple' for part in parts))
        self.assertEqual(''.join(part['text'] for part in parts), '<img src=x>腾讯 Apple')
        self.assertEqual(_json({'when': date(2026, 11, 4), 'score': float('nan')}),
                         '{"when":"2026-11-04","score":null}')

    def test_mysql_search_uses_fulltext_and_returns_structured_highlights(self):
        from datetime import datetime
        from report_store import MySqlReportRepository

        class Cursor:
            def __init__(self):
                self.calls = []
                self.current_sql = ''

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def execute(self, sql, params=None):
                self.current_sql = sql
                self.calls.append((sql, params))

            def fetchone(self):
                return (1,)

            def fetchall(self):
                return [(
                    'market-one', 'job-one', 'market', '腾讯 Apple 报告', 'Apple earnings',
                    '["HK"]', '["HK.00700"]', '["腾讯"]', 1,
                    datetime(2026, 11, 4, 1, 0), date(2026, 11, 4), '1d', '',
                    '腾讯持续增长，Apple earnings beat。', 1.0, 0,
                )]

        class Connection:
            def __init__(self):
                self.last_cursor = None

            def cursor(self):
                self.last_cursor = Cursor()
                return self.last_cursor

        connection = Connection()
        repository = MySqlReportRepository(SimpleNamespace(conn=connection))
        result = repository.search('腾讯 Apple', 1, 20)

        self.assertEqual(result['total'], 1)
        self.assertEqual(result['reports'][0]['names'], ['腾讯'])
        self.assertIn('title', result['reports'][0]['highlights'])
        self.assertIn('content_text', result['reports'][0]['highlights'])
        count_sql, count_params = connection.last_cursor.calls[0]
        search_sql, search_params = connection.last_cursor.calls[1]
        self.assertIn('MATCH(search_tokens)', count_sql)
        self.assertIn('JSON_CONTAINS(codes_json', count_sql)
        self.assertIn('ORDER BY exact_match DESC, relevance DESC', search_sql)
        self.assertEqual(count_params[0], '腾讯 apple')
        self.assertEqual(search_params[0], '腾讯 apple')

    def test_task_checkpoint_json_accepts_mysql_numeric_and_date_values(self):
        from db import _json_or_none
        serialized = _json_or_none({'market_cap': Decimal('123.45'), 'task_date': date(2026, 11, 4)})
        self.assertEqual(json.loads(serialized), {'market_cap': 123.45, 'task_date': '2026-11-04'})
