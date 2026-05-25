from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Callable, Iterable, List, Optional, Union

from .models import (
    BlockStatus,
    FundFlowPoint,
    KlinePoint,
    MinutePoint,
    StockTerminalSummary,
)
from .providers.base import StockTerminalProvider


class StockTerminalService:
    def __init__(
        self,
        repository,
        providers: Iterable[StockTerminalProvider],
        now: Optional[Callable[[], datetime]] = None,
    ):
        self.repository = repository
        self.providers = list(providers)
        self.now = now or (lambda: datetime.now(timezone.utc))

    def get_summary(self, market: str, code: str) -> dict:
        current = self.now()
        quote, status = self.repository.get_quote(market, code, now=current)
        if quote is not None and status.status == "cached":
            return StockTerminalSummary(
                market=market,
                code=code,
                name=quote.name,
                quote=quote,
                statuses={"quote": status},
            ).to_dict()

        try:
            fresh, provider = self._fetch_first(lambda item: item.fetch_quote(market, code))
        except RuntimeError as exc:
            if quote is not None:
                stale_status = BlockStatus(
                    status="stale",
                    source=status.source,
                    fetched_at=status.fetched_at,
                    expires_at=status.expires_at,
                    stale=True,
                    error_message=str(exc),
                )
                return StockTerminalSummary(
                    market=market,
                    code=code,
                    name=quote.name,
                    quote=quote,
                    statuses={"quote": stale_status},
                    data_gaps=["quote"],
                ).to_dict()
            error_status = BlockStatus(status="error", source="", error_message=str(exc))
            return StockTerminalSummary(
                market=market,
                code=code,
                statuses={"quote": error_status},
                data_gaps=["quote"],
            ).to_dict()

        fetched_at = fresh.fetched_at or current
        fresh.fetched_at = fetched_at
        source = self._last_provider_name(provider)
        fresh.source = source
        self.repository.save_quote(fresh, expires_at=current + timedelta(minutes=5))
        fresh_status = BlockStatus(
            status="fresh",
            source=fresh.source,
            fetched_at=fetched_at,
            expires_at=current + timedelta(minutes=5),
        )
        return StockTerminalSummary(
            market=market,
            code=code,
            name=fresh.name,
            quote=fresh,
            statuses={"quote": fresh_status},
        ).to_dict()

    def get_klines(self, market: str, code: str, timeframe: str, limit: int = 500) -> dict:
        current = self.now()
        rows, status = self.repository.get_klines(market, code, timeframe, limit=limit, now=current)
        if rows and status.status == "cached":
            return self._series_payload(market, code, rows, "kline", status, timeframe=timeframe)

        expires_at = current + timedelta(minutes=30)
        try:
            fresh, provider = self._fetch_first(
                lambda item: item.fetch_klines(market, code, timeframe, limit)
            )
        except RuntimeError as exc:
            return self._series_error_payload(market, code, rows, "kline", status, exc, timeframe=timeframe)

        source = self._last_provider_name(provider)
        self.repository.save_klines(market, code, timeframe, fresh, source=source, expires_at=expires_at)
        fresh_status = BlockStatus("fresh", source=source, fetched_at=current, expires_at=expires_at)
        return self._series_payload(market, code, fresh, "kline", fresh_status, timeframe=timeframe)

    def get_minute(self, market: str, code: str) -> dict:
        current = self.now()
        rows, status = self.repository.get_minute(market, code, now=current)
        if rows and status.status == "cached":
            return self._series_payload(market, code, rows, "minute", status)

        expires_at = current + timedelta(minutes=1)
        try:
            fresh, provider = self._fetch_first(lambda item: item.fetch_minute(market, code))
        except RuntimeError as exc:
            return self._series_error_payload(market, code, rows, "minute", status, exc)

        source = self._last_provider_name(provider)
        self.repository.save_minute(market, code, fresh, source=source, expires_at=expires_at)
        fresh_status = BlockStatus("fresh", source=source, fetched_at=current, expires_at=expires_at)
        return self._series_payload(market, code, fresh, "minute", fresh_status)

    def get_fund_flow(self, market: str, code: str) -> dict:
        current = self.now()
        rows, status = self.repository.get_fund_flow(market, code, now=current)
        if rows and status.status == "cached":
            return self._series_payload(market, code, rows, "fund_flow", status)

        expires_at = current + timedelta(minutes=30)
        try:
            fresh, provider = self._fetch_first(lambda item: item.fetch_fund_flow(market, code))
        except RuntimeError as exc:
            return self._series_error_payload(market, code, rows, "fund_flow", status, exc)

        source = self._last_provider_name(provider)
        self.repository.save_fund_flow(market, code, fresh, source=source, expires_at=expires_at)
        fresh_status = BlockStatus("fresh", source=source, fetched_at=current, expires_at=expires_at)
        return self._series_payload(market, code, fresh, "fund_flow", fresh_status)

    def _fetch_first(self, fetcher):
        errors = []
        for provider in self.providers:
            provider_name = getattr(provider, "name", provider.__class__.__name__)
            try:
                result = fetcher(provider)
            except Exception as exc:
                errors.append(f"{provider_name}: {exc}")
                continue
            if result is None:
                errors.append(f"{provider_name}: returned no data")
                continue
            return result, provider
        if errors:
            raise RuntimeError("; ".join(errors))
        raise RuntimeError("no stock terminal providers configured")

    def _last_provider_name(self, provider) -> str:
        return getattr(provider, "name", provider.__class__.__name__)

    def _series_payload(
        self,
        market: str,
        code: str,
        rows: Iterable,
        status_key: str,
        status: BlockStatus,
        timeframe: Optional[str] = None,
    ) -> dict:
        payload = {
            "market": market,
            "code": code,
            "rows": [row.to_dict() if hasattr(row, "to_dict") else dict(row) for row in rows],
            "source_status": {status_key: status.to_dict()},
            "data_gaps": [],
        }
        if timeframe is not None:
            payload["timeframe"] = timeframe
        return payload

    def _series_error_payload(
        self,
        market: str,
        code: str,
        rows: Union[List[KlinePoint], List[MinutePoint], List[FundFlowPoint]],
        status_key: str,
        status: BlockStatus,
        exc: RuntimeError,
        timeframe: Optional[str] = None,
    ) -> dict:
        if rows:
            source_status = BlockStatus(
                status="stale",
                source=status.source,
                fetched_at=status.fetched_at,
                expires_at=status.expires_at,
                stale=True,
                error_message=str(exc),
            )
        else:
            source_status = BlockStatus(status="error", source="", error_message=str(exc))
        payload = {
            "market": market,
            "code": code,
            "rows": [row.to_dict() for row in rows],
            "source_status": {status_key: source_status.to_dict()},
            "data_gaps": [status_key],
        }
        if timeframe is not None:
            payload["timeframe"] = timeframe
        return payload
