#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Search provider abstractions for signal analysis.

All providers inherit ``_http_with_retry()`` from the base class —
rate-limit handling (exponential backoff + retry) is built-in by default.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
import os
import random
import re
import sys
import time
from typing import Any, Callable, Dict, List, Optional

from .models import ScreeningSignalRow, SearchDocument

try:
    import requests
except Exception:  # pragma: no cover
    requests = None


# ── Module-level retry (reusable outside providers, e.g. hot_sectors) ──

_RETRYABLE_STATUSES: set[int] = {429, 432, 500, 502, 503, 504}
_RETRY_MAX: int = 3
_RETRY_BASE_SEC: float = 1.0
_RETRY_MAX_SEC: float = 30.0


def _http_retry(
    send_fn: Callable[[], Any],
    label: str = "",
    max_retries: int = _RETRY_MAX,
    base_delay: float = _RETRY_BASE_SEC,
    max_delay: float = _RETRY_MAX_SEC,
) -> Any:
    """Call *send_fn*() with exponential backoff on rate-limit errors (429/432/5xx)."""
    last_exc: Optional[Exception] = None
    for attempt in range(max_retries + 1):
        try:
            response = send_fn()
        except Exception as exc:
            last_exc = exc
            if attempt < max_retries:
                delay = min(base_delay * (2 ** attempt), max_delay) + random.uniform(0, 1.0)
                print(f"[{label}] 异常({type(exc).__name__}), 重试 {attempt+1}/{max_retries} in {delay:.1f}s")
                time.sleep(delay)
                continue
            raise

        status = getattr(response, "status_code", 0)
        if status in _RETRYABLE_STATUSES:
            last_exc = RuntimeError(f"HTTP {status}")
            if attempt < max_retries:
                delay = min(base_delay * (2 ** attempt), max_delay) + random.uniform(0, 1.0)
                print(f"[{label}] HTTP {status} 限流, 重试 {attempt+1}/{max_retries} in {delay:.1f}s")
                time.sleep(delay)
                continue
            raise RuntimeError(f"[{label}] HTTP {status} after {max_retries} retries")

        # Zhipu body-level codes
        if status == 200:
            try:
                body = response.json() if callable(getattr(response, "json", None)) else {}
            except Exception:
                body = {}
            if isinstance(body, dict):
                error = body.get("error") or {}
                code = str(error.get("code", ""))
                if code in ("1302", "1305"):
                    last_exc = RuntimeError(f"ZhipuAI code={code}")
                    if attempt < max_retries:
                        delay = min(base_delay * (2 ** attempt), max_delay) + random.uniform(0, 1.0)
                        print(f"[{label}] ZhipuAI code={code} 限流, 重试 {attempt+1}/{max_retries} in {delay:.1f}s")
                        time.sleep(delay)
                        continue
                    raise RuntimeError(f"[{label}] ZhipuAI code={code} after {max_retries} retries")

        return response
    raise last_exc if last_exc else RuntimeError(f"[{label}] retries exhausted")


class SearchProvider(ABC):
    """Provider interface for market and company news search.

    Every subclass inherits :meth:`_http_with_retry` for built-in rate-limit
    handling with exponential backoff.  New providers MUST use this method
    for all outbound HTTP calls to get retry protection by default.
    """

    name = "base"
    is_available = True

    # ── Retry constants (override in subclass if needed) ─────

    _RETRY_MAX_RETRIES: int = 3
    _RETRY_BASE_DELAY_SEC: float = 1.0
    _RETRY_MAX_DELAY_SEC: float = 30.0

    # Status codes that always deserve a retry
    _RETRYABLE_STATUSES: set[int] = {429, 432, 500, 502, 503, 504}

    # ── Abstract contract ───────────────────────────────────

    @abstractmethod
    def search(self, query: str, max_results: int) -> List[SearchDocument]:
        """Return normalized search snippets for a query."""

    @abstractmethod
    def search_companies_batch(
        self,
        market: str,
        rows: List[ScreeningSignalRow],
        max_results: int,
    ) -> Dict[str, List[SearchDocument]]:
        """Return company-search snippets grouped by stock code."""

    # ── Shared retry helper ─────────────────────────────────

    def _http_with_retry(
        self,
        send_fn: Callable[[], Any],
        *,
        max_retries: Optional[int] = None,
        base_delay: Optional[float] = None,
        max_delay: Optional[float] = None,
    ) -> Any:
        """Call *send_fn*() with exponential backoff on rate-limit errors.

        **All new providers MUST call this for outbound HTTP requests.**
        This is the standard retry mechanism inherited by every SearchProvider.

        Retryable errors: HTTP 429, 432, 5xx, and ZhipuAI body codes 1302/1305.
        """
        return _http_retry(
            send_fn,
            label=getattr(self, "name", self.__class__.__name__),
            max_retries=max_retries if max_retries is not None else self._RETRY_MAX_RETRIES,
            base_delay=base_delay if base_delay is not None else self._RETRY_BASE_DELAY_SEC,
            max_delay=max_delay if max_delay is not None else self._RETRY_MAX_DELAY_SEC,
        )


