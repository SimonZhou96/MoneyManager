from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List

from market_intel.models import IntelItem
from market_intel.providers.base import MarketIntelProvider, ttl_for_item_type

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None


class NewsIntelProvider(MarketIntelProvider):
    provider_name = "news"
    name = "news"

    def __init__(self, session: Any = None, timeout_sec: float = 5.0):
        self.session = session
        if self.session is None and requests is not None:
            self.session = requests.Session()
        self.timeout_sec = timeout_sec

    @property
    def is_available(self) -> bool:
        return self.session is not None

    def fetch_stock(self, market: str, code: str) -> List[IntelItem]:
        return []

    def fetch_market(self, market: str) -> List[IntelItem]:
        if not self.is_available:
            return []
        response = self.session.get(
            "https://www.cls.cn/nodeapi/telegraphList",
            params={"app": "CailianpressWeb"},
            timeout=self.timeout_sec,
        )
        response.raise_for_status()
        payload = response.json()
        rows = payload.get("roll_data", []) if isinstance(payload, dict) else []
        return [self._item(market, row) for row in _dict_rows(rows) if _title(row)]

    def _item(self, market: str, row: Dict[str, Any]) -> IntelItem:
        fetched_at = datetime.now(timezone.utc)
        title = _title(row)
        published_at = _unix_datetime(row.get("ctime") or row.get("time"))
        return IntelItem(
            scope_type="market",
            market=market,
            code="",
            source="财联社",
            provider=self.provider_name,
            item_type="market_news",
            title=title,
            summary=str(row.get("content") or row.get("brief") or "").strip(),
            published_at=published_at,
            raw_json=dict(row),
            fetched_at=fetched_at,
            expires_at=fetched_at + ttl_for_item_type("market_news"),
            dedupe_key=f"{self.provider_name}:market_news:{row.get('id') or published_at or title}",
        )


def _dict_rows(rows: Any) -> Iterable[Dict[str, Any]]:
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _title(row: Dict[str, Any]) -> str:
    return str(row.get("title") or row.get("content") or "").strip()


def _unix_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        timestamp = int(value)
    except (TypeError, ValueError):
        return None
    if timestamp > 10_000_000_000:
        timestamp = timestamp / 1000
    return datetime.fromtimestamp(timestamp, timezone.utc)
