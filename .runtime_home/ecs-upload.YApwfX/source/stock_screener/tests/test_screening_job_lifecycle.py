import copy
import os
import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

from rq import SimpleWorker
from rq.job import Job

from db import MarketDatabase, MySqlConfig
from test_screening_market_locks import RedisTestCase
from web import jobs, main, queue
from web.auth import CurrentUser
from web.business import BusinessError


class JobDatabase:
    """Only the database boundary is replaced; Redis/RQ and routes are real."""
    def __init__(self):
        self.jobs = {}
        self.locks = []
        self.tasks = {}
        self.conn = self
        self.snapshot = None

    def begin(self):
        self.snapshot = copy.deepcopy((self.jobs, self.locks, self.tasks))

    def commit(self):
        self.snapshot = None

    def rollback(self):
        if self.snapshot is not None:
            self.jobs, self.locks, self.tasks = self.snapshot
            self.snapshot = None

    def close(self):
        pass

    def init_web_schema(self):
        pass

    def get_screening_run_locks(self, run_date, markets, timeframe, chain_key=None, pool_scope=None):
        return [copy.deepcopy(item) for item in self.locks
                if item['run_date'] == str(run_date) and item['market'] in markets
                and item['timeframe'] == timeframe and item['chain_key'] == chain_key
                and item['pool_scope'] == pool_scope]

    def get_active_screening_jobs(self, run_date, markets, exclude_job_id=None):
        return [copy.deepcopy(item) for item in self.locks
                if item['run_date'] == str(run_date) and item['market'] in markets
                and item['status'] in ('queued', 'running', 'cancelling')
                and item['job_id'] != exclude_job_id
                and self.jobs[item['job_id']]['status'] in ('queued', 'running', 'cancelling')]

    def create_web_screening_job(self, job_id, user_id, markets, timeframe, options, execution_mode='web_backend'):
        self.jobs[job_id] = dict(job_id=job_id, user_id=user_id, markets=markets,
                                 timeframe=timeframe, options=copy.deepcopy(options),
                                 status='queued', task_ids=[], execution_mode=execution_mode)

    def create_screening_run_locks(self, job_id, run_date, markets, timeframe, chain_key, pool_scope):
        for market in markets:
            self.locks = [item for item in self.locks if not (
                item['run_date'] == str(run_date) and item['market'] == market
                and item['timeframe'] == timeframe and item['chain_key'] == chain_key
                and item['pool_scope'] == pool_scope)]
            self.locks.append(dict(job_id=job_id, run_date=str(run_date), market=market,
                                   timeframe=timeframe, chain_key=chain_key, pool_scope=pool_scope,
                                   status='queued'))

    def update_web_screening_job(self, job_id, status, task_ids=None, error_message=None,
                                 finished=False, summary=None, options=None,
                                 expected_status=None, lock_token=None):
        job = self.jobs[job_id]
        if expected_status is not None and job['status'] != expected_status:
            return False
        if lock_token is not None and job['options'].get('screening_lock', {}).get('token') != lock_token:
            return False
        job.update(status=status if status is not None else job['status'], error_message=error_message)
        if task_ids is not None:
            job['task_ids'] = list(task_ids)
        if options is not None:
            job['options'] = copy.deepcopy(options)
        if summary is not None:
            job['summary'] = copy.deepcopy(summary)
        return True

    def update_screening_run_lock(self, job_id, market, status, task_id=None, error_message=None):
        for item in self.locks:
            if item['job_id'] == job_id and item['market'] == market and item['status'] != 'completed':
                item.update(status=status, task_id=task_id, error_message=error_message)

    def get_web_screening_job(self, job_id):
        return copy.deepcopy(self.jobs.get(job_id))

    def get_completed_screening_markets(self, job_id):
        return list({item['market'] for item in self.locks if item['job_id'] == job_id and item['status'] == 'completed'})

    def get_task_report_checkpoint(self, task_type, task_id):
        return copy.deepcopy((self.jobs[task_id].get('summary') or {}).get('report') or {})

    def save_task_report_checkpoint(self, task_type, task_id, checkpoint, lock_token=None):
        job = self.jobs[task_id]
        if job['status'] != 'running' or (lock_token and job['options']['screening_lock']['token'] != lock_token):
            return False
        job.setdefault('summary', {})['report'] = copy.deepcopy(checkpoint)
        return True

    def get_task_by_id(self, task_id):
        return copy.deepcopy(self.tasks.get(task_id))

    def update_task_status(self, task_id, status):
        self.tasks[task_id]['status'] = status

    def count_screening_task_items(self, task_ids, status=None):
        return sum(len(self.get_pending_screening_task_items(task_id)) for task_id in task_ids) if status == 'queued' else 0

    def get_pending_screening_task_items(self, task_id):
        task = self.tasks.get(task_id, {})
        return [{'code': 'HK.00700'}] if task.get('pending', False) else []


