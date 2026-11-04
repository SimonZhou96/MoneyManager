from __future__ import annotations

from datetime import date
from typing import Iterable

import pandas as pd

from kline_fetcher import managed_fetcher_chain

from .models import Bar


class CachedKlineDataProvider:
    def __init__(
        self,
        repository,
        timeframe: str = "1d",
        max_count: int = 1000,
        allow_network_fallback: bool = True,
        quote_ctx=None,
        close_quote_ctx: bool = False,
    ):
        self.repository = repository
        self.timeframe = timeframe or "1d"
        self.max_count = int(max_count)
        self.allow_network_fallback = bool(allow_network_fallback)
        self.quote_ctx = quote_ctx
        self.close_quote_ctx = bool(close_quote_ctx)

    def close(self) -> None:
        if self.close_quote_ctx and self.quote_ctx is not None:
            self.quote_ctx.close()

    def bars_for(self, market: str, symbol: str, start: date, end: date) -> list[Bar]:
        df = self._load_frame(market, symbol)
        if df.empty:
            return []
        work = df.copy()
        work["date"] = pd.to_datetime(work["date"]).dt.date
        work = work[(work["date"] >= start) & (work["date"] <= end)]
        work = work.drop_duplicates(subset=["date"]).sort_values("date")
        return [
            Bar(
                ts=row["date"],
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row.get("volume") or 0),
            )
            for _, row in work.sort_values("date").iterrows()
            if _has_ohlc(row)
        ]

    def _load_frame(self, market: str, symbol: str) -> pd.DataFrame:
        if self.quote_ctx is not None:
            return self._fetch_from_fetchers(
                [self._futu_fetcher()], market, symbol
            )
        if self.allow_network_fallback:
            return self._fetch_network_frame(market, symbol)
        return pd.DataFrame()

    def _futu_fetcher(self):
        from kline_fetcher import FutuKlineFetcher
        return FutuKlineFetcher(self.quote_ctx)

    def _fetch_futu_frame(self, market: str, symbol: str) -> pd.DataFrame:
        return self._fetch_from_fetchers([self._futu_fetcher()], market, symbol)

    def _fetch_network_frame(self, market: str, symbol: str) -> pd.DataFrame:
        with managed_fetcher_chain() as fetchers:
            return self._fetch_from_fetchers(fetchers, market, symbol)

    def _fetch_from_fetchers(self, fetchers, market: str, symbol: str) -> pd.DataFrame:
        for code in _code_candidates(market, symbol):
            for fetcher in fetchers:
                frame = fetcher.fetch(code, market=market, timeframe=self.timeframe, max_count=self.max_count)
                if frame is not None and not frame.empty:
                    return frame
        return pd.DataFrame()


def _code_candidates(market: str, symbol: str) -> Iterable[str]:
    yield symbol
    prefix = f"{market}."
    if symbol.startswith(prefix):
        yield symbol[len(prefix):]


def _has_ohlc(row) -> bool:
    return all(pd.notna(row.get(key)) for key in ("open", "high", "low", "close"))
