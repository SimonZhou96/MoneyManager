from __future__ import annotations

import hashlib
import os
from datetime import date
from pathlib import Path
from typing import List

from redis import Redis
from rq import Queue, get_current_job
from rq.command import send_stop_job_command
from rq.exceptions import NoSuchJobError
from rq.job import Callback, Job

from db import MarketDatabase, MySqlConfig
from feishu_notifier import send_screening_result
from market import market_label, normalize_market
from scheduled_daily_job import get_default_screening_params, run_market_screening_worker
from task_reports import notify_task_report, publish_task_report
from .business import BusinessError
from .queue import release_screening_locks


def finish_web_screening_job(
    mysql_config: MySqlConfig, job_id: str, screening_lock: dict, connection: Redis,
    status: str, error_message: str | None = None,
) -> None:
    """Finalize one execution attempt; stale RQ callbacks cannot modify a resumed attempt."""
    db = MarketDatabase(mysql_config)
    try:
        db.conn.begin()
        job = db.get_web_screening_job(job_id)
        if not job or job['options'].get('screening_lock', {}).get('token') != screening_lock['token']:
            db.conn.rollback()
            return
        if job['status'] not in {'completed', 'failed', 'cancelled'}:
            if job['status'] == 'cancelling':
                status = 'cancelled'
            if not db.update_web_screening_job(job_id, status, error_message=error_message, finished=True,
                                               expected_status=job['status'], lock_token=screening_lock['token']):
                db.conn.rollback()
                return
            for market in screening_lock['markets']:
                db.update_screening_run_lock(job_id, market, status, error_message=error_message)
            for task_id in job.get('task_ids') or []:
                task = db.get_task_by_id(task_id)
                if task and task.get('status') not in {'completed', 'failed', 'cancelled'}:
                    db.update_task_status(task_id, status)
        db.conn.commit()
        release_screening_locks(connection, job_id, screening_lock)
    except Exception:
        db.conn.rollback()
        raise
    finally:
        db.close()


def screening_job_stopped(job: Job, connection: Redis) -> None:
    finish_web_screening_job(job.kwargs['mysql_config'], job.kwargs['job_id'],
                             job.kwargs['screening_lock'], connection, 'cancelled')


def screening_job_failed(job: Job, connection: Redis, exc_type, exc_value, traceback) -> None:
    # An abandoned worker is not proof that its execution process has exited.
    if exc_type.__name__ == 'AbandonedJobError':
        return
    finish_web_screening_job(job.kwargs['mysql_config'], job.kwargs['job_id'],
                             job.kwargs['screening_lock'], connection, 'failed', str(exc_value))


def stop_web_screening_job(db: MarketDatabase, job: dict, connection: Redis) -> str:
    options = job.get('options') or {}
    lock = options.get('screening_lock')
    if not lock or not options.get('rq_job_id'):
        raise BusinessError('SCREENING_STOP_UNCONFIRMED', '旧执行器任务需要先确认进程停止，不能自动释放名额')
    job_id = job['job_id']
    try:
        rq_job = Job.fetch(options['rq_job_id'], connection=connection)
    except NoSuchJobError:
        rq_job = None
    if job['status'] == 'queued':
        # A dequeued worker must win queued -> running before doing any stock work.
        db.conn.begin()
        try:
            cancelled = db.update_web_screening_job(job_id, 'cancelled', finished=True,
                                                   expected_status='queued', lock_token=lock['token'])
            if cancelled:
                for market in lock['markets']:
                    db.update_screening_run_lock(job_id, market, 'cancelled')
            db.conn.commit()
        except Exception:
            db.conn.rollback()
            raise
        if cancelled:
            if rq_job is not None:
                rq_job.cancel()
            release_screening_locks(connection, job_id, lock)
            return 'cancelled'
        job = db.get_web_screening_job(job_id)
    if job['status'] in {'completed', 'failed', 'cancelled'}:
        release_screening_locks(connection, job_id, lock)
        return job['status']
    if rq_job is None:
        raise BusinessError('SCREENING_STOP_UNCONFIRMED', '无法确认 RQ 执行进程状态，保留任务锁，请核实 worker 后重试')
    rq_status = rq_job.get_status(refresh=True)
    if rq_status in {'finished', 'stopped', 'canceled'}:
        screening_job_stopped(rq_job, connection)
        return db.get_web_screening_job(job_id)['status']
    if rq_status == 'failed':
        if 'AbandonedJobError' in (rq_job.exc_info or ''):
            raise BusinessError('SCREENING_STOP_UNCONFIRMED', 'worker 异常失联，尚未确认执行进程停止，保留任务锁')
        screening_job_stopped(rq_job, connection)
        return db.get_web_screening_job(job_id)['status']
    db.update_web_screening_job(job_id, 'cancelling', expected_status=job['status'], lock_token=lock['token'])
    if rq_status == 'started':
        send_stop_job_command(connection, rq_job.id)
    return 'cancelling'


