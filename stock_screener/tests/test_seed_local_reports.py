import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from test_task_reports import ReportIndex


class SeedLocalReportsTests(unittest.TestCase):
    def setUp(self):
        self.store = ReportIndex()
        self.store.client = SimpleNamespace(create=lambda **kwargs: self.store.save(kwargs['document']))
        self.documents = [{
            'report_id': 'single_stock-preview-task-001', 'task_id': 'preview-task-001', 'task_type': 'single_stock',
            'title': '腾讯示例', 'summary': '政策观察', 'markets': ['HK'], 'codes': ['HK.00700'], 'names': ['腾讯'],
            'passed_count': 1, 'generated_at': '2026-10-29T08:30:00+08:00', 'task_date': '2026-10-29',
            'content_markdown': '# MOCK 示例\n技术条件模拟通过',
        }]

    def seed(self, documents=None, url='https://localhost:9201'):
        from scripts.seed_local_report_examples import seed_local_report_examples
        with patch.dict(os.environ, {'ELASTICSEARCH_URL': url}), \
                patch('scripts.seed_local_report_examples.ElasticsearchReportRepository.from_env', return_value=self.store):
            return seed_local_report_examples(documents if documents is not None else self.documents)

    def test_local_import_is_marked_and_repeat_does_not_overwrite(self):
        result = self.seed()
        self.assertEqual(result['inserted'], 1)
        self.assertEqual(self.seed()['skipped'], 1)
        self.assertEqual(self.store.writes, 1)
        stored = self.store.documents['single_stock-preview-task-001']
        self.assertTrue(stored['title'].startswith('【本地搜索示例】'))
        self.assertIn('人工智能', stored['content_text'])
        self.assertNotIn('revision', self.documents[0])

    def test_remote_es_is_refused(self):
        with self.assertRaisesRegex(ValueError, '本地'):
            self.seed(url='https://es.example.com:9201')
        self.assertEqual(self.store.writes, 0)

    def test_all_ids_are_checked_before_any_write(self):
        second = dict(self.documents[0], task_id='preview-task-002', report_id='single_stock-preview-task-002')
        self.store.documents[second['report_id']] = dict(second, title='真实报告，不允许覆盖')
        with self.assertRaisesRegex(ValueError, '覆盖'):
            self.seed([self.documents[0], second])
        self.assertEqual(self.store.writes, 0)

    def test_non_fixture_task_id_is_refused(self):
        with self.assertRaises(ValueError):
            self.seed([dict(self.documents[0], task_id='actual-task')])
        self.assertEqual(self.store.writes, 0)
