import unittest
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from web import reports
from web.auth import CurrentUser, get_db, require_user
from web.business import BusinessError
from web.main import app


class ReportApiTests(unittest.TestCase):
    def setUp(self):
        self.store = MagicMock()
        self.store.search.return_value = {
            'reports': [{'report_id': 'market-one', 'title': '腾讯 AI 报告', 'summary': '降息'}],
            'total': 1, 'page': 1, 'page_size': 20,
        }
        self.store.get.return_value = {
            'report_id': 'market-one', 'content': {'schema_version': 1, 'markets': []},
        }
        self.overrides = dict(app.dependency_overrides)
        app.dependency_overrides[reports.report_repository] = lambda: self.store
        app.dependency_overrides[get_db] = lambda: MagicMock()
        app.dependency_overrides[require_user] = lambda: CurrentUser(id=7, username='test', role='admin')
        self.http = TestClient(app)

    def tearDown(self):
        self.http.close()
        app.dependency_overrides.clear()
        app.dependency_overrides.update(self.overrides)

    def test_search_keeps_pagination_api_and_passes_search_to_repository(self):
        response = self.http.get('/api/reports?q=腾讯&page=2&page_size=10').json()
        self.assertEqual(response['reports'][0]['report_id'], 'market-one')
        self.store.search.assert_called_once_with('腾讯', 2, 10)

    def test_detail_returns_structured_content(self):
        response = self.http.get('/api/reports/market-one').json()
        self.assertEqual(response['content']['schema_version'], 1)
        self.assertNotIn('content_markdown', response)

    def test_store_failure_is_not_reported_as_empty_results(self):
        self.store.search.side_effect = BusinessError('REPORT_STORE_UNAVAILABLE', 'MySQL offline')
        response = self.http.get('/api/reports').json()
        self.assertFalse(response['ok'])
        self.assertEqual(response['error_code'], 'REPORT_STORE_UNAVAILABLE')

    def test_invalid_pagination_is_rejected_by_api(self):
        response = self.http.get('/api/reports?page_size=101').json()
        self.assertFalse(response['ok'])


if __name__ == '__main__':
    unittest.main()
