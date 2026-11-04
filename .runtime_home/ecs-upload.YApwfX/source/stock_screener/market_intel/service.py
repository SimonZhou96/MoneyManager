from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from time import perf_counter
from typing import Iterable, List, Optional

from market_intel.models import DataSourceStatus, IntelItem, MarketIntelBundle, StockIntelBundle
from market_intel.providers.base import dedupe_items
from market_intel.repository import MarketIntelRepository


class MarketIntelService:
    def __init__(self, repository: MarketIntelRepository, providers: Iterable[object]):
        self.repository = repository
        self.providers = [
            provider for provider in providers
            if self._is_provider_available(provider)
        ]

    def get_stock_intel(self, market: str, code: str, force_refresh: bool = False) -> dict:
        if not force_refresh:
            cached = self.repository.get_bundle("stock", market, code)
            if cached:
                return dict(cached.get("bundle_json") or cached)
        return self.refresh_stock_intel(market, code)

    def get_market_digest(self, market: str, force_refresh: bool = False) -> dict:
        if not force_refresh:
            cached = self.repository.get_bundle("market", market, "")
            if cached:
                return dict(cached.get("bundle_json") or cached)
        return self.refresh_market_digest(market)

    def refresh_stock_intel(self, market: str, code: str) -> dict:
        return self._refresh(scope_type="stock", market=market, code=code)

    def refresh_market_digest(self, market: str) -> dict:
        return self._refresh(scope_type="market", market=market, code="")

    def list_provider_runs(
        self,
        provider: Optional[str] = None,
        market: Optional[str] = None,
        code: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 50,
    ) -> List[dict]:
        return self.repository.list_provider_runs(
            provider=provider,
            market=market,
            code=code,
            status=status,
            limit=limit,
        )

    def _refresh(self, *, scope_type: str, market: str, code: str = "") -> dict:
        source_status: dict[str, DataSourceStatus] = {}
        collected: List[IntelItem] = []

        for provider in self.providers:
            provider_key = self._provider_key(provider)
            started_at = self._now()
            started_clock = perf_counter()
            try:
                if scope_type == "stock":
                    items = list(provider.fetch_stock(market, code))
                else:
                    items = [
                        replace(item, code="")
                        for item in provider.fetch_market(market)
                    ]
                finished_at = self._now()
                duration_ms = int((perf_counter() - started_clock) * 1000)
                collected.extend(items)
                source_status[provider_key] = DataSourceStatus(
                    provider=provider_key,
                    status="success",
                    item_count=len(items),
                    fetched_at=finished_at,
                )
                self.repository.insert_provider_run({
                    "provider": provider_key,
                    "scope_type": scope_type,
                    "market": market,
                    "code": code,
                    "status": "success",
                    "error_message": "",
                    "duration_ms": duration_ms,
                    "item_count": len(items),
                    "raw_json": {"item_count": len(items)},
                    "started_at": started_at,
                    "finished_at": finished_at,
                })
            except Exception as exc:  # Provider failures are isolated by design.
                finished_at = self._now()
                duration_ms = int((perf_counter() - started_clock) * 1000)
                error_message = str(exc)
                source_status[provider_key] = DataSourceStatus(
                    provider=provider_key,
                    status="failed",
                    item_count=0,
                    error_message=error_message,
                    fetched_at=finished_at,
                )
                self.repository.insert_provider_run({
                    "provider": provider_key,
                    "scope_type": scope_type,
                    "market": market,
                    "code": code,
                    "status": "failed",
                    "error_message": error_message,
                    "duration_ms": duration_ms,
                    "item_count": 0,
                    "raw_json": {"error_message": error_message},
                    "started_at": started_at,
                    "finished_at": finished_at,
                })

        items = dedupe_items(collected)
        if items:
            self.repository.upsert_items([item.to_dict() for item in items])
            freshness_status = "fresh"
        else:
            items = self._stale_items(scope_type=scope_type, market=market, code=code)
            freshness_status = "stale" if items else "empty"

        if scope_type == "stock":
            bundle = StockIntelBundle(
                market=market,
                code=code,
                items=items,
                freshness_status=freshness_status,
                source_status=source_status,
            )
        else:
            bundle = MarketIntelBundle(
                market=market,
                items=items,
                freshness_status=freshness_status,
                source_status=source_status,
            )
        payload = bundle.to_dict()
        self.repository.upsert_bundle({
            "scope_type": payload["scope_type"],
            "market": payload["market"],
            "code": payload.get("code") or "",
            "bundle_json": payload,
            "freshness_status": payload["freshness_status"],
            "source_status_json": payload["source_status"],
        })
        return payload

    def _stale_items(self, *, scope_type: str, market: str, code: str = "") -> List[IntelItem]:
        rows = self.repository.list_items(
            scope_type=scope_type,
            market=market,
            code=code,
            include_stale=True,
        )
        items = []
        for row in rows:
            item = IntelItem.from_dict(row)
            item.is_stale = True
            items.append(item)
        return items

    @staticmethod
    def _provider_key(provider: object) -> str:
        return str(
            getattr(provider, "name", None)
            or getattr(provider, "provider_name", None)
            or provider.__class__.__name__
        )

    @staticmethod
    def _is_provider_available(provider: object) -> bool:
        available = getattr(provider, "is_available", True)
        if callable(available):
            available = available()
        return bool(available)

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)
