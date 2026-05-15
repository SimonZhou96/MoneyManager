#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Shared network preflight helpers for outbound API calls."""

from __future__ import annotations

import socket
from typing import Iterable, List, Tuple
from urllib.parse import urlparse


def extract_host(url_or_host: str) -> str:
    text = (url_or_host or "").strip()
    if not text:
        return ""
    if "://" not in text:
        return text
    return (urlparse(text).hostname or "").strip()


def check_host_resolution(hosts: Iterable[str]) -> List[Tuple[str, str]]:
    """Return [(host, error_message)] for hosts that cannot be resolved."""
    failures: List[Tuple[str, str]] = []
    seen = set()
    for raw_host in hosts:
        host = extract_host(raw_host)
        if not host or host in seen:
            continue
        seen.add(host)
        try:
            socket.getaddrinfo(host, None)
        except Exception as exc:
            failures.append((host, f"{type(exc).__name__}: {exc}"))
    return failures


def format_resolution_failures(prefix: str, hosts: Iterable[str]) -> List[str]:
    messages = []
    for host, reason in check_host_resolution(hosts):
        messages.append(f"{prefix}: 域名解析失败 `{host}` | {reason}")
    return messages
