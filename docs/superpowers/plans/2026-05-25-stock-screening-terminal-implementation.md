# Stock Screening Terminal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Merge Code Screening and Market Intel into one stock screener terminal where selecting a screening result lazily loads A/HK/US quote, K-line, minute, fund-flow, market-intel, and evidence data.

**Architecture:** Add a focused `stock_terminal` backend package with normalized models, cache repository, provider adapters, service orchestration, and FastAPI routes. Keep custom-list screening unchanged, remove the standalone Market Intel navigation entry, and render a two-pane React workbench with lazy selected-stock terminal tabs.

**Tech Stack:** Python 3, FastAPI, unittest, MySQL via `MarketDatabase`, pandas for cached K-line data, React 18, TypeScript, Vite, existing source-text frontend contract tests.

---

## Scope Check

This is one implementation plan because the backend stock-terminal layer and the merged frontend depend on the same normalized API contract. Keep Graphify/grafhify outside product runtime code. If used during execution, write its outputs under ignored development folders only.

The current worktree has unrelated uncommitted changes. Before implementation, run `git status --short` and avoid staging or reverting files not touched by the current task.

## File Structure

- Create: `stock_screener/stock_terminal/__init__.py`
  - Public package marker and exports.
- Create: `stock_screener/stock_terminal/models.py`
  - Normalized data-block status, quote, K-line, minute, fund-flow, and summary models.
- Create: `stock_screener/stock_terminal/repository.py`
  - Cache repository protocol, in-memory test repository, and MySQL adapter around `MarketDatabase`.
- Create: `stock_screener/stock_terminal/providers/base.py`
  - Provider protocol and empty provider used in tests and degraded runtime.
- Create: `stock_screener/stock_terminal/providers/factory.py`
  - Build provider chain using existing K-line fetchers and live provider adapters.
- Create: `stock_screener/stock_terminal/providers/eastmoney.py`
  - Eastmoney/Sina-style live provider helpers for quote, minute, and fund-flow where available.
- Create: `stock_screener/stock_terminal/service.py`
  - Cache-first orchestration and block-level failure handling.
- Create: `stock_screener/web/stock_terminal.py`
  - FastAPI router under `/api/stock-terminal`.
- Create: `stock_screener/sql/018_stock_terminal.sql`
  - Quote, minute, and fund-flow cache schema.
- Modify: `stock_screener/db.py`
  - Add `init_stock_terminal_schema` and cache read/write methods.
- Modify: `stock_screener/web/main.py`
  - Include the stock-terminal router.
- Modify: `stock_screener/web_frontend/src/features/marketIntel/api.ts`
  - Reuse or move market-intel client calls into the merged terminal panel.
- Create: `stock_screener/web_frontend/src/features/stockTerminal/api.ts`
  - Stock-terminal API client.
- Create: `stock_screener/web_frontend/src/features/stockTerminal/types.ts`
  - TypeScript API shapes.
- Create: `stock_screener/web_frontend/src/features/stockTerminal/StockTerminalPanel.tsx`
  - Right-side selected-stock terminal tabs.
- Modify: `stock_screener/web_frontend/src/main.tsx`
  - Remove standalone Market Intel navigation, rename/reframe Code Screening, add selected-row state, render terminal panel.
- Modify: `stock_screener/web_frontend/src/styles.css`
  - Two-pane terminal layout, selected row, terminal tabs, block-status badges, responsive behavior.
- Create: `stock_screener/tests/test_stock_terminal_models.py`
- Create: `stock_screener/tests/test_stock_terminal_repository.py`
- Create: `stock_screener/tests/test_stock_terminal_service.py`
- Create: `stock_screener/tests/test_stock_terminal_api.py`
- Modify: `stock_screener/tests/test_code_screening_frontend.py`

## Task 1: Stock Terminal Models

**Files:**
- Create: `stock_screener/stock_terminal/__init__.py`
- Create: `stock_screener/stock_terminal/models.py`
- Test: `stock_screener/tests/test_stock_terminal_models.py`

- [ ] **Step 1: Write failing model tests**

Create `stock_screener/tests/test_stock_terminal_models.py`:

```python
import unittest
from datetime import datetime, timedelta, timezone

from stock_terminal.models import (
    BlockStatus,
    FundFlowPoint,
    KlinePoint,
    MinutePoint,
    QuoteSnapshot,
    StockTerminalSummary,
    data_status,
)


class StockTerminalModelsTest(unittest.TestCase):
    def test_data_status_serializes_source_and_stale_flag(self):
        fetched_at = datetime(2026, 5, 25, 9, 30, tzinfo=timezone.utc)
        status = data_status(
            status="cached",
            source="test-cache",
            fetched_at=fetched_at,
            expires_at=fetched_at + timedelta(minutes=5),
        )

        payload = status.to_dict()

        self.assertEqual(payload["status"], "cached")
        self.assertEqual(payload["source"], "test-cache")
        self.assertEqual(payload["fetched_at"], "2026-05-25T09:30:00+00:00")
        self.assertEqual(payload["expires_at"], "2026-05-25T09:35:00+00:00")
        self.assertFalse(payload["stale"])
        self.assertEqual(payload["error_message"], "")

    def test_summary_contains_block_statuses(self):
        summary = StockTerminalSummary(
            market="A",
            code="SH.600519",
            name="贵州茅台",
            quote=QuoteSnapshot(
                market="A",
                code="SH.600519",
                name="贵州茅台",
                price=1688.0,
                change_percent=1.2,
                fetched_at=datetime(2026, 5, 25, 9, 30, tzinfo=timezone.utc),
                source="fake",
            ),
            statuses={
                "quote": BlockStatus(status="fresh", source="fake"),
                "kline": BlockStatus(status="empty", source="cache"),
            },
        )

        payload = summary.to_dict()

        self.assertEqual(payload["market"], "A")
        self.assertEqual(payload["code"], "SH.600519")
        self.assertEqual(payload["quote"]["price"], 1688.0)
        self.assertEqual(payload["source_status"]["quote"]["status"], "fresh")
        self.assertEqual(payload["source_status"]["kline"]["status"], "empty")

    def test_series_points_serialize_numbers_and_time(self):
        at = datetime(2026, 5, 25, 9, 31, tzinfo=timezone.utc)

        kline = KlinePoint(at=at, open=1.0, high=2.0, low=0.5, close=1.5, volume=100.0)
        minute = MinutePoint(at=at, price=1.5, average_price=1.4, volume=80.0)
        flow = FundFlowPoint(
            at=at,
            inflow=10.0,
            outflow=6.0,
            net_inflow=4.0,
            main_net_inflow=3.0,
            retail_net_inflow=1.0,
        )

        self.assertEqual(kline.to_dict()["close"], 1.5)
        self.assertEqual(minute.to_dict()["average_price"], 1.4)
        self.assertEqual(flow.to_dict()["net_inflow"], 4.0)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
PYTHONPATH=stock_screener python3 -m unittest stock_screener.tests.test_stock_terminal_models -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'stock_terminal'`.

- [ ] **Step 3: Implement models**

Create `stock_screener/stock_terminal/__init__.py`:

```python
"""Selected-stock terminal data layer."""
```

Create `stock_screener/stock_terminal/models.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
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
        return float(value)
    except (TypeError, ValueError):
        return None


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
            "error_message": self.error_message or "",
        }


def data_status(
    status: str,
    source: str = "",
    fetched_at: Optional[datetime] = None,
    expires_at: Optional[datetime] = None,
    error_message: str = "",
) -> BlockStatus:
    now = datetime.now(timezone.utc)
    stale = bool(expires_at and expires_at < now)
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
    price: Optional[float] = None
    change: Optional[float] = None
    change_percent: Optional[float] = None
    open_price: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    previous_close: Optional[float] = None
    volume: Optional[float] = None
    turnover: Optional[float] = None
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
    open: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    close: Optional[float] = None
    volume: Optional[float] = None
    turnover: Optional[float] = None

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
    price: Optional[float] = None
    average_price: Optional[float] = None
    volume: Optional[float] = None
    turnover: Optional[float] = None

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
    inflow: Optional[float] = None
    outflow: Optional[float] = None
    net_inflow: Optional[float] = None
    main_net_inflow: Optional[float] = None
    retail_net_inflow: Optional[float] = None

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
            "quote": self.quote.to_dict() if self.quote else None,
            "source_status": {key: value.to_dict() for key, value in self.statuses.items()},
            "data_gaps": list(self.data_gaps),
        }
```

- [ ] **Step 4: Run tests to verify pass**

Run:

```bash
PYTHONPATH=stock_screener python3 -m unittest stock_screener.tests.test_stock_terminal_models -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add stock_screener/stock_terminal/__init__.py stock_screener/stock_terminal/models.py stock_screener/tests/test_stock_terminal_models.py
git commit -m "feat: add stock terminal models"
```

## Task 2: Stock Terminal Cache Repository

**Files:**
- Create: `stock_screener/sql/018_stock_terminal.sql`
- Create: `stock_screener/stock_terminal/repository.py`
- Modify: `stock_screener/db.py`
- Test: `stock_screener/tests/test_stock_terminal_repository.py`

- [ ] **Step 1: Write failing repository tests**

Create `stock_screener/tests/test_stock_terminal_repository.py`:

