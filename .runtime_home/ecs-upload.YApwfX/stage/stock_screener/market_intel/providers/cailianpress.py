from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any, Dict, Iterable, List

from market_intel.models import IntelItem
from market_intel.providers.base import MarketIntelProvider, ttl_for_item_type

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None


class CailianpressIntelProvider(MarketIntelProvider):
    provider_name = "cailianpress"
    name = "cailianpress"

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
            headers={"Referer": "https://www.cls.cn/"},
            timeout=self.timeout_sec,
        )
        response.raise_for_status()
        rows = _extract_roll_data(response.json())
        return [self._item(market, row) for row in rows if _title(row)]

    def _item(self, market: str, row: Dict[str, Any]) -> IntelItem:
        fetched_at = datetime.now(timezone.utc)
        title = _title(row)
        published_at = _unix_datetime(row.get("ctime") or row.get("time"))
        source_id = row.get("id") or row.get("telegraph_id") or published_at or title
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
            dedupe_key=f"{self.provider_name}:market_news:{source_id}",
        )


def parse_cailianpress_html(html: str) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    box_pattern = re.compile(
        r'class="[^"]*telegraph-content-box[^"]*"[^>]*>.*?'
        r'class="[^"]*telegraph-time[^"]*"[^>]*>(?P<time>.*?)</[^>]+>.*?'
        r'class="[^"]*telegraph-content[^"]*"[^>]*>(?P<content>.*?)</div>',
        flags=re.S,
    )
    for match in box_pattern.finditer(html or ""):
        time_text = _strip_html(match.group("time"))
        content = _strip_html(match.group("content"))
        if content:
            rows.append({"title": content, "content": content, "time": time_text})
    if rows:
        return rows
    for content in re.findall(r'class="[^"]*telegraph-content[^"]*"[^>]*>(.*?)</div>', html or "", flags=re.S):
        text = _strip_html(content)
        if text:
            rows.append({"title": text, "content": text, "time": ""})
    return rows


def _extract_roll_data(payload: Any) -> List[Dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    rows = payload.get("roll_data")
    if isinstance(rows, list):
        return list(_dict_rows(rows))
    data = payload.get("data")
    if isinstance(data, dict) and isinstance(data.get("roll_data"), list):
        return list(_dict_rows(data.get("roll_data")))
    return []


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


def _first_match(text: str, pattern: str) -> str:
    match = re.search(pattern, text, flags=re.S)
    return match.group(1) if match else ""


def _strip_html(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", text or "")).strip()
