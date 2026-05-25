from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, List

from ..models import FundFlowPoint, KlinePoint, MinutePoint, QuoteSnapshot


def _parse_dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif value in (None, ""):
        parsed = datetime.now(timezone.utc)
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


class EastmoneyStockTerminalProvider:
    name = "eastmoney"

    def __init__(self, db=None):
        self.db = db
        self.last_source = self.name

    def fetch_quote(self, market: str, code: str) -> QuoteSnapshot:
        raise RuntimeError("eastmoney quote capability is scheduled for Task 7")

    def fetch_klines(self, market: str, code: str, timeframe: str, limit: int) -> List[KlinePoint]:
        try:
            from ...kline_fetcher import KlineFetcherFactory
        except ImportError:
            from kline_fetcher import KlineFetcherFactory

        fetchers = KlineFetcherFactory.create_fetcher_chain(db=self.db)
        errors = []
        for fetcher in fetchers:
            source = fetcher.get_name()
            try:
                frame = fetcher.fetch(code, market=market, timeframe=timeframe, max_count=limit)
            except Exception as exc:
                errors.append(f"{source}: {exc}")
                continue
            rows = self._rows_from_frame(frame, limit)
            if rows:
                self.last_source = source
                return rows

        if errors:
            raise RuntimeError("; ".join(errors))
        raise RuntimeError("eastmoney kline provider returned no data")

    def fetch_minute(self, market: str, code: str) -> List[MinutePoint]:
        raise RuntimeError("eastmoney minute capability is scheduled for Task 7")

    def fetch_fund_flow(self, market: str, code: str) -> List[FundFlowPoint]:
        raise RuntimeError("eastmoney fund flow capability is scheduled for Task 7")

    def _rows_from_frame(self, frame: Any, limit: int) -> List[KlinePoint]:
        if frame is None or getattr(frame, "empty", False):
            return []
        limited = frame.tail(max(1, int(limit))) if hasattr(frame, "tail") else frame
        rows: List[KlinePoint] = []
        for _, row in limited.iterrows():
            rows.append(
                KlinePoint(
                    at=_parse_dt(row.get("date") or row.get("bar_time")),
                    open=row.get("open"),
                    high=row.get("high"),
                    low=row.get("low"),
                    close=row.get("close"),
                    volume=row.get("volume"),
                    turnover=row.get("turnover"),
                )
            )
        return rows