```python
import unittest
from datetime import datetime, timedelta, timezone

from stock_terminal.models import FundFlowPoint, KlinePoint, MinutePoint, QuoteSnapshot
from stock_terminal.repository import InMemoryStockTerminalRepository


class StockTerminalRepositoryTest(unittest.TestCase):
    def test_quote_cache_returns_cached_status_before_expiry(self):
        repo = InMemoryStockTerminalRepository()
        now = datetime(2026, 5, 25, 9, 30, tzinfo=timezone.utc)
        quote = QuoteSnapshot(market="US", code="US.AAPL", name="Apple", price=190.0, fetched_at=now, source="fake")

        repo.save_quote(quote, expires_at=now + timedelta(minutes=5))
        cached, status = repo.get_quote("US", "US.AAPL", now=now + timedelta(minutes=1))

        self.assertEqual(cached.price, 190.0)
        self.assertEqual(status.status, "cached")
        self.assertFalse(status.stale)

    def test_quote_cache_returns_stale_status_after_expiry(self):
        repo = InMemoryStockTerminalRepository()
        now = datetime(2026, 5, 25, 9, 30, tzinfo=timezone.utc)
        quote = QuoteSnapshot(market="US", code="US.AAPL", price=190.0, fetched_at=now, source="fake")

        repo.save_quote(quote, expires_at=now + timedelta(minutes=1))
        cached, status = repo.get_quote("US", "US.AAPL", now=now + timedelta(minutes=3))

        self.assertEqual(cached.price, 190.0)
        self.assertEqual(status.status, "stale")
        self.assertTrue(status.stale)

    def test_kline_minute_and_fund_flow_cache_round_trip(self):
        repo = InMemoryStockTerminalRepository()
        now = datetime(2026, 5, 25, 9, 30, tzinfo=timezone.utc)
        kline_rows = [KlinePoint(at=now, open=9.0, high=11.0, low=8.0, close=10.0, volume=1000.0)]
        minute_rows = [MinutePoint(at=now, price=10.0, average_price=9.9, volume=100.0)]
        flow_rows = [FundFlowPoint(at=now, inflow=10.0, outflow=4.0, net_inflow=6.0)]

        repo.save_klines("A", "SH.600519", "1d", kline_rows, source="fake", expires_at=now + timedelta(minutes=30))
        repo.save_minute("A", "SH.600519", minute_rows, source="fake", expires_at=now + timedelta(minutes=1))
        repo.save_fund_flow("A", "SH.600519", flow_rows, source="fake", expires_at=now + timedelta(minutes=30))

        kline_cached, kline_status = repo.get_klines("A", "SH.600519", "1d", limit=20, now=now)
        minute_cached, minute_status = repo.get_minute("A", "SH.600519", now=now)
        flow_cached, flow_status = repo.get_fund_flow("A", "SH.600519", now=now)

        self.assertEqual(kline_cached[0].close, 10.0)
        self.assertEqual(kline_status.status, "cached")
        self.assertEqual(minute_cached[0].price, 10.0)
        self.assertEqual(minute_status.status, "cached")
        self.assertEqual(flow_cached[0].net_inflow, 6.0)
        self.assertEqual(flow_status.status, "cached")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
PYTHONPATH=stock_screener python3 -m unittest stock_screener.tests.test_stock_terminal_repository -v
```

Expected: FAIL with `ModuleNotFoundError` or missing `stock_terminal.repository`.

- [ ] **Step 3: Add deployment schema**

Create `stock_screener/sql/018_stock_terminal.sql`:

```sql
CREATE TABLE IF NOT EXISTS stock_quote_cache (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    market VARCHAR(16) NOT NULL,
    code VARCHAR(32) NOT NULL,
    payload_json JSON NOT NULL,
    source VARCHAR(64) NOT NULL DEFAULT '',
    fetched_at DATETIME NOT NULL,
    expires_at DATETIME NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_stock_quote_cache_scope (market, code),
    KEY idx_stock_quote_cache_expiry (expires_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Selected-stock quote TTL cache';

CREATE TABLE IF NOT EXISTS stock_minute_cache (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    market VARCHAR(16) NOT NULL,
    code VARCHAR(32) NOT NULL,
    trade_date DATE NOT NULL,
    payload_json JSON NOT NULL,
    source VARCHAR(64) NOT NULL DEFAULT '',
    fetched_at DATETIME NOT NULL,
    expires_at DATETIME NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_stock_minute_cache_scope (market, code, trade_date),
    KEY idx_stock_minute_cache_expiry (expires_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Selected-stock intraday minute TTL cache';

CREATE TABLE IF NOT EXISTS stock_fund_flow_cache (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    market VARCHAR(16) NOT NULL,
    code VARCHAR(32) NOT NULL,
    payload_json JSON NOT NULL,
    source VARCHAR(64) NOT NULL DEFAULT '',
    fetched_at DATETIME NOT NULL,
    expires_at DATETIME NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_stock_fund_flow_cache_scope (market, code),
    KEY idx_stock_fund_flow_cache_expiry (expires_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Selected-stock fund-flow TTL cache';
```

- [ ] **Step 4: Implement in-memory repository**

Create `stock_screener/stock_terminal/repository.py`:

```python
from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

import pandas as pd

from stock_terminal.models import BlockStatus, FundFlowPoint, KlinePoint, MinutePoint, QuoteSnapshot


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _cache_status(source: str, fetched_at: Optional[datetime], expires_at: Optional[datetime], now: Optional[datetime] = None) -> BlockStatus:
    current = now or _now()
    stale = bool(expires_at and expires_at <= current)
    return BlockStatus(
        status="stale" if stale else "cached",
        source=source,
        fetched_at=fetched_at,
        expires_at=expires_at,
        stale=stale,
    )


class InMemoryStockTerminalRepository:
    def __init__(self):
        self.quotes: Dict[Tuple[str, str], Tuple[QuoteSnapshot, datetime]] = {}
        self.klines: Dict[Tuple[str, str, str], Tuple[List[KlinePoint], str, datetime, datetime]] = {}
        self.minutes: Dict[Tuple[str, str, date], Tuple[List[MinutePoint], str, datetime, datetime]] = {}
        self.fund_flows: Dict[Tuple[str, str], Tuple[List[FundFlowPoint], str, datetime, datetime]] = {}

    def get_quote(self, market: str, code: str, now: Optional[datetime] = None):
        item = self.quotes.get((market, code))
        if not item:
            return None, BlockStatus(status="empty", source="quote_cache")
        quote, expires_at = item
        return deepcopy(quote), _cache_status(quote.source, quote.fetched_at, expires_at, now)

    def save_quote(self, quote: QuoteSnapshot, expires_at: datetime) -> None:
        self.quotes[(quote.market, quote.code)] = (deepcopy(quote), expires_at)

    def get_klines(self, market: str, code: str, timeframe: str, limit: int = 120, now: Optional[datetime] = None):
        item = self.klines.get((market, code, timeframe))
        if not item:
            return [], BlockStatus(status="empty", source="kline_cache")
        rows, source, fetched_at, expires_at = item
        return deepcopy(rows[-int(limit):]), _cache_status(source, fetched_at, expires_at, now)

    def save_klines(self, market: str, code: str, timeframe: str, rows: Iterable[KlinePoint], source: str, expires_at: datetime) -> None:
        fetched_at = _now()
        self.klines[(market, code, timeframe)] = (deepcopy(list(rows)), source, fetched_at, expires_at)

    def get_minute(self, market: str, code: str, trade_date: Optional[date] = None, now: Optional[datetime] = None):
        lookup_date = trade_date or (now or _now()).date()
        item = self.minutes.get((market, code, lookup_date))
        if not item:
            return [], BlockStatus(status="empty", source="minute_cache")
        rows, source, fetched_at, expires_at = item
        return deepcopy(rows), _cache_status(source, fetched_at, expires_at, now)

    def save_minute(self, market: str, code: str, rows: Iterable[MinutePoint], source: str, expires_at: datetime) -> None:
        row_list = list(rows)
        trade_date = row_list[0].at.date() if row_list else _now().date()
        fetched_at = _now()
        self.minutes[(market, code, trade_date)] = (deepcopy(row_list), source, fetched_at, expires_at)

    def get_fund_flow(self, market: str, code: str, now: Optional[datetime] = None):
        item = self.fund_flows.get((market, code))
        if not item:
            return [], BlockStatus(status="empty", source="fund_flow_cache")
        rows, source, fetched_at, expires_at = item
        return deepcopy(rows), _cache_status(source, fetched_at, expires_at, now)

    def save_fund_flow(self, market: str, code: str, rows: Iterable[FundFlowPoint], source: str, expires_at: datetime) -> None:
        fetched_at = _now()
        self.fund_flows[(market, code)] = (deepcopy(list(rows)), source, fetched_at, expires_at)


def _parse_dt(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value.replace(tzinfo=value.tzinfo or timezone.utc)
    if isinstance(value, str) and value:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc)
    return None


def _kline_from_frame(df: pd.DataFrame, limit: int) -> List[KlinePoint]:
    rows: List[KlinePoint] = []
    if df is None or df.empty:
        return rows
    for item in df.tail(int(limit)).to_dict("records"):
        at = item.get("date") or item.get("bar_time")
        rows.append(KlinePoint(
            at=_parse_dt(at) or _now(),
            open=item.get("open"),
            high=item.get("high"),
            low=item.get("low"),
            close=item.get("close"),
            volume=item.get("volume"),
            turnover=item.get("turnover"),
        ))
    return rows


class MySqlStockTerminalRepository:
    def __init__(self, db):
        self.db = db

    def get_quote(self, market: str, code: str, now: Optional[datetime] = None):
        item = self.db.get_stock_terminal_json_cache("stock_quote_cache", market, code)
        if not item:
            return None, BlockStatus(status="empty", source="quote_cache")
        payload = dict(item["payload"])
        payload["fetched_at"] = _parse_dt(payload.get("fetched_at"))
        quote = QuoteSnapshot(**payload)
        return quote, _cache_status(item["source"], item["fetched_at"], item["expires_at"], now)

    def save_quote(self, quote: QuoteSnapshot, expires_at: datetime) -> None:
        self.db.upsert_stock_terminal_json_cache(
            "stock_quote_cache",
            quote.market,
            quote.code,
            quote.to_dict(),
            quote.source,
            quote.fetched_at or _now(),
            expires_at,
        )

    def get_klines(self, market: str, code: str, timeframe: str, limit: int = 120, now: Optional[datetime] = None):
        df = self.db.get_kline_cache(market=market, code=code, timeframe=timeframe, max_count=int(limit))
        rows = _kline_from_frame(df, limit)
        if not rows:
            return [], BlockStatus(status="empty", source="stock_kline_cache")
        return rows, BlockStatus(status="cached", source="stock_kline_cache")

    def save_klines(self, market: str, code: str, timeframe: str, rows: Iterable[KlinePoint], source: str, expires_at: datetime) -> None:
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

    def get_minute(self, market: str, code: str, trade_date: Optional[date] = None, now: Optional[datetime] = None):
        lookup_date = trade_date or (now or _now()).date()
        item = self.db.get_stock_terminal_json_cache("stock_minute_cache", market, code, trade_date=lookup_date)
        if not item:
            return [], BlockStatus(status="empty", source="minute_cache")
        rows = [MinutePoint(at=_parse_dt(row.get("at")) or _now(), **{key: row.get(key) for key in ("price", "average_price", "volume", "turnover")}) for row in item["payload"].get("rows", [])]
        return rows, _cache_status(item["source"], item["fetched_at"], item["expires_at"], now)

    def save_minute(self, market: str, code: str, rows: Iterable[MinutePoint], source: str, expires_at: datetime) -> None:
        row_list = list(rows)
        trade_date = row_list[0].at.date() if row_list else _now().date()
        self.db.upsert_stock_terminal_json_cache(
            "stock_minute_cache",
            market,
            code,
            {"rows": [row.to_dict() for row in row_list]},
            source,
            _now(),
            expires_at,
            trade_date=trade_date,
        )

    def get_fund_flow(self, market: str, code: str, now: Optional[datetime] = None):
        item = self.db.get_stock_terminal_json_cache("stock_fund_flow_cache", market, code)
        if not item:
            return [], BlockStatus(status="empty", source="fund_flow_cache")
        rows = [FundFlowPoint(at=_parse_dt(row.get("at")) or _now(), **{key: row.get(key) for key in ("inflow", "outflow", "net_inflow", "main_net_inflow", "retail_net_inflow")}) for row in item["payload"].get("rows", [])]
        return rows, _cache_status(item["source"], item["fetched_at"], item["expires_at"], now)

    def save_fund_flow(self, market: str, code: str, rows: Iterable[FundFlowPoint], source: str, expires_at: datetime) -> None:
        row_list = list(rows)
        self.db.upsert_stock_terminal_json_cache(
            "stock_fund_flow_cache",
            market,
            code,
            {"rows": [row.to_dict() for row in row_list]},
            source,
            _now(),
            expires_at,
        )
```

