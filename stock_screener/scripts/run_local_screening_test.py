"""Loopback-only single-stock test API; never imported by the normal runtime."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import replace
import json
from pathlib import Path
from unittest.mock import patch

from filters import FilterOutput, FilterResult
from rule_engine import RuleExecutionContext, RULE_EXECUTION_CALLBACK_CACHE_KEY
import task_reports


TEST_NOTICE = '【本地测试：技术条件模拟通过，不代表真实行情满足条件】'


@contextmanager
def local_screening_test(codes: list[str]):
    """Simulate technical rule outputs only for this process's explicit list."""
    codes = frozenset(codes)
    execute_rule = RuleExecutionContext.execute
    save_report = task_reports.ElasticsearchReportRepository.save
    analyze_report = task_reports.run_signal_analysis_for_market

    def execute(execution, rule_key):
        metadata = execution.metadata_by_key.get(rule_key)
        if not (execution.stock.code in codes and metadata and metadata.enabled
                and metadata.rule_type == 'strategy' and metadata.strategy_category == 'technical'):
            return execute_rule(execution, rule_key)
        if rule_key in execution.truth_by_key:
            return execution.truth_by_key[rule_key]
        callback = execution.filter_context.get_cache(RULE_EXECUTION_CALLBACK_CACHE_KEY)
        if callable(callback):
            callback({'rule_key': rule_key, 'rule_name': metadata.rule_name,
                      'rule_type': metadata.rule_type, 'strategy_category': metadata.strategy_category})
        output = FilterOutput(
            filter_name=metadata.implementation, result=FilterResult.PASS, reason=TEST_NOTICE,
            details={'rule_key': rule_key, 'local_test': True, 'satisfied': True,
                     'direction': (metadata.params or {}).get('direction') or 'bullish'},
        )
        execution.outputs_by_key[rule_key] = output
        execution.truth_by_key[rule_key] = True
        execution.ordered_outputs.append(output)
        return True

    def analyze(**kwargs):
        kwargs['rows'] = [
            replace(row, conditions_met=TEST_NOTICE + '|' + row.conditions_met)
            if row.code in codes and TEST_NOTICE not in row.conditions_met else row
            for row in kwargs.get('rows') or []
        ]
        return analyze_report(**kwargs)

    def save(store, report):
        if codes.intersection(report.get('codes') or []):
            for key in ('title', 'summary', 'content_markdown', 'content_text'):
                if TEST_NOTICE not in report.get(key, ''):
                    report[key] = TEST_NOTICE + '\n\n' + report.get(key, '')
        return save_report(store, report)

    with patch.object(RuleExecutionContext, 'execute', execute), \
            patch.object(task_reports, 'run_signal_analysis_for_market', analyze), \
            patch.object(task_reports.ElasticsearchReportRepository, 'save', save):
        yield


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--enable-simulated-technicals', action='store_true', required=True)
    parser.add_argument('--watchlist', type=Path, default=root / 'tests/fixtures/local_screening_watchlist.json')
    parser.add_argument('--port', type=int, default=8002)
    args = parser.parse_args()
    data = json.loads(args.watchlist.read_text(encoding='utf-8'))
    codes = data.get('codes')
    if not isinstance(codes, list) or not codes or any(not isinstance(code, str) or not code.strip() for code in codes):
        parser.error('测试名单 codes 必须是非空的完整股票代码列表')
    import uvicorn
    from web.main import app
    print(f'LOCAL TEST ONLY: 技术条件模拟通过名单：{", ".join(codes)}；仅监听 127.0.0.1。')
    with local_screening_test(codes):
        uvicorn.run(app, host='127.0.0.1', port=args.port)


if __name__ == '__main__':
    main()
