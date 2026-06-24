from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable, List, Optional, Tuple

from .models import BlockStatus, FundFlowPoint, KlinePoint, MinutePoint, QuoteSnapshot

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(value: Any) -> Optional[datetime]:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _market_timezone(market: str):
    market_key = str(market or "").upper()
    names = {
        "A": ("Asia/Shanghai",),
        "HK": ("Asia/Hong_Kong", "Asia/Shanghai"),
        "US": ("America/New_York",),
    }.get(market_key, ("UTC",))
    if ZoneInfo is None:
        return timezone.utc
    for name in names:
        try:
            return ZoneInfo(name)
        except Exception:
            continue
    return timezone.utc


def _minute_trade_date(market: str, now: Optional[datetime] = None) -> date:
    current = _parse_dt(now) or _now()
    return current.astimezone(_market_timezone(market)).date()


def _cache_status(
    source: str,
    fetched_at: Any,
    expires_at: Any,
    now: Optional[datetime] = None,
) -> BlockStatus:
    current = _parse_dt(now) or _now()
    fetched = _parse_dt(fetched_at)
    expires = _parse_dt(expires_at)
    stale = expires is not None and expires <= current
    return BlockStatus(
        status="stale" if stale else "cached",
        source=source or "",
        fetched_at=fetched,
        expires_at=expires,
        stale=stale,
    )


def _empty_status(source: str = "cache") -> BlockStatus:
    return BlockStatus(status="empty", source=source)


def _error_status(payload: dict, source: str, fetched_at: Any, expires_at: Any) -> BlockStatus:
    return BlockStatus(
        status="error",
        source=source or payload.get("source") or "",
        fetched_at=_parse_dt(payload.get("fetched_at") or fetched_at),
        expires_at=_parse_dt(payload.get("expires_at") or expires_at),
        stale=False,
        error_message=str(payload.get("error") or payload.get("error_message") or "quote provider failed"),
    )


def _quote_from_payload(payload: dict, fallback_fetched_at: Any = None, fallback_source: str = "") -> QuoteSnapshot:
    data = dict(payload or {})
    data.pop("status", None)
    data.pop("error", None)
    data.pop("error_message", None)
    data["fetched_at"] = _parse_dt(data.get("fetched_at") or fallback_fetched_at)
    data["source"] = data.get("source") or fallback_source
    return QuoteSnapshot(**data)


def _kline_from_payload(row: dict) -> KlinePoint:
    return KlinePoint(
        at=_parse_dt(row.get("at") or row.get("date") or row.get("bar_time")) or _now(),
        open=row.get("open"),
        high=row.get("high"),
        low=row.get("low"),
        close=row.get("close"),
        volume=row.get("volume"),
        turnover=row.get("turnover"),
    )


def _minute_from_payload(row: dict) -> MinutePoint:
    return MinutePoint(
        at=_parse_dt(row.get("at") or row.get("time") or row.get("bar_time")) or _now(),
        price=row.get("price"),
        average_price=row.get("average_price"),
        volume=row.get("volume"),
        turnover=row.get("turnover"),
    )


def _fund_flow_from_payload(row: dict) -> FundFlowPoint:
    return FundFlowPoint(
        at=_parse_dt(row.get("at") or row.get("time") or row.get("date")) or _now(),
        inflow=row.get("inflow"),
        outflow=row.get("outflow"),
        net_inflow=row.get("net_inflow"),
        main_net_inflow=row.get("main_net_inflow"),
        retail_net_inflow=row.get("retail_net_inflow"),
    )


def _kline_from_frame(df: Any, limit: int) -> List[KlinePoint]:
    if df is None or getattr(df, "empty", False):
        return []
    frame = df.tail(max(1, int(limit))) if hasattr(df, "tail") else df
    rows: List[KlinePoint] = []
    for _, row in frame.iterrows():
        rows.append(
            KlinePoint(
                at=_parse_dt(row.get("date") or row.get("bar_time")) or _now(),
                open=row.get("open"),
                high=row.get("high"),
                low=row.get("low"),
                close=row.get("close"),
                volume=row.get("volume"),
                turnover=row.get("turnover"),
            )
        )
    return rows


def _trade_date_for_rows(
    market: str,
    rows: Iterable[MinutePoint],
    fallback_now: Optional[datetime] = None,
) -> date:
    row_list = list(rows)
    if row_list:
        return _minute_trade_date(market, _parse_dt(row_list[0].at))
    return _minute_trade_date(market, fallback_now)