- [ ] **Step 5: Add MarketDatabase schema hook and MySQL methods**

Modify `stock_screener/db.py`:

Add imports near existing imports if missing:

```python
import json
from pathlib import Path
```

Add these methods near existing schema/cache helpers:

```python
    def init_stock_terminal_schema(self) -> None:
        schema_path = Path(__file__).resolve().parent / "sql" / "018_stock_terminal.sql"
        statements = [part.strip() for part in schema_path.read_text(encoding="utf-8").split(";") if part.strip()]
        with self.conn.cursor() as cursor:
            for statement in statements:
                cursor.execute(statement)

    def upsert_stock_terminal_json_cache(self, table: str, market: str, code: str, payload: dict, source: str, fetched_at, expires_at, trade_date=None) -> None:
        payload_text = json.dumps(payload, ensure_ascii=False)
        if table == "stock_minute_cache":
            sql = """
                INSERT INTO stock_minute_cache
                    (market, code, trade_date, payload_json, source, fetched_at, expires_at)
                VALUES (%s, %s, %s, CAST(%s AS JSON), %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    payload_json=VALUES(payload_json),
                    source=VALUES(source),
                    fetched_at=VALUES(fetched_at),
                    expires_at=VALUES(expires_at)
            """
            args = (market, code, trade_date, payload_text, source, fetched_at, expires_at)
        elif table == "stock_quote_cache":
            sql = """
                INSERT INTO stock_quote_cache
                    (market, code, payload_json, source, fetched_at, expires_at)
                VALUES (%s, %s, CAST(%s AS JSON), %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    payload_json=VALUES(payload_json),
                    source=VALUES(source),
                    fetched_at=VALUES(fetched_at),
                    expires_at=VALUES(expires_at)
            """
            args = (market, code, payload_text, source, fetched_at, expires_at)
        elif table == "stock_fund_flow_cache":
            sql = """
                INSERT INTO stock_fund_flow_cache
                    (market, code, payload_json, source, fetched_at, expires_at)
                VALUES (%s, %s, CAST(%s AS JSON), %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    payload_json=VALUES(payload_json),
                    source=VALUES(source),
                    fetched_at=VALUES(fetched_at),
                    expires_at=VALUES(expires_at)
            """
            args = (market, code, payload_text, source, fetched_at, expires_at)
        else:
            raise ValueError(f"unsupported stock terminal cache table: {table}")
        with self.conn.cursor() as cursor:
            cursor.execute(sql, args)

    def get_stock_terminal_json_cache(self, table: str, market: str, code: str, trade_date=None):
        if table == "stock_minute_cache":
            sql = """
                SELECT payload_json, source, fetched_at, expires_at
                FROM stock_minute_cache
                WHERE market=%s AND code=%s AND trade_date=%s
            """
            args = (market, code, trade_date)
        elif table in ("stock_quote_cache", "stock_fund_flow_cache"):
            sql = f"""
                SELECT payload_json, source, fetched_at, expires_at
                FROM {table}
                WHERE market=%s AND code=%s
            """
            args = (market, code)
        else:
            raise ValueError(f"unsupported stock terminal cache table: {table}")
        with self.conn.cursor() as cursor:
            cursor.execute(sql, args)
            row = cursor.fetchone()
        if not row:
            return None
        payload = row[0]
        if isinstance(payload, str):
            payload = json.loads(payload)
        return {
            "payload": payload,
            "source": row[1],
            "fetched_at": row[2],
            "expires_at": row[3],
        }
```

- [ ] **Step 6: Run focused tests**

Run:

```bash
PYTHONPATH=stock_screener python3 -m unittest stock_screener.tests.test_stock_terminal_repository -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add stock_screener/sql/018_stock_terminal.sql stock_screener/stock_terminal/repository.py stock_screener/db.py stock_screener/tests/test_stock_terminal_repository.py
git commit -m "feat: add stock terminal cache repository"
```

## Task 3: Provider Interfaces and Cache-First Service

**Files:**
- Create: `stock_screener/stock_terminal/providers/base.py`
- Create: `stock_screener/stock_terminal/providers/factory.py`
- Create: `stock_screener/stock_terminal/providers/eastmoney.py`
- Create: `stock_screener/stock_terminal/service.py`
- Test: `stock_screener/tests/test_stock_terminal_service.py`

- [ ] **Step 1: Write failing service tests**

Create `stock_screener/tests/test_stock_terminal_service.py`:

