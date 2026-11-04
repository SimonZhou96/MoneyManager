from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List

from market_intel.models import IntelItem
from market_intel.providers.base import MarketIntelProvider, ttl_for_item_type

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None


class GlobalIndexProvider(MarketIntelProvider):
    provider_name = "global_index"
    name = "global_index"

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
            "https://push2.eastmoney.com/api/qt/ulist.np/get",
            params={"fields": "f2,f3,f12,f14"},
            timeout=self.timeout_sec,
        )
        response.raise_for_status()
        payload = response.json()
        return [self._item(market, row) for row in _extract_rows(payload) if _name(row)]

    def _item(self, market: str, row: Dict[str, Any]) -> IntelItem:
        fetched_at = datetime.now(timezone.utc)
        name = _name(row)
        price = row.get("f2") or row.get("price") or row.get("最新价")
        change_pct = row.get("f3") or row.get("change_pct") or row.get("涨跌幅")
        summary_parts = []
        if price not in (None, ""):
            summary_parts.append(f"price: {price}")
        if change_pct not in (None, ""):
            summary_parts.append(f"change_pct: {change_pct}")
        return IntelItem(
            scope_type="market",
            market=market,
            code=str(row.get("f12") or row.get("code") or "").strip(),
            source="东方财富指数",
            provider=self.provider_name,
            item_type="index_snapshot",
            title=name,
            summary=", ".join(summary_parts),
            raw_json=dict(row),
            fetched_at=fetched_at,
            expires_at=fetched_at + ttl_for_item_type("index_snapshot"),
            dedupe_key=f"{self.provider_name}:index_snapshot:{row.get('f12') or name}",
        )


def _extract_rows(payload: Any) -> Iterable[Dict[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if not isinstance(payload, dict):
        return []
    data = payload.get("data", payload)
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    if isinstance(data, dict):
        diff = data.get("diff")
        if isinstance(diff, list):
            return [row for row in diff if isinstance(row, dict)]
    return []


def _name(row: Dict[str, Any]) -> str:
    return str(row.get("f14") or row.get("name") or row.get("指数名称") or "").strip()