class InMemoryStockTerminalRepository:
    def __init__(self):
        self._quotes = {}
        self._klines = {}
        self._minutes = {}
        self._fund_flows = {}

    def save_quote(self, quote: QuoteSnapshot, expires_at: datetime) -> None:
        key = (quote.market, quote.code)
        fetched_at = quote.fetched_at or _now()
        self._quotes[key] = (deepcopy(quote), quote.source, fetched_at, expires_at)

    def save_quote_error(
        self,
        market: str,
        code: str,
        error_message: str,
        source: str = "",
        fetched_at: Optional[datetime] = None,
        expires_at: Optional[datetime] = None,
    ) -> None:
        fetched = fetched_at or _now()
        expires = expires_at or fetched + timedelta(minutes=1)
        payload = {"status": "error", "error": str(error_message), "source": source or "", "fetched_at": fetched.isoformat(), "expires_at": expires.isoformat()}
        self._quotes[(market, code)] = (None, source or "", fetched, expires, payload)

    def get_quote(
        self,
        market: str,
        code: str,
        now: Optional[datetime] = None,
    ) -> Tuple[Optional[QuoteSnapshot], BlockStatus]:
        cached = self._quotes.get((market, code))
        if cached is None:
            return None, _empty_status()
        if len(cached) == 5:
            _quote, source, fetched_at, expires_at, payload = cached
            return None, _error_status(payload, source, fetched_at, expires_at)
        quote, source, fetched_at, expires_at = cached
        return deepcopy(quote), _cache_status(source, fetched_at, expires_at, now=now)

    def save_klines(
        self,
        market: str,
        code: str,
        timeframe: str,
        rows: Iterable[KlinePoint],
        source: str,
        expires_at: datetime,
    ) -> None:
        key = (market, code, timeframe)
        self._klines[key] = (deepcopy(list(rows)), source, _now(), expires_at)

    def get_klines(
        self,
        market: str,
        code: str,
        timeframe: str,
        limit: int = 500,
        now: Optional[datetime] = None,
        before: Optional[datetime] = None,
    ) -> Tuple[List[KlinePoint], BlockStatus]:
        cached = self._klines.get((market, code, timeframe))
        if cached is None:
            return [], _empty_status()
        rows, source, fetched_at, expires_at = cached
        if before is not None:
            rows = [r for r in rows if r.at < before]
        return deepcopy(rows[-max(1, int(limit)):]), _cache_status(source, fetched_at, expires_at, now=now)

    def save_minute(
        self,
        market: str,
        code: str,
        rows: Iterable[MinutePoint],
        source: str,
        expires_at: datetime,
        trade_date: Optional[date] = None,
    ) -> None:
        row_list = list(rows)
        cache_trade_date = trade_date or _trade_date_for_rows(market, row_list)
        self._minutes[(market, code, cache_trade_date)] = (deepcopy(row_list), source, _now(), expires_at)

    def get_minute(
        self,
        market: str,
        code: str,
        now: Optional[datetime] = None,
        trade_date: Optional[date] = None,
    ) -> Tuple[List[MinutePoint], BlockStatus]:
        cache_trade_date = trade_date or _minute_trade_date(market, now)
        cached = self._minutes.get((market, code, cache_trade_date))
        if cached is None:
            return [], _empty_status()
        rows, source, fetched_at, expires_at = cached
        return deepcopy(rows), _cache_status(source, fetched_at, expires_at, now=now)

    def save_fund_flow(
        self,
        market: str,
        code: str,
        rows: Iterable[FundFlowPoint],
        source: str,
        expires_at: datetime,
    ) -> None:
        self._fund_flows[(market, code)] = (deepcopy(list(rows)), source, _now(), expires_at)

    def get_fund_flow(
        self,
        market: str,
        code: str,
        now: Optional[datetime] = None,
    ) -> Tuple[List[FundFlowPoint], BlockStatus]:
        cached = self._fund_flows.get((market, code))
        if cached is None:
            return [], _empty_status()
        rows, source, fetched_at, expires_at = cached
        return deepcopy(rows), _cache_status(source, fetched_at, expires_at, now=now)