```python
import unittest
from datetime import datetime, timedelta, timezone

from stock_terminal.models import FundFlowPoint, KlinePoint, MinutePoint, QuoteSnapshot
from stock_terminal.repository import InMemoryStockTerminalRepository
from stock_terminal.service import StockTerminalService


class FakeProvider:
    name = "fake"

    def __init__(self):
        self.quote_calls = []
        self.kline_calls = []
        self.minute_calls = []
        self.fund_flow_calls = []

    def fetch_quote(self, market, code):
        self.quote_calls.append((market, code))
        return QuoteSnapshot(market=market, code=code, name=code, price=10.0, source=self.name)

    def fetch_klines(self, market, code, timeframe, limit):
        self.kline_calls.append((market, code, timeframe, limit))
        at = datetime(2026, 5, 25, tzinfo=timezone.utc)
        return [KlinePoint(at=at, open=9, high=11, low=8, close=10, volume=100)]

    def fetch_minute(self, market, code):
        self.minute_calls.append((market, code))
        at = datetime(2026, 5, 25, 9, 31, tzinfo=timezone.utc)
        return [MinutePoint(at=at, price=10, average_price=9.8, volume=100)]

    def fetch_fund_flow(self, market, code):
        self.fund_flow_calls.append((market, code))
        at = datetime(2026, 5, 25, tzinfo=timezone.utc)
        return [FundFlowPoint(at=at, inflow=10, outflow=4, net_inflow=6)]


class FailingProvider(FakeProvider):
    name = "failing"

    def fetch_quote(self, market, code):
        raise RuntimeError("quote down")


class StockTerminalServiceTest(unittest.TestCase):
    def test_summary_uses_cached_quote_without_provider_call(self):
        repo = InMemoryStockTerminalRepository()
        provider = FakeProvider()
        now = datetime(2026, 5, 25, 9, 30, tzinfo=timezone.utc)
        repo.save_quote(
            QuoteSnapshot(market="US", code="US.AAPL", name="Apple", price=190, fetched_at=now, source="cache"),
            expires_at=now + timedelta(minutes=5),
        )
        service = StockTerminalService(repo, [provider], now=lambda: now)

        payload = service.get_summary("US", "US.AAPL")

        self.assertEqual(payload["quote"]["price"], 190.0)
        self.assertEqual(payload["source_status"]["quote"]["status"], "cached")
        self.assertEqual(provider.quote_calls, [])

    def test_summary_fetches_provider_when_cache_empty(self):
        repo = InMemoryStockTerminalRepository()
        provider = FakeProvider()
        now = datetime(2026, 5, 25, 9, 30, tzinfo=timezone.utc)
        service = StockTerminalService(repo, [provider], now=lambda: now)

        payload = service.get_summary("A", "SH.600519")

        self.assertEqual(payload["quote"]["price"], 10.0)
        self.assertEqual(payload["source_status"]["quote"]["status"], "fresh")
        self.assertEqual(provider.quote_calls, [("A", "SH.600519")])

    def test_summary_returns_error_block_when_provider_fails(self):
        repo = InMemoryStockTerminalRepository()
        now = datetime(2026, 5, 25, 9, 30, tzinfo=timezone.utc)
        service = StockTerminalService(repo, [FailingProvider()], now=lambda: now)

        payload = service.get_summary("US", "US.AAPL")

        self.assertIsNone(payload["quote"])
        self.assertEqual(payload["source_status"]["quote"]["status"], "error")
        self.assertIn("quote down", payload["source_status"]["quote"]["error_message"])

    def test_lazy_blocks_fetch_on_demand(self):
        repo = InMemoryStockTerminalRepository()
        provider = FakeProvider()
        now = datetime(2026, 5, 25, 9, 30, tzinfo=timezone.utc)
        service = StockTerminalService(repo, [provider], now=lambda: now)

        kline = service.get_klines("US", "US.AAPL", "1d", 50)
        minute = service.get_minute("US", "US.AAPL")
        flow = service.get_fund_flow("US", "US.AAPL")

        self.assertEqual(kline["source_status"]["kline"]["status"], "fresh")
        self.assertEqual(minute["source_status"]["minute"]["status"], "fresh")
        self.assertEqual(flow["source_status"]["fund_flow"]["status"], "fresh")
        self.assertEqual(provider.kline_calls, [("US", "US.AAPL", "1d", 50)])
        self.assertEqual(provider.minute_calls, [("US", "US.AAPL")])
        self.assertEqual(provider.fund_flow_calls, [("US", "US.AAPL")])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
PYTHONPATH=stock_screener python3 -m unittest stock_screener.tests.test_stock_terminal_service -v
```

Expected: FAIL with missing service/provider modules.

- [ ] **Step 3: Implement provider protocol**

Create `stock_screener/stock_terminal/providers/base.py`:

```python
from __future__ import annotations

from typing import List, Protocol

from stock_terminal.models import FundFlowPoint, KlinePoint, MinutePoint, QuoteSnapshot


class StockTerminalProvider(Protocol):
    name: str

    def fetch_quote(self, market: str, code: str) -> QuoteSnapshot:
        raise NotImplementedError

    def fetch_klines(self, market: str, code: str, timeframe: str, limit: int) -> List[KlinePoint]:
        raise NotImplementedError

    def fetch_minute(self, market: str, code: str) -> List[MinutePoint]:
        raise NotImplementedError

    def fetch_fund_flow(self, market: str, code: str) -> List[FundFlowPoint]:
        raise NotImplementedError


class EmptyStockTerminalProvider:
    name = "empty"

    def fetch_quote(self, market: str, code: str) -> QuoteSnapshot:
        raise RuntimeError("quote provider unavailable")

    def fetch_klines(self, market: str, code: str, timeframe: str, limit: int) -> List[KlinePoint]:
        raise RuntimeError("kline provider unavailable")

    def fetch_minute(self, market: str, code: str) -> List[MinutePoint]:
        raise RuntimeError("minute provider unavailable")

    def fetch_fund_flow(self, market: str, code: str) -> List[FundFlowPoint]:
        raise RuntimeError("fund-flow provider unavailable")
```

- [ ] **Step 4: Implement service**

Create `stock_screener/stock_terminal/service.py`:

```python
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Callable, Iterable, List

from stock_terminal.models import BlockStatus, StockTerminalSummary


class StockTerminalService:
    def __init__(self, repository, providers: Iterable, now: Callable[[], datetime] | None = None):
        self.repository = repository
        self.providers = list(providers)
        self.now = now or (lambda: datetime.now(timezone.utc))

    def get_summary(self, market: str, code: str):
        quote, status = self.repository.get_quote(market, code, now=self.now())
        if quote is not None and status.status == "cached":
            return StockTerminalSummary(
                market=market,
                code=code,
                name=quote.name or code,
                quote=quote,
                statuses={"quote": status},
            ).to_dict()
        try:
            quote = self._fetch_first("fetch_quote", market, code)
            quote.fetched_at = quote.fetched_at or self.now()
            self.repository.save_quote(quote, expires_at=self.now() + timedelta(minutes=5))
            status = BlockStatus(status="fresh", source=quote.source, fetched_at=quote.fetched_at, expires_at=self.now() + timedelta(minutes=5))
            return StockTerminalSummary(
                market=market,
                code=code,
                name=quote.name or code,
                quote=quote,
                statuses={"quote": status},
            ).to_dict()
        except Exception as exc:
            stale_quote = quote if quote is not None else None
            error_status = BlockStatus(
                status="stale" if stale_quote else "error",
                source=getattr(stale_quote, "source", "") or "provider",
                fetched_at=getattr(stale_quote, "fetched_at", None),
                stale=bool(stale_quote),
                error_message=str(exc),
            )
            return StockTerminalSummary(
                market=market,
                code=code,
                name=getattr(stale_quote, "name", "") or code,
                quote=stale_quote,
                statuses={"quote": error_status},
                data_gaps=["quote"],
            ).to_dict()

    def get_klines(self, market: str, code: str, timeframe: str, limit: int):
        cached, cache_status = self.repository.get_klines(market, code, timeframe, limit=limit, now=self.now())
        if cached and cache_status.status == "cached":
            return {
                "market": market,
                "code": code,
                "timeframe": timeframe,
                "rows": [row.to_dict() for row in cached],
                "source_status": {"kline": cache_status.to_dict()},
            }
        rows = self._fetch_first("fetch_klines", market, code, timeframe, limit)
        self.repository.save_klines(market, code, timeframe, rows, source=self._last_provider_name(), expires_at=self.now() + timedelta(minutes=30))
        return {
            "market": market,
            "code": code,
            "timeframe": timeframe,
            "rows": [row.to_dict() for row in rows],
            "source_status": {"kline": BlockStatus(status="fresh", source=self._last_provider_name()).to_dict()},
        }

    def get_minute(self, market: str, code: str):
        cached, cache_status = self.repository.get_minute(market, code, now=self.now())
        if cached and cache_status.status == "cached":
            return {
                "market": market,
                "code": code,
                "rows": [row.to_dict() for row in cached],
                "source_status": {"minute": cache_status.to_dict()},
            }
        rows = self._fetch_first("fetch_minute", market, code)
        self.repository.save_minute(market, code, rows, source=self._last_provider_name(), expires_at=self.now() + timedelta(minutes=1))
        return {
            "market": market,
            "code": code,
            "rows": [row.to_dict() for row in rows],
            "source_status": {"minute": BlockStatus(status="fresh", source=self._last_provider_name()).to_dict()},
        }

    def get_fund_flow(self, market: str, code: str):
        cached, cache_status = self.repository.get_fund_flow(market, code, now=self.now())
        if cached and cache_status.status == "cached":
            return {
                "market": market,
                "code": code,
                "rows": [row.to_dict() for row in cached],
                "source_status": {"fund_flow": cache_status.to_dict()},
            }
        rows = self._fetch_first("fetch_fund_flow", market, code)
        self.repository.save_fund_flow(market, code, rows, source=self._last_provider_name(), expires_at=self.now() + timedelta(minutes=30))
        return {
            "market": market,
            "code": code,
            "rows": [row.to_dict() for row in rows],
            "source_status": {"fund_flow": BlockStatus(status="fresh", source=self._last_provider_name()).to_dict()},
        }

    def _fetch_first(self, method_name: str, *args):
        errors: List[str] = []
        for provider in self.providers:
            self._last_provider = provider
            try:
                result = getattr(provider, method_name)(*args)
                if result is not None:
                    return result
            except Exception as exc:
                errors.append(f"{getattr(provider, 'name', 'provider')}: {exc}")
        raise RuntimeError("; ".join(errors) or f"{method_name} unavailable")

    def _last_provider_name(self) -> str:
        return getattr(getattr(self, "_last_provider", None), "name", "provider")
```

- [ ] **Step 5: Add provider factory and live provider skeleton**

Create `stock_screener/stock_terminal/providers/factory.py`:

```python
from __future__ import annotations

from stock_terminal.providers.eastmoney import EastmoneyStockTerminalProvider


def build_stock_terminal_providers(db=None):
    return [EastmoneyStockTerminalProvider(db=db)]
```

Create `stock_screener/stock_terminal/providers/eastmoney.py` with existing K-line fetchers wired immediately. Quote, minute, and fund-flow raise explicit capability errors in this task; Task 7 replaces those methods with live parsing before the feature is considered complete.