def enqueue_web_screening_job(
    queue: Queue, db: MarketDatabase, mysql_config: MySqlConfig, job_id: str,
    artifact_root: str, screening_lock: dict, resume: bool = False,
) -> None:
    job = db.get_web_screening_job(job_id)
    options = job['options']
    kwargs = dict(mysql_config=mysql_config, job_id=job_id, artifact_root=artifact_root, screening_lock=screening_lock)
    if not resume:
        kwargs.update(markets=job['markets'], timeframe=job['timeframe'],
                      send_feishu=options['send_feishu'],
                      chain_key=options['chain_key'], pool_types=options['pool_types'])
    try:
        queue.enqueue(resume_web_screening_job if resume else run_web_screening_job,
                      kwargs=kwargs, job_id=options['rq_job_id'],
                      job_timeout=int(os.getenv('WEB_JOB_TIMEOUT_SEC', '21600')),
                      on_failure=Callback(screening_job_failed), on_stopped=Callback(screening_job_stopped))
    except Exception as exc:
        # Enqueue may have reached Redis before a connection error. Close the DB
        # execution gate, or request/confirm a stop, before releasing any locks.
        try:
            stop_web_screening_job(db, db.get_web_screening_job(job_id), queue.connection)
        except Exception as stop_error:
            raise BusinessError('SCREENING_STOP_UNCONFIRMED', f'任务 {job_id} 入队状态未确认，保留市场锁，请核实 worker 后终止') from stop_error
        raise BusinessError('SCREENING_ENQUEUE_FAILED', 'RQ 入队失败，任务已取消或正在确认停止') from exc


def run_web_screening_job(
    mysql_config: MySqlConfig,
    job_id: str,
    markets: List[str],
    timeframe: str,
    artifact_root: str,
    enable_ai_analysis: bool = True,
    send_feishu: bool = False,
    chain_key: str | None = None,
    pool_types: List[str] | None = None,
    screening_lock: dict | None = None,
    resume: bool = False,
) -> None:
    rq_job = get_current_job()
    if not screening_lock or rq_job is None:
        raise RuntimeError('全市场筛选只能通过持有市场锁的 RQ worker 执行')
    db = MarketDatabase(mysql_config)
    if not db.update_web_screening_job(job_id, 'running', expected_status='queued', lock_token=screening_lock['token']):
        db.close()
        return
    job = db.get_web_screening_job(job_id)
    options = job.get('options') or {}
    task_type = options.get('report_task_type', 'market')
    checkpoint = db.get_task_report_checkpoint(task_type, job_id) or {}
    records_by_market = dict(checkpoint.get('records_by_market') or {})
    task_ids: List[str] = list(job.get('task_ids') or [])
    previous_tasks = [db.get_task_by_id(task_id) for task_id in task_ids] if resume else []
    db.close()

    def record_market_task(task_id: str) -> None:
        if task_id in task_ids:
            return
        task_ids.append(task_id)
        progress_db = MarketDatabase(mysql_config)
        try:
            progress_db.update_web_screening_job(job_id, None, task_ids=task_ids, lock_token=screening_lock['token'])
        finally:
            progress_db.close()

    try:
        params = get_default_screening_params()
        if chain_key:
            params["chain_key"] = chain_key
        webhook_url = os.getenv("FEISHU_WEBHOOK_URL", "").strip()

        for raw_market in markets:
            market = normalize_market(raw_market)
            task = next((item for item in previous_tasks if item and item['market'] == market), None)
            resume_args = {'task_id': task['task_id']} if task else {}
            result = run_market_screening_worker(
                mysql_config=mysql_config,
                market=market,
                timeframe=timeframe,
                default_params=params,
                verbose=False,
                chain_key=(options.get('chain_by_market') or {}).get(market) or chain_key,
                pool_types=pool_types,
                on_task_created=record_market_task,
                **resume_args,
            )
            if result.error:
                raise RuntimeError(result.error)
            if result.skipped or not result.task_id:
                raise RuntimeError(f'{market} 未执行筛选，未记录为成功')
            if result.task_id:
                record_market_task(result.task_id)
            result_db = MarketDatabase(mysql_config)
            try:
                child = result_db.get_task_by_id(result.task_id)
                if not child or child.get('status') != 'completed':
                    raise RuntimeError(f'{market} 子任务未成功完成')
            finally:
                result_db.close()
            market_db = MarketDatabase(mysql_config)
            try:
                records_by_market[market] = result.passed
                checkpoint.update(status='collecting', records_by_market=records_by_market)
                if not market_db.save_task_report_checkpoint(task_type, job_id, checkpoint, lock_token=screening_lock['token']):
                    raise RuntimeError('任务已终止或执行令牌已变化')
                market_db.update_screening_run_lock(job_id, market, 'running', task_id=result.task_id)
            finally:
                market_db.close()
        report_db = MarketDatabase(mysql_config)
        try:
            report = publish_task_report(mysql_config, report_db, task_type, job_id, job['markets'], timeframe,
                                         records_by_market, chain_key or '', lock_token=screening_lock['token'])
        finally:
            report_db.close()
        finish_web_screening_job(mysql_config, job_id, screening_lock, rq_job.connection, 'completed')
        if send_feishu:
            try:
                notify_task_report(report, job_id)
            except Exception:
                pass  # Optional delivery never changes a published task's outcome.
    except Exception as exc:
        finish_web_screening_job(mysql_config, job_id, screening_lock, rq_job.connection,
                                 'failed', f'{type(exc).__name__}: {exc}')
        raise