class NullSearchProvider(SearchProvider):
    """No-op provider used when search credentials are not configured."""

    name = "null"
    is_available = False

    def search(self, query: str, max_results: int) -> List[SearchDocument]:
        return []

    def search_companies_batch(
        self,
        market: str,
        rows: List[ScreeningSignalRow],
        max_results: int,
    ) -> Dict[str, List[SearchDocument]]:
        return {row.code: [] for row in rows}


class FallbackSearchProvider(SearchProvider):
    """Try search providers in order, falling back to the next when one returns no results."""

    name = "fallback"

    def __init__(self, providers: List[SearchProvider]):
        self.providers = [
            provider
            for provider in providers
            if getattr(provider, "is_available", False)
        ]
        self.last_errors: List[str] = []
        self.last_success_provider: str = ""

    @property
    def is_available(self) -> bool:
        return any(getattr(provider, "is_available", False) for provider in self.providers)

    @property
    def provider_names(self) -> List[str]:
        return [getattr(provider, "name", provider.__class__.__name__) for provider in self.providers]

    def _active_providers(self) -> List[SearchProvider]:
        """Return providers that are currently available (respects dynamic is_available)."""
        return [p for p in self.providers if getattr(p, "is_available", False)]

    def search(self, query: str, max_results: int) -> List[SearchDocument]:
        self.last_errors = []
        self.last_success_provider = ""
        active = self._active_providers()
        total = len(active)
        for i, provider in enumerate(active):
            name = getattr(provider, "name", provider.__class__.__name__)
            try:
                result = provider.search(query, max_results)
                if result:
                    self.last_success_provider = name
                    _log_fallback_step(name, "search", True, len(result), i + 1, total)
                    return result
                # Empty result → fall through to next provider
                _log_fallback_step(name, "search", False, 0, i + 1, total, reason="0 results")
            except Exception as exc:
                self.last_errors.append(self._format_provider_error(provider, exc))
                _log_fallback_step(name, "search", False, 0, i + 1, total, reason=str(exc)[:80])

        _log_fallback_exhausted("search", total)
        return []

    def search_companies_batch(
        self,
        market: str,
        rows: List[ScreeningSignalRow],
        max_results: int,
    ) -> Dict[str, List[SearchDocument]]:
        """Batch-first, then per-stock fallback for unmatched stocks.

        Phase 1: Try batch search with each provider sequentially.
        Phase 2: For stocks still empty, try per-stock with each provider in order.
        Each step logs verbosely so the fallback chain is fully observable.
        """
        if not rows:
            return {}
        self.last_errors = []
        self.last_success_provider = ""
        active = self._active_providers()
        total = len(active)

        grouped: Dict[str, List[SearchDocument]] = {row.code: [] for row in rows}
        code_set = {row.code for row in rows}

        # ── Phase 1: Batch search (fast) ──
        for i, provider in enumerate(active):
            name = getattr(provider, "name", provider.__class__.__name__)
            remaining = [r for r in rows if not grouped.get(r.code)]
            if not remaining:
                break
            try:
                result = provider.search_companies_batch(market, remaining, max_results)
                hit = 0
                for row in remaining:
                    docs = list((result or {}).get(row.code, []))
                    if docs:
                        grouped[row.code] = docs
                        hit += 1
                if hit:
                    _log_fallback_step(name, "companies_batch", True, hit, i + 1, total)
                else:
                    _log_fallback_step(name, "companies_batch", False, hit, i + 1, total,
                                       reason="0 results after quality filter")
            except Exception as exc:
                self.last_errors.append(self._format_provider_error(provider, exc))
                _log_fallback_step(name, "companies_batch", False, 0, i + 1, total,
                                   reason=str(exc)[:80])

        # ── Phase 2: Per-stock fallback for unmatched stocks ──
        unmatched = [r for r in rows if not grouped.get(r.code)]
        if unmatched:
            fallback_active = self._active_providers()
            for row in unmatched:
                for i, provider in enumerate(fallback_active):
                    name = getattr(provider, "name", provider.__class__.__name__)
                    try:
                        result = provider.search_companies_batch(market, [row], max_results)
                        docs = list((result or {}).get(row.code, []))
                        if docs:
                            grouped[row.code] = docs
                            break
                    except Exception:
                        continue
                if not grouped.get(row.code):
                    grouped[row.code] = []

        # ── Summary ──
        matched = sum(1 for docs in grouped.values() if docs)
        active_names = [getattr(p, "name", p.__class__.__name__) for p in active]
        if matched:
            print(f"[搜索fallback] ✅ 最终: {matched}/{len(rows)} stocks matched "
                  f"(tried {', '.join(active_names)})")
        else:
            _log_fallback_exhausted("companies_batch", total)

        return grouped

    @staticmethod
    def _format_provider_error(provider: SearchProvider, exc: Exception) -> str:
        label = getattr(provider, "name", provider.__class__.__name__)
        return f"{label}: {type(exc).__name__}: {exc}"


