from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from math import isfinite
from typing import Any, Dict, List, Optional


def _dt(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def _num(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not isfinite(number):
        return None
    return number


@dataclass
class BlockStatus:
    status: str
    source: str = ""
    fetched_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    stale: bool = False
    error_message: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "source": self.source,
            "fetched_at": _dt(self.fetched_at),
            "expires_at": _dt(self.expires_at),
            "stale": bool(self.stale),
            "error_message": self.error_message,
        }


def data_status(
    status: str,
    source: str = "",
    fetched_at: Optional[datetime] = None,
    expires_at: Optional[datetime] = None,
    error_message: str = "",
) -> BlockStatus:
    stale = False
    if expires_at is not None:
        expires_at_utc = expires_at
        if expires_at_utc.tzinfo is None:
            expires_at_utc = expires_at_utc.replace(tzinfo=timezone.utc)
        else:
            expires_at_utc = expires_at_utc.astimezone(timezone.utc)
        stale = expires_at_utc < datetime.now(timezone.utc)
    return BlockStatus(
        status=status,
        source=source,
        fetched_at=fetched_at,
        expires_at=expires_at,
        stale=stale,
        error_message=error_message,
    )


@dataclass
class QuoteSnapshot:
    market: str
    code: str
    name: str = ""
    price: Any = None
    change: Any = None
    change_percent: Any = None
    open_price: Any = None
    high: Any = None
    low: Any = None
    previous_close: Any = None
    volume: Any = None
    turnover: Any = None
    fetched_at: Optional[datetime] = None
    source: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "market": self.market,
            "code": self.code,
            "name": self.name,
            "price": _num(self.price),
            "change": _num(self.change),
            "change_percent": _num(self.change_percent),
            "open_price": _num(self.open_price),
            "high": _num(self.high),
            "low": _num(self.low),
            "previous_close": _num(self.previous_close),
            "volume": _num(self.volume),
            "turnover": _num(self.turnover),
            "fetched_at": _dt(self.fetched_at),
            "source": self.source,
        }


@dataclass
class KlinePoint:
    at: datetime
    open: Any = None
    high: Any = None
    low: Any = None
    close: Any = None
    volume: Any = None
    turnover: Any = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "at": _dt(self.at),
            "open": _num(self.open),
            "high": _num(self.high),
            "low": _num(self.low),
            "close": _num(self.close),
            "volume": _num(self.volume),
            "turnover": _num(self.turnover),
        }


@dataclass
class MinutePoint:
    at: datetime
    price: Any = None
    average_price: Any = None
    volume: Any = None
    turnover: Any = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "at": _dt(self.at),
            "price": _num(self.price),
            "average_price": _num(self.average_price),
            "volume": _num(self.volume),
            "turnover": _num(self.turnover),
        }


@dataclass
class FundFlowPoint:
    at: datetime
    inflow: Any = None
    outflow: Any = None
    net_inflow: Any = None
    main_net_inflow: Any = None
    retail_net_inflow: Any = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "at": _dt(self.at),
            "inflow": _num(self.inflow),
            "outflow": _num(self.outflow),
            "net_inflow": _num(self.net_inflow),
            "main_net_inflow": _num(self.main_net_inflow),
            "retail_net_inflow": _num(self.retail_net_inflow),
        }


@dataclass
class StockTerminalSummary:
    market: str
    code: str
    name: str = ""
    quote: Optional[QuoteSnapshot] = None
    statuses: Dict[str, BlockStatus] = field(default_factory=dict)
    data_gaps: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "market": self.market,
            "code": self.code,
            "name": self.name,
            "quote": self.quote.to_dict() if self.quote is not None else None,
            "source_status": {
                key: value.to_dict() if hasattr(value, "to_dict") else dict(value)
                for key, value in self.statuses.items()
            },
            "data_gaps": list(self.data_gaps),
        }
