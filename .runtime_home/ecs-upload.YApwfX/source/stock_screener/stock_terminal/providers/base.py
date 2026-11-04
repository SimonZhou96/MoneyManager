from __future__ import annotations

from typing import List, Protocol

from ..models import FundFlowPoint, KlinePoint, MinutePoint, QuoteSnapshot


class StockTerminalProvider(Protocol):
    name: str

    def fetch_quote(self, market: str, code: str) -> QuoteSnapshot:
        ...

    def fetch_klines(self, market: str, code: str, timeframe: str, limit: int) -> List[KlinePoint]:
        ...

    def fetch_minute(self, market: str, code: str) -> List[MinutePoint]:
        ...

    def fetch_fund_flow(self, market: str, code: str) -> List[FundFlowPoint]:
        ...


class EmptyStockTerminalProvider:
    name = "empty"

    def fetch_quote(self, market: str, code: str) -> QuoteSnapshot:
        raise RuntimeError("stock terminal quote provider unavailable")

    def fetch_klines(self, market: str, code: str, timeframe: str, limit: int) -> List[KlinePoint]:
        raise RuntimeError("stock terminal kline provider unavailable")

    def fetch_minute(self, market: str, code: str) -> List[MinutePoint]:
        raise RuntimeError("stock terminal minute provider unavailable")

    def fetch_fund_flow(self, market: str, code: str) -> List[FundFlowPoint]:
        raise RuntimeError("stock terminal fund flow provider unavailable")