class ScreeningJobLifecycleTests(RedisTestCase):
    def setUp(self):
        super().setUp()
        self.db = JobDatabase()
        self.user = CurrentUser(id=7, username='tester', role='admin')
        self.rq_queue = queue.Queue('screening', connection=self.redis)
        patches = [
            patch.dict(os.environ, {'REDIS_URL': 'unix://' + self.socket}),
            patch('web.main.screening_queue', return_value=self.rq_queue, create=True),
            patch('web.main._run_date', return_value=date(2026, 10, 29)),
            patch('web.main.enforce_rate_limit'),
            patch('web.main.resolve_rule_chain', side_effect=lambda db, markets, timeframe, chain_key:
                  dict(chain_key=chain_key or 'chain_one', chain_timeframe='*', chain_name='Test chain')),
            patch('web.main.artifact_dir', return_value=__import__('pathlib').Path(self.directory.name)),
            patch('web.main.mysql_config_from_env', return_value=MySqlConfig(host='unused', port=3306, user='test', password='test', database='test')),
            patch('web.jobs.MarketDatabase', return_value=self.db),
            patch('web.jobs.get_default_screening_params', return_value={}),
            patch('web.jobs.publish_task_report', return_value=None, create=True),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def submit(self, markets=None, chain='chain_one', timeframe='1d', pools=None):
        return main.create_screening_task(
            main.ScreeningTaskRequest(markets=markets or ['HK'], chain_key=chain,
                                      timeframe=timeframe, pool_types=pools or ['best'],
                                      enable_ai_analysis=False, send_feishu=False),
            user=self.user, db=self.db,
        )

    def rq_job(self, response):
        options = self.db.jobs[response['job_id']]['options']
        return Job.fetch(options['rq_job_id'], connection=self.redis)

    def test_creating_job_uses_rq_and_blocks_other_chain_timeframe_pool_and_user(self):
        first = self.submit()
        self.assertEqual(self.rq_queue.count, 1)
        self.assertEqual(self.rq_job(first).func_name, 'web.jobs.run_web_screening_job')
        self.user = CurrentUser(id=9, username='other', role='admin')
        for changes in [dict(chain='chain_two'), dict(timeframe='1wk'), dict(pools=['all_etf'])]:
            with self.assertRaises(BusinessError) as raised:
                self.submit(**changes)
            self.assertEqual(raised.exception.error_code, 'SCREENING_MARKET_BUSY')
        self.assertEqual(len(self.db.jobs), 1)
        self.assertEqual(self.rq_queue.count, 1)

    def test_multi_market_rejection_does_not_create_orphan_job(self):
        self.submit(['US'])
        with self.assertRaises(BusinessError):
            self.submit(['HK', 'US'])
        self.assertEqual(len(self.db.jobs), 1)
        self.assertIsNone(self.redis.get('screening:active:2026-10-29:HK'))

    def test_existing_completed_scope_stays_deduplicated(self):
        self.db.locks.append(dict(job_id='completed', run_date='2026-10-29', market='HK',
                                  timeframe='1d', chain_key='chain_one', pool_scope='best', status='completed'))
        with self.assertRaises(BusinessError) as raised:
            self.submit()
        self.assertEqual(raised.exception.error_code, 'SCREENING_ALREADY_COMPLETED')
        self.assertFalse(self.redis.exists('screening:active:2026-10-29:HK'))

    def test_database_failure_rolls_back_job_and_lock(self):
        with patch.object(self.db, 'create_screening_run_locks', side_effect=RuntimeError('DB failure')):
            with self.assertRaises(BusinessError):
                self.submit()
        self.assertFalse(self.db.jobs)
        self.assertFalse(self.redis.exists('screening:active:2026-10-29:HK'))
        self.assertEqual(self.rq_queue.count, 0)

    def test_enqueue_failure_releases_lock_and_leaves_no_active_job(self):
        with patch.object(self.rq_queue, 'enqueue', side_effect=RuntimeError('enqueue failure')):
            with self.assertRaises(BusinessError):
                self.submit()
        self.assertTrue(all(job['status'] in ('failed', 'cancelled') for job in self.db.jobs.values()))
        self.assertFalse(self.redis.exists('screening:active:2026-10-29:HK'))

    def test_queued_cancel_is_idempotent_and_allows_new_chain(self):
        first = self.submit()
        result = main.cancel_screening_job(first['job_id'], user=self.user, db=self.db)
        self.assertEqual(result['status'], 'cancelled')
        self.assertEqual(main.cancel_screening_job(first['job_id'], user=self.user, db=self.db)['status'], 'cancelled')
        self.assertEqual(self.rq_queue.count, 0)
        self.assertFalse(self.redis.exists('screening:active:2026-10-29:HK'))
        second = self.submit(chain='chain_two')
        self.assertNotEqual(first['job_id'], second['job_id'])

    def test_running_cancel_keeps_lock_until_worker_stop_callback(self):
        first = self.submit()
        job = self.rq_job(first)
        job.set_status('started')
        self.db.jobs[first['job_id']]['status'] = 'running'
        with patch('web.jobs.send_stop_job_command'):
            result = main.cancel_screening_job(first['job_id'], user=self.user, db=self.db)
        self.assertEqual(result['status'], 'cancelling')
        self.assertTrue(self.redis.exists('screening:active:2026-10-29:HK'))
        with self.assertRaises(BusinessError):
            self.submit(chain='chain_two')
        jobs.screening_job_stopped(job, self.redis)
        self.assertEqual(self.db.jobs[first['job_id']]['status'], 'cancelled')
        self.assertFalse(self.redis.exists('screening:active:2026-10-29:HK'))

    def test_unknown_running_execution_is_not_unlocked(self):
        first = self.submit()
        job = self.rq_job(first)
        self.db.jobs[first['job_id']]['status'] = 'running'
        job.delete()
        with self.assertRaises(BusinessError):
            main.cancel_screening_job(first['job_id'], user=self.user, db=self.db)
        self.assertTrue(self.redis.exists('screening:active:2026-10-29:HK'))

    def test_old_stop_callback_does_not_cancel_resumed_attempt(self):
        first = self.submit()
        old = self.rq_job(first)
        main.cancel_screening_job(first['job_id'], user=self.user, db=self.db)
        self.db.tasks['hk-task'] = dict(task_id='hk-task', market='HK', timeframe='1d', status='cancelled', pending=True)
        self.db.jobs[first['job_id']]['task_ids'] = ['hk-task']
        response = main.resume_screening_job(first['job_id'], db=self.db, user=self.user)
        new_lock = self.redis.get('screening:active:2026-10-29:HK')
        jobs.screening_job_stopped(old, self.redis)
        self.assertEqual(response['status'], 'queued')
        self.assertEqual(self.db.jobs[first['job_id']]['status'], 'queued')
        self.assertEqual(self.redis.get('screening:active:2026-10-29:HK'), new_lock)

    def test_resume_competes_for_today_even_when_original_date_differs(self):
        first = self.submit()
        main.cancel_screening_job(first['job_id'], user=self.user, db=self.db)
        self.db.tasks['hk-task'] = dict(task_id='hk-task', market='HK', timeframe='1d', status='cancelled', pending=True)
        self.db.jobs[first['job_id']]['task_ids'] = ['hk-task']
        with patch('web.main._run_date', return_value=date(2026, 10, 30)):
            self.submit(chain='chain_two')
            with self.assertRaises(BusinessError) as raised:
                main.resume_screening_job(first['job_id'], db=self.db, user=self.user)
        self.assertEqual(raised.exception.error_code, 'SCREENING_MARKET_BUSY')

    def test_worker_releases_market_after_its_artifacts_finish(self):
        first = self.submit(['HK', 'US'])
        observations = []
        def run_market(**kwargs):
            market = kwargs['market']
            if market == 'US':
                observations.append(self.redis.exists('screening:active:2026-10-29:HK'))
            task_id = market + '-task'
            self.db.tasks[task_id] = dict(task_id=task_id, market=market, status='completed')
            kwargs['on_task_created'](task_id)
            self.assertIn(task_id, self.db.jobs[first['job_id']]['task_ids'])
            return SimpleNamespace(task_id=task_id, csv_paths=[], market=market, passed=[], error=None, skipped=False)
        with patch('web.jobs.run_market_screening_worker', side_effect=run_market), patch('web.jobs._record_artifacts'):
            SimpleWorker([self.rq_queue], connection=self.redis).work(burst=True, logging_level='CRITICAL')
        self.assertEqual(observations, [1])
        self.assertEqual(self.db.jobs[first['job_id']]['status'], 'completed')
        self.assertFalse(self.redis.exists('screening:active:2026-10-29:US'))

    def test_worker_market_error_is_failed_not_completed(self):
        first = self.submit()
        with patch('web.jobs.run_market_screening_worker', return_value=SimpleNamespace(
            task_id=None, csv_paths=[], market='HK', passed=[], error='market failed', skipped=True,
        )):
            SimpleWorker([self.rq_queue], connection=self.redis).work(burst=True, logging_level='CRITICAL')
        self.assertEqual(self.db.jobs[first['job_id']]['status'], 'failed')
        self.assertFalse(self.redis.exists('screening:active:2026-10-29:HK'))

    def test_dequeued_cancelled_job_never_starts_screening(self):
        first = self.submit()
        rq_job = self.rq_job(first)
        main.cancel_screening_job(first['job_id'], user=self.user, db=self.db)
        with patch('web.jobs.get_current_job', return_value=rq_job), patch('web.jobs.run_market_screening_worker') as run:
            jobs.run_web_screening_job(**rq_job.kwargs)
        run.assert_not_called()

    def test_redis_key_loss_does_not_allow_bypassing_database_active_job(self):
        first = self.submit()
        self.redis.delete('screening:active:2026-10-29:HK')
        with self.assertRaises(BusinessError) as raised:
            self.submit(chain='chain_two')
        self.assertEqual(raised.exception.error_code, 'SCREENING_MARKET_BUSY')
        self.assertIn(first['job_id'], raised.exception.message)
        self.assertEqual(len(self.db.jobs), 1)

    def test_enqueue_error_after_acceptance_cancels_queued_execution(self):
        enqueue = self.rq_queue.enqueue
        def accepted_then_error(*args, **kwargs):
            enqueue(*args, **kwargs)
            raise ConnectionError('lost acknowledgement')
        with patch.object(self.rq_queue, 'enqueue', side_effect=accepted_then_error):
            with self.assertRaises(BusinessError):
                self.submit()
        self.assertEqual(self.rq_queue.count, 0)
        self.assertEqual(next(iter(self.db.jobs.values()))['status'], 'cancelled')
        self.assertFalse(self.redis.exists('screening:active:2026-10-29:HK'))

    def test_abandoned_worker_failure_does_not_release_lock(self):
        first = self.submit()
        rq_job = self.rq_job(first)
        self.db.jobs[first['job_id']]['status'] = 'running'
        error_type = type('AbandonedJobError', (Exception,), {})
        jobs.screening_job_failed(rq_job, self.redis, error_type, error_type('lost worker'), None)
        self.assertEqual(self.db.jobs[first['job_id']]['status'], 'running')
        self.assertTrue(self.redis.exists('screening:active:2026-10-29:HK'))

    def test_child_failed_without_wrapper_error_does_not_mark_market_completed(self):
        first = self.submit()
        def run_market(**kwargs):
            self.db.tasks['failed-child'] = dict(task_id='failed-child', market='HK', status='failed')
            kwargs['on_task_created']('failed-child')
            return SimpleNamespace(task_id='failed-child', csv_paths=[], market='HK', passed=[], error=None, skipped=False)
        with patch('web.jobs.run_market_screening_worker', side_effect=run_market):
            SimpleWorker([self.rq_queue], connection=self.redis).work(burst=True, logging_level='CRITICAL')
        self.assertEqual(self.db.jobs[first['job_id']]['status'], 'failed')
        self.assertEqual(self.db.locks[0]['status'], 'failed')

    def test_empty_pool_is_not_successfully_deduplicated(self):
        first = self.submit()
        with patch('web.jobs.run_market_screening_worker', return_value=SimpleNamespace(
            task_id=None, csv_paths=[], market='HK', passed=[], error=None, skipped=True,
        )):
            SimpleWorker([self.rq_queue], connection=self.redis).work(burst=True, logging_level='CRITICAL')
        self.assertEqual(self.db.jobs[first['job_id']]['status'], 'failed')
        self.submit()

    def test_completed_child_with_failed_report_stage_can_be_resumed(self):
        first = self.submit()
        def run_market(**kwargs):
            task_id = kwargs.get('task_id') or 'complete-child'
            self.db.tasks[task_id] = dict(task_id=task_id, market='HK', status='completed')
            kwargs['on_task_created'](task_id)
            return SimpleNamespace(task_id=task_id, csv_paths=[], market='HK', passed=[], error=None, skipped=False)
        with patch('web.jobs.run_market_screening_worker', side_effect=run_market), \
             patch('web.jobs.publish_task_report', side_effect=RuntimeError('report store unavailable')):
            SimpleWorker([self.rq_queue], connection=self.redis).work(burst=True, logging_level='CRITICAL')
        self.assertEqual(self.db.jobs[first['job_id']]['status'], 'failed')
        main.resume_screening_job(first['job_id'], user=self.user, db=self.db)
        with patch('web.jobs.run_market_screening_worker', side_effect=run_market) as run, patch('web.jobs._record_artifacts'):
            SimpleWorker([self.rq_queue], connection=self.redis).work(burst=True, logging_level='CRITICAL')
        run.assert_not_called()
        self.assertEqual(self.db.jobs[first['job_id']]['task_ids'], ['complete-child'])
        self.assertEqual(self.db.jobs[first['job_id']]['status'], 'completed')
        self.assertIsNone(self.redis.get('screening:active:2026-10-29:HK'))

    def test_task_created_during_cancellation_is_still_recorded(self):
        first = self.submit()
        def run_market(**kwargs):
            self.db.jobs[first['job_id']]['status'] = 'cancelling'
            self.db.tasks['late-child'] = dict(task_id='late-child', market='HK', status='running', pending=True)
            kwargs['on_task_created']('late-child')
            self.assertEqual(self.db.jobs[first['job_id']]['status'], 'cancelling')
            self.assertIn('late-child', self.db.jobs[first['job_id']]['task_ids'])
            raise RuntimeError('stopping')
        with patch('web.jobs.run_market_screening_worker', side_effect=run_market):
            SimpleWorker([self.rq_queue], connection=self.redis).work(burst=True, logging_level='CRITICAL')
        self.assertIn('late-child', self.db.jobs[first['job_id']]['task_ids'])
        self.assertEqual(self.db.tasks['late-child']['status'], 'cancelled')

    def test_multi_market_failure_resumes_only_unfinished_market(self):
        first = self.submit(['HK', 'US'])
        def run_market(**kwargs):
            market = kwargs['market']
            if market == 'US':
                raise RuntimeError('US export failed')
            self.db.tasks['hk-done'] = dict(task_id='hk-done', market='HK', status='completed')
            kwargs['on_task_created']('hk-done')
            return SimpleNamespace(task_id='hk-done', csv_paths=[], passed=[], error=None, skipped=False)
        with patch('web.jobs.run_market_screening_worker', side_effect=run_market), patch('web.jobs._record_artifacts'):
            SimpleWorker([self.rq_queue], connection=self.redis).work(burst=True, logging_level='CRITICAL')
        self.assertEqual(self.db.get_completed_screening_markets(first['job_id']), [])
        checkpoint = self.db.get_task_report_checkpoint('market', first['job_id'])
        self.assertEqual(list(checkpoint['records_by_market']), ['HK'])
        main.resume_screening_job(first['job_id'], user=self.user, db=self.db)
        self.assertIsNotNone(self.redis.get('screening:active:2026-10-29:HK'))
        self.assertIsNotNone(self.redis.get('screening:active:2026-10-29:US'))
        self.assertEqual(self.rq_job(first).kwargs['screening_lock']['markets'], ['HK', 'US'])

        def finish_us(**kwargs):
            self.assertEqual(kwargs['market'], 'US')
            self.db.tasks['us-done'] = dict(task_id='us-done', market='US', status='completed')
            kwargs['on_task_created']('us-done')
            return SimpleNamespace(task_id='us-done', passed=[], error=None, skipped=False)

        with patch('web.jobs.run_market_screening_worker', side_effect=finish_us) as run:
            SimpleWorker([self.rq_queue], connection=self.redis).work(burst=True, logging_level='CRITICAL')
        run.assert_called_once()
        self.assertEqual(self.db.jobs[first['job_id']]['status'], 'completed')
        self.assertIsNone(self.redis.get('screening:active:2026-10-29:HK'))
        self.assertIsNone(self.redis.get('screening:active:2026-10-29:US'))

    def test_enqueue_ack_failure_after_worker_start_waits_for_stop_confirmation(self):
        enqueue = self.rq_queue.enqueue
        def accepted_then_started(*args, **kwargs):
            rq_job = enqueue(*args, **kwargs)
            rq_job.set_status('started')
            self.db.jobs[kwargs['kwargs']['job_id']]['status'] = 'running'
            raise ConnectionError('lost acknowledgement')
        with patch.object(self.rq_queue, 'enqueue', side_effect=accepted_then_started), patch('web.jobs.send_stop_job_command'):
            with self.assertRaises(BusinessError):
                self.submit()
        job = next(iter(self.db.jobs.values()))
        self.assertEqual(job['status'], 'cancelling')
        self.assertTrue(self.redis.exists('screening:active:2026-10-29:HK'))
        jobs.screening_job_stopped(Job.fetch(job['options']['rq_job_id'], connection=self.redis), self.redis)
        self.assertFalse(self.redis.exists('screening:active:2026-10-29:HK'))

    def test_redis_loss_during_enqueue_compensation_returns_business_error_and_keeps_lock(self):
        from redis.exceptions import ConnectionError as RedisConnectionError
        with patch.object(self.rq_queue, 'enqueue', side_effect=RedisConnectionError('Redis down')), \
             patch('web.jobs.Job.fetch', side_effect=RedisConnectionError('Redis still down')):
            with self.assertRaises(BusinessError):
                self.submit()
        self.assertTrue(self.redis.exists('screening:active:2026-10-29:HK'))


class ScreeningAgentIsolationTests(unittest.TestCase):
    def test_agent_cannot_claim_or_mutate_rq_owned_jobs(self):
        db = object.__new__(MarketDatabase)
        db.conn = MagicMock()
        db.get_web_screening_job = Mock(return_value={
            'execution_mode': 'rq', 'options': {'rq_job_id': 'screening-test'},
        })
        self.assertIsNone(db.claim_agent_job('web-job', 'old-agent'))
        db.heartbeat_agent_job('web-job', 'old-agent')
        db.complete_agent_screening_job('web-job', 'completed')
        db.conn.cursor.assert_not_called()

    def test_agent_listing_sql_excludes_rq_jobs(self):
        db = object.__new__(MarketDatabase)
        db.conn = MagicMock()
        cursor = db.conn.cursor.return_value.__enter__.return_value = Mock()
        cursor.fetchall.return_value = []
        db.expire_stale_agent_locks = Mock()
        db.list_pending_agent_jobs()
        sql = cursor.execute.call_args_list[0].args[0]
        self.assertIn("j.execution_mode<>'rq'", sql)
        self.assertIn("'$.rq_job_id'", sql)