```python
from __future__ import annotations

from datetime import datetime, timezone
from typing import List

from kline_fetcher import KlineFetcherFactory
from stock_terminal.models import FundFlowPoint, KlinePoint, MinutePoint, QuoteSnapshot


class EastmoneyStockTerminalProvider:
    name = "eastmoney"

    def __init__(self, db=None):
        self.db = db

    def fetch_quote(self, market: str, code: str) -> QuoteSnapshot:
        raise RuntimeError("quote live provider is implemented in Task 7")

    def fetch_klines(self, market: str, code: str, timeframe: str, limit: int) -> List[KlinePoint]:
        for fetcher in KlineFetcherFactory.create_fetcher_chain(db=self.db):
            df = fetcher.fetch(code, market=market, timeframe=timeframe, max_count=limit)
            if df is None or df.empty:
                continue
            rows: List[KlinePoint] = []
            for item in df.tail(limit).to_dict("records"):
                rows.append(KlinePoint(
                    at=item["date"].to_pydatetime() if hasattr(item["date"], "to_pydatetime") else item["date"],
                    open=item.get("open"),
                    high=item.get("high"),
                    low=item.get("low"),
                    close=item.get("close"),
                    volume=item.get("volume"),
                    turnover=item.get("turnover"),
                ))
            self.name = fetcher.get_name()
            return rows
        raise RuntimeError("no K-line provider returned data")

    def fetch_minute(self, market: str, code: str) -> List[MinutePoint]:
        raise RuntimeError("minute live provider is implemented in Task 7")

    def fetch_fund_flow(self, market: str, code: str) -> List[FundFlowPoint]:
        raise RuntimeError("fund-flow live provider is implemented in Task 7")
```

- [ ] **Step 6: Run service tests**

Run:

```bash
PYTHONPATH=stock_screener python3 -m unittest stock_screener.tests.test_stock_terminal_service -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add stock_screener/stock_terminal/providers stock_screener/stock_terminal/service.py stock_screener/tests/test_stock_terminal_service.py
git commit -m "feat: add stock terminal service"
```

## Task 4: Stock Terminal API Router

**Files:**
- Create: `stock_screener/web/stock_terminal.py`
- Modify: `stock_screener/web/main.py`
- Test: `stock_screener/tests/test_stock_terminal_api.py`

- [ ] **Step 1: Write failing API tests**

Create `stock_screener/tests/test_stock_terminal_api.py`:

```python
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from web.auth import CurrentUser, require_user
from web.business import BusinessError
from web.errors import business_error_handler
from web.stock_terminal import get_stock_terminal_service, router


class FakeStockTerminalService:
    def __init__(self):
        self.calls = []

    def get_summary(self, market, code):
        self.calls.append(("summary", market, code))
        return {"market": market, "code": code, "quote": None, "source_status": {"quote": {"status": "empty"}}}

    def get_klines(self, market, code, timeframe, limit):
        self.calls.append(("klines", market, code, timeframe, limit))
        return {"market": market, "code": code, "timeframe": timeframe, "rows": [], "source_status": {"kline": {"status": "empty"}}}

    def get_minute(self, market, code):
        self.calls.append(("minute", market, code))
        return {"market": market, "code": code, "rows": [], "source_status": {"minute": {"status": "empty"}}}

    def get_fund_flow(self, market, code):
        self.calls.append(("fund_flow", market, code))
        return {"market": market, "code": code, "rows": [], "source_status": {"fund_flow": {"status": "empty"}}}


class StockTerminalApiTest(unittest.TestCase):
    def setUp(self):
        self.service = FakeStockTerminalService()
        app = FastAPI()
        app.add_exception_handler(BusinessError, business_error_handler)
        app.include_router(router)
        app.dependency_overrides[require_user] = lambda: CurrentUser(id=1, username="tester", role="admin")
        app.dependency_overrides[get_stock_terminal_service] = lambda: self.service
        self.client = TestClient(app)

    def test_summary_normalizes_a_share_code(self):
        response = self.client.get("/api/stock-terminal/A/600519/summary")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["code"], "SH.600519")
        self.assertEqual(self.service.calls, [("summary", "A", "SH.600519")])

    def test_klines_validates_timeframe_and_limit(self):
        response = self.client.get("/api/stock-terminal/US/AAPL/klines?timeframe=1d&limit=20")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.service.calls, [("klines", "US", "US.AAPL", "1d", 20)])

    def test_minute_and_fund_flow_routes_use_selected_stock_only(self):
        minute = self.client.get("/api/stock-terminal/HK/00700/minute")
        flow = self.client.get("/api/stock-terminal/HK/00700/fund-flow")

        self.assertEqual(minute.status_code, 200)
        self.assertEqual(flow.status_code, 200)
        self.assertEqual(self.service.calls, [
            ("minute", "HK", "HK.00700"),
            ("fund_flow", "HK", "HK.00700"),
        ])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
PYTHONPATH=stock_screener python3 -m unittest stock_screener.tests.test_stock_terminal_api -v
```

Expected: FAIL with missing `web.stock_terminal`.

- [ ] **Step 3: Implement API router**

Create `stock_screener/web/stock_terminal.py`:

```python
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from db import MarketDatabase
from market import normalize_market
from stock_terminal.providers.factory import build_stock_terminal_providers
from stock_terminal.repository import MySqlStockTerminalRepository
from stock_terminal.service import StockTerminalService
from timeframe import parse_timeframe
from web.auth import CurrentUser, get_db, require_user
from web.business import BusinessError
from web.single_stock import normalize_stock_code


router = APIRouter(prefix="/api/stock-terminal", tags=["stock-terminal"])


def get_stock_terminal_service(db: MarketDatabase = Depends(get_db)) -> StockTerminalService:
    if hasattr(db, "init_stock_terminal_schema"):
        db.init_stock_terminal_schema()
    repository = MySqlStockTerminalRepository(db)
    return StockTerminalService(repository, build_stock_terminal_providers(db=db))


def _identity(market: str, code: str):
    normalized_market = normalize_market(market)
    normalized_code = normalize_stock_code(normalized_market, code)
    return normalized_market, normalized_code


@router.get("/{market}/{code}/summary")
def get_summary(
    market: str,
    code: str,
    user: CurrentUser = Depends(require_user),
    service: StockTerminalService = Depends(get_stock_terminal_service),
):
    _ = user
    try:
        normalized_market, normalized_code = _identity(market, code)
        return service.get_summary(normalized_market, normalized_code)
    except ValueError as exc:
        raise BusinessError("STOCK_TERMINAL_INVALID_REQUEST", str(exc)) from exc


@router.get("/{market}/{code}/klines")
def get_klines(
    market: str,
    code: str,
    timeframe: str = Query(default="1d"),
    limit: int = Query(default=120, ge=1, le=500),
    user: CurrentUser = Depends(require_user),
    service: StockTerminalService = Depends(get_stock_terminal_service),
):
    _ = user
    try:
        normalized_market, normalized_code = _identity(market, code)
        normalized_timeframe = parse_timeframe(timeframe)
        return service.get_klines(normalized_market, normalized_code, normalized_timeframe, limit)
    except ValueError as exc:
        raise BusinessError("STOCK_TERMINAL_INVALID_REQUEST", str(exc)) from exc


@router.get("/{market}/{code}/minute")
def get_minute(
    market: str,
    code: str,
    user: CurrentUser = Depends(require_user),
    service: StockTerminalService = Depends(get_stock_terminal_service),
):
    _ = user
    try:
        normalized_market, normalized_code = _identity(market, code)
        return service.get_minute(normalized_market, normalized_code)
    except ValueError as exc:
        raise BusinessError("STOCK_TERMINAL_INVALID_REQUEST", str(exc)) from exc


@router.get("/{market}/{code}/fund-flow")
def get_fund_flow(
    market: str,
    code: str,
    user: CurrentUser = Depends(require_user),
    service: StockTerminalService = Depends(get_stock_terminal_service),
):
    _ = user
    try:
        normalized_market, normalized_code = _identity(market, code)
        return service.get_fund_flow(normalized_market, normalized_code)
    except ValueError as exc:
        raise BusinessError("STOCK_TERMINAL_INVALID_REQUEST", str(exc)) from exc
```

- [ ] **Step 4: Include router in app**

Modify `stock_screener/web/main.py`.

Add import near existing router imports:

```python
from .stock_terminal import router as stock_terminal_router
```

Add router include after existing includes:

```python
app.include_router(stock_terminal_router)
```

- [ ] **Step 5: Run API tests**

Run:

```bash
PYTHONPATH=stock_screener python3 -m unittest stock_screener.tests.test_stock_terminal_api -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add stock_screener/web/stock_terminal.py stock_screener/web/main.py stock_screener/tests/test_stock_terminal_api.py
git commit -m "feat: add stock terminal api"
```

## Task 5: Frontend API Types and Contract Tests

**Files:**
- Create: `stock_screener/web_frontend/src/features/stockTerminal/types.ts`
- Create: `stock_screener/web_frontend/src/features/stockTerminal/api.ts`
- Modify: `stock_screener/tests/test_code_screening_frontend.py`

- [ ] **Step 1: Update frontend contract tests first**

Modify `stock_screener/tests/test_code_screening_frontend.py`.

Add this test method to `CodeScreeningFrontendTest`:

