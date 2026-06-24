from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import List

from ..models import FundFlowPoint, KlinePoint, MinutePoint, QuoteSnapshot


class FutuStockTerminalProvider:
    name = "futu"

    def __init__(self, host: str | None = None, port: int | None = None, timeout_sec: float = 5.0):
        self.host = host or os.getenv("FUTU_HOST", "127.0.0.1")
        self.port = int(port or os.getenv("FUTU_PORT", "11111"))
        self.timeout_sec = timeout_sec

    def fetch_quote(self, market: str, code: str) -> QuoteSnapshot:
        futu_code = self._futu_code(market, code)
        try:
            import futu as ft
        except Exception as exc:
            raise RuntimeError(f"futu unavailable: {exc}") from exc

        quote_ctx = None
        try:
            quote_ctx = ft.OpenQuoteContext(host=self.host, port=self.port)
            ret, data = quote_ctx.get_market_snapshot([futu_code])
            if ret != ft.RET_OK:
                raise RuntimeError(str(data))
            if data is None or data.empty:
                raise RuntimeError("empty market snapshot")
            row = data.iloc[0]
            return QuoteSnapshot(
                market=market,
                code=code,
                name=str(row.get("name") or code),
                price=row.get("last_price"),
                change=_difference(row.get("last_price"), row.get("prev_close_price")),
                change_percent=row.get("change_rate"),
                open_price=row.get("open_price"),
                high=row.get("high_price"),
                low=row.get("low_price"),
                previous_close=row.get("prev_close_price"),
                volume=row.get("volume"),
                turnover=row.get("turnover"),
                market_cap=row.get("market_val") or row.get("market_cap"),
                fetched_at=datetime.now(timezone.utc),
                source=self.name,
            )
        finally:
            if quote_ctx is not None:
                try:
                    quote_ctx.close()
                except Exception:
                    pass

    def fetch_klines(self, market: str, code: str, timeframe: str, limit: int) -> List[KlinePoint]:
        raise RuntimeError("futu kline provider is not configured for stock terminal fallback")

    def fetch_minute(self, market: str, code: str) -> List[MinutePoint]:
        raise RuntimeError("futu minute provider is not configured for stock terminal fallback")

    def fetch_fund_flow(self, market: str, code: str) -> List[FundFlowPoint]:
        raise RuntimeError("futu fund flow provider is not configured for stock terminal fallback")

    @staticmethod
    def _futu_code(market: str, code: str) -> str:
        market_key = str(market or "").upper()
        value = str(code or "").strip().upper()
        if market_key == "A":
            return value
        if market_key == "HK":
            if value.startswith("HK."):
                return value
            return f"HK.{value.zfill(5) if value.isdigit() else value}"
        if market_key == "US":
            if value.startswith("US."):
                return value
            ticker = value[:-3] if value.endswith(".US") else value
            return f"US.{ticker}"
        raise RuntimeError(f"futu quote unsupported market: {market}")


def _difference(value, previous):
    try:
        if value is None or previous is None:
            return None
        return float(value) - float(previous)
    except Exception:
        return None
