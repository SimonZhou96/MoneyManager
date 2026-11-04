from __future__ import annotations

from datetime import datetime, timezone
from typing import List

from ..models import FundFlowPoint, KlinePoint, MinutePoint, QuoteSnapshot


class YFinanceStockTerminalProvider:
    name = "yfinance"

    def fetch_quote(self, market: str, code: str) -> QuoteSnapshot:
        symbol = self._yfinance_symbol(market, code)
        try:
            import yfinance as yf
        except Exception as exc:
            raise RuntimeError(f"yfinance unavailable: {exc}") from exc

        ticker = yf.Ticker(symbol)
        history = ticker.history(period="5d", interval="1d")
        if history is None or history.empty:
            raise RuntimeError(f"yfinance returned no quote data for {symbol}")
        row = history.tail(1).iloc[0]
        previous_close = _fast_info_value(ticker, "previous_close")
        price = _fast_info_value(ticker, "last_price") or row.get("Close")
        change = _difference(price, previous_close)
        change_percent = _percent(change, previous_close)
        market_cap = _fast_info_value(ticker, "market_cap")
        return QuoteSnapshot(
            market=market,
            code=code,
            name=symbol,
            price=price,
            change=change,
            change_percent=change_percent,
            open_price=row.get("Open"),
            high=row.get("High"),
            low=row.get("Low"),
            previous_close=previous_close,
            volume=row.get("Volume"),
            turnover=None,
            market_cap=market_cap,
            fetched_at=datetime.now(timezone.utc),
            source=self.name,
        )

    def fetch_klines(self, market: str, code: str, timeframe: str, limit: int) -> List[KlinePoint]:
        raise RuntimeError("yfinance kline provider is not configured for stock terminal fallback")

    def fetch_minute(self, market: str, code: str) -> List[MinutePoint]:
        raise RuntimeError("yfinance minute provider is not configured for stock terminal fallback")

    def fetch_fund_flow(self, market: str, code: str) -> List[FundFlowPoint]:
        raise RuntimeError("yfinance fund flow provider is not configured for stock terminal fallback")

    @staticmethod
    def _yfinance_symbol(market: str, code: str) -> str:
        market_key = str(market or "").upper()
        value = str(code or "").strip().upper()
        bare = _strip_prefix(value)
        if market_key == "A":
            if value.startswith("SH."):
                return f"{bare}.SS"
            if value.startswith("SZ."):
                return f"{bare}.SZ"
            if value.startswith("BJ."):
                return f"{bare}.BJ"
            return f"{bare}.SS" if bare.startswith(("6", "9")) else f"{bare}.SZ"
        if market_key == "HK":
            return f"{bare.zfill(5) if bare.isdigit() else bare}.HK"
        if market_key == "US":
            return bare[:-3] if bare.endswith(".US") else bare
        if market_key == "JP":
            return f"{bare}.T"
        if market_key == "TW":
            return f"{bare}.TW"
        if market_key == "KR":
            return f"{bare.zfill(6) if bare.isdigit() else bare}.KS"
        raise RuntimeError(f"yfinance quote unsupported market: {market}")


def _strip_prefix(value: str) -> str:
    for prefix in ("SH.", "SZ.", "BJ.", "HK.", "US.", "JP.", "TW.", "KR."):
        if value.startswith(prefix):
            return value[len(prefix):]
    return value


def _fast_info_value(ticker, key: str):
    try:
        info = ticker.fast_info
        if hasattr(info, key):
            return getattr(info, key)
        return info.get(key)
    except Exception:
        return None


def _difference(value, previous):
    try:
        if value is None or previous is None:
            return None
        return float(value) - float(previous)
    except Exception:
        return None


def _percent(change, previous):
    try:
        if change is None or previous in (None, 0):
            return None
        return float(change) / float(previous) * 100.0
    except Exception:
        return None