```python
    def test_code_screening_is_merged_stock_terminal_entry(self):
        source = self.read_main()

        self.assertIn("个股筛选器", source)
        self.assertNotIn(">市场情报<", source)
        self.assertNotIn("page === 'marketIntel'", source)
        self.assertIn("StockTerminalPanel", source)

    def test_terminal_loading_is_row_selection_driven(self):
        source = self.read_main()

        self.assertIn("selectedTerminalRow", source)
        self.assertIn("setSelectedTerminalRow", source)
        self.assertIn("/api/stock-terminal", (ROOT / "web_frontend" / "src" / "features" / "stockTerminal" / "api.ts").read_text(encoding="utf-8"))
```

- [ ] **Step 2: Run contract tests to verify failure**

Run:

```bash
PYTHONPATH=stock_screener python3 -m unittest stock_screener.tests.test_code_screening_frontend -v
```

Expected: FAIL because `StockTerminalPanel`, `selectedTerminalRow`, and stock-terminal API files do not exist yet.

- [ ] **Step 3: Add TypeScript types**

Create `stock_screener/web_frontend/src/features/stockTerminal/types.ts`:

```typescript
export type DataBlockStatus = {
  status: 'fresh' | 'cached' | 'stale' | 'empty' | 'error' | string
  source?: string
  fetched_at?: string | null
  expires_at?: string | null
  stale?: boolean
  error_message?: string
}

export type QuoteSnapshot = {
  market: string
  code: string
  name?: string
  price?: number | null
  change?: number | null
  change_percent?: number | null
  open_price?: number | null
  high?: number | null
  low?: number | null
  previous_close?: number | null
  volume?: number | null
  turnover?: number | null
  fetched_at?: string | null
  source?: string
}

export type SeriesPoint = {
  at: string
  open?: number | null
  high?: number | null
  low?: number | null
  close?: number | null
  price?: number | null
  average_price?: number | null
  volume?: number | null
  turnover?: number | null
  inflow?: number | null
  outflow?: number | null
  net_inflow?: number | null
  main_net_inflow?: number | null
  retail_net_inflow?: number | null
}

export type StockTerminalSummary = {
  market: string
  code: string
  name?: string
  quote?: QuoteSnapshot | null
  source_status?: Record<string, DataBlockStatus>
  data_gaps?: string[]
}

export type StockTerminalSeriesResponse = {
  market: string
  code: string
  timeframe?: string
  rows: SeriesPoint[]
  source_status?: Record<string, DataBlockStatus>
}
```

- [ ] **Step 4: Add API client**

Create `stock_screener/web_frontend/src/features/stockTerminal/api.ts`:

```typescript
import { api } from '../../api'
import type { StockTerminalSeriesResponse, StockTerminalSummary } from './types'

function endpoint(market: string, code: string, suffix: string) {
  return `/api/stock-terminal/${encodeURIComponent(market)}/${encodeURIComponent(code)}${suffix}`
}

export function getStockTerminalSummary(market: string, code: string) {
  return api<StockTerminalSummary>(endpoint(market, code, '/summary'))
}

export function getStockTerminalKlines(market: string, code: string, timeframe: string, limit = 120) {
  const params = new URLSearchParams({ timeframe, limit: String(limit) })
  return api<StockTerminalSeriesResponse>(`${endpoint(market, code, '/klines')}?${params.toString()}`)
}

export function getStockTerminalMinute(market: string, code: string) {
  return api<StockTerminalSeriesResponse>(endpoint(market, code, '/minute'))
}

export function getStockTerminalFundFlow(market: string, code: string) {
  return api<StockTerminalSeriesResponse>(endpoint(market, code, '/fund-flow'))
}
```

- [ ] **Step 5: Run contract tests**

Run:

```bash
PYTHONPATH=stock_screener python3 -m unittest stock_screener.tests.test_code_screening_frontend -v
```

Expected: still FAIL until the UI task adds `StockTerminalPanel` and selection state.

- [ ] **Step 6: Commit only if the tests fail for the expected UI-only reasons**

```bash
git add stock_screener/web_frontend/src/features/stockTerminal/types.ts stock_screener/web_frontend/src/features/stockTerminal/api.ts stock_screener/tests/test_code_screening_frontend.py
git commit -m "test: define stock terminal frontend contract"
```

## Task 6: Merged Frontend Layout and Lazy Terminal Panel

**Files:**
- Create: `stock_screener/web_frontend/src/features/stockTerminal/StockTerminalPanel.tsx`
- Modify: `stock_screener/web_frontend/src/main.tsx`
- Modify: `stock_screener/web_frontend/src/styles.css`

- [ ] **Step 1: Create terminal panel component**

Create `stock_screener/web_frontend/src/features/stockTerminal/StockTerminalPanel.tsx`:

```tsx
import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { getMarketDigest, getProviderRuns, getStockIntel, previewEvidencePack } from '../marketIntel/api'
import type { EvidencePackPreview, IntelBundle, ProviderRun } from '../marketIntel/types'
import { getStockTerminalFundFlow, getStockTerminalKlines, getStockTerminalMinute, getStockTerminalSummary } from './api'
import type { DataBlockStatus, StockTerminalSeriesResponse, StockTerminalSummary } from './types'

type TerminalRow = {
  market: string
  code?: string
  name?: string
  timeframe?: string
  is_passed?: boolean
  filter_summary?: string
  sector?: string
  industry?: string
}

type TerminalTab = 'quote' | 'money' | 'intel' | 'evidence'

export function StockTerminalPanel({ row, timeframe = '1d' }: { row: TerminalRow | null; timeframe?: string }) {
  const [tab, setTab] = useState<TerminalTab>('quote')
  const [summary, setSummary] = useState<StockTerminalSummary | null>(null)
  const [klines, setKlines] = useState<StockTerminalSeriesResponse | null>(null)
  const [minute, setMinute] = useState<StockTerminalSeriesResponse | null>(null)
  const [fundFlow, setFundFlow] = useState<StockTerminalSeriesResponse | null>(null)
  const [intel, setIntel] = useState<IntelBundle | null>(null)
  const [pack, setPack] = useState<EvidencePackPreview | null>(null)
  const [runs, setRuns] = useState<ProviderRun[]>([])
  const [loading, setLoading] = useState('')
  const [error, setError] = useState('')

  const market = row?.market || ''
  const code = row?.code || ''

  useEffect(() => {
    setSummary(null)
    setKlines(null)
    setMinute(null)
    setFundFlow(null)
    setIntel(null)
    setPack(null)
    setRuns([])
    setError('')
    setTab('quote')
    if (!market || !code) return
    setLoading('summary')
    getStockTerminalSummary(market, code)
      .then(setSummary)
      .catch(err => setError(err instanceof Error ? err.message : '加载个股终端失败'))
      .finally(() => setLoading(''))
  }, [market, code])

  useEffect(() => {
    if (!market || !code) return
    if (tab === 'quote' && !klines && !minute) {
      setLoading('quote')
      Promise.all([
        getStockTerminalKlines(market, code, timeframe),
        getStockTerminalMinute(market, code),
      ])
        .then(([klineData, minuteData]) => {
          setKlines(klineData)
          setMinute(minuteData)
        })
        .catch(err => setError(err instanceof Error ? err.message : '加载行情失败'))
        .finally(() => setLoading(''))
    }
    if (tab === 'money' && !fundFlow) {
      setLoading('money')
      getStockTerminalFundFlow(market, code)
        .then(setFundFlow)
        .catch(err => setError(err instanceof Error ? err.message : '加载资金趋势失败'))
        .finally(() => setLoading(''))
    }
    if (tab === 'intel' && !intel) {
      setLoading('intel')
      getStockIntel(market as any, code, false)
        .then(setIntel)
        .catch(err => setError(err instanceof Error ? err.message : '加载市场情报失败'))
        .finally(() => setLoading(''))
    }
    if (tab === 'evidence' && !pack) {
      setLoading('evidence')
      Promise.all([
        previewEvidencePack(market as any, code, false),
        getProviderRuns(market as any, code, 20),
        getMarketDigest(market as any, false).catch(() => null),
      ])
        .then(([packData, runsData]) => {
          setPack(packData)
          setRuns(runsData.runs || [])
        })
        .catch(err => setError(err instanceof Error ? err.message : '加载证据包失败'))
        .finally(() => setLoading(''))
    }
  }, [tab, market, code, timeframe, klines, minute, fundFlow, intel, pack])

  const statuses = useMemo(() => ({
    ...(summary?.source_status || {}),
    ...(klines?.source_status || {}),
    ...(minute?.source_status || {}),
    ...(fundFlow?.source_status || {}),
    ...(intel?.source_status || {}),
    ...(pack?.source_status || {}),
  }), [summary, klines, minute, fundFlow, intel, pack])

  if (!row) {
    return (
      <aside className="stock-terminal-panel stock-terminal-empty">
        <h2>个股终端</h2>
        <p>从左侧筛选结果点选一只股票后加载行情、资金、情报和证据。</p>
      </aside>
    )
  }

  return (
    <aside className="stock-terminal-panel">
      <header className="stock-terminal-header">
        <div>
          <span className="terminal-code">{code}</span>
          <h2>{summary?.name || row.name || code}</h2>
          <p>{row.sector || '-'} · {row.industry || '-'}</p>
        </div>
        <StatusPill status={statuses.quote} />
      </header>

      <div className="stock-terminal-tabs">
        <button className={tab === 'quote' ? 'selected' : ''} onClick={() => setTab('quote')}>行情</button>
        <button className={tab === 'money' ? 'selected' : ''} onClick={() => setTab('money')}>资金</button>
        <button className={tab === 'intel' ? 'selected' : ''} onClick={() => setTab('intel')}>情报</button>
        <button className={tab === 'evidence' ? 'selected' : ''} onClick={() => setTab('evidence')}>证据</button>
      </div>

      {error && <div className="error stock-terminal-error">{error}</div>}
      {loading && <div className="empty">加载 {loading}...</div>}

      {tab === 'quote' && (
        <TerminalSection title="行情">
          <QuoteGrid summary={summary} />
          <MiniSeries title="K线" rows={klines?.rows || []} valueKey="close" status={statuses.kline} />
          <MiniSeries title="分时" rows={minute?.rows || []} valueKey="price" status={statuses.minute} />
        </TerminalSection>
      )}
      {tab === 'money' && (
        <TerminalSection title="资金">
          <MiniSeries title="资金趋势" rows={fundFlow?.rows || []} valueKey="net_inflow" status={statuses.fund_flow} />
        </TerminalSection>
      )}
      {tab === 'intel' && (
        <TerminalSection title="市场情报">
          <IntelGroups bundle={intel} />
        </TerminalSection>
      )}
      {tab === 'evidence' && (
        <TerminalSection title="证据">
          <EvidenceSummary pack={pack} runs={runs} />
        </TerminalSection>
      )}
    </aside>
  )
}

function TerminalSection({ title, children }: { title: string; children: ReactNode }) {
  return <section className="stock-terminal-section"><h3>{title}</h3>{children}</section>
}

function StatusPill({ status }: { status?: DataBlockStatus }) {
  const value = status?.status || 'empty'
  return <span className={`status terminal-status ${value}`}>{status?.source || value}</span>
}

function QuoteGrid({ summary }: { summary: StockTerminalSummary | null }) {
  const quote = summary?.quote
  const rows = [
    ['最新价', quote?.price],
    ['涨跌幅', quote?.change_percent],
    ['今开', quote?.open_price],
    ['最高', quote?.high],
    ['最低', quote?.low],
    ['昨收', quote?.previous_close],
    ['成交量', quote?.volume],
    ['成交额', quote?.turnover],
  ]
  return (
    <dl className="terminal-quote-grid">
      {rows.map(([label, value]) => (
        <div key={label}>
          <dt>{label}</dt>
          <dd>{value === undefined || value === null || value === '' ? '-' : String(value)}</dd>
        </div>
      ))}
    </dl>
  )
}

function MiniSeries({ title, rows, valueKey, status }: { title: string; rows: any[]; valueKey: string; status?: DataBlockStatus }) {
  return (
    <div className="terminal-series">
      <div className="terminal-series-heading">
        <strong>{title}</strong>
        <StatusPill status={status} />
      </div>
      {rows.length === 0 ? (
        <div className="empty">暂无{title}数据</div>
      ) : (
        <div className="terminal-series-bars">
          {rows.slice(-40).map((row, index) => {
            const raw = Number(row[valueKey] || 0)
            const height = Math.max(8, Math.min(96, Math.abs(raw)))
            return <span key={`${row.at}-${index}`} style={{ height }} title={`${row.at}: ${raw}`} />
          })}
        </div>
      )}
    </div>
  )
}

function IntelGroups({ bundle }: { bundle: IntelBundle | null }) {
  const groups = bundle?.groups || {}
  const entries = Object.entries(groups).filter(([, rows]) => Array.isArray(rows) && rows.length > 0)
  if (entries.length === 0) return <div className="empty">暂无市场情报</div>
  return (
    <div className="terminal-intel-list">
      {entries.map(([type, rows]) => (
        <section key={type}>
          <h4>{type}</h4>
          {rows.slice(0, 4).map(item => (
            <article key={item.dedupe_key || item.title} className="terminal-intel-item">
              <strong>{item.title || '未命名情报'}</strong>
              {item.summary && <p>{item.summary}</p>}
            </article>
          ))}
        </section>
      ))}
    </div>
  )
}

function EvidenceSummary({ pack, runs }: { pack: EvidencePackPreview | null; runs: ProviderRun[] }) {
  return (
    <div className="terminal-evidence">
      <dl className="terminal-quote-grid">
        <div><dt>结构化</dt><dd>{pack?.structured_items?.length || 0}</dd></div>
        <div><dt>引用</dt><dd>{pack?.citations?.length || 0}</dd></div>
        <div><dt>缺口</dt><dd>{pack?.data_gaps?.length || 0}</dd></div>
        <div><dt>Provider</dt><dd>{runs.length}</dd></div>
      </dl>
    </div>
  )
}
```

