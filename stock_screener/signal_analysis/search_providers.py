#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Search provider abstractions for signal analysis."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List

from .models import SearchDocument

try:
    import requests
except Exception:  # pragma: no cover
    requests = None


class SearchProvider(ABC):
    """Provider interface for market and company news search."""

    name = "base"
    is_available = True

    @abstractmethod
    def search(self, query: str, max_results: int) -> List[SearchDocument]:
        """Return normalized search snippets for a query."""


class NullSearchProvider(SearchProvider):
    """No-op provider used when search credentials are not configured."""

    name = "null"
    is_available = False

    def search(self, query: str, max_results: int) -> List[SearchDocument]:
        return []


class TavilySearchProvider(SearchProvider):
    """Tavily-backed search implementation."""

    name = "tavily"

    def __init__(
        self,
        api_key: str,
        endpoint: str = "https://api.tavily.com/search",
        timeout_sec: int = 30,
    ):
        self.api_key = api_key
        self.endpoint = endpoint
        self.timeout_sec = timeout_sec

    def search(self, query: str, max_results: int) -> List[SearchDocument]:
        if requests is None:
            raise RuntimeError("requests is not installed")
        if not query.strip():
            return []

        payload = {
            "api_key": self.api_key,
            "query": query,
            "search_depth": "basic",
            "include_answer": False,
            "include_raw_content": False,
            "max_results": max(1, int(max_results)),
        }
        response = requests.post(self.endpoint, json=payload, timeout=self.timeout_sec)
        if response.status_code >= 400:
            raise RuntimeError(f"Tavily search failed: HTTP {response.status_code} {response.text[:200]}")
        data = response.json() or {}
        results = data.get("results") or []

        documents: List[SearchDocument] = []
        for item in results:
            if not isinstance(item, dict):
                continue
            url = str(item.get("url") or "").strip()
            title = str(item.get("title") or "").strip()
            content = str(item.get("content") or item.get("snippet") or "").strip()
            if not (url or title or content):
                continue
            score = item.get("score")
            try:
                score = float(score) if score is not None else None
            except (TypeError, ValueError):
                score = None
            documents.append(
                SearchDocument(
                    title=title,
                    url=url,
                    content=content,
                    score=score,
                    query=query,
                )
            )
        return documents

