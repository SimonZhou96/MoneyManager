#!/usr/bin/env python3
from __future__ import annotations

import os

from redis import Redis
from rq import Worker

from .config import load_dotenv


def main() -> int:
    load_dotenv()
    redis_url = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")
    worker = Worker(["screening"], connection=Redis.from_url(redis_url))
    worker.work()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