- [ ] **Step 2: Wire selected row into CodeScreening**

Modify imports in `stock_screener/web_frontend/src/main.tsx`:

```tsx
import { StockTerminalPanel } from './features/stockTerminal/StockTerminalPanel'
```

Remove the standalone import:

```tsx
import { MarketIntelPage } from './features/marketIntel/MarketIntelPage'
```

Change navigation labels:

```tsx
<button className={page === 'codeScreening' ? 'active' : ''} onClick={() => setPage('codeScreening')}>个股筛选器</button>
```

Remove the standalone Market Intel navigation button and render branch:

```tsx
{page === 'marketIntel' && <MarketIntelPage />}
```

Inside `CodeScreening`, add state near other state declarations:

```tsx
const [selectedTerminalRow, setSelectedTerminalRow] = useState<CustomListResultRow | null>(null)
```

When a new task is submitted, reset selection:

```tsx
setSelectedTerminalRow(null)
```

Replace the result panel block:

```tsx
<div className="code-terminal-workbench">
  <div className="code-terminal-results">
    <Panel title="筛选结果">
      <CodeScreeningResultTable
        rows={result.rows || []}
        taskId={result.task_id}
        openTask={openTask}
        selectedCode={selectedTerminalRow?.code || ''}
        onSelectRow={setSelectedTerminalRow}
      />
    </Panel>
  </div>
  <StockTerminalPanel row={selectedTerminalRow} timeframe={result.timeframe || timeframe} />
</div>
```

Update `CodeScreeningResultTable` signature:

```tsx
function CodeScreeningResultTable({
  rows,
  taskId,
  openTask,
  selectedCode = '',
  onSelectRow
}: {
  rows: CustomListResultRow[]
  taskId?: string
  openTask: (taskId: string) => void
  selectedCode?: string
  onSelectRow?: (row: CustomListResultRow) => void
}) {
```

Update each result row:

```tsx
<tr
  key={`${row.input || row.code || 'row'}-${index}`}
  className={row.code && row.code === selectedCode ? 'selected-row clickable-row' : 'clickable-row'}
  onClick={() => onSelectRow?.(row)}
>
```

- [ ] **Step 3: Add CSS**

Append to `stock_screener/web_frontend/src/styles.css`:

```css
.code-terminal-workbench {
  display: grid;
  grid-template-columns: minmax(520px, 1fr) minmax(360px, 460px);
  gap: 18px;
  align-items: start;
}

.code-terminal-results {
  min-width: 0;
}

.clickable-row {
  cursor: pointer;
}

.clickable-row:hover,
.selected-row {
  background: #eff6ff;
}

.stock-terminal-panel {
  position: sticky;
  top: 18px;
  display: grid;
  gap: 14px;
  min-width: 0;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  background: #ffffff;
  padding: 16px;
}

.stock-terminal-empty {
  min-height: 320px;
  align-content: center;
  color: #65758b;
}

.stock-terminal-header {
  display: flex;
  justify-content: space-between;
  gap: 12px;
}

.stock-terminal-header h2 {
  margin: 4px 0;
  font-size: 18px;
}

.stock-terminal-header p,
.terminal-code {
  margin: 0;
  color: #64748b;
  font-size: 12px;
}

.stock-terminal-tabs {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 8px;
}

.stock-terminal-tabs button {
  min-height: 36px;
  border: 1px solid #d8e0ea;
  border-radius: 6px;
  background: #ffffff;
  color: #334155;
}

.stock-terminal-tabs button.selected {
  border-color: #1f6feb;
  background: #e8f1ff;
  color: #1f6feb;
}

.stock-terminal-section {
  display: grid;
  gap: 12px;
}

.stock-terminal-section h3 {
  margin: 0;
  font-size: 15px;
}

.terminal-quote-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
  margin: 0;
}

.terminal-quote-grid div {
  display: grid;
  gap: 4px;
  padding: 10px;
  border: 1px solid #e2e8f0;
  border-radius: 6px;
}

.terminal-quote-grid dt {
  color: #64748b;
  font-size: 12px;
}

.terminal-quote-grid dd {
  margin: 0;
  color: #172033;
  font-weight: 700;
}

.terminal-series {
  display: grid;
  gap: 8px;
}

.terminal-series-heading {
  display: flex;
  justify-content: space-between;
  gap: 12px;
}

.terminal-series-bars {
  display: flex;
  align-items: end;
  gap: 2px;
  min-height: 110px;
  padding: 10px;
  border: 1px solid #e2e8f0;
  border-radius: 6px;
}

.terminal-series-bars span {
  flex: 1;
  min-width: 2px;
  border-radius: 2px 2px 0 0;
  background: #2563eb;
}

.terminal-intel-list,
.terminal-evidence {
  display: grid;
  gap: 12px;
}

.terminal-intel-item {
  display: grid;
  gap: 6px;
  padding: 10px;
  border: 1px solid #e2e8f0;
  border-radius: 6px;
}

.terminal-intel-item p {
  margin: 0;
  color: #475569;
  font-size: 13px;
  line-height: 1.45;
}

.terminal-status {
  white-space: nowrap;
}

.stock-terminal-error {
  margin: 0;
}

@media (max-width: 1100px) {
  .code-terminal-workbench {
    grid-template-columns: 1fr;
  }

  .stock-terminal-panel {
    position: static;
  }
}
```

