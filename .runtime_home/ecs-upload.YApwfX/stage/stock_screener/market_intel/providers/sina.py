from __future__ import annotations

from datetime import datetime, timezone
import json
import re
import time
from typing import Any, Dict, List

from market_intel.models import IntelItem
from market_intel.providers.base import MarketIntelProvider, ttl_for_item_type

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None


class SinaNewsIntelProvider(MarketIntelProvider):
    provider_name = "sina"
    name = "sina"

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
            "https://zhibo.sina.com.cn/api/zhibo/feed",
            params={
                "callback": "callback",
                "page": 1,
                "page_size": 20,
                "zhibo_id": 152,
                "tag_id": 0,
                "dire": "f",
                "dpc": 1,
                "pagesize": 20,
                "type": 0,
                "_": int(time.time()),
            },
            timeout=self.timeout_sec,
        )
        response.raise_for_status()
        return [self._item(market, row) for row in parse_sina_live_feed(response.text)]

    def _item(self, market: str, row: Dict[str, Any]) -> IntelItem:
        fetched_at = datetime.now(timezone.utc)
        published_at = _parse_datetime(row.get("created_at") or row.get("time"))
        title = str(row.get("title") or row.get("content") or "").strip()
        return IntelItem(
            scope_type="market",
            market=market,
            code="",
            source="新浪财经",
            provider=self.provider_name,
            item_type="market_news",
            title=title,
            summary=str(row.get("content") or "").strip(),
            url=str(row.get("url") or "").strip(),
            published_at=published_at,
            raw_json=dict(row),
            fetched_at=fetched_at,
            expires_at=fetched_at + ttl_for_item_type("market_news"),
            dedupe_key=f"{self.provider_name}:market_news:{row.get('id') or row.get('url') or published_at or title}",
        )


def parse_sina_live_feed(text: str) -> List[Dict[str, Any]]:
    payload = _parse_jsonp(text)
    result = payload.get("result") if isinstance(payload, dict) else {}
    data = result.get("data") if isinstance(result, dict) else {}
    feed = data.get("feed") if isinstance(data, dict) else {}
    rows = feed.get("list") if isinstance(feed, dict) else []
    return [
        row
        for row in rows
        if isinstance(row, dict) and str(row.get("title") or row.get("content") or "").strip()
    ]


def _parse_jsonp(text: str) -> Dict[str, Any]:
    match = re.match(r"^[^(]*\((.*)\)\s*;?\s*$", text or "", flags=re.S)
    if not match:
        return {}
    try:
        payload = json.loads(match.group(1))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _parse_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)
