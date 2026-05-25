#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

from copy import deepcopy
from typing import Dict, List, Optional, Protocol


class MarketIntelRepository(Protocol):
    def upsert_items(self, items: List[dict]) -> None:
        """Persist normalized market intelligence items."""

    def list_items(
        self,
        *,
        scope_type: str = "stock",
        market: str,
        code: str = "",
        include_stale: bool = True,
        limit: int = 200,
    ) -> List[dict]:
        """Return normalized items for a stock or market scope."""

    def upsert_bundle(self, row: dict) -> None:
        """Persist the latest aggregate bundle for a scope."""

    def get_bundle(self, scope_type: str, market: str, code: str = "") -> Optional[dict]:
        """Return the latest aggregate bundle for a scope."""

    def insert_provider_run(self, row: dict) -> None:
        """Persist one provider execution record."""

    def list_provider_runs(
        self,
        *,
        provider: Optional[str],
        market: Optional[str],
        code: Optional[str],
        status: Optional[str],
        limit: int = 50,
    ) -> List[dict]:
        """Return recent provider execution records."""


class InMemoryMarketIntelRepository:
    def __init__(self):
        self.items: List[dict] = []
        self.bundles: Dict[tuple[str, str, str], dict] = {}
        self.provider_runs: List[dict] = []

    def upsert_items(self, items: List[dict]) -> None:
        for item in items:
            normalized = deepcopy(item)
            normalized["scope_type"] = str(normalized.get("scope_type") or "stock")
            normalized["code"] = str(normalized.get("code") or "")
            normalized["provider"] = str(normalized.get("provider") or normalized.get("source") or "")
            normalized["dedupe_key"] = str(
                normalized.get("dedupe_key") or normalized.get("source_id") or normalized.get("url") or normalized.get("title") or ""
            )
            key = (
                normalized["scope_type"],
                str(normalized.get("market") or ""),
                normalized["code"],
                normalized["provider"],
                normalized["dedupe_key"],
            )
            self.items = [
                existing for existing in self.items
                if (
                    str(existing.get("scope_type") or "stock"),
                    str(existing.get("market") or ""),
                    str(existing.get("code") or ""),
                    str(existing.get("provider") or existing.get("source") or ""),
                    str(existing.get("dedupe_key") or existing.get("source_id") or existing.get("url") or existing.get("title") or ""),
                ) != key
            ]
            self.items.append(normalized)

    def list_items(
        self,
        *,
        scope_type: str = "stock",
        market: str,
        code: str = "",
        include_stale: bool = True,
        limit: int = 200,
    ) -> List[dict]:
        rows = []
        for item in self.items:
            if str(item.get("scope_type") or "stock") != scope_type:
                continue
            if item.get("market") != market:
                continue
            if str(item.get("code") or "") != str(code or ""):
                continue
            if not include_stale and bool(item.get("is_stale")):
                continue
            rows.append(deepcopy(item))
        rows.sort(key=lambda item: str(item.get("published_at") or item.get("fetched_at") or ""), reverse=True)
        return rows[: max(1, int(limit))]

    def upsert_bundle(self, row: dict) -> None:
        key = (
            str(row.get("scope_type") or ""),
            str(row.get("market") or ""),
            str(row.get("code") or ""),
        )
        self.bundles[key] = deepcopy(row)

    def get_bundle(self, scope_type: str, market: str, code: str = "") -> Optional[dict]:
        row = self.bundles.get((scope_type, market, code or ""))
        return deepcopy(row) if row else None

    def insert_provider_run(self, row: dict) -> None:
        self.provider_runs.append(deepcopy(row))

    def list_provider_runs(
        self,
        *,
        provider: Optional[str] = None,
        market: Optional[str] = None,
        code: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 50,
    ) -> List[dict]:
        rows = []
        for row in reversed(self.provider_runs):
            if provider and row.get("provider") != provider:
                continue
            if market and row.get("market") != market:
                continue
            if code is not None and str(row.get("code") or "") != str(code or ""):
                continue
            if status and row.get("status") != status:
                continue
            rows.append(deepcopy(row))
        return rows[: max(1, int(limit))]


class MySqlMarketIntelRepository:
    def __init__(self, db):
        self.db = db

    def upsert_items(self, items: List[dict]) -> None:
        self.db.upsert_market_intel_items(items)

    def list_items(
        self,
        *,
        scope_type: str = "stock",
        market: str,
        code: str = "",
        include_stale: bool = True,
        limit: int = 200,
    ) -> List[dict]:
        return self.db.list_market_intel_items(
            scope_type=scope_type,
            market=market,
            code=code,
            include_stale=include_stale,
            limit=limit,
        )

    def upsert_bundle(self, row: dict) -> None:
        self.db.upsert_market_intel_bundle(row)

    def get_bundle(self, scope_type: str, market: str, code: str = "") -> Optional[dict]:
        return self.db.get_market_intel_bundle(scope_type, market, code)

    def insert_provider_run(self, row: dict) -> None:
        self.db.insert_market_intel_provider_run(row)

    def list_provider_runs(
        self,
        *,
        provider: Optional[str] = None,
        market: Optional[str] = None,
        code: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 50,
    ) -> List[dict]:
        return self.db.list_market_intel_provider_runs(
            provider=provider,
            market=market,
            code=code,
            status=status,
            limit=limit,
        )

    def close(self) -> None:
        close = getattr(self.db, "close", None)
        if callable(close):
            close()