- [ ] **Step 4: Run frontend contract tests**

Run:

```bash
PYTHONPATH=stock_screener python3 -m unittest stock_screener.tests.test_code_screening_frontend -v
```

Expected: PASS.

- [ ] **Step 5: Run frontend build**

Run:

```bash
cd stock_screener/web_frontend && npm run build
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add stock_screener/web_frontend/src/main.tsx stock_screener/web_frontend/src/styles.css stock_screener/web_frontend/src/features/stockTerminal/StockTerminalPanel.tsx
git commit -m "feat: merge code screening with stock terminal panel"
```

## Task 7: Live Provider Completion for A/HK/US

**Files:**
- Modify: `stock_screener/stock_terminal/providers/eastmoney.py`
- Test: `stock_screener/tests/test_stock_terminal_service.py`

- [ ] **Step 1: Add provider parser unit tests with recorded response shapes**

Append to `stock_screener/tests/test_stock_terminal_service.py`:

```python
from stock_terminal.providers.eastmoney import (
    parse_eastmoney_fund_flow_rows,
    parse_eastmoney_minute_rows,
    parse_eastmoney_quote,
)


class EastmoneyProviderParserTest(unittest.TestCase):
    def test_parse_quote_shape(self):
        payload = {"data": {"f43": 168800, "f44": 169900, "f45": 167000, "f46": 168000, "f47": 100000, "f48": 2000000, "f57": "600519", "f58": "贵州茅台", "f60": 166800, "f169": 2000, "f170": 120}}

        quote = parse_eastmoney_quote("A", "SH.600519", payload)

        self.assertEqual(quote.name, "贵州茅台")
        self.assertEqual(quote.price, 1688.0)
        self.assertEqual(quote.change_percent, 1.2)

    def test_parse_minute_shape(self):
        payload = {"data": {"trends": ["2026-05-25 09:30,10.0,10.1,100,1000", "2026-05-25 09:31,10.2,10.15,120,1300"]}}

        rows = parse_eastmoney_minute_rows(payload)

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1].price, 10.2)
        self.assertEqual(rows[1].average_price, 10.15)

    def test_parse_fund_flow_shape(self):
        payload = {"data": {"klines": ["2026-05-25,10,4,6,5,1"]}}

        rows = parse_eastmoney_fund_flow_rows(payload)

        self.assertEqual(rows[0].inflow, 10.0)
        self.assertEqual(rows[0].net_inflow, 6.0)
        self.assertEqual(rows[0].main_net_inflow, 5.0)
```

- [ ] **Step 2: Run parser tests to verify failure**

Run:

```bash
PYTHONPATH=stock_screener python3 -m unittest stock_screener.tests.test_stock_terminal_service.EastmoneyProviderParserTest -v
```

Expected: FAIL with missing parser functions.

- [ ] **Step 3: Implement parser helpers**

Append to `stock_screener/stock_terminal/providers/eastmoney.py`:

```python
from datetime import datetime, timezone


def _eastmoney_price(value):
    if value in (None, "-", ""):
        return None
    return float(value) / 100.0


def _eastmoney_percent(value):
    if value in (None, "-", ""):
        return None
    return float(value) / 100.0


def parse_eastmoney_quote(market: str, code: str, payload: dict) -> QuoteSnapshot:
    data = payload.get("data") or {}
    fetched_at = datetime.now(timezone.utc)
    return QuoteSnapshot(
        market=market,
        code=code,
        name=str(data.get("f58") or code),
        price=_eastmoney_price(data.get("f43")),
        change=_eastmoney_price(data.get("f169")),
        change_percent=_eastmoney_percent(data.get("f170")),
        open_price=_eastmoney_price(data.get("f46")),
        high=_eastmoney_price(data.get("f44")),
        low=_eastmoney_price(data.get("f45")),
        previous_close=_eastmoney_price(data.get("f60")),
        volume=float(data.get("f47") or 0),
        turnover=float(data.get("f48") or 0),
        fetched_at=fetched_at,
        source="eastmoney",
    )


def parse_eastmoney_minute_rows(payload: dict) -> List[MinutePoint]:
    rows: List[MinutePoint] = []
    for text in ((payload.get("data") or {}).get("trends") or []):
        parts = str(text).split(",")
        if len(parts) < 5:
            continue
        rows.append(MinutePoint(
            at=datetime.fromisoformat(parts[0]).replace(tzinfo=timezone.utc),
            price=float(parts[1]),
            average_price=float(parts[2]),
            volume=float(parts[3]),
            turnover=float(parts[4]),
        ))
    return rows


def parse_eastmoney_fund_flow_rows(payload: dict) -> List[FundFlowPoint]:
    rows: List[FundFlowPoint] = []
    for text in ((payload.get("data") or {}).get("klines") or []):
        parts = str(text).split(",")
        if len(parts) < 6:
            continue
        rows.append(FundFlowPoint(
            at=datetime.fromisoformat(parts[0]).replace(tzinfo=timezone.utc),
            inflow=float(parts[1]),
            outflow=float(parts[2]),
            net_inflow=float(parts[3]),
            main_net_inflow=float(parts[4]),
            retail_net_inflow=float(parts[5]),
        ))
    return rows
```

- [ ] **Step 4: Replace live method errors with HTTP calls**

In `EastmoneyStockTerminalProvider.__init__`, add a session:

```python
    def __init__(self, db=None, session=None, timeout_sec: float = 5.0):
        import requests
        self.db = db
        self.session = session or requests.Session()
        self.timeout_sec = timeout_sec
```

Replace `fetch_quote`, `fetch_minute`, and `fetch_fund_flow`:

```python
    def _secid(self, market: str, code: str) -> str:
        raw = code.replace("SH.", "").replace("SZ.", "").replace("BJ.", "").replace("HK.", "").replace("US.", "")
        if market == "A":
            prefix = "1" if code.startswith("SH.") else "0"
            return f"{prefix}.{raw}"
        if market == "HK":
            return f"116.{raw.zfill(5)}"
        return f"105.{raw}"

    def fetch_quote(self, market: str, code: str) -> QuoteSnapshot:
        response = self.session.get(
            "https://push2.eastmoney.com/api/qt/stock/get",
            params={"secid": self._secid(market, code), "fields": "f43,f44,f45,f46,f47,f48,f57,f58,f60,f169,f170"},
            timeout=self.timeout_sec,
        )
        response.raise_for_status()
        return parse_eastmoney_quote(market, code, response.json())

    def fetch_minute(self, market: str, code: str) -> List[MinutePoint]:
        response = self.session.get(
            "https://push2.eastmoney.com/api/qt/stock/trends2/get",
            params={"secid": self._secid(market, code), "fields1": "f1,f2,f3", "fields2": "f51,f53,f54,f55,f56"},
            timeout=self.timeout_sec,
        )
        response.raise_for_status()
        return parse_eastmoney_minute_rows(response.json())

    def fetch_fund_flow(self, market: str, code: str) -> List[FundFlowPoint]:
        response = self.session.get(
            "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get",
            params={"secid": self._secid(market, code), "lmt": "120", "fields1": "f1,f2,f3", "fields2": "f51,f52,f53,f54,f55,f56"},
            timeout=self.timeout_sec,
        )
        response.raise_for_status()
        return parse_eastmoney_fund_flow_rows(response.json())
```

- [ ] **Step 5: Run parser and service tests**

Run:

```bash
PYTHONPATH=stock_screener python3 -m unittest stock_screener.tests.test_stock_terminal_service -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add stock_screener/stock_terminal/providers/eastmoney.py stock_screener/tests/test_stock_terminal_service.py
git commit -m "feat: wire stock terminal live providers"
```

## Task 8: Full Regression and Browser Smoke

**Files:**
- No new files unless previous tasks reveal test-only fixes.

- [ ] **Step 1: Run backend focused tests**

Run:

```bash
PYTHONPATH=stock_screener python3 -m unittest \
  stock_screener.tests.test_stock_terminal_models \
  stock_screener.tests.test_stock_terminal_repository \
  stock_screener.tests.test_stock_terminal_service \
  stock_screener.tests.test_stock_terminal_api \
  stock_screener.tests.test_code_screening_frontend \
  stock_screener.tests.test_custom_list \
  stock_screener.tests.test_market_intel_api \
  stock_screener.tests.test_market_intel_frontend \
  -v
```

Expected: PASS.

- [ ] **Step 2: Run frontend build**

Run:

```bash
cd stock_screener/web_frontend && npm run build
```

Expected: PASS.

- [ ] **Step 3: Start local web app**

Use the existing project launcher or web server command used in this checkout. If using the unified launcher:

```bash
cd stock_screener
./run_moneymanager.sh
```

Expected: local web URL is printed and the app starts without import errors.

- [ ] **Step 4: Browser smoke**

In the browser:

- open the local web URL;
- log in if required;
- navigate to `个股筛选器`;
- submit one A-share code such as `600519`;
- verify a result row appears;
- click the row;
- verify the right panel appears and loads `行情`, `资金`, `情报`, and `证据` tabs lazily;
- confirm provider failures show block-level `empty`, `stale`, or `error` states instead of breaking the page.

- [ ] **Step 5: Final commit if smoke reveals small fixes**

If smoke requires fixes, commit only touched implementation files:

```bash
git status --short
git add <touched-files>
git commit -m "fix: polish stock terminal smoke issues"
```

Expected: no unrelated dirty files are staged.