class MySqlStockTerminalRepository:
    def __init__(self, db, kline_ttl: Optional[timedelta] = None):
        self.db = db
        self.kline_ttl = kline_ttl or timedelta(minutes=30)

    def save_quote(self, quote: QuoteSnapshot, expires_at: datetime) -> None:
        fetched_at = quote.fetched_at or _now()
        payload = quote.to_dict()
        payload["fetched_at"] = fetched_at.isoformat()
        self.db.upsert_stock_terminal_json_cache(
            "stock_quote_cache",
            quote.market,
            quote.code,
            payload,
            quote.source,
            fetched_at,
            expires_at,
        )

    def save_quote_error(
        self,
        market: str,
        code: str,
        error_message: str,
        source: str = "",
        fetched_at: Optional[datetime] = None,
        expires_at: Optional[datetime] = None,
    ) -> None:
        fetched = fetched_at or _now()
        expires = expires_at or fetched + timedelta(minutes=1)
        payload = {
            "status": "error",
            "market": market,
            "code": code,
            "error": str(error_message),
            "source": source or "",
            "fetched_at": fetched.isoformat(),
            "expires_at": expires.isoformat(),
        }
        self.db.upsert_stock_terminal_json_cache(
            "stock_quote_cache",
            market,
            code,
            payload,
            source or "error",
            fetched,
            expires,
        )

    def get_quote(
        self,
        market: str,
        code: str,
        now: Optional[datetime] = None,
    ) -> Tuple[Optional[QuoteSnapshot], BlockStatus]:
        row = self.db.get_stock_terminal_json_cache("stock_quote_cache", market, code)
        if not row:
            return None, _empty_status()
        payload = row.get("payload_json") or {}
        if payload.get("status") == "error":
            return None, _error_status(payload, row.get("source") or "", row.get("fetched_at"), row.get("expires_at"))
        quote = _quote_from_payload(payload, row.get("fetched_at"), row.get("source") or "")
        status = _cache_status(row.get("source") or quote.source, row.get("fetched_at"), row.get("expires_at"), now=now)
        return quote, status

    def save_klines(
        self,
        market: str,
        code: str,
        timeframe: str,
        rows: Iterable[KlinePoint],
        source: str,
        expires_at: datetime,
    ) -> None:
        payload = []
        for row in rows:
            payload.append({
                "market": market,
                "code": code,
                "timeframe": timeframe,
                "bar_time": row.at,
                "open": row.open,
                "high": row.high,
                "low": row.low,
                "close": row.close,
                "volume": row.volume,
                "turnover": row.turnover,
                "source": source,
            })
        self.db.upsert_kline_cache(payload)

    def get_klines(
        self,
        market: str,
        code: str,
        timeframe: str,
        limit: int = 500,
        now: Optional[datetime] = None,
        before: Optional[datetime] = None,
    ) -> Tuple[List[KlinePoint], BlockStatus]:
        frame = self.db.get_kline_cache(market, code, timeframe, max_count=limit, before=before)
        rows = _kline_from_frame(frame, limit)
        if not rows:
            return [], _empty_status()
        source = ""
        if "source" in frame.columns and not frame["source"].empty:
            source = str(frame["source"].iloc[-1] or "")
        fetched_at = None
        if "updated_at" in frame.columns:
            updated_values = [_parse_dt(value) for value in frame["updated_at"].tolist()]
            updated_values = [value for value in updated_values if value is not None]
            if updated_values:
                fetched_at = max(updated_values)
        if fetched_at is None:
            return rows, BlockStatus(
                status="stale",
                source=source,
                stale=True,
                error_message="missing kline updated_at",
            )
        expires_at = fetched_at + self.kline_ttl
        return rows, _cache_status(source, fetched_at, expires_at, now=now)

    def save_minute(
        self,
        market: str,
        code: str,
        rows: Iterable[MinutePoint],
        source: str,
        expires_at: datetime,
        trade_date: Optional[date] = None,
    ) -> None:
        row_list = list(rows)
        cache_trade_date = trade_date or _trade_date_for_rows(market, row_list)
        self.db.upsert_stock_terminal_json_cache(
            "stock_minute_cache",
            market,
            code,
            [row.to_dict() for row in row_list],
            source,
            _now(),
            expires_at,
            trade_date=cache_trade_date,
        )

    def get_minute(
        self,
        market: str,
        code: str,
        now: Optional[datetime] = None,
        trade_date: Optional[date] = None,
    ) -> Tuple[List[MinutePoint], BlockStatus]:
        cache_trade_date = trade_date or _minute_trade_date(market, now)
        row = self.db.get_stock_terminal_json_cache(
            "stock_minute_cache",
            market,
            code,
            trade_date=cache_trade_date,
        )
        if not row:
            return [], _empty_status()
        points = [_minute_from_payload(item) for item in (row.get("payload_json") or [])]
        status = _cache_status(row.get("source") or "", row.get("fetched_at"), row.get("expires_at"), now=now)
        return points, status

    def save_fund_flow(
        self,
        market: str,
        code: str,
        rows: Iterable[FundFlowPoint],
        source: str,
        expires_at: datetime,
    ) -> None:
        row_list = list(rows)
        self.db.upsert_stock_terminal_json_cache(
            "stock_fund_flow_cache",
            market,
            code,
            [row.to_dict() for row in row_list],
            source,
            _now(),
            expires_at,
        )

    def get_fund_flow(
        self,
        market: str,
        code: str,
        now: Optional[datetime] = None,
    ) -> Tuple[List[FundFlowPoint], BlockStatus]:
        row = self.db.get_stock_terminal_json_cache("stock_fund_flow_cache", market, code)
        if not row:
            return [], _empty_status()
        points = [_fund_flow_from_payload(item) for item in (row.get("payload_json") or [])]
        status = _cache_status(row.get("source") or "", row.get("fetched_at"), row.get("expires_at"), now=now)
        return points, status