def resume_web_screening_job(mysql_config: MySqlConfig, job_id: str, artifact_root: str, screening_lock: dict) -> None:
    """Continue only unfinished child-task items for a previously interrupted web job."""
    db = MarketDatabase(mysql_config)
    job = db.get_web_screening_job(job_id)
    if not job:
        db.close()
        return
    db.close()
    options = job.get("options") or {}
    checkpoint = job.get('summary', {}).get('report') or {}
    remaining = [market for market in job['markets'] if market not in (checkpoint.get('records_by_market') or {})]
    run_web_screening_job(mysql_config, job_id, remaining, job['timeframe'], artifact_root,
                          send_feishu=options.get('send_feishu', False), chain_key=options.get('chain_key'),
                          pool_types=options.get('pool_types'), screening_lock=screening_lock, resume=True)


def enqueue_report_retry(db: MarketDatabase, task_type: str, task_id: str, user) -> dict:
    from .config import mysql_config_from_env
    from .queue import screening_queue
    job = db.get_single_stock_run(task_id) if task_type == 'single_stock' else db.get_web_screening_job(task_id)
    if not job:
        raise BusinessError('REPORT_TASK_NOT_FOUND', '报告所属任务不存在')
    actual_type = 'single_stock' if task_type == 'single_stock' else (job.get('options') or {}).get('report_task_type', (job.get('options') or {}).get('job_kind', 'market'))
    if task_type != actual_type:
        raise BusinessError('INVALID_REPORT_TASK_TYPE', '报告类型与任务不匹配')
    if not db.get_task_report_checkpoint(task_type, task_id):
        raise BusinessError('REPORT_NOT_RETRYABLE', '任务尚未进入报告阶段，请先恢复筛选')
    if task_type in {'market', 'scheduled'}:
        from .main import resume_screening_job
        return resume_screening_job(task_id, user=user, db=db)
    if job['status'] not in {'failed', 'cancelled'}:
        raise BusinessError('REPORT_NOT_RETRYABLE', '只能重试失败或已终止任务的报告')
    queue = screening_queue()
    queue.connection.ping()
    if not db.claim_report_retry(task_type, task_id, job['status']):
        raise BusinessError('REPORT_NOT_RETRYABLE', '任务状态已变化，请刷新')
    try:
        queue.enqueue(retry_task_report, kwargs=dict(mysql_config=mysql_config_from_env(), task_type=task_type, task_id=task_id),
                      job_timeout=int(os.getenv('WEB_JOB_TIMEOUT_SEC', '21600')))
    except Exception as exc:
        db.finish_report_retry(task_type, task_id, 'failed', '报告重试入队失败')
        raise BusinessError('REPORT_RETRY_FAILED', '报告重试入队失败') from exc
    return {'job_id': task_id, 'status': 'queued'}


def retry_task_report(mysql_config: MySqlConfig, task_type: str, task_id: str) -> None:
    db = MarketDatabase(mysql_config)
    try:
        if not db.claim_report_retry(task_type, task_id, 'queued', status='running'):
            return
        checkpoint = db.get_task_report_checkpoint(task_type, task_id)
        publish_task_report(mysql_config, db, task_type, task_id, checkpoint.get('markets') or [],
                            checkpoint.get('timeframe', '1d'), checkpoint.get('records_by_market') or {},
                            checkpoint.get('chain_key') or '')
        db.finish_report_retry(task_type, task_id, 'completed')
    except Exception as exc:
        db.finish_report_retry(task_type, task_id, 'failed', str(exc))
        raise
    finally:
        db.close()


def _record_artifacts(mysql_config: MySqlConfig, task_id: str, market: str, paths: List[str]) -> None:
    if not paths:
        return
    db = MarketDatabase(mysql_config)
    for path_text in paths:
        path = Path(path_text)
        if not path.exists():
            continue
        suffix = path.suffix.lower()
        artifact_type = "report" if suffix == ".md" else "csv"
        db.create_screening_artifact({
            "task_id": task_id,
            "market": market,
            "artifact_type": artifact_type,
            "file_name": path.name,
            "file_path": str(path.resolve()),
            "content_type": "text/markdown; charset=utf-8" if suffix == ".md" else "text/csv; charset=utf-8",
            "file_size": path.stat().st_size,
            "checksum": _sha256(path),
        })
    db.close()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
