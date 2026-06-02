from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, List

from market_intel.models import IntelItem
from market_intel.providers.base import MarketIntelProvider, ttl_for_item_type

try:
    import requests
except ImportError:  # pragma: no cover - exercised only in minimal runtimes.
    requests = None


def eastmoney_secu_code(market: str, code: str) -> str:
    market_text = (market or "").upper()
    code_text = (code or "").strip().upper()
    if market_text == "A":
        suffix = ".SH" if code_text.startswith(("600", "601", "603", "605", "688", "689")) else ".SZ"
        return f"{code_text}{suffix}"
    if market_text == "HK":
        return f"{str(int(code_text.removesuffix('.HK'))).zfill(4)}.HK"
    if market_text == "US":
        return code_text.removesuffix(".US")
    return code_text


class EastmoneyMarketIntelProvider(MarketIntelProvider):
    provider_name = "eastmoney"
    name = "eastmoney"

    def __init__(self, session: Any = None, timeout_sec: float = 5.0):
        self.session = session
        if self.session is None and requests is not None:
            self.session = requests.Session()
        self.timeout_sec = timeout_sec

    @property
    def is_available(self) -> bool:
        return self.session is not None

    def fetch_stock(self, market: str, code: str) -> List[IntelItem]:
        if not self.is_available:
            return []
        secu_code = eastmoney_secu_code(market, code)
        return [
            *self._safe_fetch(lambda: self._fetch_announcements(market, code, secu_code)),
            *self._safe_fetch(lambda: self._fetch_research_reports(market, code, secu_code)),
            *self._safe_fetch(lambda: self._fetch_financial_summary(market, code, secu_code)),
        ]

    def fetch_market(self, market: str) -> List[IntelItem]:
        return []

    def _safe_fetch(self, fetcher: Callable[[], List[IntelItem]]) -> List[IntelItem]:
        try:
            return fetcher()
        except Exception:
            return []

    def _fetch_announcements(self, market: str, code: str, secu_code: str) -> List[IntelItem]:
        payload = self._get_json(
            "https://np-anotice-stock.eastmoney.com/api/security/ann",
            params={"secucode": secu_code},
        )
        items: List[IntelItem] = []
        for row in _extract_rows(payload):
            title = _first_text(row, "title", "notice_title", "ANN_RELCOLUMNS")
            if not title:
                continue
            items.append(self._item(
                market=market,
                code=code,
                item_type="announcement",
                title=title,
                summary=_first_text(row, "summary", "content"),
                url=_first_text(row, "url", "attach_url", "art_url"),
                published_at=_parse_datetime(_first_text(row, "notice_date", "publish_date", "eiTime")),
                raw_json=row,
                dedupe_key=_first_text(row, "art_code", "notice_id", "id") or title,
            ))
        return items

    def _fetch_research_reports(self, market: str, code: str, secu_code: str) -> List[IntelItem]:
        payload = self._get_json(
            "https://reportapi.eastmoney.com/report/list",
            params={"secuCode": secu_code},
        )
        items: List[IntelItem] = []
        for row in _extract_rows(payload):
            title = _first_text(row, "title", "REPORT_TITLE", "report_title")
            if not title:
                continue
            items.append(self._item(
                market=market,
                code=code,
                item_type="research_report",
                title=title,
                summary=_first_text(row, "summary", "EM_RATING_NAME", "rating"),
                url=_first_text(row, "url", "report_url", "Url"),
                published_at=_parse_datetime(_first_text(row, "publish_date", "PUBLISH_DATE")),
                raw_json=row,
                dedupe_key=_first_text(row, "info_code", "INFO_CODE", "id") or title,
            ))
        return items

    def _fetch_financial_summary(self, market: str, code: str, secu_code: str) -> List[IntelItem]:
        payload = self._get_json(
            "https://datacenter.eastmoney.com/securities/api/data/get",
            params={"secucode": secu_code},
        )
        rows = list(_extract_rows(payload))
        if not rows:
            return []
        row = rows[0]
        name = _first_text(row, "security_name_abbr", "SECURITY_NAME_ABBR", "name") or code
        summary_parts = []
        for label, keys in [
            ("营收", ("total_operate_income", "TOTAL_OPERATE_INCOME")),
            ("净利润", ("net_profit", "NETPROFIT", "PARENT_NETPROFIT")),
            ("ROE", ("roe", "ROE")),
        ]:
            value = _first_value(row, *keys)
            if value not in (None, ""):
                summary_parts.append(f"{label}: {value}")
        return [self._item(
            market=market,
            code=code,
            item_type="financial",
            title=f"{name} 财务摘要",
            summary="; ".join(summary_parts),
            raw_json=row,
            dedupe_key=f"{secu_code}:financial:{_first_text(row, 'report_date', 'REPORT_DATE') or 'latest'}",
        )]

    def _get_json(self, url: str, params: Dict[str, Any]) -> Dict[str, Any]:
        response = self.session.get(url, params=params, timeout=self.timeout_sec)
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, dict) else {"data": payload}

    def _item(
        self,
        market: str,
        code: str,
        item_type: str,
        title: str,
        raw_json: Dict[str, Any],
        dedupe_key: str,
        summary: str = "",
        url: str = "",
        published_at: datetime | None = None,
    ) -> IntelItem:
        fetched_at = datetime.now(timezone.utc)
        return IntelItem(
            scope_type="stock",
            market=market,
            code=code,
            source="东方财富",
            provider=self.provider_name,
            item_type=item_type,
            title=title,
            summary=summary,
            url=url,
            published_at=published_at,
            raw_json=dict(raw_json),
            fetched_at=fetched_at,
            expires_at=fetched_at + ttl_for_item_type(item_type),
            dedupe_key=f"{self.provider_name}:{item_type}:{dedupe_key}",
        )


def _extract_rows(payload: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    data = payload.get("data", payload)
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    if isinstance(data, dict):
        for key in ("list", "data", "diff"):
            value = data.get(key)
            if isinstance(value, list):
                return [row for row in value if isinstance(row, dict)]
        return [data]
    return []


def _first_value(row: Dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return value
    return None


def _first_text(row: Dict[str, Any], *keys: str) -> str:
    value = _first_value(row, *keys)
    return "" if value is None else str(value).strip()


def _parse_datetime(value: str) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    if text.isdigit():
        timestamp = int(text)
        if timestamp > 10_000_000_000:
            timestamp = timestamp / 1000
        return datetime.fromtimestamp(timestamp, timezone.utc)
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None
