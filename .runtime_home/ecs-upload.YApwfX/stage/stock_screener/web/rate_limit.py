from __future__ import annotations

import math
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from threading import Lock
from typing import Deque, Dict, Optional

from .business import BusinessError


@dataclass(frozen=True)
class RateLimitRule:
    limit: int
    window_seconds: int
    error_code: str
    message_template: str


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    retry_after_seconds: int = 0


class InMemorySlidingWindowRateLimiter:
    """Process-local sliding-window limiter for the single web-api instance."""

    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._events: Dict[str, Deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def check(self, key: str, rule: RateLimitRule) -> RateLimitDecision:
        now = float(self._clock())
        cutoff = now - rule.window_seconds
        with self._lock:
            events = self._events[key]
            while events and events[0] <= cutoff:
                events.popleft()
            if len(events) >= rule.limit:
                retry_after = int(math.ceil(rule.window_seconds - (now - events[0])))
                return RateLimitDecision(False, max(1, retry_after))
            events.append(now)
            return RateLimitDecision(True, 0)

    def reset(self) -> None:
        with self._lock:
            self._events.clear()


def _format_retry_after(seconds: int) -> str:
    seconds = max(1, int(seconds))
    if seconds < 60:
        return f"{seconds} 秒"
    minutes = math.ceil(seconds / 60)
    if minutes < 60:
        return f"{minutes} 分钟"
    hours = minutes // 60
    remaining_minutes = minutes % 60
    if remaining_minutes:
        return f"{hours} 小时 {remaining_minutes} 分钟"
    return f"{hours} 小时"


def enforce_rate_limit(key: str, rule: RateLimitRule) -> None:
    decision = rate_limiter.check(key, rule)
    if decision.allowed:
        return
    message = rule.message_template.format(
        retry_after_seconds=decision.retry_after_seconds,
        retry_after_text=_format_retry_after(decision.retry_after_seconds),
    )
    raise BusinessError(
        error_code=rule.error_code,
        message=message,
        retry_after_seconds=decision.retry_after_seconds,
    )


rate_limiter = InMemorySlidingWindowRateLimiter()

LOGIN_REQUEST_RULE = RateLimitRule(
    limit=20,
    window_seconds=60,
    error_code="RATE_LIMIT_LOGIN",
    message_template="登录请求太频繁，请 {retry_after_text} 后再试",
)
WEB_READ_RULE = RateLimitRule(
    limit=120,
    window_seconds=60,
    error_code="RATE_LIMIT_WEB_READ",
    message_template="页面请求太频繁，请 {retry_after_text} 后再试",
)
CREATE_TASK_RULE = RateLimitRule(
    limit=5,
    window_seconds=30 * 60,
    error_code="RATE_LIMIT_CREATE_TASK",
    message_template="筛选任务创建太频繁，请 {retry_after_text} 后再试",
)
SINGLE_STOCK_RULE = RateLimitRule(
    limit=30,
    window_seconds=10 * 60,
    error_code="RATE_LIMIT_SINGLE_STOCK",
    message_template="单股分析请求太频繁，请 {retry_after_text} 后再试",
)
ARTIFACT_DOWNLOAD_RULE = RateLimitRule(
    limit=30,
    window_seconds=60,
    error_code="RATE_LIMIT_ARTIFACT_DOWNLOAD",
    message_template="文件下载请求太频繁，请 {retry_after_text} 后再试",
)
AGENT_BULK_RULE = RateLimitRule(
    limit=120,
    window_seconds=60,
    error_code="RATE_LIMIT_AGENT_BULK",
    message_template="Agent 推送请求太频繁，请 {retry_after_text} 后再试",
)
