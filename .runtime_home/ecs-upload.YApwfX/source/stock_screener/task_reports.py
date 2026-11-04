"""Task-owned AI reports, resumable generation, and Elasticsearch storage."""
from __future__ import annotations

import os
import re
import time
from datetime import date, datetime, timezone
from urllib.parse import quote

from elasticsearch import Elasticsearch, NotFoundError

from feishu_notifier import send_feishu_text
from signal_analysis.models import ScreeningSignalRow, SignalAnalysisResult
from signal_analysis.service import run_signal_analysis_for_market
from web.business import BusinessError


REPORT_INDEX = 'screening_reports_v1'
REPORT_MAPPING = {
    'dynamic': 'strict',
    'properties': {
        **{key: {'type': 'keyword'} for key in ('report_id', 'task_id', 'task_type', 'markets', 'codes', 'names', 'timeframe', 'chain_key')},
        **{key: {'type': 'text', 'analyzer': 'cjk'} for key in ('title', 'summary', 'content_text', 'company_names')},
        'content_markdown': {'type': 'text', 'index': False},
        'generated_at': {'type': 'date'}, 'task_date': {'type': 'date'},
        'passed_count': {'type': 'integer'},
        'revision': {'type': 'long'},
    },
}


def report_highlight_segments(fragment: str) -> list[dict]:
    """Convert ES markers to plain text parts; never return executable HTML."""
    segments = []
    for part in re.split(r'(\ue000[^\ue001]*\ue001)', fragment):
        if not part:
            continue
        matched = part.startswith('\ue000') and part.endswith('\ue001')
        segments.append({'text': part[1:-1] if matched else part, 'matched': matched})
    return segments


class ElasticsearchReportRepository:
    def __init__(self, client: Elasticsearch):
        self.client = client

    @classmethod
    def from_env(cls):
        url = os.getenv('ELASTICSEARCH_URL', '').strip()
        key = os.getenv('ELASTICSEARCH_API_KEY', '').strip()
        ca = os.getenv('ELASTICSEARCH_CA_CERT', '').strip()
        if not url or not key or not ca:
            raise BusinessError('REPORT_STORE_UNAVAILABLE', '报告 ES 地址、API Key 或 CA 未配置')
        return cls(Elasticsearch(url, api_key=key, ca_certs=ca, verify_certs=True,
                                 request_timeout=15, max_retries=0))

    def close(self):
        self.client.close()

    def initialize(self):
        if not self.client.indices.exists(index=REPORT_INDEX):
            self.client.indices.create(index=REPORT_INDEX, mappings=REPORT_MAPPING,
                                       settings={'number_of_shards': 1, 'number_of_replicas': 0})

    def save(self, report: dict):
        try:
            self.client.index(index=REPORT_INDEX, id=report['report_id'], document=report, refresh='wait_for',
                              version=report.get('revision', 1), version_type='external')
        except Exception as exc:
            raise BusinessError('REPORT_STORE_UNAVAILABLE', '报告写入 ES 失败，已保留草稿，可重试入库') from exc

    def get(self, report_id: str):
        try:
            return self.client.get(index=REPORT_INDEX, id=report_id)['_source']
        except NotFoundError as exc:
            if getattr(exc, 'body', {}).get('found') is False:
                return None
            raise BusinessError('REPORT_STORE_UNAVAILABLE', '报告索引未初始化或不可用') from exc
        except Exception as exc:
            raise BusinessError('REPORT_STORE_UNAVAILABLE', '报告 ES 暂不可用，请稍后重试') from exc

    def search(self, q: str = '', page: int = 1, page_size: int = 20):
        if page < 1 or not 1 <= page_size <= 100 or page * page_size > 10000:
            raise BusinessError('INVALID_REPORT_PAGINATION', '报告分页参数超出范围')
        query = {'match_all': {}}
        highlight = None
        sort = [{'generated_at': 'desc'}, {'report_id': 'asc'}]
        if q.strip():
            query = {'bool': {'should': [
                {'multi_match': {'query': q.strip(), 'fields': ['title^3', 'summary^2', 'company_names^2', 'content_text']}},
                {'term': {'codes': {'value': q.strip(), 'case_insensitive': True}}},
                {'term': {'task_id': q.strip()}},
            ], 'minimum_should_match': 1}}
            sort.insert(0, {'_score': 'desc'})
            highlight = {
                'type': 'unified', 'pre_tags': ['\ue000'], 'post_tags': ['\ue001'],
                'max_analyzed_offset': 1000000,
                'fields': {
                    **{field: {'number_of_fragments': 0} for field in
                       ('title', 'summary', 'company_names', 'codes', 'task_id')},
                    'content_text': {'fragment_size': 160, 'number_of_fragments': 2},
                },
            }
        try:
            result = self.client.search(index=REPORT_INDEX, query=query, sort=sort,
                                        from_=(page - 1) * page_size, size=page_size, track_total_hits=True,
                                        highlight=highlight,
                                        source_excludes=['content_markdown', 'content_text'])
            reports = []
            for hit in result['hits']['hits']:
                report = dict(hit['_source'])
                if q.strip():
                    report['highlights'] = {
                        field: [report_highlight_segments(fragment) for fragment in fragments]
                        for field, fragments in hit.get('highlight', {}).items()
                    }
                reports.append(report)
            return {'reports': reports,
                    'total': result['hits']['total']['value'], 'page': page, 'page_size': page_size}
        except Exception as exc:
            raise BusinessError('REPORT_STORE_UNAVAILABLE', '报告搜索暂不可用，请稍后重试') from exc


