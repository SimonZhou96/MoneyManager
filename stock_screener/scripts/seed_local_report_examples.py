"""Import frontend report examples into the explicitly confirmed local ES only."""
import argparse
import json
import os
import re
import sys
import time
from urllib.parse import urlparse

from task_reports import ElasticsearchReportRepository, REPORT_INDEX


EXAMPLE_PREFIX = '【本地搜索示例】'


def seed_local_report_examples(documents: list[dict]) -> dict:
    target = urlparse(os.getenv('ELASTICSEARCH_URL', ''))
    if target.scheme != 'https' or target.hostname not in {'localhost', '127.0.0.1', '::1'} or target.port != 9201:
        raise ValueError('只允许写入本地 HTTPS ES（9201），拒绝远端或其他实例')
    if not isinstance(documents, list) or not 1 <= len(documents) <= 100:
        raise ValueError('示例报告必须是 1–100 条 JSON 数组')
    seen = set()
    prepared = []
    for index, source in enumerate(documents):
        task_id = source.get('task_id', '')
        task_type = source.get('task_type', '')
        if not re.fullmatch(r'preview-task-\d{3}', task_id) or task_type not in {'market', 'single_stock', 'custom_list', 'scheduled'}:
            raise ValueError('只接受 preview-task- 开头的前端示例任务')
        if source.get('report_id') != f'{task_type}-{task_id}' or source['report_id'] in seen:
            raise ValueError('报告 ID 与示例任务不一致或重复')
        seen.add(source['report_id'])
        report = dict(source)
        report['title'] = EXAMPLE_PREFIX + source['title']
        report['summary'] = EXAMPLE_PREFIX + source['summary']
        topic = ('人工智能', '数字经济', '新能源', '消费复苏')[index % 4]
        report['content_markdown'] = source['content_markdown'] + f'\n\n## 搜索高亮测试\n\n正文关键词：{topic}。本段仅供搜索高亮测试，不是事实或投资判断。'
        report['content_text'] = re.sub(r'[#*`|>]', ' ', report['content_markdown'])
        report['company_names'] = ' '.join(source['names'])
        report['revision'] = time.time_ns()
        prepared.append(report)
    store = ElasticsearchReportRepository.from_env()
    try:
        pending = []
        for report in prepared:
            existing = store.get(report['report_id'])
            if existing:
                if not existing.get('title', '').startswith(EXAMPLE_PREFIX) or existing.get('task_id') != report['task_id']:
                    raise ValueError(f"拒绝覆盖已有非示例报告：{report['report_id']}")
            else:
                pending.append(report)
        for report in pending:
            # Atomic create: even a concurrent writer cannot be overwritten.
            store.client.create(index=REPORT_INDEX, id=report['report_id'], document=report, refresh='wait_for')
        return {'inserted': len(pending), 'skipped': len(prepared) - len(pending),
                'report_ids': [report['report_id'] for report in prepared]}
    finally:
        store.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--confirm-local-es', action='store_true', required=True)
    parser.parse_args()
    print(json.dumps(seed_local_report_examples(json.load(sys.stdin)), ensure_ascii=False))
