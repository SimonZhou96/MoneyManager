# Market Intel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a backend-first market intelligence layer that fetches, caches, exposes, and reuses stock/market evidence for screening, search, LLM analysis, simple web review, and Chinese reports.

**Architecture:** Add a focused `stock_screener/market_intel/` package with dataclass models, provider adapters, repository/service boundaries, evidence-pack construction, and report rendering. Wire it into existing FastAPI routers and `signal_analysis` through explicit optional steps so rule-chain screening remains the primary decision layer and provider failures never block CSV output.

**Tech Stack:** Python dataclasses, FastAPI, MySQL/PyMySQL, `requests`, existing `signal_analysis` search and LLM providers, React/Vite/TypeScript, Python `unittest`.

---

## Scope Check

This plan implements one integrated v1 because each part is testable and supports the same vertical flow:

```text
market_intel schema
-> repository and models
-> providers and service cache
-> EvidencePack builder
-> API and simple frontend
-> signal_analysis and report integration
```

The implementation must keep these v1 boundaries:

- Market intelligence is auxiliary evidence, not a screening pass/fail rule.
- Automated flows only fetch for passed rows, explicit custom-list rows, or explicit single-stock analysis.
- Search remains batch-first through the existing `signal_analysis.search_providers` interface.
- LLM calls remain batch-based through the existing `LLM_ANALYSIS_BATCH_SIZE` behavior.
- Provider failures downgrade to stale or empty evidence with source status.

## File Structure

Create:

- `stock_screener/sql/017_market_intel.sql` - deployment schema for market-intel cache, bundle, and provider run tables.
- `stock_screener/market_intel/__init__.py` - public package exports.
- `stock_screener/market_intel/models.py` - `IntelItem`, bundles, status objects, and `EvidencePack`.
- `stock_screener/market_intel/repository.py` - repository adapters around `MarketDatabase` plus in-memory repository for tests.
- `stock_screener/market_intel/providers/base.py` - provider protocol and helper normalization.
- `stock_screener/market_intel/providers/eastmoney.py` - Eastmoney stock-level provider.
- `stock_screener/market_intel/providers/news.py` - market news provider.
- `stock_screener/market_intel/providers/global_index.py` - global/major index provider.
- `stock_screener/market_intel/providers/factory.py` - environment-driven provider selection.
- `stock_screener/market_intel/service.py` - cache, refresh, stale fallback, and bundle construction.
- `stock_screener/market_intel/evidence.py` - `EvidencePackBuilder` that merges structured items, search documents, and manual context.
- `stock_screener/market_intel/reporting.py` - Chinese single-stock and multi-stock report renderers with chart-ready tables.
- `stock_screener/web/market_intel.py` - FastAPI router under `/api/market-intel`.
- `stock_screener/web_frontend/src/features/marketIntel/types.ts` - frontend types.
- `stock_screener/web_frontend/src/features/marketIntel/api.ts` - frontend API helpers.
- `stock_screener/web_frontend/src/features/marketIntel/MarketIntelPage.tsx` - simple market-intel page.
- `stock_screener/tests/test_market_intel_models.py`
- `stock_screener/tests/test_market_intel_repository.py`
- `stock_screener/tests/test_market_intel_providers.py`
- `stock_screener/tests/test_market_intel_service.py`
- `stock_screener/tests/test_market_intel_evidence.py`
- `stock_screener/tests/test_market_intel_api.py`
- `stock_screener/tests/test_market_intel_reporting.py`
- `stock_screener/tests/test_market_intel_frontend.py`

Modify:

- `stock_screener/db.py` - add schema initialization and CRUD methods for market-intel tables.
- `stock_screener/web/main.py` - include `market_intel` router and initialize schema on startup.
- `stock_screener/signal_analysis/chain.py` - add optional market-intel evidence step and report renderer handoff.
- `stock_screener/signal_analysis/service.py` - construct market-intel service when enabled.
- `stock_screener/signal_analysis/models.py` - add optional evidence-pack fields to result serialization only if needed by renderer.
- `stock_screener/web_frontend/src/main.tsx` - add `市场情报` nav item and page entry.
- `stock_screener/web_frontend/src/styles.css` - add page-specific layout styles.
- `stock_screener/.env.example` - document feature flags, provider toggles, and cache TTLs.
- `stock_screener/deploy/README.md` - document backend-data-first rollout and no-blocking behavior.

---

### Task 1: Schema And Repository Contract

**Files:**
- Create: `stock_screener/sql/017_market_intel.sql`
- Create: `stock_screener/market_intel/repository.py`
- Modify: `stock_screener/db.py`
- Test: `stock_screener/tests/test_market_intel_repository.py`

- [ ] **Step 1: Write failing repository tests**

Create `stock_screener/tests/test_market_intel_repository.py`:

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import unittest
from datetime import datetime, timedelta

from db import MarketDatabase
from market_intel.repository import InMemoryMarketIntelRepository


class FakeCursor:
    def __init__(self):
        self.executed = []
        self.executemany_calls = []
        self.rows = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def executemany(self, sql, values):
        self.executemany_calls.append((sql, list(values)))

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return list(self.rows)


class FakeConnection:
    def __init__(self):
        self.cursor_obj = FakeCursor()

    def cursor(self):
        return self.cursor_obj


class MarketIntelRepositoryTests(unittest.TestCase):
    def test_schema_file_mentions_all_tables(self):
        with open("sql/017_market_intel.sql", "r", encoding="utf-8") as f:
            sql = f.read()

        for table in [
            "market_intel_items",
            "market_intel_bundles",
            "market_intel_provider_runs",
        ]:
            self.assertIn(f"CREATE TABLE IF NOT EXISTS {table}", sql)

    def test_init_market_intel_schema_executes_deployment_sql(self):
        conn = FakeConnection()
        db = MarketDatabase.__new__(MarketDatabase)
        db.conn = conn

        db.init_market_intel_schema()

        executed_sql = "\n".join(sql for sql, _ in conn.cursor_obj.executed)
        self.assertIn("CREATE TABLE IF NOT EXISTS market_intel_items", executed_sql)
        self.assertIn("CREATE TABLE IF NOT EXISTS market_intel_bundles", executed_sql)
        self.assertIn("CREATE TABLE IF NOT EXISTS market_intel_provider_runs", executed_sql)

    def test_in_memory_repository_stores_bundle_and_provider_runs(self):
        repo = InMemoryMarketIntelRepository()
        now = datetime(2026, 5, 25, 9, 30, 0)
        expires = now + timedelta(minutes=30)

        repo.upsert_items([
            {
                "scope_type": "stock",
                "market": "A",
                "code": "600519",
                "source": "东方财富",
                "provider": "eastmoney",
                "item_type": "announcement",
                "title": "贵州茅台公告",
                "summary": "公告摘要",
                "url": "https://example.com/notice",
                "published_at": now,
                "raw_json": {"id": "n1"},
                "fetched_at": now,
                "expires_at": expires,
                "is_stale": False,
                "dedupe_key": "eastmoney:notice:n1",
            }
        ])
        repo.upsert_bundle({
            "scope_type": "stock",
            "market": "A",
            "code": "600519",
            "bundle_json": {"groups": {"announcement": [{"title": "贵州茅台公告"}]}},
            "freshness_status": "fresh",
            "source_status_json": {"eastmoney": {"status": "success"}},
        })
        repo.insert_provider_run({
            "provider": "eastmoney",
            "scope_type": "stock",
            "market": "A",
            "code": "600519",
            "status": "success",
            "error_message": "",
            "duration_ms": 12,
            "item_count": 1,
            "started_at": now,
            "finished_at": now,
        })

        bundle = repo.get_bundle("stock", "A", "600519")
        runs = repo.list_provider_runs(provider="eastmoney", market="A", code="600519", status=None, limit=10)

        self.assertEqual(bundle["freshness_status"], "fresh")
        self.assertEqual(bundle["bundle_json"]["groups"]["announcement"][0]["title"], "贵州茅台公告")
        self.assertEqual(runs[0]["status"], "success")

    def test_database_upsert_items_encodes_json_payload(self):
        conn = FakeConnection()
        db = MarketDatabase.__new__(MarketDatabase)
        db.conn = conn

        db.upsert_market_intel_items([
            {
                "scope_type": "market",
                "market": "US",
                "code": "",
                "source": "新浪",
                "provider": "news",
                "item_type": "market_news",
                "title": "美股盘前要闻",
                "summary": "三大指数期货小幅波动",
                "url": "https://example.com/news",
                "published_at": "2026-05-25T09:30:00",
                "raw_json": {"情绪": "中性"},
                "fetched_at": "2026-05-25T09:31:00",
                "expires_at": "2026-05-25T09:46:00",
                "is_stale": False,
                "dedupe_key": "news:us:1",
            }
        ])

        values = conn.cursor_obj.executemany_calls[-1][1][0]
        self.assertIn("美股盘前要闻", values)
        self.assertTrue(any("情绪" in str(value) for value in values))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the repository test and verify it fails for missing files/methods**

Run:

```bash
cd stock_screener
PYTHONPATH=. python -m unittest tests.test_market_intel_repository -v
```

Expected: FAIL with missing `market_intel` package or missing `init_market_intel_schema`.

- [ ] **Step 3: Create deployment SQL**

Create `stock_screener/sql/017_market_intel.sql`:

```sql
CREATE TABLE IF NOT EXISTS market_intel_items (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    scope_type VARCHAR(16) NOT NULL COMMENT 'stock/market',
    market VARCHAR(8) NOT NULL COMMENT 'HK/US/A',
    code VARCHAR(32) NOT NULL DEFAULT '' COMMENT '股票代码，市场级为空字符串',
    source VARCHAR(64) NOT NULL COMMENT '来源展示名',
    provider VARCHAR(64) NOT NULL COMMENT 'provider key',
    item_type VARCHAR(64) NOT NULL COMMENT 'financial/announcement/research_report/money_flow/long_tiger/index_snapshot/market_news/search_document/hot_sector/other',
    title VARCHAR(512) NOT NULL DEFAULT '',
    summary TEXT NULL,
    url VARCHAR(1024) NOT NULL DEFAULT '',
    published_at DATETIME(6) NULL,
    raw_json JSON NULL,
    fetched_at DATETIME(6) NOT NULL,
    expires_at DATETIME(6) NOT NULL,
    is_stale TINYINT(1) NOT NULL DEFAULT 0,
    dedupe_key VARCHAR(255) NOT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (id),
    UNIQUE KEY uk_market_intel_dedupe (scope_type, market, code, provider, dedupe_key),
    KEY idx_market_intel_scope (scope_type, market, code, item_type),
    KEY idx_market_intel_expires (expires_at),
    KEY idx_market_intel_published (published_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='市场情报标准化条目';

CREATE TABLE IF NOT EXISTS market_intel_bundles (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    scope_type VARCHAR(16) NOT NULL COMMENT 'stock/market',
    market VARCHAR(8) NOT NULL,
    code VARCHAR(32) NOT NULL DEFAULT '',
    bundle_json JSON NOT NULL,
    freshness_status VARCHAR(16) NOT NULL COMMENT 'fresh/partial/stale/empty',
    source_status_json JSON NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (id),
    UNIQUE KEY uk_market_intel_bundle_scope (scope_type, market, code),
    KEY idx_market_intel_bundle_status (freshness_status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='市场情报聚合包';

CREATE TABLE IF NOT EXISTS market_intel_provider_runs (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    provider VARCHAR(64) NOT NULL,
    scope_type VARCHAR(16) NOT NULL,
    market VARCHAR(8) NOT NULL,
    code VARCHAR(32) NOT NULL DEFAULT '',
    status VARCHAR(16) NOT NULL COMMENT 'success/failed/timeout/skipped',
    error_message TEXT NULL,
    duration_ms INT NOT NULL DEFAULT 0,
    item_count INT NOT NULL DEFAULT 0,
    started_at DATETIME(6) NOT NULL,
    finished_at DATETIME(6) NOT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (id),
    KEY idx_market_intel_runs_scope (provider, scope_type, market, code),
    KEY idx_market_intel_runs_status (status),
    KEY idx_market_intel_runs_started (started_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='市场情报 provider 执行记录';
```

- [ ] **Step 4: Add repository package with in-memory implementation**

Create `stock_screener/market_intel/__init__.py`:

```python
"""Backend data layer for stock and market intelligence."""
```

Create `stock_screener/market_intel/repository.py`:

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List, Optional, Protocol


class MarketIntelRepository(Protocol):
    def upsert_items(self, items: List[dict]) -> None:
        """Persist normalized market intelligence items."""

    def list_items(
        self,
        *,
        scope_type: str,
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
        limit: int,
    ) -> List[dict]:
        """Return recent provider execution records."""


