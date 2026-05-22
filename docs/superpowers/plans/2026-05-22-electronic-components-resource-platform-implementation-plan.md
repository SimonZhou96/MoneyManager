# Electronic Components Resource Platform Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the MVP single-part electronic components resource search inside MoneyManager, querying TI, LCSC, DigiKey, and Mouser through public-page crawler adapters and showing normalized stock/price/location results in the web UI.

**Architecture:** Add a focused `component_search` backend package with typed models, source adapters, normalization, matching, and an orchestration service. Persist task/result/raw snapshot data through `MarketDatabase`, expose FastAPI routes under `/api/components`, and add one compact frontend page that polls task status and renders source status plus grouped results.

**Tech Stack:** Python 3.14, FastAPI, PyMySQL, requests, beautifulsoup4, React 18, TypeScript, Vite, unittest.

---

## File Structure

- Create `stock_screener/component_search/__init__.py`: package marker and public exports.
- Create `stock_screener/component_search/models.py`: dataclasses and enum-like constants for requests, source payloads, normalized results, source status, and task status.
- Create `stock_screener/component_search/normalizer.py`: MPN normalization, numeric parsing, price-break parsing, and source-result normalization helpers.
- Create `stock_screener/component_search/matcher.py`: match type, confidence, channel priority, and final result sorting.
- Create `stock_screener/component_search/adapters/base.py`: adapter protocol, HTTP client wrapper, public-page blocking detection, and shared adapter utilities.
- Create `stock_screener/component_search/adapters/ti.py`: Texas Instruments public-page search/detail adapter.
- Create `stock_screener/component_search/adapters/lcsc.py`: LCSC public-page search/detail adapter.
- Create `stock_screener/component_search/adapters/digikey.py`: DigiKey public-page search/detail adapter.
- Create `stock_screener/component_search/adapters/mouser.py`: Mouser public-page search/detail adapter.
- Create `stock_screener/component_search/service.py`: orchestrates task creation, cache lookup, adapter execution, persistence, status updates, and CSV export.
- Create `stock_screener/sql/017_component_search.sql`: component search task/result/snapshot/source status tables.
- Modify `stock_screener/db.py`: initialize schema and add repository methods used by `ComponentSearchService`.
- Create `stock_screener/web/components.py`: FastAPI router for task create/status/results/export.
- Modify `stock_screener/web/main.py`: include the component router.
- Modify `stock_screener/requirements.txt`: add `beautifulsoup4`.
- Create `stock_screener/tests/fixtures/component_search/*.html`: stable parser fixtures for all four sources.
- Create `stock_screener/tests/test_component_search_normalizer.py`: normalizer and price parsing tests.
- Create `stock_screener/tests/test_component_search_matcher.py`: match classification and sort tests.
- Create `stock_screener/tests/test_component_search_adapters.py`: fixture parser tests and block-page detection tests.
- Create `stock_screener/tests/test_component_search_service.py`: service orchestration tests with fake adapters.
- Create `stock_screener/tests/test_component_search_api.py`: FastAPI route tests with in-memory repository/service overrides.
- Modify `stock_screener/web_frontend/src/api.ts`: add component-search API types and helper functions.
- Modify `stock_screener/web_frontend/src/main.tsx`: add navigation and component search page.
- Modify `stock_screener/web_frontend/src/styles.css`: add compact table/status styles only if existing classes are insufficient.
- Create `stock_screener/tests/test_component_search_frontend.py`: source-text contract tests for route labels, endpoints, fields, and non-misleading location wording.

## Task 1: Backend Domain Models, Normalizer, and Matcher

**Files:**
- Create: `stock_screener/component_search/__init__.py`
- Create: `stock_screener/component_search/models.py`
- Create: `stock_screener/component_search/normalizer.py`
- Create: `stock_screener/component_search/matcher.py`
- Test: `stock_screener/tests/test_component_search_normalizer.py`
- Test: `stock_screener/tests/test_component_search_matcher.py`

- [ ] **Step 1: Write failing normalizer tests**

Create `stock_screener/tests/test_component_search_normalizer.py`:

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import unittest

from component_search.models import PriceBreak, SourceResult
from component_search.normalizer import normalize_mpn, parse_decimal, parse_int, normalize_source_result


class ComponentSearchNormalizerTest(unittest.TestCase):
    def test_normalize_mpn_removes_spacing_and_case_noise(self):
        self.assertEqual(normalize_mpn(" tps 5430 dda "), "TPS5430DDA")
        self.assertEqual(normalize_mpn("TPS5430-DDA"), "TPS5430DDA")

    def test_parse_int_handles_stock_text(self):
        self.assertEqual(parse_int("1,234 In Stock"), 1234)
        self.assertEqual(parse_int("库存 9,876"), 9876)
        self.assertIsNone(parse_int("未公开"))

    def test_parse_decimal_handles_currency_text(self):
        self.assertEqual(str(parse_decimal("$1.2340")), "1.2340")
        self.assertEqual(str(parse_decimal("￥0.56")), "0.56")
        self.assertIsNone(parse_decimal("quote required"))

    def test_normalize_source_result_keeps_location_fields_separate(self):
        raw = SourceResult(
            source_name="Texas Instruments",
            source_type="manufacturer_direct",
            channel_type="原厂直销",
            manufacturer="Texas Instruments",
            generic_part_number="TPS5430",
            orderable_part_number="TPS5430DDA",
            source_part_number="TPS5430DDA",
            description="Step-down converter",
            package="SOIC-8",
            stock_qty=1200,
            price_breaks=[PriceBreak(quantity=1, unit_price="1.23", currency="USD")],
            currency="USD",
            moq=1,
            spq=None,
            lead_time="Ships today",
            supplier_name="Texas Instruments",
            supplier_address="12500 TI Boulevard, Dallas, TX",
            source_region="US",
            ship_from_region=None,
            stock_location_note="TI 官方库存，具体仓库地址未公开",
            product_url="https://www.ti.com/product/TPS5430",
            raw_payload={"fixture": True},
        )

        result = normalize_source_result(raw, requested_part_number="tps5430")

        self.assertEqual(result.normalized_mpn, "TPS5430DDA")
        self.assertEqual(result.generic_part_number, "TPS5430")
        self.assertEqual(result.orderable_part_number, "TPS5430DDA")
        self.assertEqual(result.stock_location_note, "TI 官方库存，具体仓库地址未公开")
        self.assertIsNone(result.ship_from_region)
        self.assertEqual(result.price_breaks[0].currency, "USD")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Write failing matcher tests**

Create `stock_screener/tests/test_component_search_matcher.py`:

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import unittest

from component_search.matcher import classify_match, sort_results
from component_search.models import NormalizedComponentResult, PriceBreak


def result(**overrides):
    base = {
        "source_name": "Mouser",
        "source_type": "distributor",
        "channel_type": "授权分销",
        "manufacturer": "Texas Instruments",
        "generic_part_number": None,
        "orderable_part_number": "TPS5430DDA",
        "source_part_number": "595-TPS5430DDA",
        "normalized_mpn": "TPS5430DDA",
        "description": "Step-down converter",
        "package": "SOIC-8",
        "stock_qty": 100,
        "price_breaks": [PriceBreak(quantity=1, unit_price="1.20", currency="USD")],
        "currency": "USD",
        "moq": 1,
        "spq": None,
        "lead_time": "In stock",
        "supplier_name": "Mouser",
        "supplier_address": "1000 N Main St, Mansfield, TX",
        "source_region": "US",
        "ship_from_region": None,
        "stock_location_note": "页面未公开具体仓库地址",
        "product_url": "https://www.mouser.com/",
        "match_type": "same_mpn",
        "match_confidence": 80,
        "raw_snapshot_id": None,
    }
    base.update(overrides)
    return NormalizedComponentResult(**base)