def publish_task_report(mysql_config, db, task_type: str, task_id: str, markets: list[str], timeframe: str,
                        records_by_market: dict, chain_key: str = '', lock_token: str | None = None):
    """Checkpoint every successful AI batch; after ES failure only retry the saved draft."""
    checkpoint = db.get_task_report_checkpoint(task_type, task_id) or {}
    report_id = f'{task_type}-{task_id}'

    def save_checkpoint():
        if not db.save_task_report_checkpoint(task_type, task_id, checkpoint, lock_token=lock_token):
            raise RuntimeError('任务已终止或执行令牌已变化，停止生成报告')

    checkpoint.setdefault('records_by_market', records_by_market)
    checkpoint.setdefault('task_date', date.today().isoformat())
    checkpoint.setdefault('markets', markets)
    checkpoint.setdefault('timeframe', timeframe)
    checkpoint.setdefault('chain_key', chain_key)
    if checkpoint.get('status') == 'stored':
        save_checkpoint()
        store = ElasticsearchReportRepository.from_env()
        try:
            existing = store.get(report_id)
            if existing:
                return existing
            raise RuntimeError('已完成任务的 ES 报告缺失，请从备份恢复')
        finally:
            store.close()
    records_by_market = checkpoint['records_by_market']
    count = sum(len(rows) for rows in records_by_market.values())
    if not count:
        checkpoint = {'status': 'not_required', 'report_id': None, 'reason': '没有入选股票'}
        save_checkpoint()
        return None
    checkpoint['report_id'] = report_id
    checkpoint.setdefault('results_by_market', {})
    checkpoint.setdefault('markdown_by_market', {})
    save_checkpoint()
    store = ElasticsearchReportRepository.from_env()
    try:
        if checkpoint.get('status') in {'saving', 'stored'}:
            existing = store.get(report_id)
            if existing:
                checkpoint = {'status': 'stored', 'report_id': report_id}
                save_checkpoint()
                return existing
        if 'draft' not in checkpoint:
            checkpoint['status'] = 'analyzing'
            save_checkpoint()
            for market, records in records_by_market.items():
                if not records or market in checkpoint['markdown_by_market']:
                    continue
                rows = [ScreeningSignalRow.from_record(row, i, market) for i, row in enumerate(records)]

                def record_results(results):
                    checkpoint['results_by_market'][market] = {
                        code: {**result.to_db_row(task_id, market, date.fromisoformat(checkpoint['task_date'])),
                               'check_date': checkpoint['task_date']}
                        for code, result in results.items() if result.analysis_status == 'success'}
                    save_checkpoint()

                initial = {code: SignalAnalysisResult.from_llm_item(item, model=item.get('model') or 'checkpoint')
                           for code, item in checkpoint['results_by_market'].get(market, {}).items()}
                result = run_signal_analysis_for_market(
                    mysql_config=mysql_config, task_id=f'{task_id}:{market}', market=market, rows=rows,
                    check_date=date.fromisoformat(checkpoint['task_date']), timeframe=checkpoint['timeframe'],
                    chain_key=checkpoint['chain_key'], enabled=True, initial_results=initial, on_results=record_results,
                )
                record_results(result.results_by_code)
                if not result.success or not result.report_markdown:
                    raise RuntimeError(result.skipped_reason or 'AI 报告未完整生成')
                checkpoint['markdown_by_market'][market] = result.report_markdown
                save_checkpoint()
            markdown = '\n\n'.join([f'# 任务 AI 选股报告\n\n任务：{task_id}\n\n入选数量：{count}',
                                        *[f'## {market}\n\n{text}' for market, text in checkpoint['markdown_by_market'].items()]])
            names = [row.get('name') or row['code'] for records in records_by_market.values() for row in records]
            codes = sorted({alias for records in records_by_market.values() for row in records
                            for alias in (row['code'], row['code'].split('.')[-1])})
            checkpoint['draft'] = dict(report_id=report_id, task_id=task_id, task_type=task_type,
                                       title=f'AI 选股报告 · {task_id}', summary=f"{checkpoint['task_date']}，入选 {count} 只：" + '、'.join(names[:8]),
                                       markets=checkpoint['markets'], timeframe=checkpoint['timeframe'], chain_key=checkpoint['chain_key'],
                                       codes=codes, names=names, company_names=' '.join(names), passed_count=count,
                                       task_date=checkpoint['task_date'], generated_at=datetime.now(timezone.utc).isoformat(),
                                       content_markdown=markdown, content_text=re.sub(r'[#*`|>]', ' ', markdown))
        checkpoint['status'] = 'saving'
        checkpoint['draft']['revision'] = time.time_ns()
        save_checkpoint()
        store.save(checkpoint['draft'])
        report = checkpoint['draft']
        checkpoint = {'status': 'stored', 'report_id': report_id}
        save_checkpoint()
        return report
    finally:
        store.close()


def notify_task_report(report: dict | None, task_id: str):
    webhook = os.getenv('FEISHU_WEBHOOK_URL', '').strip()
    if not webhook:
        return
    text = f'选股任务 {task_id} 已完成'
    if report:
        text += f"\n入选 {report['passed_count']} 只\n报告 ID：{report['report_id']}"
        url = os.getenv('WEB_PUBLIC_URL', '').strip().rstrip('/')
        if url:
            text += f"\n{url}/?page=reports&report_id={quote(report['report_id'], safe='')}"
    else:
        text += '\n没有入选股票，未生成 AI 报告'
    return send_feishu_text(webhook, text)
