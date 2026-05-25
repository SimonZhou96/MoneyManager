from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List
from urllib.parse import quote

from market_intel.models import IntelItem
from market_intel.providers.base import MarketIntelProvider, ttl_for_item_type

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None


class TradingViewNewsIntelProvider(MarketIntelProvider):
    provider_name = "tradingview"
    name = "tradingview"

    def __init__(self, session: Any = None, timeout_sec: float = 5.0, detail_limit: int = 5):
        self.session = session
        if self.session is None and requests is not None:
            self.session = requests.Session()
        self.timeout_sec = timeout_sec
        self.detail_limit = max(0, int(detail_limit))

    @property
    def is_available(self) -> bool:
        return self.session is not None

    def fetch_stock(self, market: str, code: str) -> List[IntelItem]:
        return []

    def fetch_market(self, market: str) -> List[IntelItem]:
        if not self.is_available:
            return []
        response = self.session.get(
            "https://news-mediator.tradingview.com/news-flow/v2/news",
            params={"filter": "lang:zh-Hans", "client": "screener", "streaming": "false"},
            timeout=self.timeout_sec,
        )
        response.raise_for_status()
        rows = parse_tradingview_news_list(response.json())
        details = self._fetch_details([row for row in rows if row.get("id")][:self.detail_limit])
        return [self._item(market, row, details.get(str(row.get("id") or ""))) for row in rows]

    def _fetch_details(self, rows: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        details: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            story_id = str(row.get("id") or "")
            if not story_id:
                continue
            response = self.session.get(
                "https://news-headlines.tradingview.com/v3/story",
                params={"id": story_id, "lang": "zh-Hans"},
                timeout=self.timeout_sec,
            )
            try:
                response.raise_for_status()
                payload = response.json()
            except Exception:
                continue
            details[story_id] = payload if isinstance(payload, dict) else {}
        return details

    def _item(self, market: str, row: Dict[str, Any], detail: Dict[str, Any] | None) -> IntelItem:
        fetched_at = datetime.now(timezone.utc)
        story_id = str(row.get("id") or "")
        story = (detail or {}).get("story") if isinstance(detail, dict) else {}
        story = story if isinstance(story, dict) else {}
        summary = str(
            row.get("summary")
            or row.get("description")
            or story.get("body")
            or ""
        ).strip()
        title = str(row.get("title") or story.get("title") or story_id).strip()
        url = str(row.get("url") or story.get("link") or "").strip()
        published_at = _parse_timestamp(row.get("published") or row.get("published_at"))
        return IntelItem(
            scope_type="market",
            market=market,
            code="",
            source="TradingView",
            provider=self.provider_name,
            item_type="market_news",
            title=title,
            summary=summary,
            url=url,
            published_at=published_at,
            raw_json={"row": dict(row), "detail": dict(detail or {})},
            fetched_at=fetched_at,
            expires_at=fetched_at + ttl_for_item_type("market_news"),
            dedupe_key=f"{self.provider_name}:market_news:{story_id or quote(title)}",
        )


def parse_tradingview_news_list(payload: Any) -> List[Dict[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("items", "data", "news"):
        rows = payload.get(key)
        if isinstance(rows, list):
            return [row for row in rows if isinstance(row, dict)]
    return []


def _parse_timestamp(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        timestamp = int(value)
    except (TypeError, ValueError):
        return None
    if timestamp > 10_000_000_000:
        timestamp = timestamp / 1000
    return datetime.fromtimestamp(timestamp, timezone.utc)
