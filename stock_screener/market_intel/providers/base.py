from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from typing import Dict, Iterable, List, Tuple

from market_intel.models import IntelItem


class MarketIntelProvider(ABC):
    provider_name: str = ""

    @property
    @abstractmethod
    def is_available(self) -> bool:
        raise NotImplementedError

    @abstractmethod
    def fetch_stock(self, market: str, code: str) -> List[IntelItem]:
        raise NotImplementedError

    @abstractmethod
    def fetch_market(self, market: str) -> List[IntelItem]:
        raise NotImplementedError


class NullMarketIntelProvider(MarketIntelProvider):
    provider_name = "null"

    @property
    def is_available(self) -> bool:
        return False

    def fetch_stock(self, market: str, code: str) -> List[IntelItem]:
        return []

    def fetch_market(self, market: str) -> List[IntelItem]:
        return []


def ttl_for_item_type(item_type: str) -> timedelta:
    ttl_map = {
        "financial": timedelta(days=1),
        "announcement": timedelta(hours=12),
        "research_report": timedelta(hours=12),
        "money_flow": timedelta(minutes=30),
        "long_tiger": timedelta(minutes=30),
        "hot_sector": timedelta(minutes=30),
        "market_news": timedelta(minutes=15),
        "index_snapshot": timedelta(minutes=15),
        "search_document": timedelta(hours=2),
    }
    return ttl_map.get(item_type, timedelta(hours=6))


def dedupe_items(items: Iterable[IntelItem]) -> List[IntelItem]:
    seen: set[Tuple[str, str, str, str, str]] = set()
    deduped: List[IntelItem] = []
    for item in items:
        key = (
            item.scope_type,
            item.market,
            item.code,
            item.provider,
            item.dedupe_key,
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _datetime_sort_timestamp(value: datetime | None) -> float:
    if value is None:
        return 0.0
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value.timestamp()


def _item_sort_timestamp(item: IntelItem) -> float:
    return _datetime_sort_timestamp(item.published_at or item.fetched_at)


def group_items(items: Iterable[IntelItem]) -> Dict[str, List[IntelItem]]:
    grouped: Dict[str, List[IntelItem]] = {}
    for item in items:
        grouped.setdefault(item.item_type, []).append(item)
    for values in grouped.values():
        values.sort(key=_item_sort_timestamp, reverse=True)
    return grouped
