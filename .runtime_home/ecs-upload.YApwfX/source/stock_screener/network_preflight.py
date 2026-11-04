#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Shared network preflight helpers for outbound API calls."""

from __future__ import annotations

import socket
import time
from typing import Iterable, List, Tuple
from urllib.parse import urlparse


def extract_host(url_or_host: str) -> str:
    text = (url_or_host or "").strip()
    if not text:
        return ""
    if "://" not in text:
        return text
    return (urlparse(text).hostname or "").strip()


def check_host_resolution(
    hosts: Iterable[str],
    *,
    attempts: int = 3,
    retry_delay_sec: float = 0.5,
) -> List[Tuple[str, str]]:
    """Return [(host, error_message)] for hosts that cannot be resolved."""
    failures: List[Tuple[str, str]] = []
    seen = set()
    attempts = max(1, int(attempts or 1))
    retry_delay_sec = max(0.0, float(retry_delay_sec or 0.0))
    for raw_host in hosts:
        host = extract_host(raw_host)
        if not host or host in seen:
            continue
        seen.add(host)
        last_error = ""
        for attempt in range(1, attempts + 1):
            try:
                socket.getaddrinfo(host, None)
                last_error = ""
                break
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                if attempt < attempts and retry_delay_sec > 0:
                    time.sleep(retry_delay_sec * (2 ** (attempt - 1)))
        if last_error:
            failures.append((host, last_error))
    return failures


def format_resolution_failures(
    prefix: str,
    hosts: Iterable[str],
    *,
    attempts: int = 3,
    retry_delay_sec: float = 0.5,
) -> List[str]:
    messages = []
    for host, reason in check_host_resolution(
        hosts,
        attempts=attempts,
        retry_delay_sec=retry_delay_sec,
    ):
        messages.append(f"{prefix}: 域名解析失败 `{host}` | {reason}")
    return messages