def _log_fallback_step(
    name: str,
    mode: str,
    success: bool,
    count: int,
    step: int,
    total: int,
    reason: str = "",
) -> None:
    if success:
        print(f"[搜索fallback] ✅ {name} ({mode}) → {count}条 | 步骤 {step}/{total}")
    else:
        suffix = f": {reason}" if reason else ""
        if step < total:
            next_name = "next provider" if step < total else "none"
            print(f"[搜索fallback] ⚠️ {name} ({mode}) → 无有效结果{suffix} → fallback to next | 步骤 {step}/{total}")
        else:
            print(f"[搜索fallback] ❌ {name} ({mode}) → 失败{suffix} | 步骤 {step}/{total}")


def _log_fallback_exhausted(mode: str, total: int) -> None:
    print(f"[搜索fallback] 🚫 全部 {total} 个 provider 已尝试完毕, 无有效结果 | mode={mode}")


class TavilySearchProvider(SearchProvider):
    """Tavily-backed search implementation.

    Tracks quota exhaustion to avoid hammering the API with
    guaranteed-to-fail calls after a 429 response.
    """

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
        self._quota_exhausted = False
        self._quota_error_count = 0
        self._quota_max_errors = 3

    @property
    def is_available(self) -> bool:
        """Provider is available only if quota hasn't been exhausted."""
        if self._quota_exhausted:
            return False
        return True

    def _record_quota_error(self) -> None:
        """Called after a failing 429 response. After consecutive failures
        exceed the threshold, mark the provider as exhausted so the fallback
        chain skips it for the rest of the session."""
        self._quota_error_count += 1
        if self._quota_error_count >= self._quota_max_errors:
            self._quota_exhausted = True
            print(f"[tavily] ⚠️ 连续 {self._quota_error_count} 次 429 错误，"
                  f"标记为配额耗尽，本次会话跳过 Tavily", file=sys.stderr)
        else:
            print(f"[tavily] ⚠️ 429 配额错误 ({self._quota_error_count}/"
                  f"{self._quota_max_errors})，退避后重试", file=sys.stderr)

    def _reset_quota_errors(self) -> None:
        """Reset consecutive error counter on a successful call."""
        self._quota_error_count = 0

    def search(self, query: str, max_results: int) -> List[SearchDocument]:
        if self._quota_exhausted:
            raise RuntimeError("Tavily quota exhausted, provider skipped")
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

        def _send():
            resp = requests.post(self.endpoint, json=payload, timeout=self.timeout_sec)
            if resp.status_code >= 400 and resp.status_code not in self._RETRYABLE_STATUSES:
                resp.raise_for_status()
            return resp

        try:
            response = self._http_with_retry(_send)
        except RuntimeError as exc:
            msg = str(exc)
            if "429" in msg:
                self._record_quota_error()
                # Backoff after 429 to let quota reset window pass
                backoff = min(30.0, 5.0 * (2 ** self._quota_error_count))
                print(f"[tavily] 退避 {backoff:.0f}s 等待配额恢复...", file=sys.stderr)
                time.sleep(backoff)
            raise
        if response.status_code >= 400:
            if response.status_code == 429:
                self._record_quota_error()
            raise RuntimeError(f"Tavily search failed: HTTP {response.status_code} {response.text[:200]}")
        self._reset_quota_errors()
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

    def search_companies_batch(
        self,
        market: str,
        rows: List[ScreeningSignalRow],
        max_results: int,
    ) -> Dict[str, List[SearchDocument]]:
        if not rows:
            return {}
        if self._quota_exhausted:
            raise RuntimeError("Tavily quota exhausted, provider skipped")
        grouped: Dict[str, List[SearchDocument]] = {row.code: [] for row in rows}
        max_chars = _company_search_query_max_chars()
        for query_rows in _split_rows_by_query_budget(market, rows, max_chars):
            if self._quota_exhausted:
                break
            query = _build_company_batch_query(market, query_rows, max_chars=max_chars)
            batch_max_results = _company_batch_max_results(max_results, len(query_rows))
            try:
                documents = self.search(query, batch_max_results)
            except RuntimeError:
                # Quota likely exhausted; stop processing sub-batches
                break
            assigned = _assign_documents_to_stocks(documents, query_rows)
            _debug_company_batch_search(market, query_rows, query, batch_max_results, documents, assigned)
            for code, code_documents in assigned.items():
                grouped.setdefault(code, []).extend(code_documents)
        return grouped


