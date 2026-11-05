"""Actual HTTP attempts share a durable daily budget, deadline and rate limit."""
from contextlib import contextmanager
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import time
from urllib.parse import urlsplit, urljoin
from unittest.mock import patch

import requests


class BudgetExceeded(RuntimeError):
    pass


class Transport:
    def __init__(self, count_attempt, *, deadline, interval=1.0, clock=time.monotonic,
                 sleep=time.sleep, session=None):
        self.count_attempt = count_attempt
        self.deadline, self.interval = deadline, interval
        self.clock, self.sleep = clock, sleep
        self.session = session or requests.Session()
        self.last_started = {}
        self.blocked_hosts = set()

    def remaining(self):
        return self.deadline - self.clock()

    def pause(self, delay):
        if delay >= self.remaining():
            raise BudgetExceeded("Daily sync time budget exhausted")
        if delay > 0:
            self.sleep(delay)

    def request(self, method, url, **kwargs):
        # AKShare's helpers have their own retry options; this layer is the only
        # retry owner so every real attempt is counted and bounded.
        for option in ("fresh_session", "max_retries", "retry_delay", "backoff_factor"):
            kwargs.pop(option, None)
        kwargs["timeout"] = (5, 15)
        kwargs["allow_redirects"] = False
        redirects = kwargs.pop("_redirects", 0)
        host = urlsplit(url).hostname
        if host in self.blocked_hosts:
            raise RuntimeError(f"Provider circuit open after repeated 429 responses: {host}")
        for attempt in range(3):
            if self.remaining() <= 0:
                raise BudgetExceeded("Daily sync time budget exhausted")
            self.pause(max(0, self.last_started.get(host, -self.interval) + self.interval - self.clock()))
            self.count_attempt()
            self.last_started[host] = self.clock()
            try:
                response = self.session.request(method, url, **kwargs)
                if response.status_code in {301, 302, 303, 307, 308}:
                    if redirects >= 5:
                        raise ValueError("Too many provider redirects")
                    return self.request(method, urljoin(url, response.headers["Location"]), _redirects=redirects+1, **kwargs)
                if response.status_code == 429 and attempt < 2:
                    retry = response.headers.get("Retry-After", "60")
                    try:
                        delay = float(retry)
                    except ValueError:
                        delay = max(0, (parsedate_to_datetime(retry) - datetime.now(timezone.utc)).total_seconds())
                    self.pause(max(1, delay))
                    continue
                if response.status_code == 429:
                    self.blocked_hosts.add(host)
                response.raise_for_status()
                return response
            except (requests.ConnectionError, requests.Timeout, requests.HTTPError) as exc:
                if isinstance(exc, requests.HTTPError) and exc.response.status_code not in {429, 500, 502, 503, 504}:
                    raise
                if attempt == 2:
                    raise
                self.pause((5, 20)[attempt])
        raise AssertionError("unreachable")

    @contextmanager
    def akshare_requests(self):
        # Only used inside the independent worker process, never API threads.
        # Pin AKShare's worker dependency to a version with this HTTP boundary.
        from akshare.utils import http
        with patch.object(http, "request", self.request), patch.object(http, "request_with_retry", self.request):
            yield
