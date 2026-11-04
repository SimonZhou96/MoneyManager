#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Manual hot-news configuration for signal analysis."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List

from .models import SearchDocument


def _split_items(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    if not text:
        return []
    for sep in ("\n", "；", ";", "|"):
        text = text.replace(sep, "\n")
    return [item.strip() for item in text.split("\n") if item.strip()]


def _normalize_mapping(value: Any) -> Dict[str, List[str]]:
    if not isinstance(value, dict):
        return {}
    result: Dict[str, List[str]] = {}
    for key, items in value.items():
        key_text = str(key).strip()
        if not key_text:
            continue
        normalized_items = _split_items(items)
        if normalized_items:
            result[key_text] = normalized_items
    return result


def _market_items(value: Any, market: str) -> List[str]:
    if isinstance(value, dict):
        return _split_items(value.get(market) or value.get("default") or value.get("*"))
    return _split_items(value)


@dataclass(frozen=True)
class ManualHotNewsConfig:
    """User-provided hot-news overrides.

    Market news overrides market search context. Company news overrides company
    search context for matching stock codes. Source URLs are optional; when
    absent, CSV output marks the source as manual configuration.
    """

    market_hot_news: List[str] = field(default_factory=list)
    company_hot_news_by_code: Dict[str, List[str]] = field(default_factory=dict)
    market_news_sources: List[str] = field(default_factory=list)
    company_news_sources_by_code: Dict[str, List[str]] = field(default_factory=dict)

    @classmethod
    def from_env(cls, market: str) -> "ManualHotNewsConfig":
        file_config = cls.from_file(os.getenv("SIGNAL_MANUAL_HOT_NEWS_FILE", "").strip(), market)
        market_key = f"SIGNAL_MANUAL_MARKET_HOT_NEWS_{market.upper()}"
        source_key = f"SIGNAL_MANUAL_NEWS_SOURCES_{market.upper()}"

        market_hot_news = (
            _split_items(os.getenv(market_key))
            or _split_items(os.getenv("SIGNAL_MANUAL_MARKET_HOT_NEWS"))
            or file_config.market_hot_news
        )
        market_news_sources = (
            _split_items(os.getenv(source_key))
            or _split_items(os.getenv("SIGNAL_MANUAL_NEWS_SOURCES"))
            or file_config.market_news_sources
        )

        company_hot_news = dict(file_config.company_hot_news_by_code)
        company_hot_news.update(_json_mapping_from_env("SIGNAL_MANUAL_COMPANY_HOT_NEWS_JSON"))

        company_news_sources = dict(file_config.company_news_sources_by_code)
        company_news_sources.update(_json_mapping_from_env("SIGNAL_MANUAL_COMPANY_NEWS_SOURCES_JSON"))

        return cls(
            market_hot_news=market_hot_news,
            company_hot_news_by_code=company_hot_news,
            market_news_sources=market_news_sources,
            company_news_sources_by_code=company_news_sources,
        )

    @classmethod
    def from_file(cls, path: str, market: str) -> "ManualHotNewsConfig":
        if not path:
            return cls()
        try:
            content = Path(path).expanduser().read_text(encoding="utf-8")
            data = json.loads(content)
        except Exception:
            return cls()
        if not isinstance(data, dict):
            return cls()

        return cls(
            market_hot_news=_market_items(data.get("market_hot_news"), market),
            company_hot_news_by_code=_normalize_mapping(data.get("company_hot_news")),
            market_news_sources=_market_items(data.get("news_sources"), market),
            company_news_sources_by_code=_normalize_mapping(data.get("company_news_sources")),
        )

    def has_any(self) -> bool:
        return bool(self.market_hot_news or self.company_hot_news_by_code)

    def company_hot_news(self, code: str) -> List[str]:
        return list(self.company_hot_news_by_code.get(code, []))

    def news_sources_for(self, code: str) -> List[str]:
        sources = list(self.market_news_sources)
        sources.extend(self.company_news_sources_by_code.get(code, []))
        if sources:
            return _dedupe(sources)
        if self.market_hot_news or self.company_hot_news(code):
            return ["manual_config"]
        return []

    def market_documents(self, query: str) -> List[SearchDocument]:
        return [
            SearchDocument(
                title=f"手动市场热点信息 {index}",
                url=self.market_news_sources[index - 1] if index <= len(self.market_news_sources) else "manual_config",
                content=f"手动配置热点信息（优先级最高）: {item}",
                score=1.0,
                query=query,
            )
            for index, item in enumerate(self.market_hot_news, start=1)
        ]

    def company_documents(self, code: str, query: str) -> List[SearchDocument]:
        items = self.company_hot_news(code)
        sources = self.company_news_sources_by_code.get(code, [])
        return [
            SearchDocument(
                title=f"手动公司热点信息 {code} {index}",
                url=sources[index - 1] if index <= len(sources) else "manual_config",
                content=f"手动配置热点信息（优先级最高）: {item}",
                score=1.0,
                query=query,
            )
            for index, item in enumerate(items, start=1)
        ]


def _json_mapping_from_env(name: str) -> Dict[str, List[str]]:
    raw = os.getenv(name, "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except Exception:
        return {}
    return _normalize_mapping(data)


def _dedupe(items: List[str]) -> List[str]:
    seen = set()
    result = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result