class ZhipuWebSearchProvider(SearchProvider):
    """BigModel/Zhipu AI Web Search implementation."""

    name = "zhipuai"

    def __init__(
        self,
        api_key: str,
        endpoint: str = "https://open.bigmodel.cn/api/paas/v4/web_search",
        timeout_sec: int = 30,
        search_engine: str = "search_std",
        content_size: str = "medium",
        recency_filter: str = "noLimit",
    ):
        self.api_key = api_key
        self.endpoint = endpoint
        self.timeout_sec = timeout_sec
        self.search_engine = search_engine
        self.content_size = content_size
        self.recency_filter = recency_filter

    def search(self, query: str, max_results: int) -> List[SearchDocument]:
        if requests is None:
            raise RuntimeError("requests is not installed")
        query = _fit_search_query(query, _zhipu_search_query_max_chars())
        if not query:
            return []

        payload = {
            "search_query": query,
            "search_engine": self.search_engine,
            "search_intent": False,
            "count": min(50, max(1, int(max_results))),
            "search_recency_filter": self.recency_filter,
            "content_size": self.content_size,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        def _send():
            resp = requests.post(self.endpoint, json=payload, headers=headers, timeout=self.timeout_sec)
            if resp.status_code >= 400 and resp.status_code not in self._RETRYABLE_STATUSES:
                resp.raise_for_status()
            return resp

        response = self._http_with_retry(_send)
        if response.status_code >= 400:
            raise RuntimeError(f"Zhipu web search failed: HTTP {response.status_code} {response.text[:200]}")
        data = response.json() or {}
        results = data.get("search_result") or data.get("results") or []

        documents: List[SearchDocument] = []
        for item in results:
            if not isinstance(item, dict):
                continue
            url = str(item.get("link") or item.get("url") or "").strip()
            title = str(item.get("title") or "").strip()
            content = str(item.get("content") or item.get("summary") or item.get("snippet") or "").strip()
            media = str(item.get("media") or "").strip()
            publish_date = str(item.get("publish_date") or "").strip()
            content_parts = [part for part in [content, f"来源: {media}" if media else "", f"发布时间: {publish_date}" if publish_date else ""] if part]
            content = "\n".join(content_parts)
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

    def search_companies_batch(
        self,
        market: str,
        rows: List[ScreeningSignalRow],
        max_results: int,
    ) -> Dict[str, List[SearchDocument]]:
        if not rows:
            return {}
        grouped: Dict[str, List[SearchDocument]] = {row.code: [] for row in rows}
        max_chars = _zhipu_search_query_max_chars()
        for query_rows in _split_rows_by_zhipu_query_budget(market, rows, max_chars):
            query = _build_zhipu_company_batch_query(market, query_rows, max_chars=max_chars)
            batch_max_results = _company_batch_max_results(max_results, len(query_rows))
            documents = self.search(query, batch_max_results)
            assigned = _assign_documents_to_stocks(documents, query_rows)
            _debug_company_batch_search(market, query_rows, query, batch_max_results, documents, assigned)
            for code, code_documents in assigned.items():
                grouped.setdefault(code, []).extend(code_documents)
        return grouped


def _build_company_batch_query(
    market: str,
    rows: List[ScreeningSignalRow],
    max_chars: Optional[int] = None,
) -> str:
    max_chars = max_chars or _company_search_query_max_chars()
    if rows and all(getattr(row, "is_etf", False) for row in rows):
        prefix = f"{market} ETF fund tracking index theme sector macro news: "
    else:
        prefix = f"{market} stocks latest news earnings events {_market_authoritative_source_terms(market)}: "
    if not rows:
        return prefix.rstrip()

    for name_max_chars in (36, 30, 24, 18, 12, 8):
        query = _compose_company_batch_query(prefix, rows, name_max_chars)
        if len(query) <= max_chars:
            return query

    if len(rows) == 1:
        return _build_single_company_query(prefix, rows[0], max_chars)

    return _compose_company_batch_query(prefix, rows, 8)


def _market_authoritative_source_terms(market: str) -> str:
    key = str(market or "").upper()
    if key == "HK":
        # Bilingual: Chinese terms work better for Bing/Baidu; "HKEX" still helps Tavily
        return "港交所公告 年报 HKEX"
    if key == "US":
        return "SEC filing 10-K 10-Q"
    if key == "A":
        return "巨潮资讯 上交所 深交所 公告"
    return "official filing annual report"


def _split_rows_by_query_budget(
    market: str,
    rows: List[ScreeningSignalRow],
    max_chars: int,
) -> List[List[ScreeningSignalRow]]:
    batches: List[List[ScreeningSignalRow]] = []
    current: List[ScreeningSignalRow] = []
    for row in rows:
        candidate = [*current, row]
        if current and len(_build_company_batch_query(market, candidate, max_chars=max_chars)) > max_chars:
            batches.append(current)
            current = [row]
        else:
            current = candidate
    if current:
        batches.append(current)
    return batches


def _compose_company_batch_query(
    prefix: str,
    rows: List[ScreeningSignalRow],
    name_max_chars: int,
) -> str:
    return f"{prefix}{'; '.join(_company_query_item(row, name_max_chars) for row in rows)}"


def _build_single_company_query(prefix: str, row: ScreeningSignalRow, max_chars: int) -> str:
    base = " ".join(_company_query_base_terms(row))
    if not base:
        return prefix[:max_chars].rstrip()
    name_budget = max_chars - len(prefix) - len(base) - 1
    name_fragment = _fit_company_name_fragment(row.name, name_budget)
    item = f"{base} {name_fragment}".strip()
    query = f"{prefix}{item}"
    if len(query) <= max_chars:
        return query
    return query[:max_chars].rstrip()


def _company_query_item(row: ScreeningSignalRow, name_max_chars: int) -> str:
    terms = _company_query_base_terms(row)
    name_fragment = _fit_company_name_fragment(row.name, name_max_chars)
    if name_fragment and name_fragment != row.code and name_fragment not in terms:
        terms.append(name_fragment)
    return " ".join(terms)


def _company_query_base_terms(row: ScreeningSignalRow) -> List[str]:
    terms: List[str] = []
    for term in _stock_query_terms(row):
        if term and term not in terms:
            terms.append(term)
    return terms


def _fit_company_name_fragment(name: str, budget: int) -> str:
    if budget <= 0:
        return ""
    value = re.sub(r"\s+", " ", (name or "")).strip()
    if len(value) <= budget:
        return value
    return value[:budget].rstrip()


def _company_search_query_max_chars() -> int:
    raw = os.getenv("SIGNAL_COMPANY_SEARCH_QUERY_MAX_CHARS", "390").strip()
    try:
        value = int(raw)
    except ValueError:
        value = 390
    return min(400, max(120, value))


def _zhipu_search_query_max_chars() -> int:
    raw = os.getenv("ZHIPUAI_WEB_SEARCH_QUERY_MAX_CHARS", "").strip()
    if not raw:
        raw = os.getenv("BIGMODEL_WEB_SEARCH_QUERY_MAX_CHARS", "70").strip()
    try:
        value = int(raw)
    except ValueError:
        value = 70
    return min(70, max(20, value))


def _fit_search_query(query: str, max_chars: int) -> str:
    return re.sub(r"\s+", " ", (query or "").strip())[:max_chars].rstrip()


def _split_rows_by_zhipu_query_budget(
    market: str,
    rows: List[ScreeningSignalRow],
    max_chars: int,
) -> List[List[ScreeningSignalRow]]:
    batches: List[List[ScreeningSignalRow]] = []
    current: List[ScreeningSignalRow] = []
    for row in rows:
        candidate = [*current, row]
        if current and len(_build_zhipu_company_batch_query(market, candidate, max_chars=max_chars)) > max_chars:
            batches.append(current)
            current = [row]
        else:
            current = candidate
    if current:
        batches.append(current)
    return batches


def _build_zhipu_company_batch_query(
    market: str,
    rows: List[ScreeningSignalRow],
    max_chars: Optional[int] = None,
) -> str:
    max_chars = max_chars or _zhipu_search_query_max_chars()
    prefix = _zhipu_company_batch_prefix(market, rows)
    if not rows:
        return prefix[:max_chars].rstrip()

    for name_max_chars in (12, 8, 4, 0):
        query = _compose_zhipu_company_batch_query(prefix, rows, name_max_chars)
        if len(query) <= max_chars:
            return query

    if len(rows) == 1:
        row = rows[0]
        base_terms = _stock_query_terms(row)[:2] or [row.code]
        query = f"{prefix}{' '.join(base_terms)}"
        return query[:max_chars].rstrip()

    return _compose_zhipu_company_batch_query(prefix, rows, 0)


def _zhipu_company_batch_prefix(market: str, rows: List[ScreeningSignalRow]) -> str:
    key = str(market or "").upper()
    if rows and all(getattr(row, "is_etf", False) for row in rows):
        return f"{key} ETF 新闻 "
    if key == "HK":
        return "港股 公告 新闻 "
    if key == "US":
        return "US stock filings news "
    if key == "A":
        return "A股 公告 新闻 "
    return f"{key} 股票 新闻 "


def _compose_zhipu_company_batch_query(
    prefix: str,
    rows: List[ScreeningSignalRow],
    name_max_chars: int,
) -> str:
    return f"{prefix}{'; '.join(_zhipu_company_query_item(row, name_max_chars) for row in rows)}"


def _zhipu_company_query_item(row: ScreeningSignalRow, name_max_chars: int) -> str:
    terms = _stock_query_terms(row)[:2] or [row.code]
    name_fragment = _fit_company_name_fragment(row.name, name_max_chars)
    if name_fragment and name_fragment not in terms:
        terms.append(name_fragment)
    return " ".join(terms)


def _company_batch_max_results(max_results: int, row_count: int) -> int:
    try:
        configured = int(max_results)
    except (TypeError, ValueError):
        configured = 1
    return max(1, configured, int(row_count or 0))


def _stock_identity_terms(row: ScreeningSignalRow) -> List[str]:
    terms = []
    name = (row.name or "").strip()
    for term in [*_stock_code_alias_terms(row), name]:
        if term and term not in terms:
            terms.append(term)
    ticker = _normalize_ticker(row.code)
    if ticker and ticker.isdigit():
        no_zero = ticker.lstrip("0")
        if len(no_zero) >= 3 and no_zero not in terms:
            terms.append(no_zero)
    name_words = [
        word
        for word in re.split(r"[^A-Za-z0-9\u4e00-\u9fff]+", name)
        if len(word) >= 3
    ]
    if len(name_words) >= 2:
        phrase = " ".join(name_words[:2])
        if phrase not in terms:
            terms.append(phrase)
    return terms


def _stock_query_terms(row: ScreeningSignalRow) -> List[str]:
    code = (row.code or "").strip()
    ticker = _normalize_ticker(code)
    market = _infer_code_market(row, code)
    terms: List[str] = []

    def add(value: str) -> None:
        value = (value or "").strip()
        if value and value not in terms:
            terms.append(value)

    add(code)
    add(ticker)
    if market == "HK":
        for value in _hk_query_ticker_forms(ticker):
            add(f"{value}.HK")
    elif market == "US":
        for value in _us_query_ticker_forms(ticker):
            add(value)
    elif market in {"SH", "SZ", "BJ", "A"}:
        exchange = market if market in {"SH", "SZ", "BJ"} else _a_share_exchange_from_code(code)
        if exchange and ticker:
            add(f"{ticker}.{exchange}")
    return terms


def _stock_code_alias_terms(row: ScreeningSignalRow) -> List[str]:
    code = (row.code or "").strip()
    ticker = _normalize_ticker(code)
    market = _infer_code_market(row, code)
    aliases: List[str] = []

    def add(value: str) -> None:
        value = (value or "").strip()
        if value and value not in aliases:
            aliases.append(value)

    add(code)
    add(ticker)

    if market == "HK":
        for value in _hk_ticker_forms(ticker):
            add(f"{value}.HK")
            add(f"{value} HK")
            add(f"{value}-HK")
    elif market == "US":
        for value in _us_ticker_forms(ticker):
            add(value)
            add(f"{value}.US")
            add(f"{value} US")
    elif market in {"SH", "SZ", "BJ", "A"}:
        exchange = market if market in {"SH", "SZ", "BJ"} else _a_share_exchange_from_code(code)
        if exchange and ticker:
            add(f"{ticker}.{exchange}")
            add(f"{exchange}{ticker}")
    return aliases


def _hk_query_ticker_forms(ticker: str) -> List[str]:
    value = (ticker or "").strip()
    if not value:
        return []
    if value.isdigit() and len(value) == 5 and value.startswith("0"):
        return [value[-4:]]
    return [value]


def _infer_code_market(row: ScreeningSignalRow, code: str) -> str:
    value = (code or "").strip()
    if "." in value:
        left, right = value.split(".", 1)
        if left.upper() in {"HK", "US", "SH", "SZ", "BJ"}:
            return left.upper()
        if right.upper() in {"HK", "US", "SH", "SZ", "SS", "BJ"}:
            return "SH" if right.upper() == "SS" else right.upper()
    market = (getattr(row, "market", "") or "").upper()
    if market == "A":
        return _a_share_exchange_from_code(value) or "A"
    return market


def _hk_ticker_forms(ticker: str) -> List[str]:
    value = (ticker or "").strip()
    if not value:
        return []
    forms = [value]
    if value.isdigit():
        if len(value) == 5 and value.startswith("0"):
            forms.append(value[-4:])
        forms.append(value.zfill(5))
        forms.append(value.zfill(4))
        no_zero = value.lstrip("0")
        if len(no_zero) >= 3:
            forms.append(no_zero)
    return _dedupe_terms(forms)


def _us_ticker_forms(ticker: str) -> List[str]:
    value = (ticker or "").strip().upper()
    if not value:
        return []
    forms = [value]
    if "-" in value:
        forms.append(value.replace("-", "."))
        forms.append(value.replace("-", " "))
    if "." in value:
        forms.append(value.replace(".", "-"))
        forms.append(value.replace(".", " "))
    return _dedupe_terms(forms)


def _us_query_ticker_forms(ticker: str) -> List[str]:
    value = (ticker or "").strip().upper()
    if not value:
        return []
    forms: List[str] = []
    if "-" in value:
        forms.append(value.replace("-", "."))
    elif "." in value:
        forms.append(value.replace(".", "-"))
    return _dedupe_terms(forms)


def _a_share_exchange_from_code(code: str) -> str:
    ticker = _normalize_ticker(code)
    if not ticker.isdigit() or len(ticker) < 6:
        return ""
    if ticker.startswith(("60", "68", "90")):
        return "SH"
    if ticker.startswith(("00", "30", "20")):
        return "SZ"
    if ticker.startswith(("43", "83", "87", "88")):
        return "BJ"
    return ""


def _dedupe_terms(values: List[str]) -> List[str]:
    result: List[str] = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return result


def _normalize_ticker(code: str) -> str:
    value = (code or "").strip()
    if "." in value:
        prefix, suffix = value.split(".", 1)
        if prefix.upper() in {"HK", "US", "SH", "SZ", "BJ"}:
            return suffix
        if suffix.upper() in {"HK", "US", "SH", "SZ", "SS", "BJ"}:
            return prefix
    return value


def _document_matches_stock(document: SearchDocument, row: ScreeningSignalRow) -> bool:
    text = _normalize_match_text(f"{document.title} {document.content} {document.url}")
    if not text:
        return False
    for term in _stock_identity_terms(row):
        normalized = _normalize_match_text(term)
        if not normalized:
            continue
        if _contains_term(text, normalized):
            return True
    return False


def _assign_documents_to_stocks(
    documents: List[SearchDocument],
    rows: List[ScreeningSignalRow],
) -> Dict[str, List[SearchDocument]]:
    grouped: Dict[str, List[SearchDocument]] = {row.code: [] for row in rows}
    for document in documents:
        for row in rows:
            if _document_matches_stock(document, row):
                grouped[row.code].append(document)
    return grouped


def _normalize_match_text(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").lower()).strip()


def _contains_term(text: str, term: str) -> bool:
    if not term:
        return False
    if re.fullmatch(r"[a-z0-9]+", term):
        return re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", text) is not None
    return term in text


def _debug_company_batch_search(
    market: str,
    rows: List[ScreeningSignalRow],
    query: str,
    max_results: int,
    documents: List[SearchDocument],
    assigned: Dict[str, List[SearchDocument]],
) -> None:
    codes = [row.code for row in rows]
    missing_codes = [code for code in codes if not assigned.get(code)]
    debug_mode = _company_search_debug_mode()
    if debug_mode == "off":
        return
    if debug_mode == "missing" and not missing_codes:
        return
    print(
        f"[公司事件搜索诊断] market={market} codes={','.join(codes)} "
        f"max_results={max_results} returned={len(documents or [])}",
        file=sys.stderr,
    )
    print(f"[公司事件搜索诊断] query={query}", file=sys.stderr)
    for row in rows:
        print(
            f"[公司事件搜索诊断] identity[{row.code}]={'|'.join(_stock_identity_terms(row))}",
            file=sys.stderr,
        )
    for index, document in enumerate(documents or [], 1):
        matched_codes = [code for code, items in assigned.items() if document in items]
        print(
            f"[公司事件搜索诊断] result#{index} matched={','.join(matched_codes) or '-'} "
            f"title={document.title[:120]} url={document.url}",
            file=sys.stderr,
        )
    if missing_codes:
        print(f"[公司事件搜索诊断] missing={','.join(missing_codes)}", file=sys.stderr)


def _company_search_debug_mode() -> str:
    value = os.getenv("SIGNAL_COMPANY_SEARCH_DEBUG")
    if value is None:
        value = os.getenv("SIGNAL_SEARCH_DEBUG")
    if value is None:
        return "missing"
    normalized = value.strip().lower()
    if normalized in {"0", "false", "no", "n", "off", "否", "关闭"}:
        return "off"
    if normalized in {"1", "true", "yes", "y", "on", "是", "full", "all"}:
        return "full"
    if normalized in {"missing", "miss", "unmatched"}:
        return "missing"
    return "missing"
