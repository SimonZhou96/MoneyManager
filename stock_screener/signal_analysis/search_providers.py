#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Search provider abstractions for signal analysis."""

from __future__ import annotations

from abc import ABC, abstractmethod
import os
import re
from typing import Dict, List, Optional

from .models import ScreeningSignalRow, SearchDocument

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

    @abstractmethod
    def search_companies_batch(
        self,
        market: str,
        rows: List[ScreeningSignalRow],
        max_results: int,
    ) -> Dict[str, List[SearchDocument]]:
        """Return company-search snippets grouped by stock code."""


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

    def search_companies_batch(
        self,
        market: str,
        rows: List[ScreeningSignalRow],
        max_results: int,
    ) -> Dict[str, List[SearchDocument]]:
        if not rows:
            return {}
        grouped: Dict[str, List[SearchDocument]] = {row.code: [] for row in rows}
        max_chars = _company_search_query_max_chars()
        for query_rows in _split_rows_by_query_budget(market, rows, max_chars):
            query = _build_company_batch_query(market, query_rows, max_chars=max_chars)
            documents = self.search(query, max_results)
            assigned = _assign_documents_to_stocks(documents, query_rows)
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
        prefix = f"{market} stocks latest news earnings events: "
    if not rows:
        return prefix.rstrip()

    for name_max_chars in (36, 30, 24, 18, 12, 8):
        query = _compose_company_batch_query(prefix, rows, name_max_chars)
        if len(query) <= max_chars:
            return query

    if len(rows) == 1:
        return _build_single_company_query(prefix, rows[0], max_chars)

    return _compose_company_batch_query(prefix, rows, 8)


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
    code = (row.code or "").strip()
    ticker = _normalize_ticker(code)
    for term in (code, ticker):
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


def _stock_identity_terms(row: ScreeningSignalRow) -> List[str]:
    terms = []
    code = (row.code or "").strip()
    ticker = _normalize_ticker(code)
    name = (row.name or "").strip()
    for term in (code, ticker, name):
        if term and term not in terms:
            terms.append(term)
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
