#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
yfinance 频控共享模块。

Yahoo Finance 自 2024Q4 起启用 TLS 指纹识别 + 速率限制（≈60 req/min/IP）。
yfinance ≥1.3.0 已内置 curl_cffi 浏览器指纹模拟，但批量请求仍可能触发 429。
本模块提供统一的抖动延迟和指数退避重试，供所有 yfinance 调用点复用。
"""

from __future__ import annotations

import logging
import random
import time
from functools import wraps
from typing import Callable, TypeVar

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable)

# 默认参数：每 chunk 间 3s 基础延迟 + 0-1s 抖动
DEFAULT_BASE_SLEEP = 3.0
DEFAULT_JITTER = 1.0
# 逐股延迟（sector resolver、macro provider 等逐 Ticker 场景）
DEFAULT_PER_TICKER_SLEEP = 0.3
# 重试参数
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_BASE_DELAY = 5.0  # 首次重试等待秒数

_RATE_LIMIT_ERRORS: tuple = ()
try:
    from yfinance.exceptions import YFRateLimitError
    _RATE_LIMIT_ERRORS = (YFRateLimitError,)
except ImportError:
    pass


def yf_sleep(base: float = DEFAULT_BASE_SLEEP,
             jitter: float = DEFAULT_JITTER) -> None:
    """带抖动的延迟，避免多线程/多进程撞在同一时刻重试。"""
    time.sleep(max(0.0, base + random.uniform(0.0, jitter)))


def per_ticker_sleep() -> None:
    """逐股调用场景的小延迟。"""
    yf_sleep(DEFAULT_PER_TICKER_SLEEP, 0.1)


def _is_rate_limit_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    if "rate limit" in msg or "too many requests" in msg or "429" in msg:
        return True
    if _RATE_LIMIT_ERRORS and isinstance(exc, _RATE_LIMIT_ERRORS):
        return True
    # yfinance wraps HTTPError in YFException/requests.HTTPError
    if hasattr(exc, "response"):
        resp = getattr(exc, "response", None)
        if resp is not None and getattr(resp, "status_code", 0) == 429:
            return True
    return False


def retry_on_rate_limit(
    func: F,
    *,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    base_delay: float = DEFAULT_BASE_DELAY,
    rate_limit_sleep: float | None = None,
) -> F:
    """装饰器：捕获 YFRateLimitError / HTTP 429，指数退避重试。

    用法:
        @retry_on_rate_limit
        def my_func(...):
            ...

        # 或带参数:
        @retry_on_rate_limit(max_attempts=5, base_delay=10.0)
        def my_func(...):
            ...
    """
    @wraps(func)
    def wrapper(*args, **kwargs):
        last_exc = None
        for attempt in range(1, max_attempts + 1):
            try:
                return func(*args, **kwargs)
            except Exception as exc:
                if not _is_rate_limit_error(exc):
                    raise
                last_exc = exc
                if attempt >= max_attempts:
                    logger.error(
                        f"yfinance 重试 {max_attempts} 次后仍被限流: {exc}"
                    )
                    raise
                delay = base_delay * (2 ** (attempt - 1))  # 5, 10, 20
                jitter = delay * 0.2
                actual = delay + random.uniform(0, jitter)
                logger.warning(
                    f"yfinance 频控 (%s/%s)，等待 %.1fs 后重试",
                    attempt, max_attempts, actual,
                )
                time.sleep(actual)
        raise last_exc  # type: ignore[misc]
    return wrapper  # type: ignore[return-value]