class InMemoryMarketIntelRepository:
    def __init__(self):
        self.items: List[dict] = []
        self.bundles: Dict[tuple[str, str, str], dict] = {}
        self.provider_runs: List[dict] = []

    def upsert_items(self, items: List[dict]) -> None:
        for item in items:
            key = (
                str(item.get("scope_type") or ""),
                str(item.get("market") or ""),
                str(item.get("code") or ""),
                str(item.get("provider") or ""),
                str(item.get("dedupe_key") or ""),
            )
            self.items = [
                existing for existing in self.items
                if (
                    str(existing.get("scope_type") or ""),
                    str(existing.get("market") or ""),
                    str(existing.get("code") or ""),
                    str(existing.get("provider") or ""),
                    str(existing.get("dedupe_key") or ""),
                ) != key
            ]
            self.items.append(deepcopy(item))

    def list_items(
        self,
        *,
        scope_type: str,
        market: str,
        code: str = "",
        include_stale: bool = True,
        limit: int = 200,
    ) -> List[dict]:
        rows = []
        for item in self.items:
            if item.get("scope_type") != scope_type:
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
        key = (str(row.get("scope_type") or ""), str(row.get("market") or ""), str(row.get("code") or ""))
        self.bundles[key] = deepcopy(row)

    def get_bundle(self, scope_type: str, market: str, code: str = "") -> Optional[dict]:
        row = self.bundles.get((scope_type, market, code or ""))
        return deepcopy(row) if row else None

    def insert_provider_run(self, row: dict) -> None:
        self.provider_runs.append(deepcopy(row))

    def list_provider_runs(
        self,
        *,
        provider: Optional[str],
        market: Optional[str],
        code: Optional[str],
        status: Optional[str],
        limit: int,
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
```

- [ ] **Step 5: Add `MarketDatabase` schema and CRUD methods**

Modify `stock_screener/db.py`:

Add `init_market_intel_schema()` near other schema initializers:

```python
    def init_market_intel_schema(self):
        sql_path = Path(__file__).resolve().parent / "sql" / "017_market_intel.sql"
        statements = [item.strip() for item in sql_path.read_text(encoding="utf-8").split(";") if item.strip()]
        with self.conn.cursor() as cursor:
            for statement in statements:
                cursor.execute(statement)
```

Add these CRUD methods after `init_market_intel_schema()`:

```python
    def upsert_market_intel_items(self, items: Iterable[dict]) -> None:
        rows = []
        for item in items:
            scope_type = str(item.get("scope_type") or "").strip()
            market = str(item.get("market") or "").strip()
            provider = str(item.get("provider") or "").strip()
            dedupe_key = str(item.get("dedupe_key") or "").strip()
            if not (scope_type and market and provider and dedupe_key):
                continue
            rows.append((
                scope_type,
                market,
                str(item.get("code") or "").strip(),
                str(item.get("source") or provider).strip(),
                provider,
                str(item.get("item_type") or "other").strip(),
                str(item.get("title") or "").strip(),
                item.get("summary"),
                str(item.get("url") or "").strip(),
                _mysql_datetime_or_none(item.get("published_at")),
                _json_or_none(item.get("raw_json")),
                _mysql_datetime_or_none(item.get("fetched_at")),
                _mysql_datetime_or_none(item.get("expires_at")),
                1 if item.get("is_stale") else 0,
                dedupe_key,
            ))
        if not rows:
            return
        sql = """
            INSERT INTO market_intel_items
                (scope_type, market, code, source, provider, item_type, title, summary, url,
                 published_at, raw_json, fetched_at, expires_at, is_stale, dedupe_key)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE
                source=VALUES(source),
                item_type=VALUES(item_type),
                title=VALUES(title),
                summary=VALUES(summary),
                url=VALUES(url),
                published_at=VALUES(published_at),
                raw_json=VALUES(raw_json),
                fetched_at=VALUES(fetched_at),
                expires_at=VALUES(expires_at),
                is_stale=VALUES(is_stale)
        """
        with self.conn.cursor() as cursor:
            cursor.executemany(sql, rows)

    def list_market_intel_items(
        self,
        *,
        scope_type: str,
        market: str,
        code: str = "",
        include_stale: bool = True,
        limit: int = 200,
    ) -> List[dict]:
        sql = """
            SELECT scope_type, market, code, source, provider, item_type, title, summary, url,
                   published_at, raw_json, fetched_at, expires_at, is_stale, dedupe_key
            FROM market_intel_items
            WHERE scope_type=%s AND market=%s AND code=%s
        """
        params: list[Any] = [scope_type, market, code or ""]
        if not include_stale:
            sql += " AND is_stale=0"
        sql += " ORDER BY COALESCE(published_at, fetched_at) DESC, id DESC LIMIT %s"
        params.append(max(1, int(limit)))
        with self.conn.cursor() as cursor:
            cursor.execute(sql, tuple(params))
            rows = cursor.fetchall() or []
        return [
            {
                "scope_type": row[0],
                "market": row[1],
                "code": row[2],
                "source": row[3],
                "provider": row[4],
                "item_type": row[5],
                "title": row[6],
                "summary": row[7],
                "url": row[8],
                "published_at": row[9],
                "raw_json": _decode_json_field(row[10], {}),
                "fetched_at": row[11],
                "expires_at": row[12],
                "is_stale": bool(row[13]),
                "dedupe_key": row[14],
            }
            for row in rows
        ]

    def upsert_market_intel_bundle(self, row: dict) -> None:
        sql = """
            INSERT INTO market_intel_bundles
                (scope_type, market, code, bundle_json, freshness_status, source_status_json)
            VALUES (%s,%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE
                bundle_json=VALUES(bundle_json),
                freshness_status=VALUES(freshness_status),
                source_status_json=VALUES(source_status_json)
        """
        with self.conn.cursor() as cursor:
            cursor.execute(sql, (
                row["scope_type"],
                row["market"],
                row.get("code") or "",
                _json_or_none(row.get("bundle_json") or {}),
                row.get("freshness_status") or "empty",
                _json_or_none(row.get("source_status_json") or {}),
            ))

    def get_market_intel_bundle(self, scope_type: str, market: str, code: str = "") -> Optional[dict]:
        sql = """
            SELECT scope_type, market, code, bundle_json, freshness_status, source_status_json, updated_at
            FROM market_intel_bundles
            WHERE scope_type=%s AND market=%s AND code=%s
            LIMIT 1
        """
        with self.conn.cursor() as cursor:
            cursor.execute(sql, (scope_type, market, code or ""))
            row = cursor.fetchone()
        if not row:
            return None
        return {
            "scope_type": row[0],
            "market": row[1],
            "code": row[2],
            "bundle_json": _decode_json_field(row[3], {}),
            "freshness_status": row[4],
            "source_status_json": _decode_json_field(row[5], {}),
            "updated_at": row[6],
        }

    def insert_market_intel_provider_run(self, row: dict) -> None:
        sql = """
            INSERT INTO market_intel_provider_runs
                (provider, scope_type, market, code, status, error_message, duration_ms,
                 item_count, started_at, finished_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """
        with self.conn.cursor() as cursor:
            cursor.execute(sql, (
                row["provider"],
                row["scope_type"],
                row["market"],
                row.get("code") or "",
                row.get("status") or "failed",
                row.get("error_message"),
                int(row.get("duration_ms") or 0),
                int(row.get("item_count") or 0),
                _mysql_datetime_or_none(row.get("started_at")),
                _mysql_datetime_or_none(row.get("finished_at")),
            ))

    def list_market_intel_provider_runs(
        self,
        *,
        provider: Optional[str] = None,
        market: Optional[str] = None,
        code: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 50,
    ) -> List[dict]:
        sql = """
            SELECT provider, scope_type, market, code, status, error_message, duration_ms,
                   item_count, started_at, finished_at
            FROM market_intel_provider_runs
            WHERE 1=1
        """
        params: list[Any] = []
        if provider:
            sql += " AND provider=%s"
            params.append(provider)
        if market:
            sql += " AND market=%s"
            params.append(market)
        if code is not None:
            sql += " AND code=%s"
            params.append(code or "")
        if status:
            sql += " AND status=%s"
            params.append(status)
        sql += " ORDER BY started_at DESC, id DESC LIMIT %s"
        params.append(max(1, int(limit)))
        with self.conn.cursor() as cursor:
            cursor.execute(sql, tuple(params))
            rows = cursor.fetchall() or []
        return [
            {
                "provider": row[0],
                "scope_type": row[1],
                "market": row[2],
                "code": row[3],
                "status": row[4],
                "error_message": row[5],
                "duration_ms": row[6],
                "item_count": row[7],
                "started_at": row[8],
                "finished_at": row[9],
            }
            for row in rows
        ]
```

- [ ] **Step 6: Add database-backed repository adapter**

Append to `stock_screener/market_intel/repository.py`:

```python
class MySqlMarketIntelRepository:
    def __init__(self, db):
        self.db = db

    def upsert_items(self, items: List[dict]) -> None:
        self.db.upsert_market_intel_items(items)

    def list_items(
        self,
        *,
        scope_type: str,
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
        provider: Optional[str],
        market: Optional[str],
        code: Optional[str],
        status: Optional[str],
        limit: int,
    ) -> List[dict]:
        return self.db.list_market_intel_provider_runs(
            provider=provider,
            market=market,
            code=code,
            status=status,
            limit=limit,
        )
```

- [ ] **Step 7: Run repository tests and commit**

Run:

```bash
cd stock_screener
PYTHONPATH=. python -m unittest tests.test_market_intel_repository -v
```

Expected: PASS.

Commit:

```bash
git add stock_screener/sql/017_market_intel.sql stock_screener/market_intel/__init__.py stock_screener/market_intel/repository.py stock_screener/db.py stock_screener/tests/test_market_intel_repository.py
git commit -m "feat: add market intel repository"
```

---

### Task 2: Domain Models And Provider Interface

**Files:**
- Create: `stock_screener/market_intel/models.py`
- Create: `stock_screener/market_intel/providers/base.py`
- Test: `stock_screener/tests/test_market_intel_models.py`

- [ ] **Step 1: Write failing model tests**

Create `stock_screener/tests/test_market_intel_models.py`:

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import unittest
from datetime import datetime, timedelta

from market_intel.models import DataSourceStatus, EvidencePack, IntelItem, StockIntelBundle
from market_intel.providers.base import dedupe_items, ttl_for_item_type


class MarketIntelModelTests(unittest.TestCase):
    def test_intel_item_round_trip_preserves_source_and_staleness(self):
        now = datetime(2026, 5, 25, 9, 30, 0)
        item = IntelItem(
            scope_type="stock",
            market="A",
            code="600519",
            source="东方财富",
            provider="eastmoney",
            item_type="financial",
            title="贵州茅台财务摘要",
            summary="营收同比增长",
            url="https://example.com/f10",
            published_at=now,
            raw_json={"营业收入同比": "12.3%"},
            fetched_at=now,
            expires_at=now + timedelta(days=1),
            is_stale=False,
            dedupe_key="eastmoney:financial:600519",
        )

        payload = item.to_dict()
        restored = IntelItem.from_dict(payload)

        self.assertEqual(restored.source, "东方财富")
        self.assertEqual(restored.raw_json["营业收入同比"], "12.3%")
        self.assertFalse(restored.is_stale)

    def test_stock_bundle_groups_items_by_type(self):
        now = datetime(2026, 5, 25, 9, 30, 0)
        item = IntelItem(
            scope_type="stock",
            market="US",
            code="AAPL",
            source="news",
            provider="news",
            item_type="market_news",
            title="Apple news",
            summary="AI product update",
            fetched_at=now,
            expires_at=now + timedelta(minutes=30),
            dedupe_key="news:aapl",
        )

        bundle = StockIntelBundle(
            market="US",
            code="AAPL",
            groups={"market_news": [item]},
            freshness_status="fresh",
            source_status={"news": DataSourceStatus(provider="news", status="success", item_count=1)},
        )

        payload = bundle.to_dict()

        self.assertEqual(payload["groups"]["market_news"][0]["title"], "Apple news")
        self.assertEqual(payload["source_status"]["news"]["status"], "success")

    def test_evidence_pack_carries_citations_and_data_gaps(self):
        pack = EvidencePack(
            market="HK",
            code="00700",
            structured_items=[],
            search_documents=[],
            manual_items=[],
            market_context=[],
            stock_context=[],
            source_status={"eastmoney": {"status": "failed"}},
            data_gaps=["东方财富 F10 暂无数据"],
            citations=[{"title": "公告", "url": "https://example.com"}],
        )

        payload = pack.to_dict()

        self.assertEqual(payload["market"], "HK")
        self.assertIn("东方财富 F10 暂无数据", payload["data_gaps"])

    def test_dedupe_items_keeps_first_item(self):
        now = datetime(2026, 5, 25, 9, 30, 0)
        first = IntelItem(
            scope_type="market",
            market="A",
            source="财联社",
            provider="news",
            item_type="market_news",
            title="同一新闻",
            summary="第一条",
            fetched_at=now,
            expires_at=now + timedelta(minutes=10),
            dedupe_key="news:1",
        )
        second = IntelItem(
            scope_type="market",
            market="A",
            source="财联社",
            provider="news",
            item_type="market_news",
            title="同一新闻",
            summary="第二条",
            fetched_at=now,
            expires_at=now + timedelta(minutes=10),
            dedupe_key="news:1",
        )

        deduped = dedupe_items([first, second])

        self.assertEqual(len(deduped), 1)
        self.assertEqual(deduped[0].summary, "第一条")

    def test_ttl_policy_is_concrete(self):
        self.assertEqual(ttl_for_item_type("financial").days, 1)
        self.assertEqual(ttl_for_item_type("market_news").seconds, 900)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the model test and verify it fails**

Run:

```bash
cd stock_screener
PYTHONPATH=. python -m unittest tests.test_market_intel_models -v
```

Expected: FAIL with missing `market_intel.models`.

- [ ] **Step 3: Create domain models**

Create `stock_screener/market_intel/models.py`:

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional


def parse_datetime(value: Any) -> Optional[datetime]:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        return parsed.replace(tzinfo=None)
    return parsed


def datetime_to_json(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    return value.isoformat(timespec="seconds")


@dataclass(frozen=True)
class IntelItem:
    scope_type: str
    market: str
    source: str
    provider: str
    item_type: str
    title: str
    fetched_at: datetime
    expires_at: datetime
    dedupe_key: str
    code: str = ""
    summary: str = ""
    url: str = ""
    published_at: Optional[datetime] = None
    raw_json: Dict[str, Any] = field(default_factory=dict)
    is_stale: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "scope_type": self.scope_type,
            "market": self.market,
            "code": self.code,
            "source": self.source,
            "provider": self.provider,
            "item_type": self.item_type,
            "title": self.title,
            "summary": self.summary,
            "url": self.url,
            "published_at": datetime_to_json(self.published_at),
            "raw_json": dict(self.raw_json or {}),
            "fetched_at": datetime_to_json(self.fetched_at),
            "expires_at": datetime_to_json(self.expires_at),
            "is_stale": bool(self.is_stale),
            "dedupe_key": self.dedupe_key,
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "IntelItem":
        fetched_at = parse_datetime(payload.get("fetched_at")) or datetime.utcnow()
        expires_at = parse_datetime(payload.get("expires_at")) or fetched_at
        return cls(
            scope_type=str(payload.get("scope_type") or ""),
            market=str(payload.get("market") or ""),
            code=str(payload.get("code") or ""),
            source=str(payload.get("source") or ""),
            provider=str(payload.get("provider") or ""),
            item_type=str(payload.get("item_type") or "other"),
            title=str(payload.get("title") or ""),
            summary=str(payload.get("summary") or ""),
            url=str(payload.get("url") or ""),
            published_at=parse_datetime(payload.get("published_at")),
            raw_json=dict(payload.get("raw_json") or {}),
            fetched_at=fetched_at,
            expires_at=expires_at,
            is_stale=bool(payload.get("is_stale")),
            dedupe_key=str(payload.get("dedupe_key") or ""),
        )


@dataclass(frozen=True)
class DataSourceStatus:
    provider: str
    status: str
    item_count: int = 0
    error_message: str = ""
    fetched_at: Optional[datetime] = None
    stale: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "provider": self.provider,
            "status": self.status,
            "item_count": int(self.item_count),
            "error_message": self.error_message,
            "fetched_at": datetime_to_json(self.fetched_at),
            "stale": bool(self.stale),
        }


@dataclass(frozen=True)
class StockIntelBundle:
    market: str
    code: str
    groups: Dict[str, List[IntelItem]]
    freshness_status: str
    source_status: Dict[str, DataSourceStatus]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "scope_type": "stock",
            "market": self.market,
            "code": self.code,
            "groups": {
                key: [item.to_dict() for item in values]
                for key, values in sorted(self.groups.items())
            },
            "freshness_status": self.freshness_status,
            "source_status": {
                key: value.to_dict()
                for key, value in sorted(self.source_status.items())
            },
        }


@dataclass(frozen=True)
class MarketIntelBundle:
    market: str
    groups: Dict[str, List[IntelItem]]
    freshness_status: str
    source_status: Dict[str, DataSourceStatus]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "scope_type": "market",
            "market": self.market,
            "code": "",
            "groups": {
                key: [item.to_dict() for item in values]
                for key, values in sorted(self.groups.items())
            },
            "freshness_status": self.freshness_status,
            "source_status": {
                key: value.to_dict()
                for key, value in sorted(self.source_status.items())
            },
        }


@dataclass(frozen=True)
class EvidencePack:
    market: str
    code: str
    structured_items: List[Dict[str, Any]]
    search_documents: List[Dict[str, Any]]
    manual_items: List[Dict[str, Any]]
    market_context: List[Dict[str, Any]]
    stock_context: List[Dict[str, Any]]
    source_status: Dict[str, Any]
    data_gaps: List[str]
    citations: List[Dict[str, Any]]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "market": self.market,
            "code": self.code,
            "structured_items": list(self.structured_items),
            "search_documents": list(self.search_documents),
            "manual_items": list(self.manual_items),
            "market_context": list(self.market_context),
            "stock_context": list(self.stock_context),
            "source_status": dict(self.source_status),
            "data_gaps": list(self.data_gaps),
            "citations": list(self.citations),
        }
```

- [ ] **Step 4: Create provider base helpers**

Create `stock_screener/market_intel/providers/base.py`:

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import timedelta
from typing import Dict, Iterable, List

from market_intel.models import IntelItem


class MarketIntelProvider(ABC):
    name = "base"
    is_available = True

    @abstractmethod
    def fetch_stock(self, market: str, code: str) -> List[IntelItem]:
        """Fetch stock-scoped items."""

    @abstractmethod
    def fetch_market(self, market: str) -> List[IntelItem]:
        """Fetch market-scoped items."""


class NullMarketIntelProvider(MarketIntelProvider):
    name = "null"
    is_available = False

    def fetch_stock(self, market: str, code: str) -> List[IntelItem]:
        return []

    def fetch_market(self, market: str) -> List[IntelItem]:
        return []


def ttl_for_item_type(item_type: str) -> timedelta:
    value = str(item_type or "").strip()
    if value == "financial":
        return timedelta(days=1)
    if value in {"announcement", "research_report"}:
        return timedelta(hours=12)
    if value in {"money_flow", "long_tiger", "hot_sector"}:
        return timedelta(minutes=30)
    if value in {"market_news", "index_snapshot"}:
        return timedelta(minutes=15)
    if value == "search_document":
        return timedelta(hours=2)
    return timedelta(hours=6)


def dedupe_items(items: Iterable[IntelItem]) -> List[IntelItem]:
    seen = set()
    result: List[IntelItem] = []
    for item in items:
        key = (
            item.scope_type,
            item.market,
            item.code,
            item.provider,
            item.dedupe_key,
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def group_items(items: Iterable[IntelItem]) -> Dict[str, List[IntelItem]]:
    grouped: Dict[str, List[IntelItem]] = {}
    for item in items:
        grouped.setdefault(item.item_type, []).append(item)
    for values in grouped.values():
        values.sort(key=lambda item: item.published_at or item.fetched_at, reverse=True)
    return grouped
```

- [ ] **Step 5: Run model tests and commit**

Run:

```bash
cd stock_screener
PYTHONPATH=. python -m unittest tests.test_market_intel_models -v
```

Expected: PASS.

Commit:

```bash
git add stock_screener/market_intel/models.py stock_screener/market_intel/providers/base.py stock_screener/tests/test_market_intel_models.py
git commit -m "feat: add market intel domain models"
```

---

### Task 3: Provider Implementations

**Files:**
- Create: `stock_screener/market_intel/providers/eastmoney.py`
- Create: `stock_screener/market_intel/providers/news.py`
- Create: `stock_screener/market_intel/providers/global_index.py`
- Create: `stock_screener/market_intel/providers/factory.py`
- Test: `stock_screener/tests/test_market_intel_providers.py`

- [ ] **Step 1: Write provider tests with fake HTTP session**

Create `stock_screener/tests/test_market_intel_providers.py`:

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import unittest

from market_intel.providers.eastmoney import EastmoneyMarketIntelProvider, eastmoney_secu_code
from market_intel.providers.factory import build_market_intel_providers
from market_intel.providers.global_index import GlobalIndexProvider
from market_intel.providers.news import NewsIntelProvider


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code
        self.text = str(payload)

    def json(self):
        return self.payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeSession:
    def __init__(self):
        self.calls = []

    def get(self, url, params=None, timeout=None, headers=None):
        self.calls.append({"url": url, "params": params or {}, "timeout": timeout, "headers": headers or {}})
        if "np-anotice-stock" in url:
            return FakeResponse({"data": {"list": [{"art_code": "n1", "title": "测试公告", "display_time": "2026-05-25 09:00:00", "url": "https://example.com/notice"}]}})
        if "report/list" in url:
            return FakeResponse({"data": [{"id": "r1", "title": "测试研报", "publishDate": "2026-05-25 08:00:00", "infoCode": "r1"}]})
        if "datacenter-web" in url:
            return FakeResponse({"result": {"data": [{"SECURITY_CODE": "600519", "REPORT_DATE": "2026-03-31", "TOTAL_OPERATE_INCOME": 100, "PARENT_NETPROFIT": 20}]}})
        if "telegraphList" in url:
            return FakeResponse({"data": {"roll_data": [{"id": 1, "title": "市场快讯", "ctime": 1779680400}]}})
        if "global" in url:
            return FakeResponse({"data": [{"name": "纳斯达克", "price": 18000, "change_pct": 0.8}]})
        return FakeResponse({})


class ProviderTests(unittest.TestCase):
    def test_eastmoney_secu_code_normalizes_markets(self):
        self.assertEqual(eastmoney_secu_code("A", "600519"), "600519.SH")
        self.assertEqual(eastmoney_secu_code("A", "000001"), "000001.SZ")
        self.assertEqual(eastmoney_secu_code("HK", "00700"), "00700.HK")
        self.assertEqual(eastmoney_secu_code("US", "AAPL"), "AAPL")

    def test_eastmoney_provider_returns_stock_items(self):
        provider = EastmoneyMarketIntelProvider(session=FakeSession(), timeout_sec=3)

        items = provider.fetch_stock("A", "600519")

        item_types = {item.item_type for item in items}
        self.assertIn("announcement", item_types)
        self.assertIn("research_report", item_types)
        self.assertIn("financial", item_types)
        self.assertTrue(all(item.provider == "eastmoney" for item in items))

    def test_news_provider_returns_market_news(self):
        provider = NewsIntelProvider(session=FakeSession(), timeout_sec=3)

        items = provider.fetch_market("A")

        self.assertEqual(items[0].item_type, "market_news")
        self.assertEqual(items[0].title, "市场快讯")

    def test_global_index_provider_returns_index_snapshot(self):
        provider = GlobalIndexProvider(session=FakeSession(), timeout_sec=3)

        items = provider.fetch_market("US")

        self.assertEqual(items[0].item_type, "index_snapshot")
        self.assertIn("纳斯达克", items[0].title)

    def test_factory_respects_disabled_flag(self):
        providers = build_market_intel_providers(enable_live=False)

        self.assertEqual(providers, [])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run provider tests and verify they fail**

Run:

```bash
cd stock_screener
PYTHONPATH=. python -m unittest tests.test_market_intel_providers -v
```

Expected: FAIL with missing provider modules.

- [ ] **Step 3: Implement Eastmoney provider**

Create `stock_screener/market_intel/providers/eastmoney.py`:

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from market_intel.models import IntelItem, parse_datetime
from market_intel.providers.base import MarketIntelProvider, dedupe_items, ttl_for_item_type

try:
    import requests
except Exception:
    requests = None


def eastmoney_secu_code(market: str, code: str) -> str:
    market_key = str(market or "").upper()
    text = str(code or "").strip().upper()
    digits = "".join(ch for ch in text if ch.isdigit())
    if market_key == "A":
        if text.startswith(("SH.", "SH")) or digits.startswith("6"):
            return f"{digits}.SH"
        return f"{digits}.SZ"
    if market_key == "HK":
        return f"{digits.zfill(5)}.HK"
    return text.replace("US.", "")


class EastmoneyMarketIntelProvider(MarketIntelProvider):
    name = "eastmoney"

    def __init__(self, session=None, timeout_sec: int = 10):
        if session is not None:
            self.session = session
        elif requests is not None:
            self.session = requests.Session()
        else:
            self.session = None
        self.timeout_sec = timeout_sec

    @property
    def is_available(self) -> bool:
        return self.session is not None

    def fetch_stock(self, market: str, code: str) -> List[IntelItem]:
        if self.session is None:
            return []
        secu_code = eastmoney_secu_code(market, code)
        fetched_at = datetime.utcnow()
        items: List[IntelItem] = []
        items.extend(self._fetch_announcements(market, code, secu_code, fetched_at))
        items.extend(self._fetch_reports(market, code, secu_code, fetched_at))
        items.extend(self._fetch_financial_summary(market, code, secu_code, fetched_at))
        return dedupe_items(items)

    def fetch_market(self, market: str) -> List[IntelItem]:
        return []

    def _get_json(self, url: str, params: Dict[str, Any]) -> Dict[str, Any]:
        response = self.session.get(
            url,
            params=params,
            timeout=self.timeout_sec,
            headers={"User-Agent": "Mozilla/5.0 MoneyManager/market-intel"},
        )
        response.raise_for_status()
        data = response.json()
        return data if isinstance(data, dict) else {}

    def _fetch_announcements(self, market: str, code: str, secu_code: str, fetched_at: datetime) -> List[IntelItem]:
        data = self._get_json(
            "https://np-anotice-stock.eastmoney.com/api/security/ann",
            {"sr": -1, "page_size": 10, "page_index": 1, "ann_type": "A", "client_source": "web", "stock_list": secu_code},
        )
        rows = ((data.get("data") or {}).get("list") or []) if isinstance(data.get("data"), dict) else []
        return [
            self._item(
                market=market,
                code=code,
                item_type="announcement",
                title=str(row.get("title") or row.get("notice_title") or ""),
                summary=str(row.get("summary") or ""),
                url=str(row.get("url") or row.get("attach_url") or ""),
                published_at=parse_datetime(row.get("display_time") or row.get("notice_date")),
                raw_json=row,
                fetched_at=fetched_at,
                dedupe_key=f"announcement:{row.get('art_code') or row.get('id') or row.get('title')}",
            )
            for row in rows
            if isinstance(row, dict) and (row.get("title") or row.get("notice_title"))
        ]

    def _fetch_reports(self, market: str, code: str, secu_code: str, fetched_at: datetime) -> List[IntelItem]:
        data = self._get_json(
            "https://reportapi.eastmoney.com/report/list",
            {"pageNo": 1, "pageSize": 10, "code": secu_code, "qType": 0},
        )
        rows = data.get("data") or []
        return [
            self._item(
                market=market,
                code=code,
                item_type="research_report",
                title=str(row.get("title") or ""),
                summary=str(row.get("summary") or row.get("orgSName") or ""),
                url=str(row.get("url") or f"https://data.eastmoney.com/report/{row.get('infoCode')}.html"),
                published_at=parse_datetime(row.get("publishDate")),
                raw_json=row,
                fetched_at=fetched_at,
                dedupe_key=f"research:{row.get('id') or row.get('infoCode') or row.get('title')}",
            )
            for row in rows
            if isinstance(row, dict) and row.get("title")
        ]

    def _fetch_financial_summary(self, market: str, code: str, secu_code: str, fetched_at: datetime) -> List[IntelItem]:
        data = self._get_json(
            "https://datacenter-web.eastmoney.com/api/data/v1/get",
            {
                "reportName": "RPT_F10_FINANCE_GINCOMEQC",
                "columns": "ALL",
                "pageNumber": 1,
                "pageSize": 4,
                "filter": f'(SECUCODE="{secu_code}")',
            },
        )
        rows = ((data.get("result") or {}).get("data") or []) if isinstance(data.get("result"), dict) else []
        return [
            self._item(
                market=market,
                code=code,
                item_type="financial",
                title=f"{code} 最新财务摘要",
                summary=_financial_summary(row),
                url="https://data.eastmoney.com/",
                published_at=parse_datetime(row.get("REPORT_DATE")),
                raw_json=row,
                fetched_at=fetched_at,
                dedupe_key=f"financial:{secu_code}:{row.get('REPORT_DATE') or fetched_at.date()}",
            )
            for row in rows[:1]
            if isinstance(row, dict)
        ]

    def _item(
        self,
        *,
        market: str,
        code: str,
        item_type: str,
        title: str,
        summary: str,
        url: str,
        published_at: Optional[datetime],
        raw_json: Dict[str, Any],
        fetched_at: datetime,
        dedupe_key: str,
    ) -> IntelItem:
        return IntelItem(
            scope_type="stock",
            market=market,
            code=code,
            source="东方财富",
            provider=self.name,
            item_type=item_type,
            title=title,
            summary=summary,
            url=url,
            published_at=published_at,
            raw_json=raw_json,
            fetched_at=fetched_at,
            expires_at=fetched_at + ttl_for_item_type(item_type),
            is_stale=False,
            dedupe_key=dedupe_key,
        )


def _financial_summary(row: Dict[str, Any]) -> str:
    revenue = row.get("TOTAL_OPERATE_INCOME") or row.get("OPERATE_INCOME")
    profit = row.get("PARENT_NETPROFIT") or row.get("NETPROFIT")
    report_date = row.get("REPORT_DATE") or ""
    parts = [f"报告期 {report_date}"]
    if revenue is not None:
        parts.append(f"营收 {revenue}")
    if profit is not None:
        parts.append(f"归母净利润 {profit}")
    return "；".join(parts)
```

- [ ] **Step 4: Implement news and index providers**

Create `stock_screener/market_intel/providers/news.py`:

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

from datetime import datetime
from typing import List

from market_intel.models import IntelItem
from market_intel.providers.base import MarketIntelProvider, dedupe_items, ttl_for_item_type

try:
    import requests
except Exception:
    requests = None


class NewsIntelProvider(MarketIntelProvider):
    name = "news"

    def __init__(self, session=None, timeout_sec: int = 10):
        if session is not None:
            self.session = session
        elif requests is not None:
            self.session = requests.Session()
        else:
            self.session = None
        self.timeout_sec = timeout_sec

    @property
    def is_available(self) -> bool:
        return self.session is not None

    def fetch_stock(self, market: str, code: str) -> List[IntelItem]:
        return []

    def fetch_market(self, market: str) -> List[IntelItem]:
        if self.session is None:
            return []
        response = self.session.get(
            "https://www.cls.cn/nodeapi/telegraphList",
            params={"app": "CailianpressWeb", "category": "", "lastTime": "", "os": "web", "rn": 20},
            timeout=self.timeout_sec,
            headers={"User-Agent": "Mozilla/5.0 MoneyManager/market-intel"},
        )
        response.raise_for_status()
        data = response.json() or {}
        rows = ((data.get("data") or {}).get("roll_data") or []) if isinstance(data.get("data"), dict) else []
        fetched_at = datetime.utcnow()
        items = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            title = str(row.get("title") or "").strip()
            if not title:
                continue
            published_at = _from_unix(row.get("ctime"))
            items.append(IntelItem(
                scope_type="market",
                market=market,
                code="",
                source="财联社",
                provider=self.name,
                item_type="market_news",
                title=title,
                summary=str(row.get("content") or title),
                url=str(row.get("shareurl") or ""),
                published_at=published_at,
                raw_json=row,
                fetched_at=fetched_at,
                expires_at=fetched_at + ttl_for_item_type("market_news"),
                dedupe_key=f"cls:{row.get('id') or title}",
            ))
        return dedupe_items(items)


def _from_unix(value):
    try:
        raw = int(value)
    except (TypeError, ValueError):
        return None
    if raw > 10_000_000_000:
        raw = raw // 1000
    return datetime.fromtimestamp(raw)
```

Create `stock_screener/market_intel/providers/global_index.py`:

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

from datetime import datetime
from typing import List

from market_intel.models import IntelItem
from market_intel.providers.base import MarketIntelProvider, ttl_for_item_type

try:
    import requests
except Exception:
    requests = None


class GlobalIndexProvider(MarketIntelProvider):
    name = "global_index"

    def __init__(self, session=None, timeout_sec: int = 10):
        if session is not None:
            self.session = session
        elif requests is not None:
            self.session = requests.Session()
        else:
            self.session = None
        self.timeout_sec = timeout_sec

    @property
    def is_available(self) -> bool:
        return self.session is not None

    def fetch_stock(self, market: str, code: str) -> List[IntelItem]:
        return []

    def fetch_market(self, market: str) -> List[IntelItem]:
        if self.session is None:
            return []
        response = self.session.get(
            "https://push2.eastmoney.com/api/qt/ulist.np/get",
            params={"fltt": 2, "secids": "100.NDX,100.DJIA,100.SPX,100.HSI,1.000001"},
            timeout=self.timeout_sec,
            headers={"User-Agent": "Mozilla/5.0 MoneyManager/market-intel"},
        )
        response.raise_for_status()
        data = response.json() or {}
        rows = data.get("data") if isinstance(data.get("data"), list) else ((data.get("data") or {}).get("diff") or [])
        fetched_at = datetime.utcnow()
        items = []
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            name = str(row.get("name") or row.get("f14") or "").strip()
            if not name:
                continue
            price = row.get("price") if "price" in row else row.get("f2")
            change_pct = row.get("change_pct") if "change_pct" in row else row.get("f3")
            items.append(IntelItem(
                scope_type="market",
                market=market,
                code="",
                source="东方财富指数",
                provider=self.name,
                item_type="index_snapshot",
                title=f"{name} 指数快照",
                summary=f"最新 {price}，涨跌幅 {change_pct}%",
                url="https://quote.eastmoney.com/",
                raw_json=row,
                fetched_at=fetched_at,
                expires_at=fetched_at + ttl_for_item_type("index_snapshot"),
                dedupe_key=f"index:{name}",
            ))
        return items
```

- [ ] **Step 5: Implement provider factory**

Create `stock_screener/market_intel/providers/factory.py`:

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import os
from typing import List

from market_intel.providers.base import MarketIntelProvider
from market_intel.providers.eastmoney import EastmoneyMarketIntelProvider
from market_intel.providers.global_index import GlobalIndexProvider
from market_intel.providers.news import NewsIntelProvider


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        return int(raw)
    except ValueError:
        return default


def build_market_intel_providers(enable_live: bool | None = None) -> List[MarketIntelProvider]:
    enabled = _env_bool("MARKET_INTEL_ENABLE_LIVE_PROVIDERS", True) if enable_live is None else bool(enable_live)
    if not enabled:
        return []
    timeout = max(1, _env_int("MARKET_INTEL_PROVIDER_TIMEOUT_SEC", 10))
    providers: List[MarketIntelProvider] = [
        EastmoneyMarketIntelProvider(timeout_sec=timeout),
        NewsIntelProvider(timeout_sec=timeout),
        GlobalIndexProvider(timeout_sec=timeout),
    ]
    return [provider for provider in providers if getattr(provider, "is_available", False)]
```

- [ ] **Step 6: Run provider tests and commit**

Run:

```bash
cd stock_screener
PYTHONPATH=. python -m unittest tests.test_market_intel_providers -v
```

Expected: PASS.

Commit:

```bash
git add stock_screener/market_intel/providers stock_screener/tests/test_market_intel_providers.py
git commit -m "feat: add market intel providers"
```

---

### Task 4: Service Cache And Stale Fallback

**Files:**
- Create: `stock_screener/market_intel/service.py`
- Test: `stock_screener/tests/test_market_intel_service.py`

- [ ] **Step 1: Write service tests**

Create `stock_screener/tests/test_market_intel_service.py`:

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import unittest
from datetime import datetime, timedelta

from market_intel.models import IntelItem
from market_intel.repository import InMemoryMarketIntelRepository
from market_intel.service import MarketIntelService


class RecordingProvider:
    name = "recording"
    is_available = True

    def __init__(self, items=None, error=None):
        self.items = items or []
        self.error = error
        self.stock_calls = []
        self.market_calls = []

    def fetch_stock(self, market, code):
        self.stock_calls.append((market, code))
        if self.error:
            raise self.error
        return list(self.items)

    def fetch_market(self, market):
        self.market_calls.append(market)
        if self.error:
            raise self.error
        return list(self.items)


def stock_item(title="公告", stale=False):
    now = datetime(2026, 5, 25, 9, 30, 0)
    return IntelItem(
        scope_type="stock",
        market="A",
        code="600519",
        source="测试源",
        provider="recording",
        item_type="announcement",
        title=title,
        summary="摘要",
        fetched_at=now,
        expires_at=now + timedelta(minutes=30),
        is_stale=stale,
        dedupe_key=f"recording:{title}",
    )


class MarketIntelServiceTests(unittest.TestCase):
    def test_get_stock_intel_uses_cache_when_available(self):
        repo = InMemoryMarketIntelRepository()
        provider = RecordingProvider(items=[stock_item("新公告")])
        service = MarketIntelService(repository=repo, providers=[provider])

        first = service.get_stock_intel("A", "600519", force_refresh=True)
        second = service.get_stock_intel("A", "600519", force_refresh=False)

        self.assertEqual(first["freshness_status"], "fresh")
        self.assertEqual(second["groups"]["announcement"][0]["title"], "新公告")
        self.assertEqual(len(provider.stock_calls), 1)

    def test_provider_failure_returns_stale_cache(self):
        repo = InMemoryMarketIntelRepository()
        service = MarketIntelService(repository=repo, providers=[RecordingProvider(items=[stock_item("旧公告")])])
        service.get_stock_intel("A", "600519", force_refresh=True)

        failing = MarketIntelService(repository=repo, providers=[RecordingProvider(error=RuntimeError("boom"))])
        bundle = failing.get_stock_intel("A", "600519", force_refresh=True)

        self.assertEqual(bundle["freshness_status"], "stale")
        self.assertTrue(bundle["groups"]["announcement"][0]["is_stale"])
        self.assertEqual(bundle["source_status"]["recording"]["status"], "failed")

    def test_market_digest_groups_market_items(self):
        now = datetime(2026, 5, 25, 9, 30, 0)
        item = IntelItem(
            scope_type="market",
            market="US",
            source="财联社",
            provider="recording",
            item_type="market_news",
            title="市场快讯",
            summary="摘要",
            fetched_at=now,
            expires_at=now + timedelta(minutes=15),
            dedupe_key="market:1",
        )
        service = MarketIntelService(
            repository=InMemoryMarketIntelRepository(),
            providers=[RecordingProvider(items=[item])],
        )

        bundle = service.get_market_digest("US", force_refresh=True)

        self.assertEqual(bundle["groups"]["market_news"][0]["title"], "市场快讯")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run service tests and verify they fail**

Run:

```bash
cd stock_screener
PYTHONPATH=. python -m unittest tests.test_market_intel_service -v
```

Expected: FAIL with missing service module.

- [ ] **Step 3: Implement service**

Create `stock_screener/market_intel/service.py`:

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import time
from datetime import datetime
from typing import Dict, List, Optional

from market_intel.models import DataSourceStatus, IntelItem, MarketIntelBundle, StockIntelBundle
from market_intel.providers.base import MarketIntelProvider, dedupe_items, group_items
from market_intel.repository import MarketIntelRepository


class MarketIntelService:
    def __init__(self, repository: MarketIntelRepository, providers: List[MarketIntelProvider]):
        self.repository = repository
        self.providers = [provider for provider in providers if getattr(provider, "is_available", False)]

    def get_stock_intel(self, market: str, code: str, force_refresh: bool = False) -> dict:
        cached = self.repository.get_bundle("stock", market, code)
        if cached and not force_refresh:
            return dict(cached["bundle_json"])
        return self.refresh_stock_intel(market, code)

    def get_market_digest(self, market: str, force_refresh: bool = False) -> dict:
        cached = self.repository.get_bundle("market", market, "")
        if cached and not force_refresh:
            return dict(cached["bundle_json"])
        return self.refresh_market_digest(market)

    def refresh_stock_intel(self, market: str, code: str) -> dict:
        return self._refresh(scope_type="stock", market=market, code=code)

    def refresh_market_digest(self, market: str) -> dict:
        return self._refresh(scope_type="market", market=market, code="")

    def list_provider_runs(
        self,
        *,
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

    def _refresh(self, *, scope_type: str, market: str, code: str) -> dict:
        collected: List[IntelItem] = []
        source_status: Dict[str, DataSourceStatus] = {}
        failures = 0
        for provider in self.providers:
            started = datetime.utcnow()
            timer = time.monotonic()
            try:
                if scope_type == "stock":
                    items = provider.fetch_stock(market, code)
                else:
                    items = provider.fetch_market(market)
                finished = datetime.utcnow()
                collected.extend(items)
                status = DataSourceStatus(
                    provider=provider.name,
                    status="success",
                    item_count=len(items),
                    fetched_at=finished,
                    stale=False,
                )
                self.repository.insert_provider_run({
                    "provider": provider.name,
                    "scope_type": scope_type,
                    "market": market,
                    "code": code,
                    "status": "success",
                    "error_message": "",
                    "duration_ms": int((time.monotonic() - timer) * 1000),
                    "item_count": len(items),
                    "started_at": started,
                    "finished_at": finished,
                })
            except Exception as exc:
                failures += 1
                finished = datetime.utcnow()
                status = DataSourceStatus(
                    provider=provider.name,
                    status="failed",
                    item_count=0,
                    error_message=f"{type(exc).__name__}: {exc}",
                    fetched_at=finished,
                    stale=True,
                )
                self.repository.insert_provider_run({
                    "provider": provider.name,
                    "scope_type": scope_type,
                    "market": market,
                    "code": code,
                    "status": "failed",
                    "error_message": status.error_message,
                    "duration_ms": int((time.monotonic() - timer) * 1000),
                    "item_count": 0,
                    "started_at": started,
                    "finished_at": finished,
                })
            source_status[provider.name] = status

        collected = dedupe_items(collected)
        if collected:
            self.repository.upsert_items([item.to_dict() for item in collected])
            freshness_status = "partial" if failures else "fresh"
            groups = group_items(collected)
        else:
            cached_items = [
                IntelItem.from_dict({**item, "is_stale": True})
                for item in self.repository.list_items(
                    scope_type=scope_type,
                    market=market,
                    code=code,
                    include_stale=True,
                    limit=200,
                )
            ]
            freshness_status = "stale" if cached_items else "empty"
            groups = group_items(cached_items)

        if scope_type == "stock":
            bundle = StockIntelBundle(
                market=market,
                code=code,
                groups=groups,
                freshness_status=freshness_status,
                source_status=source_status,
            ).to_dict()
        else:
            bundle = MarketIntelBundle(
                market=market,
                groups=groups,
                freshness_status=freshness_status,
                source_status=source_status,
            ).to_dict()

        self.repository.upsert_bundle({
            "scope_type": scope_type,
            "market": market,
            "code": code,
            "bundle_json": bundle,
            "freshness_status": freshness_status,
            "source_status_json": bundle["source_status"],
        })
        return bundle
```

- [ ] **Step 4: Run service tests and commit**

Run:

```bash
cd stock_screener
PYTHONPATH=. python -m unittest tests.test_market_intel_service -v
```

Expected: PASS.

Commit:

```bash
git add stock_screener/market_intel/service.py stock_screener/tests/test_market_intel_service.py
git commit -m "feat: add market intel service"
```

---

### Task 5: Evidence Pack And Signal Analysis Integration

**Files:**
- Create: `stock_screener/market_intel/evidence.py`
- Modify: `stock_screener/signal_analysis/chain.py`
- Modify: `stock_screener/signal_analysis/service.py`
- Test: `stock_screener/tests/test_market_intel_evidence.py`
- Test: `stock_screener/tests/test_signal_analysis.py`

- [ ] **Step 1: Write evidence-pack tests**

Create `stock_screener/tests/test_market_intel_evidence.py`:

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import unittest
from datetime import datetime, timedelta

from market_intel.evidence import EvidencePackBuilder, intel_items_to_search_documents
from market_intel.models import IntelItem
from signal_analysis.models import SearchDocument


def item(item_type, title, code="600519"):
    now = datetime(2026, 5, 25, 9, 30, 0)
    return IntelItem(
        scope_type="stock" if code else "market",
        market="A",
        code=code,
        source="东方财富",
        provider="eastmoney",
        item_type=item_type,
        title=title,
        summary=f"{title}摘要",
        url=f"https://example.com/{title}",
        fetched_at=now,
        expires_at=now + timedelta(hours=1),
        dedupe_key=f"{item_type}:{title}",
    )


class FakeMarketIntelService:
    def get_stock_intel(self, market, code, force_refresh=False):
        return {
            "market": market,
            "code": code,
            "groups": {
                "announcement": [item("announcement", "公司公告").to_dict()],
                "financial": [item("financial", "财务摘要").to_dict()],
            },
            "source_status": {"eastmoney": {"status": "success", "item_count": 2}},
            "freshness_status": "fresh",
        }

    def get_market_digest(self, market, force_refresh=False):
        return {
            "market": market,
            "code": "",
            "groups": {"market_news": [item("market_news", "市场新闻", code="").to_dict()]},
            "source_status": {"news": {"status": "success", "item_count": 1}},
            "freshness_status": "fresh",
        }


class EvidencePackTests(unittest.TestCase):
    def test_items_convert_to_search_documents(self):
        docs = intel_items_to_search_documents([item("announcement", "公司公告")])

        self.assertEqual(docs[0].title, "公司公告")
        self.assertIn("东方财富", docs[0].content)

    def test_builder_merges_structured_search_and_manual_context(self):
        builder = EvidencePackBuilder(market_intel_service=FakeMarketIntelService())
        pack = builder.build(
            market="A",
            code="600519",
            search_documents=[SearchDocument(title="搜索新闻", url="https://example.com/s", content="搜索摘要")],
            manual_items=[{"title": "手动热点", "summary": "人工录入"}],
            force_refresh=False,
        )

        payload = pack.to_dict()

        self.assertEqual(payload["market"], "A")
        self.assertEqual(payload["code"], "600519")
        self.assertTrue(any(item["title"] == "公司公告" for item in payload["stock_context"]))
        self.assertTrue(any(item["title"] == "市场新闻" for item in payload["market_context"]))
        self.assertTrue(any(item["title"] == "搜索新闻" for item in payload["search_documents"]))
        self.assertTrue(payload["citations"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Add signal-analysis regression test for batch-first behavior**

Append to `stock_screener/tests/test_signal_analysis.py`:

```python
class MarketIntelSignalAnalysisIntegrationTest(unittest.TestCase):
    def test_market_intel_step_does_not_expand_to_per_stock_search(self):
        from datetime import date
        from signal_analysis.chain import (
            BuildSearchQueriesStep,
            MarketIntelEvidenceStep,
            SearchContextStep,
            SignalAnalysisContext,
        )
        from signal_analysis.models import AnalysisSettings, ScreeningSignalRow
        from signal_analysis.search_providers import NullSearchProvider
        from signal_analysis.llm_providers import NullLLMProvider

        class Service:
            def __init__(self):
                self.stock_calls = []
                self.market_calls = []

            def get_stock_intel(self, market, code, force_refresh=False):
                self.stock_calls.append(code)
                return {"groups": {}, "source_status": {}, "freshness_status": "empty", "market": market, "code": code}

            def get_market_digest(self, market, force_refresh=False):
                self.market_calls.append(market)
                return {"groups": {}, "source_status": {}, "freshness_status": "empty", "market": market, "code": ""}

        service = Service()
        rows = [
            ScreeningSignalRow(index=0, code="600519", market="A", market_label="A股", name="贵州茅台", pe_ratio="", market_cap="", sector="", conditions_met="左一战法"),
            ScreeningSignalRow(index=1, code="000001", market="A", market_label="A股", name="平安银行", pe_ratio="", market_cap="", sector="", conditions_met="左一战法"),
        ]
        context = SignalAnalysisContext(
            task_id="task-1",
            market="A",
            csv_path="/tmp/nonexistent.csv",
            check_date=date(2026, 5, 25),
            settings=AnalysisSettings(batch_size=20, timeout_sec=30, search_max_results=5),
            search_provider=NullSearchProvider(),
            llm_provider=NullLLMProvider(),
            rows=rows,
            all_rows=rows,
        )
        context.market_intel_service = service

        BuildSearchQueriesStep().run(context)
        MarketIntelEvidenceStep().run(context)
        SearchContextStep().run(context)

        self.assertEqual(service.market_calls, ["A"])
        self.assertEqual(service.stock_calls, ["600519", "000001"])
        self.assertEqual(context.warnings.count("未配置搜索 provider，跳过联网检索，仅使用 CSV 信号交给模型分析"), 1)
```

- [ ] **Step 3: Run evidence and regression tests and verify they fail**

Run:

```bash
cd stock_screener
PYTHONPATH=. python -m unittest tests.test_market_intel_evidence tests.test_signal_analysis.MarketIntelSignalAnalysisIntegrationTest -v
```

Expected: FAIL with missing `market_intel.evidence` or `MarketIntelEvidenceStep`.

- [ ] **Step 4: Implement evidence-pack builder**

Create `stock_screener/market_intel/evidence.py`:

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

from typing import Any, Dict, Iterable, List

from market_intel.models import EvidencePack, IntelItem
from signal_analysis.models import SearchDocument


def flatten_bundle_items(bundle: Dict[str, Any]) -> List[IntelItem]:
    items: List[IntelItem] = []
    for values in (bundle.get("groups") or {}).values():
        if not isinstance(values, list):
            continue
        for value in values:
            if isinstance(value, dict):
                items.append(IntelItem.from_dict(value))
    return items


def intel_items_to_search_documents(items: Iterable[IntelItem]) -> List[SearchDocument]:
    documents: List[SearchDocument] = []
    for item in items:
        content = "；".join(
            part for part in [
                f"来源: {item.source}",
                f"类型: {item.item_type}",
                item.summary,
            ]
            if part
        )
        documents.append(SearchDocument(
            title=item.title,
            url=item.url,
            content=content,
            query=f"market_intel:{item.provider}:{item.item_type}",
        ))
    return documents


class EvidencePackBuilder:
    def __init__(self, market_intel_service):
        self.market_intel_service = market_intel_service

    def build(
        self,
        *,
        market: str,
        code: str,
        search_documents: List[SearchDocument] | None = None,
        manual_items: List[dict] | None = None,
        force_refresh: bool = False,
    ) -> EvidencePack:
        stock_bundle = self.market_intel_service.get_stock_intel(market, code, force_refresh=force_refresh)
        market_bundle = self.market_intel_service.get_market_digest(market, force_refresh=force_refresh)
        stock_items = flatten_bundle_items(stock_bundle)
        market_items = flatten_bundle_items(market_bundle)
        docs = list(search_documents or [])
        manuals = list(manual_items or [])
        structured = [item.to_dict() for item in [*stock_items, *market_items]]
        citations = _citations_from_items([*stock_items, *market_items])
        citations.extend(_citations_from_search_documents(docs))
        source_status = {
            "stock": stock_bundle.get("source_status") or {},
            "market": market_bundle.get("source_status") or {},
        }
        data_gaps = _data_gaps(stock_bundle, market_bundle, stock_items, market_items)
        return EvidencePack(
            market=market,
            code=code,
            structured_items=structured,
            search_documents=[doc.to_prompt_dict() for doc in docs],
            manual_items=manuals,
            market_context=[item.to_dict() for item in market_items],
            stock_context=[item.to_dict() for item in stock_items],
            source_status=source_status,
            data_gaps=data_gaps,
            citations=_dedupe_citations(citations),
        )


def _data_gaps(stock_bundle: dict, market_bundle: dict, stock_items: List[IntelItem], market_items: List[IntelItem]) -> List[str]:
    gaps = []
    if not stock_items:
        gaps.append("个股结构化情报为空")
    if not market_items:
        gaps.append("市场层面情报为空")
    for label, bundle in (("个股", stock_bundle), ("市场", market_bundle)):
        if bundle.get("freshness_status") in {"stale", "empty"}:
            gaps.append(f"{label}情报状态为 {bundle.get('freshness_status')}")
    return _dedupe_strings(gaps)


def _citations_from_items(items: Iterable[IntelItem]) -> List[dict]:
    citations = []
    for item in items:
        if not item.url and not item.title:
            continue
        citations.append({
            "title": item.title,
            "url": item.url,
            "source": item.source,
            "provider": item.provider,
            "item_type": item.item_type,
            "published_at": item.published_at.isoformat(timespec="seconds") if item.published_at else "",
        })
    return citations


def _citations_from_search_documents(documents: Iterable[SearchDocument]) -> List[dict]:
    return [
        {"title": doc.title, "url": doc.url, "source": "search", "provider": "search", "item_type": "search_document", "published_at": ""}
        for doc in documents
        if doc.title or doc.url
    ]


def _dedupe_citations(citations: List[dict]) -> List[dict]:
    seen = set()
    result = []
    for item in citations:
        key = (item.get("title") or "", item.get("url") or "")
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _dedupe_strings(values: List[str]) -> List[str]:
    seen = set()
    result = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result
```

- [ ] **Step 5: Add optional signal-analysis context fields and step**

Modify `SignalAnalysisContext` in `stock_screener/signal_analysis/chain.py`:

```python
    market_intel_service: Any = None
    evidence_packs: Dict[str, dict] = field(default_factory=dict)
```

Add import:

```python
from market_intel.evidence import EvidencePackBuilder, intel_items_to_search_documents, flatten_bundle_items
from market_intel.models import IntelItem
```

Add this step after `BuildSearchQueriesStep`:

```python
class MarketIntelEvidenceStep(AnalysisStep):
    name = "MarketIntelEvidenceStep"

    def run(self, context: SignalAnalysisContext) -> None:
        service = getattr(context, "market_intel_service", None)
        if service is None:
            return
        if not context.rows and context.results_by_code:
            return
        try:
            market_bundle = service.get_market_digest(context.market, force_refresh=context.force_refresh)
        except Exception as exc:
            context.warnings.append(f"市场情报获取失败: {type(exc).__name__}: {exc}")
            market_bundle = {"groups": {}, "source_status": {}, "freshness_status": "empty", "market": context.market, "code": ""}
        market_items = flatten_bundle_items(market_bundle)
        context.market_documents = dedupe_documents([
            *context.market_documents,
            *intel_items_to_search_documents(market_items),
        ])
        builder = EvidencePackBuilder(service)
        for row in context.rows:
            try:
                pack = builder.build(
                    market=context.market,
                    code=row.code,
                    search_documents=context.company_documents.get(row.code, []),
                    manual_items=[],
                    force_refresh=context.force_refresh,
                )
            except Exception as exc:
                context.warnings.append(f"{row.code} 市场情报证据包构建失败: {type(exc).__name__}: {exc}")
                continue
            context.evidence_packs[row.code] = pack.to_dict()
            stock_docs = intel_items_to_search_documents([
                IntelItem.from_dict(item)
                for item in pack.to_dict().get("stock_context", [])
            ])
            context.company_documents[row.code] = dedupe_documents([
                *(context.company_documents.get(row.code) or []),
                *stock_docs,
            ])
```

Insert the step in `SignalAnalysisChain.__init__`:

```python
            BuildSearchQueriesStep(),
            MarketIntelEvidenceStep(),
            SearchContextStep(),
```

- [ ] **Step 6: Wire service construction from environment**

Modify `stock_screener/signal_analysis/service.py` imports:

```python
from market_intel.providers.factory import build_market_intel_providers
from market_intel.repository import MySqlMarketIntelRepository
from market_intel.service import MarketIntelService
```

Add helper:

```python
def build_market_intel_service(mysql_config: MySqlConfig):
    if not env_flag("SIGNAL_ENABLE_MARKET_INTEL", False):
        return None
    db = MarketDatabase(mysql_config)
    db.init_market_intel_schema()
    return MarketIntelService(
        repository=MySqlMarketIntelRepository(db),
        providers=build_market_intel_providers(),
    )
```

Add to `SignalAnalysisContext(...)` construction:

```python
        market_intel_service=build_market_intel_service(mysql_config),
```

The connection created by `build_market_intel_service` remains owned by the service for the analysis run. Add this `close()` method to `MySqlMarketIntelRepository`:

```python
    def close(self) -> None:
        close = getattr(self.db, "close", None)
        if callable(close):
            close()
```

Then call it at the end of `run_signal_analysis_for_market`:

```python
    result = SignalAnalysisChain().run(context)
    closer = getattr(getattr(context, "market_intel_service", None), "repository", None)
    close_fn = getattr(closer, "close", None)
    if callable(close_fn):
        close_fn()
    return result
```

- [ ] **Step 7: Run tests and commit**

Run:

```bash
cd stock_screener
PYTHONPATH=. python -m unittest tests.test_market_intel_evidence tests.test_signal_analysis.MarketIntelSignalAnalysisIntegrationTest -v
```

Expected: PASS.

Commit:

```bash
git add stock_screener/market_intel/evidence.py stock_screener/signal_analysis/chain.py stock_screener/signal_analysis/service.py stock_screener/tests/test_market_intel_evidence.py stock_screener/tests/test_signal_analysis.py
git commit -m "feat: connect market intel evidence to signal analysis"
```

---

### Task 6: Market Intel API Router

**Files:**
- Create: `stock_screener/web/market_intel.py`
- Modify: `stock_screener/web/main.py`
- Test: `stock_screener/tests/test_market_intel_api.py`

- [ ] **Step 1: Write API tests**

Create `stock_screener/tests/test_market_intel_api.py`:

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import unittest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from market_intel.repository import InMemoryMarketIntelRepository
from market_intel.service import MarketIntelService
from web.auth import CurrentUser, require_user
from web.market_intel import get_market_intel_service, router


class EmptyProvider:
    name = "empty"
    is_available = True

    def fetch_stock(self, market, code):
        return []

    def fetch_market(self, market):
        return []


class MarketIntelApiTests(unittest.TestCase):
    def make_client(self):
        app = FastAPI()
        app.include_router(router)
        service = MarketIntelService(repository=InMemoryMarketIntelRepository(), providers=[EmptyProvider()])
        app.dependency_overrides[require_user] = lambda: CurrentUser(id=1, username="tester", role="admin")
        app.dependency_overrides[get_market_intel_service] = lambda: service
        return TestClient(app)

    def test_stock_intel_endpoint_returns_bundle(self):
        client = self.make_client()

        response = client.get("/api/market-intel/stocks/A/600519?refresh=true")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["market"], "A")
        self.assertEqual(payload["code"], "600519")
        self.assertIn("source_status", payload)

    def test_provider_runs_endpoint_returns_list(self):
        client = self.make_client()

        response = client.get("/api/market-intel/provider-runs")

        self.assertEqual(response.status_code, 200)
        self.assertIn("runs", response.json())

    def test_evidence_pack_preview_does_not_call_llm(self):
        client = self.make_client()

        response = client.post(
            "/api/market-intel/evidence-pack/preview",
            json={"market": "A", "code": "600519", "include_search": False, "force_refresh": True},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["market"], "A")
        self.assertEqual(payload["code"], "600519")
        self.assertIn("data_gaps", payload)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run API tests and verify they fail**

Run:

```bash
cd stock_screener
PYTHONPATH=. python -m unittest tests.test_market_intel_api -v
```

Expected: FAIL with missing `web.market_intel`.

- [ ] **Step 3: Implement router**

Create `stock_screener/web/market_intel.py`:

```python
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from market import normalize_market
from market_intel.evidence import EvidencePackBuilder
from market_intel.providers.factory import build_market_intel_providers
from market_intel.repository import MySqlMarketIntelRepository
from market_intel.service import MarketIntelService

from .auth import CurrentUser, get_db, require_user
from .business import BusinessError
from .single_stock import normalize_stock_code


router = APIRouter(prefix="/api/market-intel", tags=["market-intel"])


class RefreshRequest(BaseModel):
    force: bool = False
    include_search: bool = False
    provider_keys: List[str] = Field(default_factory=list)


class EvidencePackPreviewRequest(BaseModel):
    market: str
    code: str
    timeframe: str = "1d"
    analysis_profile: str = "default"
    include_search: bool = False
    force_refresh: bool = False


def get_market_intel_service(db=Depends(get_db)) -> MarketIntelService:
    db.init_market_intel_schema()
    return MarketIntelService(
        repository=MySqlMarketIntelRepository(db),
        providers=build_market_intel_providers(),
    )


@router.get("/stocks/{market}/{code}")
def get_stock_intel(
    market: str,
    code: str,
    refresh: bool = Query(default=False),
    include_search: bool = Query(default=False),
    max_items_per_group: int = Query(default=10, ge=1, le=50),
    user: CurrentUser = Depends(require_user),
    service: MarketIntelService = Depends(get_market_intel_service),
) -> Dict[str, Any]:
    try:
        market_key = normalize_market(market)
        normalized_code = normalize_stock_code(market_key, code)
        bundle = service.get_stock_intel(market_key, normalized_code, force_refresh=refresh)
        return _trim_groups(bundle, max_items_per_group)
    except ValueError as exc:
        raise BusinessError("MARKET_INTEL_INVALID_REQUEST", str(exc)) from exc
    except Exception as exc:
        raise BusinessError("MARKET_INTEL_STOCK_FAILED", f"市场情报获取失败: {exc}") from exc


@router.post("/stocks/{market}/{code}/refresh")
def refresh_stock_intel(
    market: str,
    code: str,
    payload: RefreshRequest,
    user: CurrentUser = Depends(require_user),
    service: MarketIntelService = Depends(get_market_intel_service),
) -> Dict[str, Any]:
    try:
        market_key = normalize_market(market)
        normalized_code = normalize_stock_code(market_key, code)
        return service.get_stock_intel(market_key, normalized_code, force_refresh=True)
    except ValueError as exc:
        raise BusinessError("MARKET_INTEL_INVALID_REQUEST", str(exc)) from exc
    except Exception as exc:
        raise BusinessError("MARKET_INTEL_REFRESH_FAILED", f"刷新市场情报失败: {exc}") from exc


@router.get("/markets/{market}/digest")
def get_market_digest(
    market: str,
    refresh: bool = Query(default=False),
    include_search: bool = Query(default=False),
    user: CurrentUser = Depends(require_user),
    service: MarketIntelService = Depends(get_market_intel_service),
) -> Dict[str, Any]:
    try:
        market_key = normalize_market(market)
        return service.get_market_digest(market_key, force_refresh=refresh)
    except ValueError as exc:
        raise BusinessError("MARKET_INTEL_INVALID_REQUEST", str(exc)) from exc
    except Exception as exc:
        raise BusinessError("MARKET_INTEL_MARKET_FAILED", f"市场摘要获取失败: {exc}") from exc


@router.get("/provider-runs")
def list_provider_runs(
    provider: Optional[str] = None,
    market: Optional[str] = None,
    code: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = Query(default=50, ge=1, le=200),
    user: CurrentUser = Depends(require_user),
    service: MarketIntelService = Depends(get_market_intel_service),
) -> Dict[str, Any]:
    return {"runs": service.list_provider_runs(provider=provider, market=market, code=code, status=status, limit=limit)}


@router.post("/evidence-pack/preview")
def preview_evidence_pack(
    payload: EvidencePackPreviewRequest,
    user: CurrentUser = Depends(require_user),
    service: MarketIntelService = Depends(get_market_intel_service),
) -> Dict[str, Any]:
    try:
        market_key = normalize_market(payload.market)
        normalized_code = normalize_stock_code(market_key, payload.code)
        pack = EvidencePackBuilder(service).build(
            market=market_key,
            code=normalized_code,
            search_documents=[],
            manual_items=[],
            force_refresh=payload.force_refresh,
        )
        return pack.to_dict()
    except ValueError as exc:
        raise BusinessError("MARKET_INTEL_INVALID_REQUEST", str(exc)) from exc
    except Exception as exc:
        raise BusinessError("MARKET_INTEL_EVIDENCE_FAILED", f"证据包预览失败: {exc}") from exc


def _trim_groups(bundle: Dict[str, Any], max_items_per_group: int) -> Dict[str, Any]:
    output = dict(bundle)
    groups = {}
    for key, values in (bundle.get("groups") or {}).items():
        groups[key] = list(values or [])[:max_items_per_group]
    output["groups"] = groups
    return output
```

- [ ] **Step 4: Include router and initialize schema**

Modify `stock_screener/web/main.py`.

Add import:

```python
from .market_intel import router as market_intel_router
```

Add router:

```python
app.include_router(market_intel_router)
```

Add schema init inside `startup()` after `db.init_web_schema()`:

```python
        db.init_market_intel_schema()
```

- [ ] **Step 5: Run API tests and commit**

Run:

```bash
cd stock_screener
PYTHONPATH=. python -m unittest tests.test_market_intel_api -v
```

Expected: PASS.

Commit:

```bash
git add stock_screener/web/market_intel.py stock_screener/web/main.py stock_screener/tests/test_market_intel_api.py
git commit -m "feat: expose market intel api"
```

---

### Task 7: Simple Frontend Page

**Files:**
- Create: `stock_screener/web_frontend/src/features/marketIntel/types.ts`
- Create: `stock_screener/web_frontend/src/features/marketIntel/api.ts`
- Create: `stock_screener/web_frontend/src/features/marketIntel/MarketIntelPage.tsx`
- Modify: `stock_screener/web_frontend/src/main.tsx`
- Modify: `stock_screener/web_frontend/src/styles.css`
- Test: `stock_screener/tests/test_market_intel_frontend.py`

- [ ] **Step 1: Write frontend static contract test**

Create `stock_screener/tests/test_market_intel_frontend.py`:

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
MAIN_TSX = ROOT / "web_frontend" / "src" / "main.tsx"
PAGE_TSX = ROOT / "web_frontend" / "src" / "features" / "marketIntel" / "MarketIntelPage.tsx"
API_TS = ROOT / "web_frontend" / "src" / "features" / "marketIntel" / "api.ts"


class MarketIntelFrontendTest(unittest.TestCase):
    def test_nav_contains_market_intel_page(self):
        source = MAIN_TSX.read_text(encoding="utf-8")

        self.assertIn("市场情报", source)
        self.assertIn("page === 'marketIntel'", source)
        self.assertIn("setPage('marketIntel')", source)

    def test_page_uses_market_intel_api_and_source_status(self):
        source = PAGE_TSX.read_text(encoding="utf-8")
        api_source = API_TS.read_text(encoding="utf-8")

        self.assertIn("来源状态", source)
        self.assertIn("公告", source)
        self.assertIn("研报", source)
        self.assertIn("资金面", source)
        self.assertIn("/api/market-intel/stocks", api_source)
        self.assertIn("/api/market-intel/markets", api_source)
        self.assertIn("/api/market-intel/evidence-pack/preview", api_source)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run frontend contract test and verify it fails**

Run:

```bash
cd stock_screener
PYTHONPATH=. python -m unittest tests.test_market_intel_frontend -v
```

Expected: FAIL with missing frontend files.

- [ ] **Step 3: Create frontend types and API helpers**

Create `stock_screener/web_frontend/src/features/marketIntel/types.ts`:

```typescript
export type MarketIntelItem = {
  scope_type: string
  market: string
  code?: string
  source: string
  provider: string
  item_type: string
  title: string
  summary?: string
  url?: string
  published_at?: string | null
  fetched_at?: string | null
  expires_at?: string | null
  is_stale?: boolean
}

export type SourceStatus = {
  provider: string
  status: string
  item_count?: number
  error_message?: string
  fetched_at?: string | null
  stale?: boolean
}

export type MarketIntelBundle = {
  scope_type: string
  market: string
  code?: string
  groups: Record<string, MarketIntelItem[]>
  freshness_status: string
  source_status: Record<string, SourceStatus>
}

export type ProviderRun = {
  provider: string
  scope_type: string
  market: string
  code?: string
  status: string
  error_message?: string
  duration_ms?: number
  item_count?: number
  started_at?: string
  finished_at?: string
}

export type EvidencePack = {
  market: string
  code: string
  structured_items: MarketIntelItem[]
  search_documents: Record<string, unknown>[]
  manual_items: Record<string, unknown>[]
  market_context: MarketIntelItem[]
  stock_context: MarketIntelItem[]
  source_status: Record<string, unknown>
  data_gaps: string[]
  citations: Record<string, unknown>[]
}
```

Create `stock_screener/web_frontend/src/features/marketIntel/api.ts`:

```typescript
import { api } from '../../api'
import type { EvidencePack, MarketIntelBundle, ProviderRun } from './types'

export function getStockIntel(market: string, code: string, refresh = false) {
  return api<MarketIntelBundle>(`/api/market-intel/stocks/${encodeURIComponent(market)}/${encodeURIComponent(code)}?refresh=${refresh ? 'true' : 'false'}`)
}

export function getMarketDigest(market: string, refresh = false) {
  return api<MarketIntelBundle>(`/api/market-intel/markets/${encodeURIComponent(market)}/digest?refresh=${refresh ? 'true' : 'false'}`)
}

export function getProviderRuns() {
  return api<{ runs: ProviderRun[] }>('/api/market-intel/provider-runs?limit=20')
}

export function previewEvidencePack(market: string, code: string, forceRefresh = false) {
  return api<EvidencePack>('/api/market-intel/evidence-pack/preview', {
    method: 'POST',
    body: JSON.stringify({ market, code, include_search: false, force_refresh: forceRefresh })
  })
}
```

- [ ] **Step 4: Create simple page**

Create `stock_screener/web_frontend/src/features/marketIntel/MarketIntelPage.tsx`:

```typescript
import React, { useState } from 'react'
import { getMarketDigest, getProviderRuns, getStockIntel, previewEvidencePack } from './api'
import type { EvidencePack, MarketIntelBundle, MarketIntelItem, ProviderRun } from './types'

const MARKETS = ['A', 'HK', 'US']
const GROUP_LABELS: Record<string, string> = {
  financial: '财务摘要',
  announcement: '公告',
  research_report: '研报',
  money_flow: '资金面',
  long_tiger: '龙虎榜',
  market_news: '市场新闻',
  index_snapshot: '指数快照',
  hot_sector: '热点板块',
  search_document: '搜索补充'
}

export function MarketIntelPage() {
  const [market, setMarket] = useState('A')
  const [code, setCode] = useState('600519')
  const [stockBundle, setStockBundle] = useState<MarketIntelBundle | null>(null)
  const [marketBundle, setMarketBundle] = useState<MarketIntelBundle | null>(null)
  const [runs, setRuns] = useState<ProviderRun[]>([])
  const [pack, setPack] = useState<EvidencePack | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  async function load(refresh: boolean) {
    setLoading(true)
    setError('')
    try {
      const [stock, digest, runRows, evidence] = await Promise.all([
        getStockIntel(market, code, refresh),
        getMarketDigest(market, refresh),
        getProviderRuns(),
        previewEvidencePack(market, code, refresh)
      ])
      setStockBundle(stock)
      setMarketBundle(digest)
      setRuns(runRows.runs || [])
      setPack(evidence)
    } catch (err) {
      setError(err instanceof Error ? err.message : '市场情报加载失败')
    } finally {
      setLoading(false)
    }
  }

  return (
    <section>
      <div className="page-header">
        <div>
          <h1>市场情报</h1>
          <p>查看结构化行情信息、来源状态和下游证据包。</p>
        </div>
      </div>
      <form className="form-grid market-intel-form" onSubmit={(event) => { event.preventDefault(); load(false) }}>
        <label>
          市场
          <select value={market} onChange={event => setMarket(event.target.value)}>
            {MARKETS.map(item => <option key={item} value={item}>{item}</option>)}
          </select>
        </label>
        <label>
          股票代码
          <input value={code} onChange={event => setCode(event.target.value)} />
        </label>
        <button className="primary" disabled={loading}>{loading ? '加载中...' : '查询'}</button>
        <button type="button" disabled={loading} onClick={() => load(true)}>刷新缓存</button>
      </form>
      {error && <div className="error">{error}</div>}
      <div className="market-intel-grid">
        <Panel title="来源状态">
          <SourceStatus bundle={stockBundle} />
          <SourceStatus bundle={marketBundle} />
        </Panel>
        <Panel title="证据包预览">
          <dl className="info-list">
            <div><dt>结构化条目</dt><dd>{pack?.structured_items?.length || 0}</dd></div>
            <div><dt>市场上下文</dt><dd>{pack?.market_context?.length || 0}</dd></div>
            <div><dt>个股上下文</dt><dd>{pack?.stock_context?.length || 0}</dd></div>
            <div><dt>数据缺口</dt><dd>{(pack?.data_gaps || []).join('；') || '无'}</dd></div>
          </dl>
        </Panel>
      </div>
      <BundleSections title="个股情报" bundle={stockBundle} />
      <BundleSections title="市场摘要" bundle={marketBundle} />
      <Panel title="最近 Provider 执行">
        <table className="data-table">
          <thead><tr><th>Provider</th><th>市场</th><th>代码</th><th>状态</th><th>数量</th><th>耗时</th></tr></thead>
          <tbody>
            {runs.map((run, index) => (
              <tr key={`${run.provider}-${index}`}>
                <td>{run.provider}</td>
                <td>{run.market}</td>
                <td>{run.code || '-'}</td>
                <td>{run.status}</td>
                <td>{run.item_count || 0}</td>
                <td>{run.duration_ms || 0}ms</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Panel>
    </section>
  )
}

function Panel({ title, children }: { title: string; children: React.ReactNode }) {
  return <section className="panel"><h2>{title}</h2>{children}</section>
}

function SourceStatus({ bundle }: { bundle: MarketIntelBundle | null }) {
  if (!bundle) return <p className="muted">暂无数据</p>
  const statuses = Object.values(bundle.source_status || {})
  return (
    <div className="source-status-list">
      <strong>{bundle.scope_type === 'stock' ? '个股' : '市场'}：{bundle.freshness_status}</strong>
      {statuses.map(status => (
        <span key={status.provider} className={`status-pill ${status.status}`}>{status.provider} {status.status} {status.item_count || 0}</span>
      ))}
    </div>
  )
}

function BundleSections({ title, bundle }: { title: string; bundle: MarketIntelBundle | null }) {
  if (!bundle) return <Panel title={title}><p className="muted">暂无数据</p></Panel>
  return (
    <Panel title={title}>
      {Object.entries(bundle.groups || {}).map(([group, items]) => (
        <section className="market-intel-group" key={group}>
          <h3>{GROUP_LABELS[group] || group}</h3>
          <table className="data-table">
            <thead><tr><th>标题</th><th>来源</th><th>发布时间</th><th>状态</th></tr></thead>
            <tbody>
              {(items || []).map((item, index) => <IntelRow key={`${item.provider}-${item.dedupe_key || index}`} item={item} />)}
            </tbody>
          </table>
        </section>
      ))}
    </Panel>
  )
}

function IntelRow({ item }: { item: MarketIntelItem }) {
  return (
    <tr>
      <td>{item.url ? <a href={item.url} target="_blank" rel="noreferrer">{item.title}</a> : item.title}</td>
      <td>{item.source}</td>
      <td>{item.published_at || item.fetched_at || '-'}</td>
      <td>{item.is_stale ? '旧缓存' : '最新'}</td>
    </tr>
  )
}
```

- [ ] **Step 5: Wire page into main app**

Modify `stock_screener/web_frontend/src/main.tsx`.

Add import:

```typescript
import { MarketIntelPage } from './features/marketIntel/MarketIntelPage'
```

Add nav button after `代码筛选`:

```tsx
          <button className={page === 'marketIntel' ? 'active' : ''} onClick={() => setPage('marketIntel')}>市场情报</button>
```

Add route:

```tsx
        {page === 'marketIntel' && <MarketIntelPage />}
```

- [ ] **Step 6: Add minimal styles**

Append to `stock_screener/web_frontend/src/styles.css`:

```css
.market-intel-form {
  margin-bottom: 16px;
}

.market-intel-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  gap: 16px;
}

.source-status-list {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
}

.status-pill {
  border: 1px solid #d6dbe6;
  border-radius: 8px;
  padding: 4px 8px;
  font-size: 12px;
  background: #f8fafc;
}

.status-pill.failed {
  border-color: #f3b4b4;
  background: #fff5f5;
  color: #9f1d1d;
}

.market-intel-group {
  margin-top: 16px;
}

.market-intel-group h3 {
  margin: 0 0 8px;
  font-size: 15px;
}
```

- [ ] **Step 7: Run frontend tests and build, then commit**

Run:

```bash
cd stock_screener
PYTHONPATH=. python -m unittest tests.test_market_intel_frontend -v
npm --prefix web_frontend run build
```

Expected: unittest PASS and Vite build exits 0.

Commit:

```bash
git add stock_screener/web_frontend/src/features/marketIntel stock_screener/web_frontend/src/main.tsx stock_screener/web_frontend/src/styles.css stock_screener/tests/test_market_intel_frontend.py
git commit -m "feat: add market intel page"
```

---

### Task 8: Chinese Report Templates And Chart-Ready Data

**Files:**
- Create: `stock_screener/market_intel/reporting.py`
- Modify: `stock_screener/signal_analysis/chain.py`
- Test: `stock_screener/tests/test_market_intel_reporting.py`
- Test: `stock_screener/tests/test_signal_analysis.py`

- [ ] **Step 1: Write reporting tests**

Create `stock_screener/tests/test_market_intel_reporting.py`:

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import unittest

from market_intel.reporting import render_multi_stock_report, render_single_stock_report


class MarketIntelReportingTests(unittest.TestCase):
    def test_single_stock_report_is_chinese_and_data_first(self):
        pack = {
            "market": "A",
            "code": "600519",
            "stock_context": [{"item_type": "financial", "title": "财务摘要", "summary": "营收同比 12%"}],
            "market_context": [{"item_type": "market_news", "title": "市场新闻", "summary": "白酒板块活跃"}],
            "data_gaps": [],
            "citations": [{"title": "财务摘要", "url": "https://example.com"}],
        }
        result = {
            "code": "600519",
            "name": "贵州茅台",
            "reliability_score": 82,
            "confidence_score": 76,
            "signal_bias": "bullish",
            "summary": "规则信号和资金面较一致",
            "positive_factors": ["规则信号较强"],
            "risk_factors": ["估值波动"],
            "news_impact": "利好",
        }

        markdown = render_single_stock_report(pack, result)

        self.assertIn("# 贵州茅台（600519）市场情报与AI复核报告", markdown)
        self.assertIn("## 2. 核心评分", markdown)
        self.assertIn("饼图：综合评分来源占比", markdown)
        self.assertIn("柱状图：近 N 日资金流入/流出", markdown)
        self.assertIn("本报告仅用于辅助观察和复盘，不构成投资建议", markdown)

    def test_multi_stock_report_uses_summary_and_distribution(self):
        packs = [
            {"market": "A", "code": "600519", "stock_context": [], "market_context": [], "data_gaps": [], "citations": []},
            {"market": "A", "code": "000001", "stock_context": [], "market_context": [], "data_gaps": ["个股结构化情报为空"], "citations": []},
        ]
        results = [
            {"code": "600519", "name": "贵州茅台", "reliability_score": 82, "confidence_score": 70, "signal_bias": "bullish", "summary": "偏强"},
            {"code": "000001", "name": "平安银行", "reliability_score": 45, "confidence_score": 50, "signal_bias": "neutral", "summary": "观察"},
        ]

        markdown = render_multi_stock_report(packs, results)

        self.assertIn("# 多股票市场情报与AI复核报告", markdown)
        self.assertIn("饼图：股票评级分布", markdown)
        self.assertIn("柱状图：综合评分 Top 10", markdown)
        self.assertIn("## 6. 重点关注股票", markdown)
        self.assertIn("## 10. 数据缺失与来源说明", markdown)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run reporting tests and verify they fail**

Run:

```bash
cd stock_screener
PYTHONPATH=. python -m unittest tests.test_market_intel_reporting -v
```

Expected: FAIL with missing reporting module.

- [ ] **Step 3: Implement reporting module**

Create `stock_screener/market_intel/reporting.py`:

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

from datetime import date
from typing import Any, Dict, List


def render_single_stock_report(pack: Dict[str, Any], result: Dict[str, Any], report_date: date | None = None) -> str:
    code = str(result.get("code") or pack.get("code") or "")
    name = str(result.get("name") or code)
    score = _score(result.get("reliability_score"))
    confidence = _score(result.get("confidence_score"))
    conclusion = _conclusion(score, str(result.get("signal_bias") or "unknown"))
    today = report_date or date.today()
    data_gaps = pack.get("data_gaps") or []
    citations = pack.get("citations") or []
    lines = [
        f"# {name}（{code}）市场情报与AI复核报告",
        "",
        f"报告日期：{today.isoformat()}",
        f"市场：{pack.get('market') or '-'}",
        f"结论类型：{conclusion}",
        "",
        "## 1. 一句话结论",
        "",
        str(result.get("summary") or "当前结构化情报不足，需要继续观察。"),
        "",
        "## 2. 核心评分",
        "",
        "| 指标 | 分数 | 说明 |",
        "|---|---:|---|",
        f"| 技术信号强度 | {score} | 来自规则链、K线、成交量、趋势指标 |",
        "| 资金面评分 | - | 来自主力资金、北向/南向、龙虎榜等；缺失时显示数据缺口 |",
        "| 基本面评分 | - | 来自财务、盈利、估值、机构预测 |",
        f"| 消息面评分 | {confidence} | 来自公告、研报、新闻、热点 |",
        f"| 综合可靠性 | {score} | 综合以上证据后的辅助判断 |",
        "",
        "## 3. 分数构成图",
        "",
        "饼图：综合评分来源占比",
        "",
        "| 来源 | 占比 |",
        "|---|---:|",
        "| 技术信号 | 40% |",
        "| 资金面 | 20% |",
        "| 基本面 | 20% |",
        "| 消息面 | 20% |",
        "",
        "## 4. 关键数据表",
        "",
        "| 类别 | 最新数据 | 变化 | 解读 |",
        "|---|---:|---:|---|",
    ]
    for item in pack.get("stock_context") or []:
        lines.append(f"| {_table(item.get('item_type'))} | {_table(item.get('title'))} | - | {_table(item.get('summary'))} |")
    if not pack.get("stock_context"):
        lines.append("| 结构化情报 | - | - | 个股结构化数据不足 |")
    lines.extend([
        "",
        "## 5. 资金面分析",
        "",
        "柱状图：近 N 日资金流入/流出",
        "",
        "| 日期 | 主力净流入 | 散户净流入 | 股价涨跌幅 |",
        "|---|---:|---:|---:|",
        "| 数据不足 | - | - | - |",
        "",
        "## 6. 公告 / 研报 / 新闻摘要",
        "",
        "| 类型 | 标题 | 时间 | 影响判断 | 来源 |",
        "|---|---|---|---|---|",
    ])
    for item in [*(pack.get("stock_context") or []), *(pack.get("market_context") or [])][:10]:
        lines.append(
            f"| {_table(item.get('item_type'))} | {_table(item.get('title'))} | {_table(item.get('published_at') or item.get('fetched_at'))} | {_table(result.get('news_impact') or '中性')} | {_table(item.get('source'))} |"
        )
    lines.extend([
        "",
        "## 7. 机会与风险",
        "",
        "| 方向 | 内容 | 证据来源 |",
        "|---|---|---|",
    ])
    for factor in result.get("positive_factors") or []:
        lines.append(f"| 机会 | {_table(factor)} | AI复核 / 规则链 |")
    for factor in result.get("risk_factors") or []:
        lines.append(f"| 风险 | {_table(factor)} | AI复核 / 市场情报 |")
    lines.extend([
        "",
        "## 8. AI 辅助复核",
        "",
        "| 项目 | 判断 |",
        "|---|---|",
        f"| 辅助方向 | {_direction(result.get('signal_bias'))} |",
        f"| 模型置信度 | {confidence} |",
        f"| 新闻影响 | {_table(result.get('news_impact') or '信息不足')} |",
        f"| 数据完整度 | {'部分缺失' if data_gaps else '完整'} |",
        "",
        "## 9. 观察建议",
        "",
        "| 操作 | 条件 |",
        "|---|---|",
        "| 继续观察 | 规则信号保持有效，且资金或消息面继续支持 |",
        "| 谨慎 | 出现放量下跌、公告风险、研报下调或数据缺口扩大 |",
        "| 移出观察池 | 规则信号失效，或风险事件确认 |",
        "",
        "## 10. 数据来源与免责声明",
        "",
        "| 来源 | 链接 |",
        "|---|---|",
    ])
    for citation in citations[:20]:
        lines.append(f"| {_table(citation.get('title') or citation.get('source'))} | {_table(citation.get('url'))} |")
    if data_gaps:
        lines.extend(["", "数据缺失：", ""])
        for gap in data_gaps:
            lines.append(f"- {gap}")
    lines.extend(["", "本报告仅用于辅助观察和复盘，不构成投资建议。"])
    return "\n".join(lines)


def render_multi_stock_report(packs: List[Dict[str, Any]], results: List[Dict[str, Any]], report_date: date | None = None) -> str:
    today = report_date or date.today()
    rows = sorted(results, key=lambda item: _score(item.get("reliability_score")), reverse=True)
    rating_counts = _rating_counts(rows)
    lines = [
        "# 多股票市场情报与AI复核报告",
        "",
        f"报告日期：{today.isoformat()}",
        f"股票数量：{len(rows)}",
        "分析来源：规则链筛选 / 自定义股票池 / 单次批量分析",
        "",
        "## 1. 总体结论",
        "",
        f"本次共分析 {len(rows)} 只股票，其中：",
        "",
        "| 分类 | 数量 | 占比 | 说明 |",
        "|---|---:|---:|---|",
    ]
    for rating in ["重点关注", "谨慎观察", "暂不关注"]:
        count = rating_counts.get(rating, 0)
        pct = round(count * 100 / len(rows), 2) if rows else 0
        lines.append(f"| {rating} | {count} | {pct}% | {_rating_reason(rating)} |")
    lines.extend([
        "",
        "## 2. 结论分布图",
        "",
        "饼图：股票评级分布",
        "",
        "| 评级 | 数量 | 占比 |",
        "|---|---:|---:|",
    ])
    for rating in ["重点关注", "谨慎观察", "暂不关注"]:
        count = rating_counts.get(rating, 0)
        pct = round(count * 100 / len(rows), 2) if rows else 0
        lines.append(f"| {rating} | {count} | {pct}% |")
    lines.extend([
        "",
        "饼图：行业 / 板块分布",
        "",
        "| 行业 / 板块 | 数量 | 占比 |",
        "|---|---:|---:|",
        "| 数据不足 | 0 | 0% |",
        "",
        "## 3. 综合排名",
        "",
        "柱状图：综合评分 Top 10",
        "",
        "| 排名 | 股票 | 代码 | 综合评分 | 技术 | 资金 | 基本面 | 消息面 | 结论 |",
        "|---:|---|---|---:|---:|---:|---:|---:|---|",
    ])
    for index, row in enumerate(rows[:10], start=1):
        score = _score(row.get("reliability_score"))
        rating = _conclusion(score, str(row.get("signal_bias") or "unknown"))
        lines.append(f"| {index} | {_table(row.get('name'))} | {_table(row.get('code'))} | {score} | {score} | - | - | {_score(row.get('confidence_score'))} | {rating} |")
    lines.extend([
        "",
        "## 4. 资金面对比",
        "",
        "柱状图：Top 10 主力净流入 / 净流出",
        "",
        "| 股票 | 主力净流入 | 股价涨跌幅 | 解读 |",
        "|---|---:|---:|---|",
        "| 数据不足 | - | - | 暂无结构化资金序列 |",
        "",
        "## 5. 消息面与事件分布",
        "",
        "柱状图：利好 / 利空 / 中性事件数量",
        "",
        "| 股票 | 公告 | 研报 | 新闻 | 影响判断 | 关键事件 |",
        "|---|---:|---:|---:|---|---|",
    ])
    pack_by_code = {pack.get("code"): pack for pack in packs}
    for row in rows:
        pack = pack_by_code.get(row.get("code")) or {}
        stock_items = pack.get("stock_context") or []
        event_count = len(stock_items)
        lines.append(f"| {_table(row.get('name'))} | {event_count} | 0 | {len(pack.get('market_context') or [])} | {_table(row.get('news_impact') or '信息不足')} | {_table(row.get('summary'))} |")
    lines.extend([
        "",
        "## 6. 重点关注股票",
        "",
    ])
    for row in [item for item in rows if _conclusion(_score(item.get("reliability_score")), str(item.get("signal_bias") or "unknown")) == "重点关注"][:5]:
        lines.extend([
            f"### {row.get('name') or row.get('code')}（{row.get('code')}）",
            "",
            "| 项目 | 结果 |",
            "|---|---|",
            f"| 综合评分 | {_score(row.get('reliability_score'))} |",
            f"| 主要机会 | {_table(row.get('summary'))} |",
            "| 主要风险 | 需要结合数据缺口和公告风险继续观察 |",
            "| 观察条件 | 规则信号和资金面继续保持一致 |",
            "",
        ])
    lines.extend([
        "## 7. 谨慎观察股票",
        "",
        "| 股票 | 代码 | 亮点 | 风险 | 建议观察条件 |",
        "|---|---|---|---|---|",
    ])
    for row in [item for item in rows if _conclusion(_score(item.get("reliability_score")), str(item.get("signal_bias") or "unknown")) == "谨慎观察"]:
        lines.append(f"| {_table(row.get('name'))} | {_table(row.get('code'))} | {_table(row.get('summary'))} | 数据或风险需要复核 | 等待信号和消息面一致 |")
    lines.extend([
        "",
        "## 8. 暂不关注股票",
        "",
        "| 股票 | 代码 | 主要原因 |",
        "|---|---|---|",
    ])
    for row in [item for item in rows if _conclusion(_score(item.get("reliability_score")), str(item.get("signal_bias") or "unknown")) == "暂不关注"]:
        lines.append(f"| {_table(row.get('name'))} | {_table(row.get('code'))} | 信号弱、风险高或缺少支撑 |")
    lines.extend([
        "",
        "## 9. 共性机会与风险",
        "",
        "| 类型 | 内容 | 涉及股票 |",
        "|---|---|---|",
        "| 共性机会 | 分数较高股票可进入观察池 | " + "、".join(str(row.get("name") or row.get("code")) for row in rows[:5]) + " |",
        "| 共性风险 | 结构化情报不完整时降低结论权重 | " + "、".join(str(row.get("name") or row.get("code")) for row in rows) + " |",
        "",
        "## 10. 数据缺失与来源说明",
        "",
        "| 来源 | 状态 | 影响 |",
        "|---|---|---|",
    ])
    for pack in packs:
        code = pack.get("code") or "-"
        gaps = pack.get("data_gaps") or []
        lines.append(f"| {code} | {'部分缺失' if gaps else '成功'} | {_table('；'.join(gaps) if gaps else '可用于辅助复核')} |")
    lines.extend(["", "免责声明：本报告仅用于辅助观察和复盘，不构成投资建议。"])
    return "\n".join(lines)


def _score(value: Any) -> int:
    try:
        return max(0, min(100, int(round(float(value)))))
    except (TypeError, ValueError):
        return 0


def _conclusion(score: int, signal_bias: str) -> str:
    if score >= 70 and signal_bias not in {"avoid", "bearish"}:
        return "重点关注"
    if score >= 40 and signal_bias != "avoid":
        return "谨慎观察"
    return "暂不关注"


def _rating_counts(rows: List[Dict[str, Any]]) -> Dict[str, int]:
    counts = {"重点关注": 0, "谨慎观察": 0, "暂不关注": 0}
    for row in rows:
        counts[_conclusion(_score(row.get("reliability_score")), str(row.get("signal_bias") or "unknown"))] += 1
    return counts


def _rating_reason(rating: str) -> str:
    if rating == "重点关注":
        return "信号、资金、消息面相对一致"
    if rating == "谨慎观察":
        return "有亮点但风险或数据不足"
    return "信号弱、风险高或缺少支撑"


def _direction(value: Any) -> str:
    mapping = {"bullish": "偏多", "bearish": "偏空", "neutral": "中性", "avoid": "回避", "unknown": "信息不足"}
    return mapping.get(str(value or "unknown"), "信息不足")


def _table(value: Any) -> str:
    text = str(value or "-").replace("|", "/").replace("\n", " ")
    return text[:240]
```

- [ ] **Step 4: Use market-intel report renderer when evidence packs exist**

Modify `stock_screener/signal_analysis/chain.py` imports:

```python
from market_intel.reporting import render_multi_stock_report
```

Modify `WriteArtifactsStep.run` before writing report:

```python
        report_text = _render_markdown_report(context)
        if context.evidence_packs:
            report_text = render_multi_stock_report(
                [context.evidence_packs[row.code] for row in (context.all_rows or context.rows) if row.code in context.evidence_packs],
                [
                    context.results_by_code[row.code].to_db_row(
                        task_id=context.task_id,
                        market=context.market,
                        check_date=context.check_date,
                        csv_path=context.csv_path,
                        timeframe=context.timeframe,
                        analysis_profile=context.analysis_profile,
                    )
                    for row in (context.all_rows or context.rows)
                    if row.code in context.results_by_code
                ],
                report_date=context.check_date,
            )

        with open(report_path, "w", encoding="utf-8") as f:
            f.write(report_text)
```

- [ ] **Step 5: Run reporting tests and commit**

Run:

```bash
cd stock_screener
PYTHONPATH=. python -m unittest tests.test_market_intel_reporting -v
PYTHONPATH=. python -m unittest tests.test_signal_analysis.MarketIntelSignalAnalysisIntegrationTest -v
```

Expected: PASS.

Commit:

```bash
git add stock_screener/market_intel/reporting.py stock_screener/signal_analysis/chain.py stock_screener/tests/test_market_intel_reporting.py
git commit -m "feat: add market intel reports"
```

---

### Task 9: Startup, Config, Docs, And Full Verification

**Files:**
- Modify: `stock_screener/.env.example`
- Modify: `stock_screener/deploy/README.md`
- Test command: Python unittest subset and frontend build

- [ ] **Step 1: Document configuration**

Append to `stock_screener/.env.example`:

```dotenv
# Market Intel backend data layer.
# Enabled for API/UI requests by default. Signal-analysis integration remains opt-in.
MARKET_INTEL_ENABLE_LIVE_PROVIDERS=1
MARKET_INTEL_PROVIDER_TIMEOUT_SEC=10
SIGNAL_ENABLE_MARKET_INTEL=0
```

- [ ] **Step 2: Document operational behavior**

Add to `stock_screener/deploy/README.md` under the web/API deployment section:

```markdown
### Market Intel

`market_intel` provides backend-cached market and stock evidence for the web UI and optional signal-analysis enrichment.

- API routes live under `/api/market-intel`.
- Schema is initialized by `MarketDatabase.init_market_intel_schema()`.
- Provider failures are recorded in `market_intel_provider_runs`.
- Screening tasks do not fail when market-intel providers fail.
- `SIGNAL_ENABLE_MARKET_INTEL=0` keeps automated signal analysis on the previous search+LLM path.
- Enable `SIGNAL_ENABLE_MARKET_INTEL=1` only after provider cache behavior is confirmed in the target environment.
```

- [ ] **Step 3: Run targeted backend tests**

Run:

```bash
cd stock_screener
PYTHONPATH=. python -m unittest \
  tests.test_market_intel_models \
  tests.test_market_intel_repository \
  tests.test_market_intel_providers \
  tests.test_market_intel_service \
  tests.test_market_intel_evidence \
  tests.test_market_intel_api \
  tests.test_market_intel_reporting \
  tests.test_market_intel_frontend \
  tests.test_signal_analysis.MarketIntelSignalAnalysisIntegrationTest \
  -v
```

Expected: PASS.

- [ ] **Step 4: Run frontend build**

Run:

```bash
cd stock_screener
npm --prefix web_frontend run build
```

Expected: Vite build exits 0.

- [ ] **Step 5: Run schema smoke check**

Run:

```bash
cd stock_screener
PYTHONPATH=. python - <<'PY'
from pathlib import Path
sql = Path("sql/017_market_intel.sql").read_text(encoding="utf-8")
for table in ["market_intel_items", "market_intel_bundles", "market_intel_provider_runs"]:
    assert f"CREATE TABLE IF NOT EXISTS {table}" in sql
print("market intel schema smoke ok")
PY
```

Expected output:

```text
market intel schema smoke ok
```

- [ ] **Step 6: Commit docs and config**

Commit:

```bash
git add stock_screener/.env.example stock_screener/deploy/README.md
git commit -m "docs: document market intel configuration"
```

---

## Final Acceptance Checklist

- [ ] `GET /api/market-intel/stocks/{market}/{code}` returns grouped stock intelligence and source status.
- [ ] `GET /api/market-intel/markets/{market}/digest` returns market news and index context.
- [ ] `POST /api/market-intel/evidence-pack/preview` returns structured items, search slots, data gaps, and citations without invoking LLM.
- [ ] Provider failures are recorded in provider runs and downgrade to stale or empty bundles.
- [ ] Screening still works when `SIGNAL_ENABLE_MARKET_INTEL=0`.
- [ ] Signal analysis with `SIGNAL_ENABLE_MARKET_INTEL=1` uses market-intel evidence without adding per-stock search calls by default.
- [ ] The `市场情报` page can query one stock, refresh cache, show source status, grouped data, and evidence-pack counts.
- [ ] Single-stock and multi-stock report renderers produce Chinese plain-language sections, tables, pie-chart data tables, and bar-chart data tables.
- [ ] All targeted backend tests and frontend build pass.

## Execution Notes

Start execution from a clean or clearly understood worktree. Existing unrelated dirty files must not be reverted. Commit only files listed in the active task.
