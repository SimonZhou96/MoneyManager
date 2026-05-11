from __future__ import annotations

import os
import threading
from typing import Callable


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