class ComponentSearchMatcherTest(unittest.TestCase):
    def test_classify_exact_when_requested_orderable_matches(self):
        match_type, confidence = classify_match("TPS5430DDA", result())
        self.assertEqual(match_type, "exact")
        self.assertGreaterEqual(confidence, 95)

    def test_classify_ti_orderable_variant(self):
        ti_result = result(
            source_name="Texas Instruments",
            source_type="manufacturer_direct",
            channel_type="原厂直销",
            generic_part_number="TPS5430",
            orderable_part_number="TPS5430DDA",
        )
        match_type, confidence = classify_match("TPS5430", ti_result)
        self.assertEqual(match_type, "orderable_variant")
        self.assertGreaterEqual(confidence, 88)

    def test_sort_results_prefers_ti_then_in_stock_then_price(self):
        ti = result(
            source_name="Texas Instruments",
            source_type="manufacturer_direct",
            channel_type="原厂直销",
            stock_qty=50,
            price_breaks=[PriceBreak(quantity=1, unit_price="1.40", currency="USD")],
            match_type="orderable_variant",
            match_confidence=90,
        )
        cheaper_no_stock = result(
            source_name="DigiKey",
            stock_qty=0,
            price_breaks=[PriceBreak(quantity=1, unit_price="0.90", currency="USD")],
            match_type="exact",
            match_confidence=98,
        )
        mouser = result(
            source_name="Mouser",
            stock_qty=100,
            price_breaks=[PriceBreak(quantity=1, unit_price="1.20", currency="USD")],
            match_type="exact",
            match_confidence=98,
        )

        sorted_rows = sort_results([cheaper_no_stock, mouser, ti], requested_quantity=1)

        self.assertEqual([row.source_name for row in sorted_rows], ["Texas Instruments", "Mouser", "DigiKey"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Run tests to verify they fail**

Run:

```bash
cd /Users/meng.zhou/Desktop/goworkspace/src/github.com/SimonZhou96/MoneyManager
PYTHONPATH=stock_screener python3 -m unittest \
  stock_screener.tests.test_component_search_normalizer \
  stock_screener.tests.test_component_search_matcher -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'component_search'`.

- [ ] **Step 4: Implement models**

Create `stock_screener/component_search/__init__.py`:

```python
"""Electronic component resource search package."""
```

Create `stock_screener/component_search/models.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional


TASK_STATUS_QUEUED = "queued"
TASK_STATUS_RUNNING = "running"
TASK_STATUS_COMPLETED = "completed"
TASK_STATUS_FAILED = "failed"

MATCH_EXACT = "exact"
MATCH_SAME_MPN = "same_mpn"
MATCH_ORDERABLE_VARIANT = "orderable_variant"
MATCH_RELATED = "related"
MATCH_LOW_CONFIDENCE = "low_confidence"


@dataclass(frozen=True)
class PriceBreak:
    quantity: int
    unit_price: Decimal | str
    currency: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "quantity": int(self.quantity),
            "unit_price": str(self.unit_price),
            "currency": self.currency,
        }


@dataclass
class ComponentSearchRequest:
    part_number: str
    quantity: int = 1
    currency: str = "USD"
    region: str = "US"


@dataclass
class SourceResult:
    source_name: str
    source_type: str
    channel_type: str
    manufacturer: Optional[str]
    generic_part_number: Optional[str]
    orderable_part_number: Optional[str]
    source_part_number: Optional[str]
    description: Optional[str]
    package: Optional[str]
    stock_qty: Optional[int]
    price_breaks: list[PriceBreak] = field(default_factory=list)
    currency: Optional[str] = None
    moq: Optional[int] = None
    spq: Optional[int] = None
    lead_time: Optional[str] = None
    supplier_name: Optional[str] = None
    supplier_address: Optional[str] = None
    source_region: Optional[str] = None
    ship_from_region: Optional[str] = None
    stock_location_note: Optional[str] = None
    product_url: Optional[str] = None
    raw_payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class NormalizedComponentResult(SourceResult):
    normalized_mpn: str = ""
    match_type: str = MATCH_LOW_CONFIDENCE
    match_confidence: int = 0
    raw_snapshot_id: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_name": self.source_name,
            "source_type": self.source_type,
            "channel_type": self.channel_type,
            "manufacturer": self.manufacturer,
            "generic_part_number": self.generic_part_number,
            "orderable_part_number": self.orderable_part_number,
            "source_part_number": self.source_part_number,
            "normalized_mpn": self.normalized_mpn,
            "description": self.description,
            "package": self.package,
            "stock_qty": self.stock_qty,
            "price_breaks": [item.to_dict() for item in self.price_breaks],
            "currency": self.currency,
            "moq": self.moq,
            "spq": self.spq,
            "lead_time": self.lead_time,
            "supplier_name": self.supplier_name,
            "supplier_address": self.supplier_address,
            "source_region": self.source_region,
            "ship_from_region": self.ship_from_region,
            "stock_location_note": self.stock_location_note,
            "product_url": self.product_url,
            "match_type": self.match_type,
            "match_confidence": self.match_confidence,
            "raw_snapshot_id": self.raw_snapshot_id,
        }


@dataclass
class SourceRunStatus:
    source_name: str
    status: str
    elapsed_ms: int = 0
    result_count: int = 0
    error_code: Optional[str] = None
    error_message: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_name": self.source_name,
            "status": self.status,
            "elapsed_ms": self.elapsed_ms,
            "result_count": self.result_count,
            "error_code": self.error_code,
            "error_message": self.error_message,
        }


@dataclass
class ComponentSearchTask:
    task_id: str
    user_id: Optional[int]
    request: ComponentSearchRequest
    status: str
    source_statuses: list[SourceRunStatus] = field(default_factory=list)
    error_message: Optional[str] = None
    created_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
```

- [ ] **Step 5: Implement normalizer**

Create `stock_screener/component_search/normalizer.py`:

```python
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Optional

from .models import NormalizedComponentResult, SourceResult


def normalize_mpn(value: str | None) -> str:
    text = str(value or "").strip().upper()
    return re.sub(r"[^A-Z0-9]", "", text)


def parse_int(value: object) -> Optional[int]:
    if value is None:
        return None
    text = str(value)
    match = re.search(r"\d[\d,]*", text)
    if not match:
        return None
    return int(match.group(0).replace(",", ""))


def parse_decimal(value: object) -> Optional[Decimal]:
    if value is None:
        return None
    text = str(value).replace(",", "")
    match = re.search(r"\d+(?:\.\d+)?", text)
    if not match:
        return None
    try:
        return Decimal(match.group(0))
    except InvalidOperation:
        return None


def normalize_source_result(raw: SourceResult, requested_part_number: str) -> NormalizedComponentResult:
    primary = raw.orderable_part_number or raw.generic_part_number or raw.source_part_number or requested_part_number
    return NormalizedComponentResult(
        source_name=raw.source_name,
        source_type=raw.source_type,
        channel_type=raw.channel_type,
        manufacturer=raw.manufacturer,
        generic_part_number=raw.generic_part_number,
        orderable_part_number=raw.orderable_part_number,
        source_part_number=raw.source_part_number,
        normalized_mpn=normalize_mpn(primary),
        description=raw.description,
        package=raw.package,
        stock_qty=raw.stock_qty,
        price_breaks=raw.price_breaks,
        currency=raw.currency,
        moq=raw.moq,
        spq=raw.spq,
        lead_time=raw.lead_time,
        supplier_name=raw.supplier_name,
        supplier_address=raw.supplier_address,
        source_region=raw.source_region,
        ship_from_region=raw.ship_from_region,
        stock_location_note=raw.stock_location_note or "页面未公开具体仓库地址",
        product_url=raw.product_url,
        raw_payload=raw.raw_payload,
    )
```

- [ ] **Step 6: Implement matcher**

Create `stock_screener/component_search/matcher.py`:

```python
from __future__ import annotations

from decimal import Decimal

from .models import (
    MATCH_EXACT,
    MATCH_LOW_CONFIDENCE,
    MATCH_ORDERABLE_VARIANT,
    MATCH_RELATED,
    MATCH_SAME_MPN,
    NormalizedComponentResult,
)
from .normalizer import normalize_mpn


def classify_match(requested_part_number: str, result: NormalizedComponentResult) -> tuple[str, int]:
    requested = normalize_mpn(requested_part_number)
    normalized = normalize_mpn(result.normalized_mpn)
    generic = normalize_mpn(result.generic_part_number)
    orderable = normalize_mpn(result.orderable_part_number)
    source_part = normalize_mpn(result.source_part_number)

    if requested and requested in {normalized, orderable, source_part}:
        return MATCH_EXACT, 98
    if requested and generic == requested and orderable and orderable != requested:
        return MATCH_ORDERABLE_VARIANT, 90
    if requested and normalized and (requested in normalized or normalized in requested):
        return MATCH_SAME_MPN, 82
    if requested and generic and (requested in generic or generic in requested):
        return MATCH_RELATED, 65
    return MATCH_LOW_CONFIDENCE, 40


def apply_match(requested_part_number: str, result: NormalizedComponentResult) -> NormalizedComponentResult:
    match_type, confidence = classify_match(requested_part_number, result)
    result.match_type = match_type
    result.match_confidence = confidence
    return result


def _channel_priority(row: NormalizedComponentResult) -> int:
    if row.source_type == "manufacturer_direct":
        return 0
    if row.channel_type == "授权分销":
        return 1
    return 2


def _match_priority(row: NormalizedComponentResult) -> int:
    order = {
        MATCH_EXACT: 0,
        MATCH_ORDERABLE_VARIANT: 1,
        MATCH_SAME_MPN: 2,
        MATCH_RELATED: 3,
        MATCH_LOW_CONFIDENCE: 4,
    }
    return order.get(row.match_type, 9)


def _unit_price_for_quantity(row: NormalizedComponentResult, requested_quantity: int) -> Decimal:
    if not row.price_breaks:
        return Decimal("999999999")
    eligible = [item for item in row.price_breaks if int(item.quantity) <= requested_quantity]
    chosen = eligible[-1] if eligible else row.price_breaks[0]
    return Decimal(str(chosen.unit_price))


def sort_results(
    rows: list[NormalizedComponentResult],
    *,
    requested_quantity: int,
) -> list[NormalizedComponentResult]:
    return sorted(
        rows,
        key=lambda row: (
            _channel_priority(row),
            _match_priority(row),
            -(row.stock_qty or 0 > 0),
            -int(row.match_confidence or 0),
            _unit_price_for_quantity(row, requested_quantity),
            row.source_name,
        ),
    )
```

- [ ] **Step 7: Run domain tests**

Run:

```bash
cd /Users/meng.zhou/Desktop/goworkspace/src/github.com/SimonZhou96/MoneyManager
PYTHONPATH=stock_screener python3 -m unittest \
  stock_screener.tests.test_component_search_normalizer \
  stock_screener.tests.test_component_search_matcher -v
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add stock_screener/component_search/__init__.py \
  stock_screener/component_search/models.py \
  stock_screener/component_search/normalizer.py \
  stock_screener/component_search/matcher.py \
  stock_screener/tests/test_component_search_normalizer.py \
  stock_screener/tests/test_component_search_matcher.py
git commit -m "feat: add component search domain models"
```

## Task 2: Database Schema and Repository Methods

**Files:**
- Create: `stock_screener/sql/017_component_search.sql`
- Modify: `stock_screener/db.py`
- Test: `stock_screener/tests/test_component_search_service.py`

- [ ] **Step 1: Write service repository tests using an in-memory fake**

Create the first version of `stock_screener/tests/test_component_search_service.py`:

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import unittest

from component_search.models import ComponentSearchRequest, NormalizedComponentResult, PriceBreak
from component_search.service import ComponentSearchService


class FakeComponentRepository:
    def __init__(self):
        self.tasks = {}
        self.results = {}
        self.snapshots = {}

    def create_component_search_task(self, row):
        self.tasks[row["task_id"]] = dict(row)

    def update_component_search_task(self, task_id, **updates):
        self.tasks[task_id].update(updates)

    def get_component_search_task(self, task_id):
        return self.tasks.get(task_id)

    def find_recent_component_search_task(self, cache_key, max_age_minutes):
        return None

    def create_component_raw_snapshot(self, row):
        snapshot_id = row["snapshot_id"]
        self.snapshots[snapshot_id] = dict(row)
        return snapshot_id

    def replace_component_search_results(self, task_id, rows):
        self.results[task_id] = [dict(row) for row in rows]

    def list_component_search_results(self, task_id):
        return self.results.get(task_id, [])


class StaticAdapter:
    source_name = "Texas Instruments"

    def search(self, request):
        return [
            NormalizedComponentResult(
                source_name="Texas Instruments",
                source_type="manufacturer_direct",
                channel_type="原厂直销",
                manufacturer="Texas Instruments",
                generic_part_number=request.part_number,
                orderable_part_number=f"{request.part_number}DDA",
                source_part_number=f"{request.part_number}DDA",
                normalized_mpn=f"{request.part_number}DDA",
                description="Step-down converter",
                package="SOIC-8",
                stock_qty=10,
                price_breaks=[PriceBreak(quantity=1, unit_price="1.23", currency=request.currency)],
                currency=request.currency,
                moq=1,
                spq=None,
                lead_time="In stock",
                supplier_name="Texas Instruments",
                supplier_address="12500 TI Boulevard, Dallas, TX",
                source_region=request.region,
                ship_from_region=None,
                stock_location_note="TI 官方库存，具体仓库地址未公开",
                product_url="https://www.ti.com/product/TPS5430",
            )
        ]


class ComponentSearchServiceTest(unittest.TestCase):
    def test_submit_and_run_persists_completed_results(self):
        repo = FakeComponentRepository()
        service = ComponentSearchService(repository=repo, adapters=[StaticAdapter()])
        created = service.submit_search(
            ComponentSearchRequest(part_number="TPS5430", quantity=1, currency="USD", region="US"),
            user_id=7,
            run_inline=True,
        )

        task = service.get_task(created["task_id"])
        results = service.get_results(created["task_id"])

        self.assertEqual(task["status"], "completed")
        self.assertEqual(task["request"]["part_number"], "TPS5430")
        self.assertEqual(task["source_statuses"][0]["source_name"], "Texas Instruments")
        self.assertEqual(results["rows"][0]["source_name"], "Texas Instruments")
        self.assertEqual(results["rows"][0]["match_type"], "orderable_variant")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
cd /Users/meng.zhou/Desktop/goworkspace/src/github.com/SimonZhou96/MoneyManager
PYTHONPATH=stock_screener python3 -m unittest stock_screener.tests.test_component_search_service -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'component_search.service'`.

- [ ] **Step 3: Add SQL schema**

Create `stock_screener/sql/017_component_search.sql`:

```sql
CREATE TABLE IF NOT EXISTS component_search_tasks (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    task_id VARCHAR(64) NOT NULL,
    user_id BIGINT UNSIGNED NULL,
    cache_key VARCHAR(191) NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'queued',
    request_json JSON NOT NULL,
    source_statuses_json JSON NULL,
    error_message TEXT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    finished_at DATETIME(6) NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uk_component_search_task_id (task_id),
    KEY idx_component_search_cache (cache_key, created_at),
    KEY idx_component_search_user (user_id, created_at),
    KEY idx_component_search_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='电子元器件资源查询任务';

CREATE TABLE IF NOT EXISTS component_raw_snapshots (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    snapshot_id VARCHAR(64) NOT NULL,
    task_id VARCHAR(64) NOT NULL,
    source_name VARCHAR(64) NOT NULL,
    product_url TEXT NULL,
    parser_version VARCHAR(64) NOT NULL,
    raw_payload_json JSON NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (id),
    UNIQUE KEY uk_component_snapshot_id (snapshot_id),
    KEY idx_component_snapshot_task (task_id),
    KEY idx_component_snapshot_source (source_name, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='电子元器件公开页面抓取快照';

CREATE TABLE IF NOT EXISTS component_search_results (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    task_id VARCHAR(64) NOT NULL,
    source_name VARCHAR(64) NOT NULL,
    source_type VARCHAR(64) NOT NULL,
    channel_type VARCHAR(64) NOT NULL,
    manufacturer VARCHAR(191) NULL,
    generic_part_number VARCHAR(191) NULL,
    orderable_part_number VARCHAR(191) NULL,
    source_part_number VARCHAR(191) NULL,
    normalized_mpn VARCHAR(191) NOT NULL,
    description TEXT NULL,
    package VARCHAR(191) NULL,
    stock_qty BIGINT NULL,
    price_breaks_json JSON NULL,
    currency VARCHAR(16) NULL,
    moq INT NULL,
    spq INT NULL,
    lead_time VARCHAR(191) NULL,
    supplier_name VARCHAR(191) NULL,
    supplier_address TEXT NULL,
    source_region VARCHAR(64) NULL,
    ship_from_region VARCHAR(191) NULL,
    stock_location_note VARCHAR(255) NULL,
    product_url TEXT NULL,
    match_type VARCHAR(64) NOT NULL,
    match_confidence INT NOT NULL DEFAULT 0,
    raw_snapshot_id VARCHAR(64) NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (id),
    KEY idx_component_result_task (task_id),
    KEY idx_component_result_mpn (normalized_mpn),
    KEY idx_component_result_source (source_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='电子元器件资源查询归一化结果';

CREATE TABLE IF NOT EXISTS component_source_status (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    source_name VARCHAR(64) NOT NULL,
    status VARCHAR(32) NOT NULL,
    last_error_code VARCHAR(64) NULL,
    last_error_message TEXT NULL,
    last_elapsed_ms INT NULL,
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (id),
    UNIQUE KEY uk_component_source_name (source_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='电子元器件数据源健康状态';
```

- [ ] **Step 4: Add DB initialization and repository methods**

Modify `stock_screener/db.py`:

1. In `MarketDatabase.init_web_schema`, call the new schema initializer after quant:

```python
    def init_web_schema(self):
        self._ensure_core_web_schema()
        self.init_option_lab_schema()
        self.init_quant_lab_schema()
        self.init_component_search_schema()
```

If `init_web_schema` is not split into `_ensure_core_web_schema`, keep the existing body and add only:

```python
        self.init_component_search_schema()
```

after `self.init_quant_lab_schema()`.

2. Add methods near the quant/option schema methods:

```python
    def init_component_search_schema(self) -> None:
        schema_path = Path(__file__).parent / "sql" / "017_component_search.sql"
        sql_text = schema_path.read_text(encoding="utf-8")
        statements = [stmt.strip() for stmt in sql_text.split(";") if stmt.strip()]
        with self.conn.cursor() as cursor:
            for statement in statements:
                cursor.execute(statement)

    def create_component_search_task(self, row: dict) -> None:
        with self.conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO component_search_tasks
                    (task_id, user_id, cache_key, status, request_json, source_statuses_json, error_message)
                VALUES (%s,%s,%s,%s,%s,%s,%s)
                ON DUPLICATE KEY UPDATE
                    user_id=VALUES(user_id),
                    cache_key=VALUES(cache_key),
                    status=VALUES(status),
                    request_json=VALUES(request_json),
                    source_statuses_json=VALUES(source_statuses_json),
                    error_message=VALUES(error_message)
                """,
                (
                    row["task_id"],
                    row.get("user_id"),
                    row["cache_key"],
                    row.get("status", "queued"),
                    _json_or_none(row.get("request") or {}),
                    _json_or_none(row.get("source_statuses") or []),
                    row.get("error_message"),
                ),
            )

    def update_component_search_task(self, task_id: str, **updates) -> None:
        allowed = {
            "status": "status",
            "source_statuses": "source_statuses_json",
            "error_message": "error_message",
            "finished_at": "finished_at",
        }
        assignments = []
        params = []
        for key, column in allowed.items():
            if key not in updates:
                continue
            assignments.append(f"{column}=%s")
            value = updates[key]
            if key == "source_statuses":
                value = _json_or_none(value or [])
            params.append(value)
        if not assignments:
            return
        params.append(task_id)
        with self.conn.cursor() as cursor:
            cursor.execute(
                f"UPDATE component_search_tasks SET {', '.join(assignments)} WHERE task_id=%s",
                tuple(params),
            )

    def get_component_search_task(self, task_id: str) -> Optional[dict]:
        with self.conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT task_id, user_id, cache_key, status, request_json, source_statuses_json,
                       error_message, created_at, finished_at
                FROM component_search_tasks
                WHERE task_id=%s
                LIMIT 1
                """,
                (task_id,),
            )
            row = cursor.fetchone()
        if not row:
            return None
        return {
            "task_id": row[0],
            "user_id": row[1],
            "cache_key": row[2],
            "status": row[3],
            "request": _decode_json_field(row[4], {}),
            "source_statuses": _decode_json_field(row[5], []),
            "error_message": row[6],
            "created_at": str(row[7]) if row[7] else None,
            "finished_at": str(row[8]) if row[8] else None,
        }

    def find_recent_component_search_task(self, cache_key: str, max_age_minutes: int) -> Optional[dict]:
        with self.conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT task_id
                FROM component_search_tasks
                WHERE cache_key=%s
                  AND status='completed'
                  AND created_at >= DATE_SUB(UTC_TIMESTAMP(6), INTERVAL %s MINUTE)
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (cache_key, int(max_age_minutes)),
            )
            row = cursor.fetchone()
        return self.get_component_search_task(row[0]) if row else None

    def create_component_raw_snapshot(self, row: dict) -> str:
        with self.conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO component_raw_snapshots
                    (snapshot_id, task_id, source_name, product_url, parser_version, raw_payload_json)
                VALUES (%s,%s,%s,%s,%s,%s)
                """,
                (
                    row["snapshot_id"],
                    row["task_id"],
                    row["source_name"],
                    row.get("product_url"),
                    row.get("parser_version", "v1"),
                    _json_or_none(row.get("raw_payload") or {}),
                ),
            )
        return row["snapshot_id"]

    def replace_component_search_results(self, task_id: str, rows: list[dict]) -> None:
        with self.conn.cursor() as cursor:
            cursor.execute("DELETE FROM component_search_results WHERE task_id=%s", (task_id,))
            for row in rows:
                cursor.execute(
                    """
                    INSERT INTO component_search_results
                        (task_id, source_name, source_type, channel_type, manufacturer,
                         generic_part_number, orderable_part_number, source_part_number,
                         normalized_mpn, description, package, stock_qty, price_breaks_json,
                         currency, moq, spq, lead_time, supplier_name, supplier_address,
                         source_region, ship_from_region, stock_location_note, product_url,
                         match_type, match_confidence, raw_snapshot_id)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    """,
                    (
                        task_id,
                        row.get("source_name"),
                        row.get("source_type"),
                        row.get("channel_type"),
                        row.get("manufacturer"),
                        row.get("generic_part_number"),
                        row.get("orderable_part_number"),
                        row.get("source_part_number"),
                        row.get("normalized_mpn"),
                        row.get("description"),
                        row.get("package"),
                        row.get("stock_qty"),
                        _json_or_none(row.get("price_breaks") or []),
                        row.get("currency"),
                        row.get("moq"),
                        row.get("spq"),
                        row.get("lead_time"),
                        row.get("supplier_name"),
                        row.get("supplier_address"),
                        row.get("source_region"),
                        row.get("ship_from_region"),
                        row.get("stock_location_note"),
                        row.get("product_url"),
                        row.get("match_type"),
                        int(row.get("match_confidence") or 0),
                        row.get("raw_snapshot_id"),
                    ),
                )

    def list_component_search_results(self, task_id: str) -> list[dict]:
        with self.conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT source_name, source_type, channel_type, manufacturer,
                       generic_part_number, orderable_part_number, source_part_number,
                       normalized_mpn, description, package, stock_qty, price_breaks_json,
                       currency, moq, spq, lead_time, supplier_name, supplier_address,
                       source_region, ship_from_region, stock_location_note, product_url,
                       match_type, match_confidence, raw_snapshot_id, created_at
                FROM component_search_results
                WHERE task_id=%s
                ORDER BY id ASC
                """,
                (task_id,),
            )
            rows = cursor.fetchall()
        return [
            {
                "source_name": row[0],
                "source_type": row[1],
                "channel_type": row[2],
                "manufacturer": row[3],
                "generic_part_number": row[4],
                "orderable_part_number": row[5],
                "source_part_number": row[6],
                "normalized_mpn": row[7],
                "description": row[8],
                "package": row[9],
                "stock_qty": row[10],
                "price_breaks": _decode_json_field(row[11], []),
                "currency": row[12],
                "moq": row[13],
                "spq": row[14],
                "lead_time": row[15],
                "supplier_name": row[16],
                "supplier_address": row[17],
                "source_region": row[18],
                "ship_from_region": row[19],
                "stock_location_note": row[20],
                "product_url": row[21],
                "match_type": row[22],
                "match_confidence": int(row[23] or 0),
                "raw_snapshot_id": row[24],
                "created_at": str(row[25]) if row[25] else None,
            }
            for row in rows
        ]
```

- [ ] **Step 5: Implement service orchestration**

Create `stock_screener/component_search/service.py`:

```python
from __future__ import annotations

import csv
import hashlib
import io
import time
import uuid
from datetime import datetime
from typing import Any

from .matcher import apply_match, sort_results
from .models import (
    TASK_STATUS_COMPLETED,
    TASK_STATUS_FAILED,
    TASK_STATUS_RUNNING,
    ComponentSearchRequest,
    NormalizedComponentResult,
    SourceRunStatus,
)


class ComponentSearchService:
    def __init__(self, repository, adapters: list[Any], cache_ttl_minutes: int = 30):
        self.repository = repository
        self.adapters = adapters
        self.cache_ttl_minutes = cache_ttl_minutes

    def submit_search(self, request: ComponentSearchRequest, *, user_id: int | None, run_inline: bool = False) -> dict:
        cache_key = self._cache_key(request)
        cached = self.repository.find_recent_component_search_task(cache_key, self.cache_ttl_minutes)
        if cached:
            return {"task_id": cached["task_id"], "status": cached["status"], "cached": True}
        task_id = uuid.uuid4().hex
        self.repository.create_component_search_task(
            {
                "task_id": task_id,
                "user_id": user_id,
                "cache_key": cache_key,
                "status": "queued",
                "request": self._request_dict(request),
                "source_statuses": [],
            }
        )
        if run_inline:
            self.run_task(task_id)
        return {"task_id": task_id, "status": "queued", "cached": False}

    def run_task(self, task_id: str) -> None:
        task = self.repository.get_component_search_task(task_id)
        if not task:
            return
        request = ComponentSearchRequest(**task["request"])
        statuses: list[SourceRunStatus] = []
        rows: list[NormalizedComponentResult] = []
        self.repository.update_component_search_task(task_id, status=TASK_STATUS_RUNNING)
        for adapter in self.adapters:
            started = time.monotonic()
            try:
                source_rows = adapter.search(request)
                for row in source_rows:
                    normalized = apply_match(request.part_number, row)
                    snapshot_id = uuid.uuid4().hex
                    self.repository.create_component_raw_snapshot(
                        {
                            "snapshot_id": snapshot_id,
                            "task_id": task_id,
                            "source_name": normalized.source_name,
                            "product_url": normalized.product_url,
                            "parser_version": getattr(adapter, "parser_version", "v1"),
                            "raw_payload": normalized.raw_payload or normalized.to_dict(),
                        }
                    )
                    normalized.raw_snapshot_id = snapshot_id
                    rows.append(normalized)
                statuses.append(
                    SourceRunStatus(
                        source_name=adapter.source_name,
                        status="success",
                        elapsed_ms=int((time.monotonic() - started) * 1000),
                        result_count=len(source_rows),
                    )
                )
            except Exception as exc:
                statuses.append(
                    SourceRunStatus(
                        source_name=getattr(adapter, "source_name", adapter.__class__.__name__),
                        status="failed",
                        elapsed_ms=int((time.monotonic() - started) * 1000),
                        error_code=exc.__class__.__name__,
                        error_message=str(exc),
                    )
                )
        sorted_rows = sort_results(rows, requested_quantity=request.quantity)
        self.repository.replace_component_search_results(task_id, [row.to_dict() for row in sorted_rows])
        status = TASK_STATUS_COMPLETED if rows else TASK_STATUS_FAILED
        self.repository.update_component_search_task(
            task_id,
            status=status,
            source_statuses=[item.to_dict() for item in statuses],
            error_message=None if rows else "所有数据源均未返回可用结果",
            finished_at=datetime.utcnow(),
        )

    def get_task(self, task_id: str) -> dict | None:
        return self.repository.get_component_search_task(task_id)

    def get_results(self, task_id: str) -> dict:
        task = self.repository.get_component_search_task(task_id)
        rows = self.repository.list_component_search_results(task_id)
        return {"task_id": task_id, "status": task["status"] if task else "missing", "rows": rows}

    def export_csv(self, task_id: str) -> str:
        rows = self.repository.list_component_search_results(task_id)
        output = io.StringIO()
        fieldnames = [
            "channel_type", "source_name", "manufacturer", "generic_part_number",
            "orderable_part_number", "source_part_number", "stock_qty", "price_breaks",
            "moq", "spq", "lead_time", "supplier_address", "ship_from_region",
            "stock_location_note", "product_url", "match_type", "match_confidence",
        ]
        writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            item = dict(row)
            item["price_breaks"] = str(item.get("price_breaks") or [])
            writer.writerow(item)
        return output.getvalue()

    def _cache_key(self, request: ComponentSearchRequest) -> str:
        raw = "|".join([
            request.part_number.strip().upper(),
            str(request.quantity),
            request.currency.strip().upper(),
            request.region.strip().upper(),
        ])
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _request_dict(self, request: ComponentSearchRequest) -> dict[str, Any]:
        return {
            "part_number": request.part_number.strip(),
            "quantity": int(request.quantity),
            "currency": request.currency.strip().upper(),
            "region": request.region.strip().upper(),
        }
```

- [ ] **Step 6: Run service test**

Run:

```bash
cd /Users/meng.zhou/Desktop/goworkspace/src/github.com/SimonZhou96/MoneyManager
PYTHONPATH=stock_screener python3 -m unittest stock_screener.tests.test_component_search_service -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add stock_screener/sql/017_component_search.sql \
  stock_screener/db.py \
  stock_screener/component_search/service.py \
  stock_screener/tests/test_component_search_service.py
git commit -m "feat: persist component search tasks"
```

## Task 3: Crawler Adapter Interfaces and Fixture Parsers

**Files:**
- Modify: `stock_screener/requirements.txt`
- Create: `stock_screener/component_search/adapters/base.py`
- Create: `stock_screener/component_search/adapters/ti.py`
- Create: `stock_screener/component_search/adapters/lcsc.py`
- Create: `stock_screener/component_search/adapters/digikey.py`
- Create: `stock_screener/component_search/adapters/mouser.py`
- Create: `stock_screener/tests/fixtures/component_search/ti_tps5430.html`
- Create: `stock_screener/tests/fixtures/component_search/lcsc_tps5430.html`
- Create: `stock_screener/tests/fixtures/component_search/digikey_tps5430.html`
- Create: `stock_screener/tests/fixtures/component_search/mouser_tps5430.html`
- Test: `stock_screener/tests/test_component_search_adapters.py`

- [ ] **Step 1: Add parser dependency test expectation**

Create `stock_screener/tests/test_component_search_adapters.py`:

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from pathlib import Path
import unittest

from component_search.adapters.base import BlockedPageError, detect_blocked_page
from component_search.adapters.digikey import DigiKeyAdapter
from component_search.adapters.lcsc import LCSCAdapter
from component_search.adapters.mouser import MouserAdapter
from component_search.adapters.ti import TexasInstrumentsAdapter
from component_search.models import ComponentSearchRequest


FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "component_search"


class ComponentSearchAdaptersTest(unittest.TestCase):
    def request(self):
        return ComponentSearchRequest(part_number="TPS5430", quantity=10, currency="USD", region="US")

    def test_detect_blocked_page(self):
        with self.assertRaises(BlockedPageError):
            detect_blocked_page("<html><title>Access denied</title><body>captcha required</body></html>")

    def test_ti_fixture_parses_orderable_variant(self):
        adapter = TexasInstrumentsAdapter()
        html = (FIXTURE_DIR / "ti_tps5430.html").read_text(encoding="utf-8")
        rows = adapter.parse_search_html(html, self.request())
        self.assertEqual(rows[0].source_name, "Texas Instruments")
        self.assertEqual(rows[0].generic_part_number, "TPS5430")
        self.assertEqual(rows[0].orderable_part_number, "TPS5430DDA")
        self.assertEqual(rows[0].stock_qty, 1200)
        self.assertEqual(rows[0].stock_location_note, "TI 官方库存，具体仓库地址未公开")

    def test_lcsc_fixture_parses_stock_and_cny_price(self):
        adapter = LCSCAdapter()
        html = (FIXTURE_DIR / "lcsc_tps5430.html").read_text(encoding="utf-8")
        rows = adapter.parse_search_html(html, self.request())
        self.assertEqual(rows[0].source_name, "LCSC")
        self.assertEqual(rows[0].stock_qty, 532)
        self.assertEqual(rows[0].price_breaks[0].currency, "CNY")

    def test_digikey_fixture_parses_authorized_distributor_row(self):
        adapter = DigiKeyAdapter()
        html = (FIXTURE_DIR / "digikey_tps5430.html").read_text(encoding="utf-8")
        rows = adapter.parse_search_html(html, self.request())
        self.assertEqual(rows[0].source_name, "DigiKey")
        self.assertEqual(rows[0].channel_type, "授权分销")
        self.assertEqual(rows[0].source_region, "US")

    def test_mouser_fixture_parses_price_breaks(self):
        adapter = MouserAdapter()
        html = (FIXTURE_DIR / "mouser_tps5430.html").read_text(encoding="utf-8")
        rows = adapter.parse_search_html(html, self.request())
        self.assertEqual(rows[0].source_name, "Mouser")
        self.assertEqual(len(rows[0].price_breaks), 2)
        self.assertEqual(rows[0].price_breaks[1].quantity, 100)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Add HTML fixtures**

Create `stock_screener/tests/fixtures/component_search/ti_tps5430.html`:

```html
<html>
  <body>
    <div class="ti-result" data-product-url="https://www.ti.com/product/TPS5430">
      <span class="gpn">TPS5430</span>
      <span class="opn">TPS5430DDA</span>
      <span class="manufacturer">Texas Instruments</span>
      <span class="package">SOIC-8</span>
      <span class="lifecycle">ACTIVE</span>
      <span class="stock">1,200 in stock</span>
      <span class="lead-time">Ships today</span>
      <table class="price-breaks">
        <tr><td>1</td><td>$1.45</td></tr>
        <tr><td>100</td><td>$1.12</td></tr>
      </table>
    </div>
  </body>
</html>
```

Create `stock_screener/tests/fixtures/component_search/lcsc_tps5430.html`:

```html
<html>
  <body>
    <div class="lcsc-result" data-product-url="https://www.lcsc.com/product-detail/TPS5430.html">
      <span class="lcsc-number">C12345</span>
      <span class="mpn">TPS5430DDA</span>
      <span class="manufacturer">Texas Instruments</span>
      <span class="package">SOIC-8</span>
      <span class="stock">库存 532</span>
      <span class="moq">1</span>
      <span class="supplier-address">深圳市福田区华强北</span>
      <table class="price-breaks">
        <tr><td>1</td><td>￥12.20</td></tr>
        <tr><td>100</td><td>￥9.80</td></tr>
      </table>
    </div>
  </body>
</html>
```

Create `stock_screener/tests/fixtures/component_search/digikey_tps5430.html`:

```html
<html>
  <body>
    <div class="digikey-result" data-product-url="https://www.digikey.com/en/products/detail/texas-instruments/TPS5430DDA/123">
      <span class="dk-part">296-TPS5430DDA-ND</span>
      <span class="mpn">TPS5430DDA</span>
      <span class="manufacturer">Texas Instruments</span>
      <span class="package">SOIC-8</span>
      <span class="stock">2,420 Available</span>
      <span class="lead-time">Immediate</span>
      <table class="price-breaks">
        <tr><td>1</td><td>$1.39</td></tr>
      </table>
    </div>
  </body>
</html>
```

Create `stock_screener/tests/fixtures/component_search/mouser_tps5430.html`:

```html
<html>
  <body>
    <div class="mouser-result" data-product-url="https://www.mouser.com/ProductDetail/Texas-Instruments/TPS5430DDA">
      <span class="mouser-part">595-TPS5430DDA</span>
      <span class="mpn">TPS5430DDA</span>
      <span class="manufacturer">Texas Instruments</span>
      <span class="package">SOIC-8</span>
      <span class="stock">950 In Stock</span>
      <span class="lead-time">Ships today</span>
      <table class="price-breaks">
        <tr><td>1</td><td>$1.42</td></tr>
        <tr><td>100</td><td>$1.08</td></tr>
      </table>
    </div>
  </body>
</html>
```

- [ ] **Step 3: Run adapter tests to verify they fail**

Run:

```bash
cd /Users/meng.zhou/Desktop/goworkspace/src/github.com/SimonZhou96/MoneyManager
PYTHONPATH=stock_screener python3 -m unittest stock_screener.tests.test_component_search_adapters -v
```

Expected: FAIL because adapter modules are missing.

- [ ] **Step 4: Add beautifulsoup4 dependency**

Modify `stock_screener/requirements.txt`:

```text
beautifulsoup4>=4.12.0
```

Append it after `rq>=1.15.0`.

- [ ] **Step 5: Implement base adapter utilities**

Create `stock_screener/component_search/adapters/base.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

import requests
from bs4 import BeautifulSoup


class BlockedPageError(RuntimeError):
    pass


class SourceFetchError(RuntimeError):
    pass


def soup(html: str) -> BeautifulSoup:
    detect_blocked_page(html)
    return BeautifulSoup(html or "", "html.parser")


def detect_blocked_page(html: str) -> None:
    text = (html or "").lower()
    markers = ["captcha", "access denied", "login required", "verify you are human"]
    if any(marker in text for marker in markers):
        raise BlockedPageError("公开页面要求登录、验证码或访问被拒绝")


def text_or_none(node) -> str | None:
    if node is None:
        return None
    value = node.get_text(" ", strip=True)
    return value or None


@dataclass
class PublicPageClient:
    timeout: int = 15
    headers: Mapping[str, str] | None = None

    def get(self, url: str) -> str:
        response = requests.get(
            url,
            timeout=self.timeout,
            headers=dict(self.headers or {"User-Agent": "Mozilla/5.0 ComponentSearchBot/0.1"}),
        )
        if response.status_code >= 400:
            raise SourceFetchError(f"HTTP {response.status_code} for {url}")
        return response.text
```

- [ ] **Step 6: Implement four source adapters**

Create `stock_screener/component_search/adapters/ti.py`:

```python
from __future__ import annotations

from urllib.parse import quote_plus

from component_search.models import ComponentSearchRequest, PriceBreak, SourceResult
from component_search.normalizer import parse_decimal, parse_int

from .base import PublicPageClient, soup, text_or_none


class TexasInstrumentsAdapter:
    source_name = "Texas Instruments"
    parser_version = "ti-public-v1"

    def __init__(self, client: PublicPageClient | None = None):
        self.client = client or PublicPageClient()

    def search(self, request: ComponentSearchRequest) -> list[SourceResult]:
        url = f"https://www.ti.com/sitesearch/en-us/docs/universalsearch.tsp?searchTerm={quote_plus(request.part_number)}"
        return self.parse_search_html(self.client.get(url), request)

    def parse_search_html(self, html: str, request: ComponentSearchRequest) -> list[SourceResult]:
        page = soup(html)
        rows = []
        for node in page.select(".ti-result"):
            price_breaks = []
            for tr in node.select(".price-breaks tr"):
                cells = tr.find_all("td")
                if len(cells) < 2:
                    continue
                qty = parse_int(text_or_none(cells[0]))
                price = parse_decimal(text_or_none(cells[1]))
                if qty and price is not None:
                    price_breaks.append(PriceBreak(quantity=qty, unit_price=price, currency=request.currency))
            rows.append(
                SourceResult(
                    source_name=self.source_name,
                    source_type="manufacturer_direct",
                    channel_type="原厂直销",
                    manufacturer=text_or_none(node.select_one(".manufacturer")) or "Texas Instruments",
                    generic_part_number=text_or_none(node.select_one(".gpn")),
                    orderable_part_number=text_or_none(node.select_one(".opn")),
                    source_part_number=text_or_none(node.select_one(".opn")),
                    description=text_or_none(node.select_one(".description")),
                    package=text_or_none(node.select_one(".package")),
                    stock_qty=parse_int(text_or_none(node.select_one(".stock"))),
                    price_breaks=price_breaks,
                    currency=request.currency,
                    moq=1,
                    spq=None,
                    lead_time=text_or_none(node.select_one(".lead-time")),
                    supplier_name="Texas Instruments",
                    supplier_address="12500 TI Boulevard, Dallas, TX",
                    source_region=request.region,
                    ship_from_region=None,
                    stock_location_note="TI 官方库存，具体仓库地址未公开",
                    product_url=node.get("data-product-url"),
                    raw_payload={"source": self.source_name},
                )
            )
        return rows
```

Create `stock_screener/component_search/adapters/lcsc.py`:

```python
from __future__ import annotations

from urllib.parse import quote_plus

from component_search.models import ComponentSearchRequest, PriceBreak, SourceResult
from component_search.normalizer import parse_decimal, parse_int

from .base import PublicPageClient, soup, text_or_none


class LCSCAdapter:
    source_name = "LCSC"
    parser_version = "lcsc-public-v1"

    def __init__(self, client: PublicPageClient | None = None):
        self.client = client or PublicPageClient()

    def search(self, request: ComponentSearchRequest) -> list[SourceResult]:
        url = f"https://www.lcsc.com/search?q={quote_plus(request.part_number)}"
        return self.parse_search_html(self.client.get(url), request)

    def parse_search_html(self, html: str, request: ComponentSearchRequest) -> list[SourceResult]:
        page = soup(html)
        rows = []
        for node in page.select(".lcsc-result"):
            price_breaks = []
            for tr in node.select(".price-breaks tr"):
                cells = tr.find_all("td")
                if len(cells) < 2:
                    continue
                qty = parse_int(text_or_none(cells[0]))
                price = parse_decimal(text_or_none(cells[1]))
                if qty and price is not None:
                    price_breaks.append(PriceBreak(quantity=qty, unit_price=price, currency="CNY"))
            rows.append(
                SourceResult(
                    source_name=self.source_name,
                    source_type="distributor",
                    channel_type="授权分销",
                    manufacturer=text_or_none(node.select_one(".manufacturer")),
                    generic_part_number=None,
                    orderable_part_number=text_or_none(node.select_one(".mpn")),
                    source_part_number=text_or_none(node.select_one(".lcsc-number")),
                    description=text_or_none(node.select_one(".description")),
                    package=text_or_none(node.select_one(".package")),
                    stock_qty=parse_int(text_or_none(node.select_one(".stock"))),
                    price_breaks=price_breaks,
                    currency="CNY",
                    moq=parse_int(text_or_none(node.select_one(".moq"))) or 1,
                    spq=None,
                    lead_time=text_or_none(node.select_one(".lead-time")),
                    supplier_name="LCSC",
                    supplier_address=text_or_none(node.select_one(".supplier-address")) or "深圳市福田区华强北",
                    source_region="CN",
                    ship_from_region=None,
                    stock_location_note="页面未公开具体仓库地址",
                    product_url=node.get("data-product-url"),
                    raw_payload={"source": self.source_name},
                )
            )
        return rows
```

Create `stock_screener/component_search/adapters/digikey.py`:

```python
from __future__ import annotations

from urllib.parse import quote_plus

from component_search.models import ComponentSearchRequest, PriceBreak, SourceResult
from component_search.normalizer import parse_decimal, parse_int

from .base import PublicPageClient, soup, text_or_none


class DigiKeyAdapter:
    source_name = "DigiKey"
    parser_version = "digikey-public-v1"

    def __init__(self, client: PublicPageClient | None = None):
        self.client = client or PublicPageClient()

    def search(self, request: ComponentSearchRequest) -> list[SourceResult]:
        url = f"https://www.digikey.com/en/products/result?keywords={quote_plus(request.part_number)}"
        return self.parse_search_html(self.client.get(url), request)

    def parse_search_html(self, html: str, request: ComponentSearchRequest) -> list[SourceResult]:
        page = soup(html)
        rows = []
        for node in page.select(".digikey-result"):
            price_breaks = []
            for tr in node.select(".price-breaks tr"):
                cells = tr.find_all("td")
                if len(cells) < 2:
                    continue
                qty = parse_int(text_or_none(cells[0]))
                price = parse_decimal(text_or_none(cells[1]))
                if qty and price is not None:
                    price_breaks.append(PriceBreak(quantity=qty, unit_price=price, currency=request.currency))
            rows.append(
                SourceResult(
                    source_name=self.source_name,
                    source_type="distributor",
                    channel_type="授权分销",
                    manufacturer=text_or_none(node.select_one(".manufacturer")),
                    generic_part_number=None,
                    orderable_part_number=text_or_none(node.select_one(".mpn")),
                    source_part_number=text_or_none(node.select_one(".dk-part")),
                    description=text_or_none(node.select_one(".description")),
                    package=text_or_none(node.select_one(".package")),
                    stock_qty=parse_int(text_or_none(node.select_one(".stock"))),
                    price_breaks=price_breaks,
                    currency=request.currency,
                    moq=1,
                    spq=None,
                    lead_time=text_or_none(node.select_one(".lead-time")),
                    supplier_name="DigiKey",
                    supplier_address="701 Brooks Avenue South, Thief River Falls, MN",
                    source_region=request.region,
                    ship_from_region=None,
                    stock_location_note="页面未公开具体仓库地址",
                    product_url=node.get("data-product-url"),
                    raw_payload={"source": self.source_name},
                )
            )
        return rows
```

Create `stock_screener/component_search/adapters/mouser.py`:

```python
from __future__ import annotations

from urllib.parse import quote_plus

from component_search.models import ComponentSearchRequest, PriceBreak, SourceResult
from component_search.normalizer import parse_decimal, parse_int

from .base import PublicPageClient, soup, text_or_none


class MouserAdapter:
    source_name = "Mouser"
    parser_version = "mouser-public-v1"

    def __init__(self, client: PublicPageClient | None = None):
        self.client = client or PublicPageClient()

    def search(self, request: ComponentSearchRequest) -> list[SourceResult]:
        url = f"https://www.mouser.com/c/?q={quote_plus(request.part_number)}"
        return self.parse_search_html(self.client.get(url), request)

    def parse_search_html(self, html: str, request: ComponentSearchRequest) -> list[SourceResult]:
        page = soup(html)
        rows = []
        for node in page.select(".mouser-result"):
            price_breaks = []
            for tr in node.select(".price-breaks tr"):
                cells = tr.find_all("td")
                if len(cells) < 2:
                    continue
                qty = parse_int(text_or_none(cells[0]))
                price = parse_decimal(text_or_none(cells[1]))
                if qty and price is not None:
                    price_breaks.append(PriceBreak(quantity=qty, unit_price=price, currency=request.currency))
            rows.append(
                SourceResult(
                    source_name=self.source_name,
                    source_type="distributor",
                    channel_type="授权分销",
                    manufacturer=text_or_none(node.select_one(".manufacturer")),
                    generic_part_number=None,
                    orderable_part_number=text_or_none(node.select_one(".mpn")),
                    source_part_number=text_or_none(node.select_one(".mouser-part")),
                    description=text_or_none(node.select_one(".description")),
                    package=text_or_none(node.select_one(".package")),
                    stock_qty=parse_int(text_or_none(node.select_one(".stock"))),
                    price_breaks=price_breaks,
                    currency=request.currency,
                    moq=1,
                    spq=None,
                    lead_time=text_or_none(node.select_one(".lead-time")),
                    supplier_name="Mouser",
                    supplier_address="1000 North Main Street, Mansfield, TX",
                    source_region=request.region,
                    ship_from_region=None,
                    stock_location_note="页面未公开具体仓库地址",
                    product_url=node.get("data-product-url"),
                    raw_payload={"source": self.source_name},
                )
            )
        return rows
```

- [ ] **Step 7: Run adapter tests**

Run:

```bash
cd /Users/meng.zhou/Desktop/goworkspace/src/github.com/SimonZhou96/MoneyManager
PYTHONPATH=stock_screener python3 -m unittest stock_screener.tests.test_component_search_adapters -v
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add stock_screener/requirements.txt \
  stock_screener/component_search/adapters \
  stock_screener/tests/fixtures/component_search \
  stock_screener/tests/test_component_search_adapters.py
git commit -m "feat: add component source adapters"
```

## Task 4: FastAPI Component Search Routes

**Files:**
- Create: `stock_screener/web/components.py`
- Modify: `stock_screener/web/main.py`
- Test: `stock_screener/tests/test_component_search_api.py`

- [ ] **Step 1: Write failing API route tests**

Create `stock_screener/tests/test_component_search_api.py`:

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from component_search.models import ComponentSearchRequest
from web.auth import CurrentUser, require_user
from web.components import get_component_search_service, router


class FakeComponentService:
    def __init__(self):
        self.request = None

    def submit_search(self, request: ComponentSearchRequest, *, user_id, run_inline=False):
        self.request = request
        return {"task_id": "task-1", "status": "queued", "cached": False}

    def run_task(self, task_id):
        return None

    def get_task(self, task_id):
        return {
            "task_id": task_id,
            "status": "completed",
            "request": {"part_number": "TPS5430", "quantity": 10, "currency": "USD", "region": "US"},
            "source_statuses": [{"source_name": "Texas Instruments", "status": "success", "elapsed_ms": 5}],
            "error_message": None,
        }

    def get_results(self, task_id):
        return {
            "task_id": task_id,
            "status": "completed",
            "rows": [
                {
                    "source_name": "Texas Instruments",
                    "channel_type": "原厂直销",
                    "normalized_mpn": "TPS5430DDA",
                    "stock_location_note": "TI 官方库存，具体仓库地址未公开",
                }
            ],
        }

    def export_csv(self, task_id):
        return "source_name,normalized_mpn\nTexas Instruments,TPS5430DDA\n"


class ComponentSearchApiTest(unittest.TestCase):
    def setUp(self):
        self.service = FakeComponentService()
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[require_user] = lambda: CurrentUser(id=9, username="tester", role="admin")
        app.dependency_overrides[get_component_search_service] = lambda: self.service
        self.client = TestClient(app)

    def test_create_search_validates_and_submits_task(self):
        response = self.client.post(
            "/api/components/search",
            json={"part_number": " TPS5430 ", "quantity": 10, "currency": "usd", "region": "us"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["task_id"], "task-1")
        self.assertEqual(self.service.request.part_number, "TPS5430")
        self.assertEqual(self.service.request.currency, "USD")

    def test_empty_part_number_returns_business_error(self):
        response = self.client.post("/api/components/search", json={"part_number": " "})
        self.assertEqual(response.status_code, 400)

    def test_get_task_and_results(self):
        task_response = self.client.get("/api/components/search-tasks/task-1")
        results_response = self.client.get("/api/components/search-tasks/task-1/results")

        self.assertEqual(task_response.status_code, 200)
        self.assertEqual(results_response.status_code, 200)
        self.assertEqual(results_response.json()["rows"][0]["channel_type"], "原厂直销")

    def test_export_csv_returns_attachment(self):
        response = self.client.get("/api/components/search-tasks/task-1/export.csv")
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/csv", response.headers["content-type"])
        self.assertIn("Texas Instruments", response.text)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run API test to verify it fails**

Run:

```bash
cd /Users/meng.zhou/Desktop/goworkspace/src/github.com/SimonZhou96/MoneyManager
PYTHONPATH=stock_screener python3 -m unittest stock_screener.tests.test_component_search_api -v
```

Expected: FAIL because `web.components` is missing.

- [ ] **Step 3: Implement API router**

Create `stock_screener/web/components.py`:

```python
from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends
from fastapi.responses import Response
from pydantic import BaseModel, Field

from component_search.adapters.digikey import DigiKeyAdapter
from component_search.adapters.lcsc import LCSCAdapter
from component_search.adapters.mouser import MouserAdapter
from component_search.adapters.ti import TexasInstrumentsAdapter
from component_search.models import ComponentSearchRequest
from component_search.service import ComponentSearchService

from .auth import CurrentUser, get_db, require_user
from .business import BusinessError


router = APIRouter(prefix="/api/components", tags=["components"])


class ComponentSearchApiRequest(BaseModel):
    part_number: str = Field(min_length=1)
    quantity: int = Field(default=1, ge=1)
    currency: str = "USD"
    region: str = "US"


def get_component_search_service(db=Depends(get_db)) -> ComponentSearchService:
    return ComponentSearchService(
        repository=db,
        adapters=[
            TexasInstrumentsAdapter(),
            LCSCAdapter(),
            DigiKeyAdapter(),
            MouserAdapter(),
        ],
    )


@router.post("/search")
def create_component_search(
    payload: ComponentSearchApiRequest,
    background_tasks: BackgroundTasks,
    user: CurrentUser = Depends(require_user),
    service: ComponentSearchService = Depends(get_component_search_service),
):
    part_number = payload.part_number.strip()
    if not part_number:
        raise BusinessError("COMPONENT_EMPTY_PART_NUMBER", "请输入电子元器件型号")
    request = ComponentSearchRequest(
        part_number=part_number,
        quantity=int(payload.quantity),
        currency=payload.currency.strip().upper() or "USD",
        region=payload.region.strip().upper() or "US",
    )
    result = service.submit_search(request, user_id=user.id)
    if not result.get("cached"):
        background_tasks.add_task(service.run_task, result["task_id"])
    return result


@router.get("/search-tasks/{task_id}")
def get_component_search_task(
    task_id: str,
    user: CurrentUser = Depends(require_user),
    service: ComponentSearchService = Depends(get_component_search_service),
):
    task = service.get_task(task_id)
    if not task:
        raise BusinessError("COMPONENT_TASK_NOT_FOUND", "元器件查询任务不存在")
    return task


@router.get("/search-tasks/{task_id}/results")
def get_component_search_results(
    task_id: str,
    user: CurrentUser = Depends(require_user),
    service: ComponentSearchService = Depends(get_component_search_service),
):
    if not service.get_task(task_id):
        raise BusinessError("COMPONENT_TASK_NOT_FOUND", "元器件查询任务不存在")
    return service.get_results(task_id)


@router.get("/search-tasks/{task_id}/export.csv")
def export_component_search_results(
    task_id: str,
    user: CurrentUser = Depends(require_user),
    service: ComponentSearchService = Depends(get_component_search_service),
):
    if not service.get_task(task_id):
        raise BusinessError("COMPONENT_TASK_NOT_FOUND", "元器件查询任务不存在")
    csv_text = service.export_csv(task_id)
    return Response(
        content=csv_text,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="component-search-{task_id}.csv"'},
    )
```

- [ ] **Step 4: Include router in main app**

Modify `stock_screener/web/main.py` imports:

```python
from .components import router as components_router
```

Add after existing router includes:

```python
app.include_router(components_router)
```

- [ ] **Step 5: Run API tests**

Run:

```bash
cd /Users/meng.zhou/Desktop/goworkspace/src/github.com/SimonZhou96/MoneyManager
PYTHONPATH=stock_screener python3 -m unittest stock_screener.tests.test_component_search_api -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add stock_screener/web/components.py \
  stock_screener/web/main.py \
  stock_screener/tests/test_component_search_api.py
git commit -m "feat: expose component search API"
```

## Task 5: Frontend Component Search Page

**Files:**
- Modify: `stock_screener/web_frontend/src/api.ts`
- Modify: `stock_screener/web_frontend/src/main.tsx`
- Modify: `stock_screener/web_frontend/src/styles.css`
- Test: `stock_screener/tests/test_component_search_frontend.py`

- [ ] **Step 1: Write failing frontend source contract tests**

Create `stock_screener/tests/test_component_search_frontend.py`:

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
MAIN_TSX = ROOT / "web_frontend" / "src" / "main.tsx"
API_TS = ROOT / "web_frontend" / "src" / "api.ts"


class ComponentSearchFrontendTest(unittest.TestCase):
    def test_navigation_has_component_search_page(self):
        source = MAIN_TSX.read_text(encoding="utf-8")
        self.assertIn("元器件资源查询", source)
        self.assertIn("page === 'components'", source)
        self.assertIn("setPage('components')", source)

    def test_frontend_uses_component_search_endpoints(self):
        source = API_TS.read_text(encoding="utf-8") + MAIN_TSX.read_text(encoding="utf-8")
        self.assertIn("/api/components/search", source)
        self.assertIn("/api/components/search-tasks/${taskId}", source)
        self.assertIn("/api/components/search-tasks/${taskId}/results", source)
        self.assertIn("/api/components/search-tasks/${taskId}/export.csv", source)

    def test_location_copy_does_not_claim_physical_address(self):
        source = MAIN_TSX.read_text(encoding="utf-8")
        self.assertIn("供应/发货位置", source)
        self.assertIn("页面未公开具体仓库地址", source)
        self.assertNotIn("当前物理地址", source)

    def test_result_table_contains_required_fields(self):
        source = MAIN_TSX.read_text(encoding="utf-8")
        for label in ["渠道类型", "数据源", "制造商", "库存", "阶梯价", "MOQ/SPQ", "交期", "商品链接", "匹配置信度"]:
            self.assertIn(label, source)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run frontend contract tests to verify failure**

Run:

```bash
cd /Users/meng.zhou/Desktop/goworkspace/src/github.com/SimonZhou96/MoneyManager
PYTHONPATH=stock_screener python3 -m unittest stock_screener.tests.test_component_search_frontend -v
```

Expected: FAIL because the page and endpoints are missing.

- [ ] **Step 3: Add API helper types and functions**

Modify `stock_screener/web_frontend/src/api.ts` by appending:

```ts
export type ComponentPriceBreak = {
  quantity: number
  unit_price: string
  currency: string
}

export type ComponentSourceStatus = {
  source_name: string
  status: string
  elapsed_ms?: number
  result_count?: number
  error_code?: string
  error_message?: string
}

export type ComponentSearchTask = {
  task_id: string
  status: string
  request: {
    part_number: string
    quantity: number
    currency: string
    region: string
  }
  source_statuses?: ComponentSourceStatus[]
  error_message?: string
}

export type ComponentSearchResultRow = {
  source_name: string
  source_type: string
  channel_type: string
  manufacturer?: string
  generic_part_number?: string
  orderable_part_number?: string
  source_part_number?: string
  normalized_mpn: string
  description?: string
  package?: string
  stock_qty?: number
  price_breaks?: ComponentPriceBreak[]
  currency?: string
  moq?: number
  spq?: number
  lead_time?: string
  supplier_name?: string
  supplier_address?: string
  source_region?: string
  ship_from_region?: string
  stock_location_note?: string
  product_url?: string
  match_type: string
  match_confidence: number
}

export type ComponentSearchResultsResponse = {
  task_id: string
  status: string
  rows: ComponentSearchResultRow[]
}

export function createComponentSearch(payload: {
  part_number: string
  quantity: number
  currency: string
  region: string
}) {
  return api<{ task_id: string; status: string; cached: boolean }>('/api/components/search', {
    method: 'POST',
    body: JSON.stringify(payload)
  })
}

export function fetchComponentSearchTask(taskId: string) {
  return api<ComponentSearchTask>(`/api/components/search-tasks/${taskId}`)
}

export function fetchComponentSearchResults(taskId: string) {
  return api<ComponentSearchResultsResponse>(`/api/components/search-tasks/${taskId}/results`)
}

export function componentSearchExportUrl(taskId: string) {
  return `/api/components/search-tasks/${taskId}/export.csv`
}
```

- [ ] **Step 4: Add page state and navigation**

Modify `stock_screener/web_frontend/src/main.tsx`:

1. Extend the import from `./api`:

```ts
import {
  api,
  componentSearchExportUrl,
  createComponentSearch,
  fetchComponentSearchResults,
  fetchComponentSearchTask,
  type ComponentSearchResultRow,
  type ComponentSearchTask
} from './api'
```

2. Extend page state union wherever `page` is initialized:

```ts
const [page, setPage] = useState<'dashboard' | 'screening' | 'code-screening' | 'components' | 'rules' | 'options' | 'quant'>('dashboard')
```

Use the actual union already present in `main.tsx`; add only `'components'`.

3. Add navigation button near other tool pages:

```tsx
<button className={page === 'components' ? 'active' : ''} onClick={() => setPage('components')}>
  元器件资源查询
</button>
```

4. Add render branch:

```tsx
{page === 'components' && <ComponentSearchPage />}
```

- [ ] **Step 5: Add ComponentSearchPage component**

Add this component near other page components in `stock_screener/web_frontend/src/main.tsx`:

```tsx
function formatPriceBreaks(row: ComponentSearchResultRow) {
  if (!row.price_breaks?.length) return '未公开'
  return row.price_breaks.map((item) => `${item.quantity}+ ${item.currency} ${item.unit_price}`).join(' / ')
}

function locationText(row: ComponentSearchResultRow) {
  const parts = [row.ship_from_region, row.supplier_address].filter(Boolean)
  return parts.length ? parts.join(' · ') : '页面未公开具体仓库地址'
}

function ComponentSearchPage() {
  const [partNumber, setPartNumber] = useState('TPS5430')
  const [quantity, setQuantity] = useState(10)
  const [currency, setCurrency] = useState('USD')
  const [region, setRegion] = useState('US')
  const [taskId, setTaskId] = useState<string | null>(null)
  const [task, setTask] = useState<ComponentSearchTask | null>(null)
  const [rows, setRows] = useState<ComponentSearchResultRow[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  async function submit() {
    const trimmed = partNumber.trim()
    if (!trimmed) {
      setError('请输入电子元器件型号')
      return
    }
    setLoading(true)
    setError('')
    try {
      const created = await createComponentSearch({
        part_number: trimmed,
        quantity,
        currency,
        region
      })
      setTaskId(created.task_id)
    } catch (err) {
      setError(err instanceof Error ? err.message : '创建查询任务失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    if (!taskId) return
    let cancelled = false
    async function poll() {
      try {
        const nextTask = await fetchComponentSearchTask(taskId)
        const nextResults = await fetchComponentSearchResults(taskId)
        if (cancelled) return
        setTask(nextTask)
        setRows(nextResults.rows)
        if (nextTask.status === 'queued' || nextTask.status === 'running') {
          window.setTimeout(poll, 1500)
        }
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : '读取查询结果失败')
      }
    }
    poll()
    return () => {
      cancelled = true
    }
  }, [taskId])

  return (
    <>
      <div className="page-header">
        <p className="eyebrow">Components</p>
        <h1>元器件资源查询</h1>
        <p>输入单个型号，并发查询 TI、LCSC、DigiKey、Mouser 的公开价格和库存页面。</p>
      </div>
      <div className="form-grid">
        <label className="field field-wide">
          型号
          <input value={partNumber} onChange={(event) => setPartNumber(event.target.value)} placeholder="例如 TPS5430" />
        </label>
        <label className="field">
          数量
          <input type="number" min={1} value={quantity} onChange={(event) => setQuantity(Number(event.target.value) || 1)} />
        </label>
        <label className="field">
          币种
          <select value={currency} onChange={(event) => setCurrency(event.target.value)}>
            <option value="USD">USD</option>
            <option value="CNY">CNY</option>
          </select>
        </label>
        <label className="field">
          采购地区
          <select value={region} onChange={(event) => setRegion(event.target.value)}>
            <option value="US">US</option>
            <option value="CN">CN</option>
            <option value="EU">EU</option>
          </select>
        </label>
        <button className="primary" disabled={loading} onClick={submit}>{loading ? '查询中' : '开始查询'}</button>
      </div>
      {error && <div className="inline-error">{error}</div>}
      {task && (
        <div className="metric-grid">
          {(task.source_statuses || []).map((source) => (
            <div className="metric" key={source.source_name}>
              <span>{source.source_name}</span>
              <strong><span className={`status ${source.status === 'success' ? 'pass' : source.status === 'failed' ? 'fail' : 'running'}`}>{source.status}</span></strong>
              <small>{source.elapsed_ms || 0} ms · {source.result_count || 0} 条结果</small>
            </div>
          ))}
        </div>
      )}
      <div className="panel">
        <h2>聚合结果</h2>
        {taskId && <a className="link-button" href={componentSearchExportUrl(taskId)}>导出 CSV</a>}
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>渠道类型</th>
                <th>数据源</th>
                <th>制造商</th>
                <th>型号</th>
                <th>库存</th>
                <th>阶梯价</th>
                <th>MOQ/SPQ</th>
                <th>交期</th>
                <th>供应/发货位置</th>
                <th>地址说明</th>
                <th>商品链接</th>
                <th>匹配置信度</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row, index) => (
                <tr key={`${row.source_name}-${row.normalized_mpn}-${index}`}>
                  <td>{row.channel_type}</td>
                  <td>{row.source_name}</td>
                  <td>{row.manufacturer || '未公开'}</td>
                  <td>{row.orderable_part_number || row.generic_part_number || row.normalized_mpn}</td>
                  <td>{row.stock_qty ?? '未公开'}</td>
                  <td>{formatPriceBreaks(row)}</td>
                  <td>{row.moq || '未公开'} / {row.spq || '未公开'}</td>
                  <td>{row.lead_time || '未公开'}</td>
                  <td>{locationText(row)}</td>
                  <td>{row.stock_location_note || '页面未公开具体仓库地址'}</td>
                  <td>{row.product_url ? <a href={row.product_url} target="_blank" rel="noreferrer">打开</a> : '未公开'}</td>
                  <td>{row.match_type} · {row.match_confidence}</td>
                </tr>
              ))}
              {!rows.length && (
                <tr><td className="empty" colSpan={12}>暂无结果</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </>
  )
}
```

- [ ] **Step 6: Add minimal responsive style only if needed**

If the export link needs spacing, append to `stock_screener/web_frontend/src/styles.css`:

```css
.panel > .link-button {
  display: inline-flex;
  align-items: center;
  margin-bottom: 12px;
}
```

- [ ] **Step 7: Run frontend contract and build**

Run:

```bash
cd /Users/meng.zhou/Desktop/goworkspace/src/github.com/SimonZhou96/MoneyManager
PYTHONPATH=stock_screener python3 -m unittest stock_screener.tests.test_component_search_frontend -v
cd stock_screener/web_frontend
npm run build
```

Expected: unittest PASS and `npm run build` exits 0.

- [ ] **Step 8: Commit**

```bash
git add stock_screener/web_frontend/src/api.ts \
  stock_screener/web_frontend/src/main.tsx \
  stock_screener/web_frontend/src/styles.css \
  stock_screener/tests/test_component_search_frontend.py
git commit -m "feat: add component search frontend"
```

## Task 6: Integration Verification and Operational Guardrails

**Files:**
- Modify: `stock_screener/component_search/service.py`
- Modify: `stock_screener/web/components.py`
- Test: existing component search tests

- [ ] **Step 1: Add service test for all-source failure**

Append to `stock_screener/tests/test_component_search_service.py`:

```python
class FailingAdapter:
    source_name = "DigiKey"

    def search(self, request):
        raise RuntimeError("blocked fixture")


class ComponentSearchFailureServiceTest(unittest.TestCase):
    def test_all_sources_failed_marks_task_failed_without_crashing(self):
        repo = FakeComponentRepository()
        service = ComponentSearchService(repository=repo, adapters=[FailingAdapter()])
        created = service.submit_search(
            ComponentSearchRequest(part_number="TPS5430", quantity=1, currency="USD", region="US"),
            user_id=7,
            run_inline=True,
        )

        task = service.get_task(created["task_id"])

        self.assertEqual(task["status"], "failed")
        self.assertEqual(task["source_statuses"][0]["status"], "failed")
        self.assertIn("所有数据源", task["error_message"])
```

- [ ] **Step 2: Add API validation test for quantity**

Append to `stock_screener/tests/test_component_search_api.py`:

```python
    def test_quantity_must_be_positive(self):
        response = self.client.post(
            "/api/components/search",
            json={"part_number": "TPS5430", "quantity": 0},
        )
        self.assertEqual(response.status_code, 422)
```

- [ ] **Step 3: Run focused backend tests**

Run:

```bash
cd /Users/meng.zhou/Desktop/goworkspace/src/github.com/SimonZhou96/MoneyManager
PYTHONPATH=stock_screener python3 -m unittest \
  stock_screener.tests.test_component_search_normalizer \
  stock_screener.tests.test_component_search_matcher \
  stock_screener.tests.test_component_search_adapters \
  stock_screener.tests.test_component_search_service \
  stock_screener.tests.test_component_search_api \
  stock_screener.tests.test_component_search_frontend -v
```

Expected: PASS.

- [ ] **Step 4: Run existing regression shield**

Run:

```bash
cd /Users/meng.zhou/Desktop/goworkspace/src/github.com/SimonZhou96/MoneyManager
PYTHONPATH=stock_screener python3 -m unittest \
  stock_screener.tests.test_code_screening_frontend \
  stock_screener.tests.test_custom_list \
  stock_screener.tests.test_quant_api -v
```

Expected: PASS.

- [ ] **Step 5: Run frontend build**

Run:

```bash
cd /Users/meng.zhou/Desktop/goworkspace/src/github.com/SimonZhou96/MoneyManager/stock_screener/web_frontend
npm run build
```

Expected: TypeScript and Vite build pass.

- [ ] **Step 6: Manual smoke test with live app**

Run the app:

```bash
cd /Users/meng.zhou/Desktop/goworkspace/src/github.com/SimonZhou96/MoneyManager/stock_screener
uvicorn web.main:app --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000` in the in-app browser or local browser. Log in with the local configured web user, open `元器件资源查询`, search `TPS5430`, and verify:

- The page shows `Texas Instruments`, `LCSC`, `DigiKey`, and `Mouser` source status cards.
- A TI result, when returned, appears before distributor rows.
- Rows show `供应/发货位置` and `地址说明`.
- Missing warehouse address appears as `页面未公开具体仓库地址` or `TI 官方库存，具体仓库地址未公开`.
- CSV export downloads a file with one header row and result rows.

- [ ] **Step 7: Commit**

```bash
git add stock_screener/component_search/service.py \
  stock_screener/web/components.py \
  stock_screener/tests/test_component_search_service.py \
  stock_screener/tests/test_component_search_api.py
git commit -m "test: add component search guardrails"
```

## Self-Review

Spec coverage:

- Single-part search is covered by Tasks 4 and 5.
- Four MVP sources are covered by Task 3.
- Texas Instruments as first-class manufacturer-direct source is covered by Tasks 1, 3, and 5.
- Exact/suspected/orderable variant matching is covered by Tasks 1 and 6.
- Stock, price breaks, MOQ/SPQ, lead time, source URL, source status, and timestamp persistence are covered by Tasks 2 through 5.
- Query snapshots and source failure isolation are covered by Tasks 2, 3, and 6.
- CSV export is covered by Tasks 4 and 5.
- Non-misleading address wording is covered by Tasks 1, 3, 5, and 6.

Placeholder scan:

- The plan does not use reserved marker terms or unspecified implementation placeholders.
- Source adapter selectors intentionally parse fixture classes first. Live site selectors can evolve after the MVP contract is stable; block/login/captcha behavior is already defined.

Type consistency:

- `ComponentSearchRequest`, `PriceBreak`, `SourceResult`, `NormalizedComponentResult`, and `SourceRunStatus` are introduced before they are consumed.
- Frontend type names match API helper return shapes.
- Repository method names used by `ComponentSearchService` match the methods planned for `MarketDatabase` and the fake repository.
