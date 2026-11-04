from __future__ import annotations

import os
import json
import secrets
import threading
from datetime import date
from typing import Callable

from redis import Redis
from redis.exceptions import RedisError
from rq import Queue

from .business import BusinessError


def screening_queue() -> Queue:
    redis_url = os.getenv("REDIS_URL", "").strip()
    if not redis_url:
        raise BusinessError("SCREENING_QUEUE_UNAVAILABLE", "全市场筛选需要配置 Redis 和 RQ worker")
    return Queue("screening", connection=Redis.from_url(
        redis_url, socket_connect_timeout=3, socket_timeout=5,
    ))


def acquire_screening_locks(connection: Redis, job_id: str, markets: list[str], run_date: date) -> dict:
    lock = {"date": run_date.isoformat(), "token": secrets.token_hex(16), "markets": sorted(set(markets))}
    keys = [f"screening:active:{lock['date']}:{market}" for market in lock["markets"]]
    value = json.dumps({"job_id": job_id, "token": lock["token"]}, sort_keys=True)
    try:
        conflict = connection.eval("""
            for i, key in ipairs(KEYS) do
                local owner = redis.call('GET', key)
                if owner then return {key, owner} end
            end
            for i, key in ipairs(KEYS) do redis.call('SET', key, ARGV[1], 'NX') end
            return {}
        """, len(keys), *keys, value)
    except RedisError as exc:
        raise BusinessError("SCREENING_QUEUE_UNAVAILABLE", "Redis 不可用，未启动全市场筛选") from exc
    if conflict:
        key, owner = [item.decode() if isinstance(item, bytes) else item for item in conflict]
        raise BusinessError(
            "SCREENING_MARKET_BUSY",
            f"{key.rsplit(':', 1)[-1]} 市场当日已有进行中的任务 {json.loads(owner)['job_id']}，请等待结束或手动终止",
        )
    return lock


def release_screening_locks(connection: Redis, job_id: str, lock: dict, markets: list[str] | None = None) -> None:
    keys = [f"screening:active:{lock['date']}:{market}" for market in (markets if markets is not None else lock["markets"])]
    if not keys:
        return
    value = json.dumps({"job_id": job_id, "token": lock["token"]}, sort_keys=True)
    connection.eval("""
        for i, key in ipairs(KEYS) do
            if redis.call('GET', key) == ARGV[1] then redis.call('DEL', key) end
        end
        return 1
    """, len(keys), *keys, value)


def enqueue_or_thread(fn: Callable, **kwargs) -> str:
    """Enqueue a background job through RQ; fall back to a daemon thread locally."""
    redis_url = os.getenv("REDIS_URL", "").strip()
    if redis_url:
        try:
            from redis import Redis
            from rq import Queue

            queue = Queue("screening", connection=Redis.from_url(redis_url))
            job = queue.enqueue(fn, kwargs=kwargs, job_timeout=int(os.getenv("WEB_JOB_TIMEOUT_SEC", "21600")))
            return f"rq:{job.id}"
        except Exception:
            pass

    thread = threading.Thread(target=fn, kwargs=kwargs, daemon=True)
    thread.start()
    return "thread"
