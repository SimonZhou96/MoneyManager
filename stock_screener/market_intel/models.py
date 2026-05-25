from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional


def parse_datetime(value: Any) -> Optional[datetime]:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = f"{text[:-1]}+00:00"
        return datetime.fromisoformat(text)
    raise TypeError(f"Unsupported datetime value: {value!r}")


def datetime_to_json(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    return value.isoformat()


@dataclass
class IntelItem:
    scope_type: str
    market: str
    source: str
    provider: str
    item_type: str
    title: str
    fetched_at: datetime
    expires_at: datetime
    dedupe_key: str
    code: str = ""
    summary: str = ""
    url: str = ""
    published_at: Optional[datetime] = None
    raw_json: Dict[str, Any] = field(default_factory=dict)
    is_stale: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "scope_type": self.scope_type,
            "market": self.market,
            "code": self.code,
            "source": self.source,
            "provider": self.provider,
            "item_type": self.item_type,
            "title": self.title,
            "summary": self.summary,
            "url": self.url,
            "published_at": datetime_to_json(self.published_at),
            "raw_json": dict(self.raw_json),
            "fetched_at": datetime_to_json(self.fetched_at),
            "expires_at": datetime_to_json(self.expires_at),
            "is_stale": self.is_stale,
            "dedupe_key": self.dedupe_key,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "IntelItem":
        return cls(
            scope_type=data["scope_type"],
            market=data["market"],
            code=data.get("code") or "",
            source=data["source"],
            provider=data["provider"],
            item_type=data["item_type"],
            title=data["title"],
            summary=data.get("summary") or "",
            url=data.get("url") or "",
            published_at=parse_datetime(data.get("published_at")),
            raw_json=dict(data.get("raw_json") or {}),
            fetched_at=parse_datetime(data.get("fetched_at")),
            expires_at=parse_datetime(data.get("expires_at")),
            is_stale=bool(data.get("is_stale", False)),
            dedupe_key=data["dedupe_key"],
        )


@dataclass
class DataSourceStatus:
    provider: str
    status: str
    item_count: int
    error_message: str = ""
    fetched_at: Optional[datetime] = None
    stale: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "provider": self.provider,
            "status": self.status,
            "item_count": self.item_count,
            "error_message": self.error_message,
            "fetched_at": datetime_to_json(self.fetched_at),
            "stale": self.stale,
        }


def _group_item_dicts(items: List[IntelItem]) -> Dict[str, List[Dict[str, Any]]]:
    grouped: Dict[str, List[IntelItem]] = {}
    for item in items:
        grouped.setdefault(item.item_type, []).append(item)
    return {
        item_type: [item.to_dict() for item in sorted(
            values,
            key=lambda item: item.published_at or item.fetched_at,
            reverse=True,
        )]
        for item_type, values in grouped.items()
    }


def _source_status_dicts(source_status: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {
        key: value.to_dict() if hasattr(value, "to_dict") else dict(value)
        for key, value in source_status.items()
    }


@dataclass
class StockIntelBundle:
    market: str
    code: str
    items: List[IntelItem] = field(default_factory=list)
    freshness_status: str = "empty"
    source_status: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "scope_type": "stock",
            "market": self.market,
            "code": self.code,
            "items": _group_item_dicts(self.items),
            "freshness_status": self.freshness_status,
            "source_status": _source_status_dicts(self.source_status),
        }


@dataclass
class MarketIntelBundle:
    market: str
    items: List[IntelItem] = field(default_factory=list)
    freshness_status: str = "empty"
    source_status: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "scope_type": "market",
            "market": self.market,
            "code": "",
            "items": _group_item_dicts(self.items),
            "freshness_status": self.freshness_status,
            "source_status": _source_status_dicts(self.source_status),
        }


@dataclass
class EvidencePack:
    market: str
    code: str = ""
    structured_items: List[IntelItem] = field(default_factory=list)
    search_documents: List[IntelItem] = field(default_factory=list)
    manual_items: List[IntelItem] = field(default_factory=list)
    market_context: Dict[str, Any] = field(default_factory=dict)
    stock_context: Dict[str, Any] = field(default_factory=dict)
    source_status: Dict[str, Any] = field(default_factory=dict)
    data_gaps: List[str] = field(default_factory=list)
    citations: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "market": self.market,
            "code": self.code,
            "structured_items": [item.to_dict() for item in self.structured_items],
            "search_documents": [item.to_dict() for item in self.search_documents],
            "manual_items": [item.to_dict() for item in self.manual_items],
            "market_context": dict(self.market_context),
            "stock_context": dict(self.stock_context),
            "source_status": _source_status_dicts(self.source_status),
            "data_gaps": list(self.data_gaps),
            "citations": list(self.citations),
        }
