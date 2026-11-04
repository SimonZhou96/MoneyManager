import unittest
from unittest.mock import MagicMock

from fastapi.testclient import TestClient
from task_reports import ElasticsearchReportRepository
from web import reports
from web.auth import CurrentUser, get_db, require_user
from web.main import app


class ReportApiTests(unittest.TestCase):
    def setUp(self):
        self.client = MagicMock()
        self.store = ElasticsearchReportRepository(self.client)
        self.client.search.return_value = {'hits': {'total': {'value': 1}, 'hits': [
            {'_source': {'report_id': 'market-one', 'title': '腾讯 AI 报告', 'summary': '降息'}}]}}
        self.client.get.return_value = {'_source': {'report_id': 'market-one', 'content_markdown': '# 分析'}}
        self.overrides = dict(app.dependency_overrides)
        app.dependency_overrides[reports.report_repository] = lambda: self.store
        app.dependency_overrides[get_db] = lambda: MagicMock()
        app.dependency_overrides[require_user] = lambda: CurrentUser(id=7, username='test', role='admin')
        self.http = TestClient(app)

    def tearDown(self):
        self.http.close()
        app.dependency_overrides.clear()
        app.dependency_overrides.update(self.overrides)

    def test_search_returns_metadata_and_uses_chinese_fulltext_and_exact_codes(self):
        response = self.http.get('/api/reports?q=腾讯&page=2&page_size=10').json()
        self.assertEqual(response['reports'][0]['report_id'], 'market-one')
        request = self.client.search.call_args.kwargs
        self.assertEqual(request['from_'], 10)
        self.assertIn('content_markdown', request['source_excludes'])
        self.assertIn('腾讯', str(request['query']))

    def test_detail_returns_stored_markdown(self):
        response = self.http.get('/api/reports/market-one').json()
        self.assertEqual(response['content_markdown'], '# 分析')

    def test_search_returns_es_highlights_as_safe_text_segments(self):
        self.client.search.return_value['hits']['hits'][0]['highlight'] = {
            'title': ['\ue000腾讯\ue001 AI 报告'],
            'content_text': ['<img src=x onerror=alert(1)>政策支持\ue000腾讯\ue001发展'],
        }
        response = self.http.get('/api/reports?q=腾讯').json()
        highlights = response['reports'][0].get('highlights')
        self.assertIsNotNone(highlights)
        self.assertEqual(highlights['title'][0], [
            {'text': '腾讯', 'matched': True}, {'text': ' AI 报告', 'matched': False},
        ])
        self.assertEqual(highlights['content_text'][0][0]['text'], '<img src=x onerror=alert(1)>政策支持')
        self.assertFalse(highlights['content_text'][0][0]['matched'])
        request = self.client.search.call_args.kwargs
        self.assertIn('content_text', request['highlight']['fields'])
        self.assertEqual(request['highlight']['pre_tags'], ['\ue000'])

    def test_empty_search_does_not_request_highlight(self):
        response = self.http.get('/api/reports').json()
        self.assertNotIn('highlights', response['reports'][0])
        self.assertIsNone(self.client.search.call_args.kwargs.get('highlight'))

    def test_store_failure_is_not_reported_as_empty_results(self):
        self.client.search.side_effect = RuntimeError('ES offline')
        response = self.http.get('/api/reports').json()
        self.assertFalse(response['ok'])
        self.assertEqual(response['error_code'], 'REPORT_STORE_UNAVAILABLE')

    def test_invalid_pagination_is_rejected(self):
        response = self.http.get('/api/reports?page_size=101').json()
        self.assertFalse(response['ok'])

    def test_persistence_uses_deterministic_id_and_wait_for(self):
        self.store.save({'report_id': 'market-one', 'title': '报告'})
        request = self.client.index.call_args.kwargs
        self.assertEqual(request['id'], 'market-one')
        self.assertEqual(request['refresh'], 'wait_for')
