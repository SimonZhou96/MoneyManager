# Market Intel Macro Scoring Rule Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a time-aware market-intel macro scoring rule that AI scores from `-100` to `100`, exposes full score details, and combines with technical strategy score at `60% / 40%`.

**Architecture:** Keep hard filters and technical strategies binary. Add a focused macro scoring layer under `stock_screener/market_intel`, then register one new `MarketIntelMacroScoreStrategizer` through the existing rule engine. Wire runtime dependencies through `FilterContext` cache so batch and single-stock flows can reuse market-intel services and avoid repeated provider calls.

**Tech Stack:** Python `unittest`, dataclasses, existing `MarketIntelService`, existing `RuleEngine`, FastAPI routes, React/Vite frontend.

---

## Scope Check

This is one subsystem: a new scoring macro rule inside the existing stock screener. It touches backend models, persistence, rule execution, score aggregation, and frontend display, but all changes serve the same end-to-end rule-chain feature.

## File Structure

- Modify `stock_screener/market_intel/models.py`
  - Add `event_time` to `IntelItem`.
  - Sort intelligence items by `event_time -> published_at -> fetched_at`.
- Modify `stock_screener/sql/017_market_intel.sql`
  - Add `event_time DATETIME(6) NULL` to `market_intel_items`.
- Modify `stock_screener/db.py`
  - Persist and read `event_time`.
  - Add optional screening score columns if absent.
- Modify `stock_screener/market_intel/repository.py`
  - Keep in-memory repository sorting compatible with `event_time`.
- Create `stock_screener/market_intel/macro_scoring.py`
  - Pure data models, time-aware evidence preprocessing, score parsing, score aggregation.
- Create `stock_screener/market_intel/macro_llm.py`
  - AI prompt builder and scorer adapter using existing LLM provider configuration.
- Modify `stock_screener/macro_strategies.py`
  - Add `MarketIntelMacroScoreStrategizer`.
- Modify `stock_screener/rule_engine.py`
  - Register `MarketIntelMacroScoreStrategizer`.
  - Split old `signal_analysis` macro requirement from new market-intel macro scoring requirement.
- Modify `stock_screener/sql/001_screening_rules.sql`
  - Seed `market_intel_macro_score_link` for `HK`, `US`, and `A`.
- Modify `stock_screener/api/screen_service.py`
  - Attach market-intel service and macro scorer to `FilterContext`.
  - Write technical, macro, and final scores to screening result rows.
- Modify `stock_screener/web/single_stock.py`
  - Attach market-intel service and macro scorer for single-stock rule execution.
- Modify `stock_screener/web/main.py`
  - Return score fields and a rule-detail endpoint for selected result rows.
- Modify `stock_screener/web_frontend/src/main.tsx`
  - Add score columns and macro-score detail rendering.
- Modify `stock_screener/web_frontend/src/styles.css`
  - Add restrained styles for score chips, subscore bars, and temporal evidence rows.
- Add tests:
  - `stock_screener/tests/test_market_intel_models.py`
  - `stock_screener/tests/test_market_intel_repository.py`
  - `stock_screener/tests/test_market_intel_macro_scoring.py`
  - `stock_screener/tests/test_rule_engine.py`
  - `stock_screener/tests/test_web_platform.py`
  - `stock_screener/tests/test_code_screening_frontend.py`

## Task 1: Preserve Evidence Time in Market Intel Items

**Files:**
- Modify: `stock_screener/market_intel/models.py`
- Modify: `stock_screener/market_intel/repository.py`
- Modify: `stock_screener/sql/017_market_intel.sql`
- Modify: `stock_screener/db.py`
- Test: `stock_screener/tests/test_market_intel_models.py`
- Test: `stock_screener/tests/test_market_intel_repository.py`

- [ ] **Step 1: Add failing model tests for `event_time` round trip and sort order**

Add these tests to `stock_screener/tests/test_market_intel_models.py`:

```python
from datetime import datetime, timezone

from market_intel.models import IntelItem, StockIntelBundle


def test_intel_item_round_trips_event_time(self):
    item = IntelItem(
        scope_type="stock",
        market="A",
        code="SZ.000001",
        source="eastmoney",
        provider="eastmoney",
        item_type="announcement",
        title="新订单公告",
        summary="公告摘要",
        url="https://example.com/a",
        event_time=datetime(2026, 5, 26, 9, 0, tzinfo=timezone.utc),
        published_at=datetime(2026, 5, 26, 9, 30, tzinfo=timezone.utc),
        fetched_at=datetime(2026, 5, 26, 10, 0, tzinfo=timezone.utc),
        expires_at=datetime(2026, 5, 27, 10, 0, tzinfo=timezone.utc),
        dedupe_key="announcement-1",
    )

    restored = IntelItem.from_dict(item.to_dict())

    self.assertEqual(restored.event_time, item.event_time)
    self.assertEqual(restored.to_dict()["event_time"], "2026-05-26T09:00:00+00:00")


def test_bundle_groups_sort_by_event_time_before_published_time(self):
    older_published_newer_event = IntelItem(
        scope_type="stock",
        market="A",
        code="SZ.000001",
        source="source-a",
        provider="provider-a",
        item_type="market_news",
        title="较新的事件",
        event_time=datetime(2026, 5, 26, 11, 0, tzinfo=timezone.utc),
        published_at=datetime(2026, 5, 26, 8, 0, tzinfo=timezone.utc),
        fetched_at=datetime(2026, 5, 26, 12, 0, tzinfo=timezone.utc),
        expires_at=datetime(2026, 5, 26, 18, 0, tzinfo=timezone.utc),
        dedupe_key="newer-event",
    )
    newer_published_older_event = IntelItem(
        scope_type="stock",
        market="A",
        code="SZ.000001",
        source="source-b",
        provider="provider-b",
        item_type="market_news",
        title="较旧的事件",
        event_time=datetime(2026, 5, 25, 11, 0, tzinfo=timezone.utc),
        published_at=datetime(2026, 5, 26, 10, 0, tzinfo=timezone.utc),
        fetched_at=datetime(2026, 5, 26, 12, 0, tzinfo=timezone.utc),
        expires_at=datetime(2026, 5, 26, 18, 0, tzinfo=timezone.utc),
        dedupe_key="older-event",
    )

    payload = StockIntelBundle(
        market="A",
        code="SZ.000001",
        items=[newer_published_older_event, older_published_newer_event],
        freshness_status="fresh",
    ).to_dict()

    rows = payload["groups"]["market_news"]
    self.assertEqual([row["title"] for row in rows], ["较新的事件", "较旧的事件"])
```

- [ ] **Step 2: Run the focused model tests and verify failure**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. python3 -m unittest stock_screener.tests.test_market_intel_models
```

Expected: failure because `IntelItem.__init__` does not accept `event_time`.

- [ ] **Step 3: Add `event_time` to `IntelItem` and sorting**

In `stock_screener/market_intel/models.py`, update the relevant parts to this shape:

```python
def _item_sort_timestamp(item: "IntelItem") -> float:
    return _datetime_sort_timestamp(item.event_time or item.published_at or item.fetched_at)


@dataclass
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
    event_time: Optional[datetime] = None
    published_at: Optional[datetime] = None
    raw_json: Dict[str, Any] = field(default_factory=dict)
    is_stale: bool = False
```

Add `event_time` to `to_dict()`:

```python
"event_time": datetime_to_json(self.event_time),
```

Add `event_time` to `from_dict()`:

```python
event_time=parse_datetime(data.get("event_time")),
```

- [ ] **Step 4: Persist `event_time` in SQL and DB methods**

In `stock_screener/sql/017_market_intel.sql`, add this column after `url`:

```sql
    event_time DATETIME(6) NULL,
```

In `stock_screener/db.py`, update market-intel item persistence:

```python
event_time = item.get("event_time")
```

Include `event_time` in the `INSERT INTO market_intel_items (...)` column list, values list, and duplicate update list:

```sql
event_time=VALUES(event_time),
```

Include `event_time` in `list_market_intel_items` select output:

```python
SELECT scope_type, market, code, source, provider, item_type, title, summary, url,
       event_time, published_at, raw_json, fetched_at, expires_at, is_stale, dedupe_key
```

```python
"event_time": row[9],
"published_at": row[10],
"raw_json": _decode_json_field(row[11], {}),
"fetched_at": row[12],
"expires_at": row[13],
"is_stale": bool(row[14]),
"dedupe_key": row[15],
```

If `db.py` has an idempotent schema initializer for optional columns, add:

```python
self._ensure_column(
    "market_intel_items",
    "event_time",
    "ALTER TABLE market_intel_items ADD COLUMN event_time DATETIME(6) NULL AFTER url",
)
```

- [ ] **Step 5: Update in-memory repository sort key**

In `stock_screener/market_intel/repository.py`, change the `list_items` sort key to:

```python
rows.sort(
    key=lambda item: str(item.get("event_time") or item.get("published_at") or item.get("fetched_at") or ""),
    reverse=True,
)
```

- [ ] **Step 6: Add repository assertion for SQL containing `event_time`**

Add to `test_deployment_sql_contains_tables_and_indexes` or the closest SQL test in `stock_screener/tests/test_market_intel_repository.py`:

```python
self.assertIn("event_time DATETIME(6) NULL", sql)
```

- [ ] **Step 7: Run focused market-intel tests**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. python3 -m unittest \
  stock_screener.tests.test_market_intel_models \
  stock_screener.tests.test_market_intel_repository
```

Expected: all tests pass.

- [ ] **Step 8: Commit**

```bash
git add stock_screener/market_intel/models.py stock_screener/market_intel/repository.py stock_screener/sql/017_market_intel.sql stock_screener/db.py stock_screener/tests/test_market_intel_models.py stock_screener/tests/test_market_intel_repository.py
git commit -m "feat: preserve market intel event time"
```

## Task 2: Add Pure Macro Scoring Models and Evidence Preprocessor

**Files:**
- Create: `stock_screener/market_intel/macro_scoring.py`
- Test: `stock_screener/tests/test_market_intel_macro_scoring.py`

- [ ] **Step 1: Write failing tests for time-aware preprocessing**

Create `stock_screener/tests/test_market_intel_macro_scoring.py` with:

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import unittest
from datetime import datetime, timezone

from market_intel.macro_scoring import MacroEvidencePreprocessor
from market_intel.models import EvidencePack, IntelItem


def item(title, item_type, event_hour, summary="", published_hour=None):
    published_hour = event_hour if published_hour is None else published_hour
    return IntelItem(
        scope_type="stock",
        market="A",
        code="SZ.000001",
        source="test",
        provider="test",
        item_type=item_type,
        title=title,
        summary=summary,
        event_time=datetime(2026, 5, 26, event_hour, 0, tzinfo=timezone.utc),
        published_at=datetime(2026, 5, 26, published_hour, 10, tzinfo=timezone.utc),
        fetched_at=datetime(2026, 5, 26, 15, 0, tzinfo=timezone.utc),
        expires_at=datetime(2026, 5, 26, 18, 0, tzinfo=timezone.utc),
        dedupe_key=title,
    )


class MacroEvidencePreprocessorTests(unittest.TestCase):
    def test_preprocessor_groups_dimensions_and_sorts_effective_time(self):
        pack = EvidencePack(
            market="A",
            code="SZ.000001",
            structured_items=[
                item("较早利好订单", "announcement", 9, "获得订单"),
                item("较新风险提示", "announcement", 14, "订单延期风险"),
                item("AI 板块走强", "hot_sector", 13, "板块热度上升"),
            ],
        )

        package = MacroEvidencePreprocessor().build(pack, as_of=datetime(2026, 5, 26, 15, 0, tzinfo=timezone.utc))

        self.assertEqual(package.market, "A")
        self.assertEqual(package.code, "SZ.000001")
        self.assertEqual([row.title for row in package.company_events], ["较新风险提示", "较早利好订单"])
        self.assertEqual([row.title for row in package.hot_sectors], ["AI 板块走强"])
        self.assertTrue(any(row.type == "newer_event_reverses_older_signal" for row in package.temporal_findings))
        self.assertEqual(package.company_events[0].age_hours, 1.0)

    def test_missing_time_creates_data_gap(self):
        no_time = IntelItem(
            scope_type="stock",
            market="A",
            code="SZ.000001",
            source="test",
            provider="test",
            item_type="market_news",
            title="无时间新闻",
            fetched_at=datetime(2026, 5, 26, 15, 0, tzinfo=timezone.utc),
            expires_at=datetime(2026, 5, 26, 18, 0, tzinfo=timezone.utc),
            dedupe_key="no-time",
        )
        no_time.event_time = None
        no_time.published_at = None
        pack = EvidencePack(market="A", code="SZ.000001", structured_items=[no_time])

        package = MacroEvidencePreprocessor().build(pack, as_of=datetime(2026, 5, 26, 15, 0, tzinfo=timezone.utc))

        self.assertIn("无时间新闻 缺少 event_time/published_at", package.data_gaps)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the new test and verify failure**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. python3 -m unittest stock_screener.tests.test_market_intel_macro_scoring
```

Expected: import failure because `market_intel.macro_scoring` does not exist.

- [ ] **Step 3: Create macro scoring data models and preprocessor**

Create `stock_screener/market_intel/macro_scoring.py` with these public objects:

```python
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from market_intel.models import EvidencePack, IntelItem, datetime_to_json


DEFAULT_MACRO_SUB_WEIGHTS = {
    "company_event_strength": 0.2,
    "sector_heat": 0.2,
    "news_validation": 0.2,
    "impact_direction": 0.2,
    "source_credibility": 0.1,
    "freshness": 0.1,
}


@dataclass(frozen=True)
class MacroEvidenceRow:
    title: str
    summary: str
    source: str
    provider: str
    item_type: str
    url: str = ""
    event_time: Optional[datetime] = None
    published_at: Optional[datetime] = None
    fetched_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    age_hours: Optional[float] = None
    is_stale: bool = False

    def to_prompt_dict(self) -> Dict[str, Any]:
        return {
            "title": self.title,
            "summary": self.summary,
            "source": self.source,
            "provider": self.provider,
            "item_type": self.item_type,
            "url": self.url,
            "event_time": datetime_to_json(self.event_time),
            "published_at": datetime_to_json(self.published_at),
            "fetched_at": datetime_to_json(self.fetched_at),
            "expires_at": datetime_to_json(self.expires_at),
            "age_hours": self.age_hours,
            "is_stale": self.is_stale,
        }


@dataclass(frozen=True)
class TemporalFinding:
    type: str
    description: str
    older_evidence_title: str = ""
    newer_evidence_title: str = ""

    def to_dict(self) -> Dict[str, str]:
        return {
            "type": self.type,
            "description": self.description,
            "older_evidence_title": self.older_evidence_title,
            "newer_evidence_title": self.newer_evidence_title,
        }


@dataclass(frozen=True)
class MacroEvidencePackage:
    market: str
    code: str
    as_of: datetime
    company_events: List[MacroEvidenceRow] = field(default_factory=list)
    hot_sectors: List[MacroEvidenceRow] = field(default_factory=list)
    company_hot_news: List[MacroEvidenceRow] = field(default_factory=list)
    market_hot_news: List[MacroEvidenceRow] = field(default_factory=list)
    other_items: List[MacroEvidenceRow] = field(default_factory=list)
    temporal_findings: List[TemporalFinding] = field(default_factory=list)
    source_status: Dict[str, Any] = field(default_factory=dict)
    data_gaps: List[str] = field(default_factory=list)

    @property
    def has_scoreable_evidence(self) -> bool:
        return bool(self.company_events or self.hot_sectors or self.company_hot_news or self.market_hot_news or self.other_items)

    def to_prompt_dict(self) -> Dict[str, Any]:
        return {
            "market": self.market,
            "code": self.code,
            "as_of": datetime_to_json(self.as_of),
            "company_events": [row.to_prompt_dict() for row in self.company_events],
            "hot_sectors": [row.to_prompt_dict() for row in self.hot_sectors],
            "company_hot_news": [row.to_prompt_dict() for row in self.company_hot_news],
            "market_hot_news": [row.to_prompt_dict() for row in self.market_hot_news],
            "other_items": [row.to_prompt_dict() for row in self.other_items],
            "temporal_findings": [item.to_dict() for item in self.temporal_findings],
            "source_status": dict(self.source_status),
            "data_gaps": list(self.data_gaps),
        }

    def evidence_digest(self) -> str:
        payload = json.dumps(self.to_prompt_dict(), ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()
```

Add `MacroEvidencePreprocessor`:

```python
class MacroEvidencePreprocessor:
    COMPANY_EVENT_TYPES = {"announcement", "financial", "research_report", "search_document"}
    HOT_SECTOR_TYPES = {"hot_sector", "money_flow", "index_snapshot"}
    NEWS_TYPES = {"market_news", "search_document"}

    def build(self, pack: EvidencePack, *, as_of: Optional[datetime] = None) -> MacroEvidencePackage:
        as_of = as_of or datetime.now(timezone.utc)
        rows = [self._row_from_item(item, as_of) for item in self._all_items(pack)]
        rows = [row for row in rows if row is not None]
        rows.sort(key=_row_sort_key, reverse=True)
        data_gaps = list(pack.data_gaps or [])
        data_gaps.extend(self._time_data_gaps(rows))
        package = MacroEvidencePackage(
            market=pack.market,
            code=pack.code,
            as_of=as_of,
            company_events=[row for row in rows if row.item_type in self.COMPANY_EVENT_TYPES],
            hot_sectors=[row for row in rows if row.item_type in self.HOT_SECTOR_TYPES],
            company_hot_news=[row for row in rows if row.item_type in self.NEWS_TYPES and row.item_type != "hot_sector" and row.url],
            market_hot_news=[row for row in rows if row.item_type == "market_news"],
            other_items=[row for row in rows if row.item_type not in self.COMPANY_EVENT_TYPES | self.HOT_SECTOR_TYPES | self.NEWS_TYPES],
            temporal_findings=self._temporal_findings(rows),
            source_status=dict(pack.source_status or {}),
            data_gaps=_dedupe_strings(data_gaps),
        )
        return package

    def _all_items(self, pack: EvidencePack) -> List[IntelItem]:
        return [*pack.structured_items, *pack.search_documents, *pack.manual_items]

    def _row_from_item(self, item: IntelItem, as_of: datetime) -> MacroEvidenceRow:
        effective_time = _effective_time(item)
        age_hours = None
        if effective_time is not None:
            age_hours = round((as_of - _as_aware_utc(effective_time)).total_seconds() / 3600, 2)
        return MacroEvidenceRow(
            title=item.title,
            summary=item.summary,
            source=item.source,
            provider=item.provider,
            item_type=item.item_type,
            url=item.url,
            event_time=item.event_time,
            published_at=item.published_at,
            fetched_at=item.fetched_at,
            expires_at=item.expires_at,
            age_hours=age_hours,
            is_stale=bool(item.is_stale),
        )
```

Add helpers:

```python
def _effective_time(item: IntelItem) -> Optional[datetime]:
    return item.event_time or item.published_at or item.fetched_at


def _row_sort_key(row: MacroEvidenceRow) -> float:
    value = row.event_time or row.published_at or row.fetched_at
    if value is None:
        return 0.0
    return _as_aware_utc(value).timestamp()


def _as_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _dedupe_strings(values: Iterable[str]) -> List[str]:
    rows: List[str] = []
    seen = set()
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        rows.append(text)
    return rows
```

Implement `_time_data_gaps` and `_temporal_findings`:

```python
    def _time_data_gaps(self, rows: List[MacroEvidenceRow]) -> List[str]:
        gaps = []
        for row in rows:
            if row.event_time is None and row.published_at is None:
                gaps.append(f"{row.title} 缺少 event_time/published_at")
            if row.fetched_at is None:
                gaps.append(f"{row.title} 缺少 fetched_at")
        return gaps

    def _temporal_findings(self, rows: List[MacroEvidenceRow]) -> List[TemporalFinding]:
        company_rows = [row for row in rows if row.item_type in self.COMPANY_EVENT_TYPES]
        if len(company_rows) < 2:
            return []
        newest = company_rows[0]
        older = company_rows[-1]
        newest_text = f"{newest.title} {newest.summary}"
        older_text = f"{older.title} {older.summary}"
        if _looks_negative(newest_text) and _looks_positive(older_text):
            return [TemporalFinding(
                type="newer_event_reverses_older_signal",
                description="较新的公司事件包含风险或利空表述，可能反转较早利好信号",
                older_evidence_title=older.title,
                newer_evidence_title=newest.title,
            )]
        if _looks_positive(newest_text) and _looks_positive(older_text):
            return [TemporalFinding(
                type="newer_event_confirms_older_signal",
                description="较新的公司事件延续或确认较早利好信号",
                older_evidence_title=older.title,
                newer_evidence_title=newest.title,
            )]
        return []
```

Add keyword helpers:

```python
def _looks_positive(text: str) -> bool:
    return any(word in text for word in ("利好", "增长", "订单", "中标", "突破", "上调", "回购", "盈利"))


def _looks_negative(text: str) -> bool:
    return any(word in text for word in ("利空", "风险", "延期", "下滑", "处罚", "监管", "亏损", "减持"))
```

- [ ] **Step 4: Run macro scoring preprocessor tests**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. python3 -m unittest stock_screener.tests.test_market_intel_macro_scoring
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add stock_screener/market_intel/macro_scoring.py stock_screener/tests/test_market_intel_macro_scoring.py
git commit -m "feat: build time-aware macro evidence packages"
```

## Task 3: Add Macro Score Parser and Weighted Aggregation

**Files:**
- Modify: `stock_screener/market_intel/macro_scoring.py`
- Test: `stock_screener/tests/test_market_intel_macro_scoring.py`

- [ ] **Step 1: Add failing parser and aggregation tests**

Append these tests:

```python
from market_intel.macro_scoring import MacroScoreParser, aggregate_rule_scores


def test_macro_score_parser_clamps_values_and_recomputes_passed(self):
    payload = {
        "macro_score": 120,
        "passed": False,
        "threshold": 60,
        "sub_scores": {
            "company_event_strength": 70,
            "sector_heat": 80,
            "news_validation": 90,
            "impact_direction": 60,
            "source_credibility": 50,
            "freshness": -150,
        },
        "weighted_contribution": {},
        "summary": "偏利好",
        "temporal_summary": "较新事件确认较早信号",
        "risks": ["热度衰减"],
        "evidence_refs": [{"title": "AI 板块", "published_at": "2026-05-26T10:00:00"}],
    }

    result = MacroScoreParser().parse(payload, threshold=60)

    self.assertEqual(result.macro_score, 100.0)
    self.assertTrue(result.passed)
    self.assertEqual(result.sub_scores["freshness"], -100.0)
    self.assertEqual(result.temporal_summary, "较新事件确认较早信号")


def test_aggregate_rule_scores_combines_technical_and_macro_scores(self):
    outputs = [
        {"rule_type": "strategy", "strategy_category": "technical", "result": "pass", "details": {}},
        {"rule_type": "strategy", "strategy_category": "technical", "result": "fail", "details": {}},
        {"rule_type": "strategy", "strategy_category": "macro", "result": "pass", "details": {"macro_score": 80}},
    ]

    score = aggregate_rule_scores(outputs, technical_weight=0.6, macro_weight=0.4)

    self.assertEqual(score["technical_score"], 50.0)
    self.assertEqual(score["macro_score"], 80.0)
    self.assertEqual(score["final_score"], 62.0)
```

- [ ] **Step 2: Run and verify failure**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. python3 -m unittest stock_screener.tests.test_market_intel_macro_scoring
```

Expected: failure because `MacroScoreParser` and `aggregate_rule_scores` do not exist.

- [ ] **Step 3: Add parser result model**

Add to `macro_scoring.py`:

```python
@dataclass(frozen=True)
class MacroScoreResult:
    macro_score: float
    passed: bool
    threshold: float
    sub_scores: Dict[str, float]
    weighted_contribution: Dict[str, float]
    summary: str
    temporal_summary: str = ""
    risks: List[str] = field(default_factory=list)
    evidence_refs: List[Dict[str, Any]] = field(default_factory=list)
    raw: Dict[str, Any] = field(default_factory=dict)

    def to_details(self) -> Dict[str, Any]:
        return {
            "macro_score": self.macro_score,
            "passed": self.passed,
            "threshold": self.threshold,
            "sub_scores": dict(self.sub_scores),
            "weighted_contribution": dict(self.weighted_contribution),
            "summary": self.summary,
            "temporal_summary": self.temporal_summary,
            "risks": list(self.risks),
            "evidence_refs": list(self.evidence_refs),
        }
```

Add parser:

```python
class MacroScoreParser:
    def parse(self, payload: Dict[str, Any], *, threshold: float) -> MacroScoreResult:
        if not isinstance(payload, dict):
            raise ValueError("macro score payload must be a JSON object")
        macro_score = _clamp_score(payload.get("macro_score"))
        sub_scores = {
            key: _clamp_score(value)
            for key, value in dict(payload.get("sub_scores") or {}).items()
        }
        for key in DEFAULT_MACRO_SUB_WEIGHTS:
            sub_scores.setdefault(key, 0.0)
        return MacroScoreResult(
            macro_score=macro_score,
            passed=macro_score >= threshold,
            threshold=float(threshold),
            sub_scores=sub_scores,
            weighted_contribution={
                key: _safe_float(value)
                for key, value in dict(payload.get("weighted_contribution") or {}).items()
            },
            summary=str(payload.get("summary") or ""),
            temporal_summary=str(payload.get("temporal_summary") or ""),
            risks=[str(item) for item in payload.get("risks") or [] if str(item).strip()],
            evidence_refs=[dict(item) for item in payload.get("evidence_refs") or [] if isinstance(item, dict)],
            raw=dict(payload),
        )
```

Add helpers:

```python
def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _clamp_score(value: Any) -> float:
    return max(-100.0, min(100.0, _safe_float(value)))
```

- [ ] **Step 4: Add score aggregation helper**

Add:

```python
def aggregate_rule_scores(
    rows: Iterable[Dict[str, Any]],
    *,
    technical_weight: float = 0.6,
    macro_weight: float = 0.4,
) -> Dict[str, Any]:
    technical_values: List[float] = []
    macro_values: List[float] = []
    for row in rows:
        category = str(row.get("strategy_category") or "").lower()
        rule_type = str(row.get("rule_type") or "").lower()
        result = str(row.get("result") or "").lower()
        details = row.get("details") if isinstance(row.get("details"), dict) else {}
        if rule_type == "strategy" and category != "macro":
            technical_values.append(100.0 if result == "pass" else 0.0)
        if category == "macro" and details.get("macro_score") is not None:
            macro_values.append(_clamp_score(details.get("macro_score")))
    technical_score = round(sum(technical_values) / len(technical_values), 2) if technical_values else None
    macro_score = round(sum(macro_values) / len(macro_values), 2) if macro_values else None
    final_score = None
    if technical_score is not None and macro_score is not None:
        final_score = round(technical_score * technical_weight + macro_score * macro_weight, 2)
    elif technical_score is not None:
        final_score = technical_score
    elif macro_score is not None:
        final_score = macro_score
    return {
        "technical_score": technical_score,
        "macro_score": macro_score,
        "final_score": final_score,
        "technical_weight": technical_weight,
        "macro_weight": macro_weight,
    }
```

- [ ] **Step 5: Run parser and aggregation tests**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. python3 -m unittest stock_screener.tests.test_market_intel_macro_scoring
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add stock_screener/market_intel/macro_scoring.py stock_screener/tests/test_market_intel_macro_scoring.py
git commit -m "feat: parse macro scores and aggregate rule scores"
```

## Task 4: Add AI Macro Scorer Adapter

**Files:**
- Create: `stock_screener/market_intel/macro_llm.py`
- Modify: `stock_screener/market_intel/macro_scoring.py`
- Test: `stock_screener/tests/test_market_intel_macro_scoring.py`

- [ ] **Step 1: Add failing scorer test with fake JSON client**

Append:

```python
from market_intel.macro_llm import MacroScorePromptBuilder, MacroScoreLLMScorer


class FakeJsonClient:
    model_name = "fake-model"

    def __init__(self):
        self.calls = []

    def complete_json(self, *, system_prompt, user_prompt, json_schema):
        self.calls.append({
            "system_prompt": system_prompt,
            "user_prompt": user_prompt,
            "json_schema": json_schema,
        })
        return {
            "macro_score": 61,
            "passed": False,
            "threshold": 60,
            "sub_scores": {
                "company_event_strength": 60,
                "sector_heat": 70,
                "news_validation": 50,
                "impact_direction": 70,
                "source_credibility": 60,
                "freshness": 65,
            },
            "weighted_contribution": {},
            "summary": "宏观共振刚达到阈值",
            "temporal_summary": "较新事件未反转较早信号",
            "risks": [],
            "evidence_refs": [],
        }


def test_macro_llm_scorer_builds_prompt_and_recomputes_passed(self):
    pack = EvidencePack(
        market="A",
        code="SZ.000001",
        structured_items=[item("AI 订单公告", "announcement", 10, "新增 AI 订单")],
    )
    package = MacroEvidencePreprocessor().build(pack, as_of=datetime(2026, 5, 26, 15, 0, tzinfo=timezone.utc))
    client = FakeJsonClient()

    result = MacroScoreLLMScorer(client).score(package, threshold=60)

    self.assertEqual(result.macro_score, 61)
    self.assertTrue(result.passed)
    self.assertIn("不能编造事实", client.calls[0]["system_prompt"])
    self.assertIn("event_time", client.calls[0]["user_prompt"])
```

- [ ] **Step 2: Run and verify failure**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. python3 -m unittest stock_screener.tests.test_market_intel_macro_scoring
```

Expected: import failure because `market_intel.macro_llm` does not exist.

- [ ] **Step 3: Create `macro_llm.py` prompt builder and scorer**

Create `stock_screener/market_intel/macro_llm.py`:

```python
from __future__ import annotations

import json
from typing import Any, Dict, Protocol

from market_intel.macro_scoring import MacroEvidencePackage, MacroScoreParser, MacroScoreResult


class JsonCompletionClient(Protocol):
    model_name: str

    def complete_json(self, *, system_prompt: str, user_prompt: str, json_schema: Dict[str, Any]) -> Dict[str, Any]:
        ...


class MacroScorePromptBuilder:
    @staticmethod
    def system_prompt() -> str:
        return (
            "你是股票筛选系统中的宏观证据评分器。你只能根据输入的 market_intel 证据评分，不能编造事实。"
            "每条证据的 event_time、published_at、fetched_at、expires_at、age_hours、is_stale 都会影响可信度。"
            "如果同一公司存在时间不同且方向相反的事件，必须说明较新事件是否反转或削弱较早信号。"
            "输出必须是 JSON object，分数范围为 -100 到 100。"
        )

    @staticmethod
    def user_prompt(package: MacroEvidencePackage, *, threshold: float) -> str:
        payload = {
            "task": "对宏观证据进行六维评分，并输出总分、风险、证据引用和时间顺序判断。",
            "threshold": threshold,
            "score_range": "-100 to 100",
            "sub_weights": {
                "company_event_strength": 0.2,
                "sector_heat": 0.2,
                "news_validation": 0.2,
                "impact_direction": 0.2,
                "source_credibility": 0.1,
                "freshness": 0.1,
            },
            "evidence": package.to_prompt_dict(),
            "output_rules": [
                "passed 字段会由代码重算，模型仍需给出初步判断。",
                "证据不足的维度给接近 0 的分数，并在 summary 或 risks 说明。",
                "过期、缺时间、较新利空事件会降低 freshness/source_credibility/impact_direction。",
            ],
        }
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)

    @staticmethod
    def json_schema() -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "macro_score": {"type": "number"},
                "passed": {"type": "boolean"},
                "threshold": {"type": "number"},
                "sub_scores": {"type": "object"},
                "weighted_contribution": {"type": "object"},
                "summary": {"type": "string"},
                "temporal_summary": {"type": "string"},
                "risks": {"type": "array", "items": {"type": "string"}},
                "evidence_refs": {"type": "array", "items": {"type": "object"}},
            },
            "required": ["macro_score", "sub_scores", "summary"],
            "additionalProperties": True,
        }
```

Add scorer:

```python
class MacroScoreLLMScorer:
    def __init__(self, client: JsonCompletionClient, prompt_builder: MacroScorePromptBuilder | None = None):
        self.client = client
        self.prompt_builder = prompt_builder or MacroScorePromptBuilder()
        self.parser = MacroScoreParser()

    @property
    def model_name(self) -> str:
        return str(getattr(self.client, "model_name", ""))

    def score(self, package: MacroEvidencePackage, *, threshold: float = 60) -> MacroScoreResult:
        payload = self.client.complete_json(
            system_prompt=self.prompt_builder.system_prompt(),
            user_prompt=self.prompt_builder.user_prompt(package, threshold=threshold),
            json_schema=self.prompt_builder.json_schema(),
        )
        return self.parser.parse(payload, threshold=threshold)
```

- [ ] **Step 4: Add production JSON client factory**

Add a thin adapter in `macro_llm.py` that uses the existing LLM provider order but sends the macro prompt directly:

```python
class MacroScoreJsonClient:
    def __init__(self, provider: Any):
        self.provider = provider

    @property
    def model_name(self) -> str:
        return str(getattr(self.provider, "model_name", ""))

    def complete_json(self, *, system_prompt: str, user_prompt: str, json_schema: Dict[str, Any]) -> Dict[str, Any]:
        complete_json = getattr(self.provider, "complete_json", None)
        if callable(complete_json):
            return complete_json(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                json_schema=json_schema,
            )
        raise RuntimeError("configured LLM provider does not support macro JSON completion")
```

Then extend concrete providers in `stock_screener/signal_analysis/llm_providers.py` with a `complete_json(...)` method for OpenAI-compatible and Codex Responses providers. Reuse their existing request code paths and existing `_parse_json_content`.

- [ ] **Step 5: Run scorer tests**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. python3 -m unittest stock_screener.tests.test_market_intel_macro_scoring
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add stock_screener/market_intel/macro_llm.py stock_screener/market_intel/macro_scoring.py stock_screener/signal_analysis/llm_providers.py stock_screener/tests/test_market_intel_macro_scoring.py
git commit -m "feat: add AI macro score scorer"
```

## Task 5: Implement `MarketIntelMacroScoreStrategizer`

**Files:**
- Modify: `stock_screener/macro_strategies.py`
- Test: `stock_screener/tests/test_rule_engine.py`

- [ ] **Step 1: Add failing strategizer tests**

Add to `stock_screener/tests/test_rule_engine.py`:

```python
from datetime import datetime, timezone, timedelta

from market_intel.models import IntelItem, StockIntelBundle, MarketIntelBundle


class FakeMarketIntelService:
    def __init__(self):
        self.calls = []

    def get_stock_intel(self, market, code, force_refresh=False):
        self.calls.append(("stock", market, code, force_refresh))
        now = datetime(2026, 5, 26, 10, 0, tzinfo=timezone.utc)
        return StockIntelBundle(
            market=market,
            code=code,
            items=[IntelItem(
                scope_type="stock",
                market=market,
                code=code,
                source="test",
                provider="test",
                item_type="announcement",
                title="AI 订单公告",
                summary="新增 AI 订单",
                event_time=now,
                published_at=now,
                fetched_at=now,
                expires_at=now + timedelta(hours=6),
                dedupe_key="order",
            )],
            freshness_status="fresh",
        ).to_dict()

    def get_market_digest(self, market, force_refresh=False):
        self.calls.append(("market", market, "", force_refresh))
        now = datetime(2026, 5, 26, 10, 0, tzinfo=timezone.utc)
        return MarketIntelBundle(
            market=market,
            items=[],
            freshness_status="empty",
        ).to_dict()


class FakeMacroScorer:
    model_name = "fake"

    def score(self, package, threshold=60):
        from market_intel.macro_scoring import MacroScoreResult
        return MacroScoreResult(
            macro_score=72,
            passed=True,
            threshold=threshold,
            sub_scores={
                "company_event_strength": 70,
                "sector_heat": 60,
                "news_validation": 80,
                "impact_direction": 70,
                "source_credibility": 70,
                "freshness": 80,
            },
            weighted_contribution={},
            summary="宏观共振成立",
            temporal_summary="没有较新事件反转信号",
            risks=[],
            evidence_refs=[],
        )


def test_market_intel_macro_score_rule_passes_and_returns_details(self):
    metadata_items = [
        metadata(
            "market_intel_macro_score_link",
            "strategy",
            "MarketIntelMacroScoreStrategizer",
            strategy_category="macro",
            params={"threshold": 60, "refresh_policy": "cache_or_refresh"},
        ),
    ]
    reg = RuleRegistry.default()
    engine = RuleEngine(metadata_items, chain({"ref": "market_intel_macro_score_link"}), registry=reg)
    context = FilterContext(check_date=date(2026, 5, 26), market="A")
    context.set_cache("market_intel_service", FakeMarketIntelService())
    context.set_cache("macro_score_scorer", FakeMacroScorer())

    result = engine.evaluate_stock(StockInfo(market="A", code="SZ.000001", name="平安银行"), context)

    self.assertTrue(result.passed)
    output = result.filter_outputs[0]
    self.assertEqual(output.result, FilterResult.PASS)
    self.assertEqual(output.details["macro_score"], 72)
    self.assertEqual(output.details["temporal_summary"], "没有较新事件反转信号")
```

- [ ] **Step 2: Run and verify failure**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. python3 -m unittest stock_screener.tests.test_rule_engine.RuleEngineTest.test_market_intel_macro_score_rule_passes_and_returns_details
```

Expected: failure because `MarketIntelMacroScoreStrategizer` is not registered.

- [ ] **Step 3: Add strategizer implementation**

In `stock_screener/macro_strategies.py`, import:

```python
from market_intel.evidence import EvidencePackBuilder
from market_intel.macro_scoring import MacroEvidencePreprocessor
```

Add constants:

```python
_MARKET_INTEL_SERVICE_KEY = "market_intel_service"
_MACRO_SCORE_SCORER_KEY = "macro_score_scorer"
```

Add class:

```python
class MarketIntelMacroScoreStrategizer(Strategizer):
    def __init__(
        self,
        threshold: float = 60,
        refresh_policy: str = "cache_or_refresh",
        name: str = "MarketIntelMacroScoreStrategizer",
        enabled: bool = True,
    ):
        super().__init__(name=name, enabled=enabled)
        self.threshold = float(threshold)
        self.refresh_policy = refresh_policy

    def apply(self, stock: StockInfo, context: FilterContext) -> StrategizerOutput:
        service = context.get_cache(_MARKET_INTEL_SERVICE_KEY)
        scorer = context.get_cache(_MACRO_SCORE_SCORER_KEY)
        if service is None:
            return StrategizerOutput(
                name=self.name,
                satisfied=False,
                result="skip",
                reason="缺少 market_intel_service，无法执行宏观评分",
                details={"code": stock.code},
            )
        if scorer is None:
            return StrategizerOutput(
                name=self.name,
                satisfied=False,
                result="error",
                reason="缺少 macro_score_scorer，无法调用 AI 宏观评分",
                details={"code": stock.code, "error": True},
            )
        force_refresh = self.refresh_policy == "force_refresh"
        pack = EvidencePackBuilder(service).build(
            market=stock.market or context.market,
            code=stock.code,
            force_refresh=force_refresh,
        )
        package = MacroEvidencePreprocessor().build(pack)
        if not package.has_scoreable_evidence:
            return StrategizerOutput(
                name=self.name,
                satisfied=False,
                result="skip",
                reason="缺少可评分的 market_intel 证据",
                details={
                    "code": stock.code,
                    "data_gaps": package.data_gaps,
                    "source_status": package.source_status,
                },
            )
        score = scorer.score(package, threshold=self.threshold)
        details = score.to_details()
        details.update({
            "code": stock.code,
            "model": getattr(scorer, "model_name", ""),
            "evidence_digest": package.evidence_digest(),
            "source_status": package.source_status,
            "data_gaps": package.data_gaps,
            "temporal_findings": [item.to_dict() for item in package.temporal_findings],
        })
        return StrategizerOutput(
            name=self.name,
            satisfied=score.passed,
            result="pass" if score.passed else "fail",
            reason=score.summary or ("宏观评分通过" if score.passed else "宏观评分未达到阈值"),
            details=details,
        )
```

- [ ] **Step 4: Register the strategy**

In `stock_screener/rule_engine.py`, import and register:

```python
from macro_strategies import (
    CompanyEventHotNewsStrategizer,
    CompanyEventHotSectorStrategizer,
    MarketIntelMacroScoreStrategizer,
)
```

```python
registry.register_strategy(
    "MarketIntelMacroScoreStrategizer",
    lambda params: MarketIntelMacroScoreStrategizer(
        threshold=float(params.get("threshold", 60)),
        refresh_policy=str(params.get("refresh_policy") or "cache_or_refresh"),
    ),
)
```

- [ ] **Step 5: Run focused rule test**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. python3 -m unittest stock_screener.tests.test_rule_engine.RuleEngineTest.test_market_intel_macro_score_rule_passes_and_returns_details
```

Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add stock_screener/macro_strategies.py stock_screener/rule_engine.py stock_screener/tests/test_rule_engine.py
git commit -m "feat: add market intel macro score strategizer"
```

## Task 6: Split Macro Runtime Requirements and Seed Rule Metadata

**Files:**
- Modify: `stock_screener/rule_engine.py`
- Modify: `stock_screener/sql/001_screening_rules.sql`
- Test: `stock_screener/tests/test_rule_engine.py`

- [ ] **Step 1: Add failing tests for requirement split and SQL seed**

Add:

```python
def test_rule_engine_splits_signal_analysis_and_market_intel_macro_requirements(self):
    engine = RuleEngine(
        [
            metadata(
                "market_intel_macro_score_link",
                "strategy",
                "MarketIntelMacroScoreStrategizer",
                strategy_category="macro",
            ),
        ],
        chain({"ref": "market_intel_macro_score_link"}),
        registry=RuleRegistry.default(),
    )

    self.assertTrue(engine.requires_market_intel_macro_score())
    self.assertFalse(engine.requires_signal_analysis())
    self.assertTrue(engine.requires_macro_analysis())


def test_deployment_sql_contains_market_intel_macro_score_rule(self):
    sql_path = Path(__file__).resolve().parents[1] / "sql" / "001_screening_rules.sql"
    content = sql_path.read_text(encoding="utf-8")

    self.assertIn("market_intel_macro_score_link", content)
    self.assertIn("MarketIntelMacroScoreStrategizer", content)
    for market in ("HK", "US", "A"):
        self.assertIn(f"('{market}', 'market_intel_macro_score_link'", content)
```

- [ ] **Step 2: Run and verify failure**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. python3 -m unittest stock_screener.tests.test_rule_engine
```

Expected: failure because requirement methods or SQL seed are missing.

- [ ] **Step 3: Add requirement split methods**

In `RuleEngine`, add:

```python
SIGNAL_ANALYSIS_IMPLEMENTATIONS = {
    "CompanyEventHotSectorStrategizer",
    "CompanyEventHotNewsStrategizer",
}

MARKET_INTEL_MACRO_IMPLEMENTATIONS = {
    "MarketIntelMacroScoreStrategizer",
}

def requires_signal_analysis(self) -> bool:
    return self._references_enabled_implementations(self.SIGNAL_ANALYSIS_IMPLEMENTATIONS)

def requires_market_intel_macro_score(self) -> bool:
    return self._references_enabled_implementations(self.MARKET_INTEL_MACRO_IMPLEMENTATIONS)

def requires_macro_analysis(self) -> bool:
    return self.requires_signal_analysis() or self.requires_market_intel_macro_score()

def _references_enabled_implementations(self, implementations: Set[str]) -> bool:
    for rule_key in self.referenced_rule_keys:
        metadata = self.metadata_by_key.get(rule_key)
        if metadata and metadata.enabled and metadata.rule_type == RULE_TYPE_STRATEGY and metadata.implementation in implementations:
            return True
    return False
```

Remove the old broad `requires_macro_analysis` loop after adding the split methods.

- [ ] **Step 4: Add SQL metadata rows**

In `stock_screener/sql/001_screening_rules.sql`, add one row per market:

```sql
('HK', 'market_intel_macro_score_link', 'Market Intel 宏观评分', 'strategy', 'macro', 'MarketIntelMacroScoreStrategizer', '{"threshold": 60, "refresh_policy": "cache_or_refresh", "technical_weight": 0.6, "macro_weight": 0.4}', 1, 230, '基于 market_intel 证据包进行 AI 宏观评分，输出 -100 到 100 的宏观共振分'),
```

Use the same row for `US` and `A`, keeping market value and display order consistent with existing macro rows.

- [ ] **Step 5: Run rule-engine tests**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. python3 -m unittest stock_screener.tests.test_rule_engine
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add stock_screener/rule_engine.py stock_screener/sql/001_screening_rules.sql stock_screener/tests/test_rule_engine.py
git commit -m "feat: register market intel macro score rule"
```

## Task 7: Wire Runtime Services into Batch and Single-Stock Flows

**Files:**
- Modify: `stock_screener/api/screen_service.py`
- Modify: `stock_screener/web/single_stock.py`
- Modify: `stock_screener/signal_analysis/service.py`
- Test: `stock_screener/tests/test_screen_service_strategy_gate.py`
- Test: `stock_screener/tests/test_web_platform.py`

- [ ] **Step 1: Add a test that market-intel macro rules do not trigger old signal analysis**

In `stock_screener/tests/test_web_platform.py`, add a source-level regression test:

```python
def test_single_stock_market_intel_macro_rule_uses_market_intel_runtime_without_signal_analysis_gate(self):
    source = Path("web/single_stock.py").read_text(encoding="utf-8")

    self.assertIn("requires_signal_analysis()", source)
    self.assertIn("requires_market_intel_macro_score()", source)
    self.assertIn("market_intel_service", source)
    self.assertIn("macro_score_scorer", source)
```

In `stock_screener/tests/test_screen_service_strategy_gate.py`, add:

```python
def test_screen_service_wires_market_intel_macro_runtime_separately_from_signal_analysis(self):
    source = Path("api/screen_service.py").read_text(encoding="utf-8")

    self.assertIn("requires_signal_analysis()", source)
    self.assertIn("requires_market_intel_macro_score()", source)
    self.assertIn("market_intel_service", source)
    self.assertIn("macro_score_scorer", source)
```

- [ ] **Step 2: Run and verify failure**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. python3 -m unittest \
  stock_screener.tests.test_web_platform \
  stock_screener.tests.test_screen_service_strategy_gate
```

Expected: failures because runtime wiring has not been added.

- [ ] **Step 3: Add a macro scorer factory helper**

In `stock_screener/signal_analysis/service.py`, add a helper near `build_market_intel_service`:

```python
def build_macro_score_scorer(settings: Optional[AnalysisSettings] = None):
    from market_intel.macro_llm import MacroScoreJsonClient, MacroScoreLLMScorer

    settings = settings or build_settings_from_env()
    llm_provider = LLMProviderFactory.from_env(settings)
    if not getattr(llm_provider, "is_available", False):
        return None
    return MacroScoreLLMScorer(MacroScoreJsonClient(llm_provider))
```

- [ ] **Step 4: Wire batch screening context**

In `stock_screener/api/screen_service.py`, when a `RuleEngine` exists, prepare runtime dependencies:

```python
market_intel_service = None
macro_score_scorer = None
if rule_engine.requires_market_intel_macro_score():
    market_intel_service = build_market_intel_service(mysql_config)
    macro_score_scorer = build_macro_score_scorer()
```

Before `rule_engine.evaluate_stock(...)`, set context cache:

```python
if market_intel_service is not None:
    context.set_cache("market_intel_service", market_intel_service)
if macro_score_scorer is not None:
    context.set_cache("macro_score_scorer", macro_score_scorer)
```

Change old macro signal-analysis loader condition from:

```python
if rule_engine.requires_macro_analysis():
```

to:

```python
if rule_engine.requires_signal_analysis():
```

- [ ] **Step 5: Wire single-stock context**

In `stock_screener/web/single_stock.py`, import:

```python
from market_intel.providers.factory import build_market_intel_providers
from market_intel.repository import MySqlMarketIntelRepository
from market_intel.service import MarketIntelService
from signal_analysis.service import build_macro_score_scorer
```

After creating `context`, add:

```python
if rule_engine.requires_market_intel_macro_score():
    context.set_cache(
        "market_intel_service",
        MarketIntelService(MySqlMarketIntelRepository(db), build_market_intel_providers()),
    )
    scorer = build_macro_score_scorer()
    if scorer is not None:
        context.set_cache("macro_score_scorer", scorer)
```

Change signal-analysis loader condition to:

```python
if rule_engine.requires_signal_analysis():
```

- [ ] **Step 6: Run wiring tests**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. python3 -m unittest \
  stock_screener.tests.test_web_platform \
  stock_screener.tests.test_screen_service_strategy_gate
```

Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add stock_screener/api/screen_service.py stock_screener/web/single_stock.py stock_screener/signal_analysis/service.py stock_screener/tests/test_screen_service_strategy_gate.py stock_screener/tests/test_web_platform.py
git commit -m "feat: wire macro score runtime services"
```

## Task 8: Persist and Return Technical, Macro, and Final Scores

**Files:**
- Modify: `stock_screener/db.py`
- Modify: `stock_screener/api/screen_service.py`
- Modify: `stock_screener/web/main.py`
- Test: `stock_screener/tests/test_web_platform.py`
- Test: `stock_screener/tests/test_screen_service_strategy_gate.py`

- [ ] **Step 1: Add failing source tests for score fields**

Add to `test_web_platform.py`:

```python
def test_screening_results_api_exposes_score_fields(self):
    db_source = Path("db.py").read_text(encoding="utf-8")
    main_source = Path("web/main.py").read_text(encoding="utf-8")

    for field in ("technical_score", "macro_score", "final_score", "score_details"):
        self.assertIn(field, db_source)
        self.assertIn(field, main_source)
```

Add to `test_screen_service_strategy_gate.py`:

```python
def test_screen_service_writes_score_summary_from_rule_details(self):
    source = Path("api/screen_service.py").read_text(encoding="utf-8")

    self.assertIn("aggregate_rule_scores", source)
    self.assertIn("technical_score", source)
    self.assertIn("final_score", source)
```

- [ ] **Step 2: Run and verify failure**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. python3 -m unittest \
  stock_screener.tests.test_web_platform \
  stock_screener.tests.test_screen_service_strategy_gate
```

Expected: failures because score fields are not wired.

- [ ] **Step 3: Add screening result score columns**

In `stock_screener/db.py`, add idempotent column ensures for `screening_results`:

```python
self._ensure_column(
    "screening_results",
    "technical_score",
    "ALTER TABLE screening_results ADD COLUMN technical_score DECIMAL(8,2) NULL AFTER filter_summary",
)
self._ensure_column(
    "screening_results",
    "macro_score",
    "ALTER TABLE screening_results ADD COLUMN macro_score DECIMAL(8,2) NULL AFTER technical_score",
)
self._ensure_column(
    "screening_results",
    "final_score",
    "ALTER TABLE screening_results ADD COLUMN final_score DECIMAL(8,2) NULL AFTER macro_score",
)
self._ensure_column(
    "screening_results",
    "score_details",
    "ALTER TABLE screening_results ADD COLUMN score_details JSON NULL AFTER final_score",
)
```

Update `upsert_screening_results` column list and value extraction:

```python
item.get("technical_score"),
item.get("macro_score"),
item.get("final_score"),
_json_or_none(item.get("score_details") or {}),
```

Update `get_screening_results_by_task` select and row mapping to include:

```python
"technical_score": float(row[index]) if row[index] is not None else None,
"macro_score": float(row[index + 1]) if row[index + 1] is not None else None,
"final_score": float(row[index + 2]) if row[index + 2] is not None else None,
"score_details": _decode_json_field(row[index + 3], {}),
```

- [ ] **Step 4: Compute score summary in screen service**

In `stock_screener/api/screen_service.py`, import:

```python
from market_intel.macro_scoring import aggregate_rule_scores
```

After `filter_details` is built, compute:

```python
score_summary = aggregate_rule_scores([
    {
        "rule_type": item.get("rule_type") or "",
        "strategy_category": item.get("strategy_category") or "",
        "result": item.get("result") or "",
        "details": item.get("details") or {},
    }
    for item in filter_details
])
```

Add to `db_record`:

```python
"technical_score": score_summary.get("technical_score"),
"macro_score": score_summary.get("macro_score"),
"final_score": score_summary.get("final_score"),
"score_details": score_summary,
```

When building `filter_details`, include rule metadata:

```python
"rule_type": metadata.rule_type if metadata else "",
"strategy_category": metadata.strategy_category if metadata else "",
```

- [ ] **Step 5: Return score fields from API**

In `stock_screener/web/main.py`, keep route shape unchanged. The route should return rows from `db.get_screening_results_by_task`; once the DB method includes fields, the frontend receives them.

- [ ] **Step 6: Run score persistence tests**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. python3 -m unittest \
  stock_screener.tests.test_web_platform \
  stock_screener.tests.test_screen_service_strategy_gate
```

Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add stock_screener/db.py stock_screener/api/screen_service.py stock_screener/web/main.py stock_screener/tests/test_web_platform.py stock_screener/tests/test_screen_service_strategy_gate.py
git commit -m "feat: persist macro weighted screening scores"
```

## Task 9: Render Macro Score Details in Frontend

**Files:**
- Modify: `stock_screener/web_frontend/src/main.tsx`
- Modify: `stock_screener/web_frontend/src/styles.css`
- Test: `stock_screener/tests/test_code_screening_frontend.py`

- [ ] **Step 1: Add frontend source tests**

Add to `stock_screener/tests/test_code_screening_frontend.py`:

```python
def test_frontend_renders_macro_score_details_and_temporal_summary(self):
    source = Path("web_frontend/src/main.tsx").read_text(encoding="utf-8")

    self.assertIn("MacroScoreDetails", source)
    self.assertIn("temporal_summary", source)
    self.assertIn("sub_scores", source)
    self.assertIn("final_score", source)
```

- [ ] **Step 2: Run and verify failure**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. python3 -m unittest stock_screener.tests.test_code_screening_frontend
```

Expected: failure because the component does not exist.

- [ ] **Step 3: Extend frontend types**

In `main.tsx`, extend `ScreeningResult`:

```ts
type MacroScoreDetails = {
  macro_score?: number
  threshold?: number
  summary?: string
  temporal_summary?: string
  sub_scores?: Record<string, number>
  risks?: string[]
  evidence_refs?: EvidenceLink[]
  temporal_findings?: Array<Record<string, string>>
}

type ScreeningResult = {
  market: string
  code: string
  name?: string
  is_passed: boolean
  sector?: string
  industry?: string
  filter_summary?: string
  close_price?: number
  technical_score?: number
  macro_score?: number
  final_score?: number
  score_details?: Record<string, unknown>
}
```

- [ ] **Step 4: Add score columns to task table**

Change the task result table columns:

```tsx
<Table rows={results} columns={['is_passed', 'code', 'name', 'sector', 'industry', 'close_price', 'technical_score', 'macro_score', 'final_score', 'filter_summary']} />
```

Add labels:

```ts
technical_score: '技术分',
macro_score: '宏观分',
final_score: '综合分',
score_details: '评分明细',
```

- [ ] **Step 5: Add `MacroScoreDetails` renderer**

Add:

```tsx
function MacroScoreDetails({ details }: { details?: MacroScoreDetails | null }) {
  if (!details || details.macro_score === undefined || details.macro_score === null) return null
  const subScores = Object.entries(details.sub_scores || {})
  return (
    <div className="macro-score-card">
      <div className="macro-score-heading">
        <strong>宏观评分 {formatScore(details.macro_score)}</strong>
        <span>阈值 {displayMissing(details.threshold)}</span>
      </div>
      {details.summary && <p>{details.summary}</p>}
      {details.temporal_summary && <p className="temporal-summary">{details.temporal_summary}</p>}
      {subScores.length > 0 && (
        <div className="subscore-grid">
          {subScores.map(([key, value]) => (
            <div className="subscore-row" key={key}>
              <span>{macroDimensionLabel(key)}</span>
              <meter min={-100} max={100} low={0} high={60} optimum={80} value={Number(value)} />
              <strong>{formatScore(value)}</strong>
            </div>
          ))}
        </div>
      )}
      {(details.risks || []).length > 0 && (
        <ul className="risk-list">
          {(details.risks || []).map(item => <li key={item}>{item}</li>)}
        </ul>
      )}
      <CitationTags links={details.evidence_refs || []} />
    </div>
  )
}
```

Add helpers:

```ts
function formatScore(value?: number | string | null) {
  if (value === undefined || value === null || value === '') return '-'
  const numeric = Number(value)
  return Number.isFinite(numeric) ? numeric.toFixed(1) : String(value)
}

function macroDimensionLabel(key: string) {
  const labels: Record<string, string> = {
    company_event_strength: '公司事件强度',
    sector_heat: '板块热度',
    news_validation: '新闻验证',
    impact_direction: '影响方向',
    source_credibility: '来源可信度',
    freshness: '时效性',
  }
  return labels[key] || key
}
```

- [ ] **Step 6: Add selected row macro details in `TaskDetail`**

Add state:

```tsx
const [selectedResult, setSelectedResult] = useState<ScreeningResult | null>(null)
```

Pass row click to the table:

```tsx
<Table
  rows={results}
  columns={['is_passed', 'code', 'name', 'sector', 'industry', 'close_price', 'technical_score', 'macro_score', 'final_score', 'filter_summary']}
  onRowClick={row => setSelectedResult(row)}
/>
```

Render below the table:

```tsx
{selectedResult && (
  <Panel title={`评分明细 ${selectedResult.code}`}>
    <div className="metric-grid">
      <Metric label="技术分" value={formatScore(selectedResult.technical_score)} />
      <Metric label="宏观分" value={formatScore(selectedResult.macro_score)} />
      <Metric label="综合分" value={formatScore(selectedResult.final_score)} />
    </div>
    <MacroScoreDetails details={(selectedResult.score_details?.macro_details || selectedResult.score_details) as MacroScoreDetails} />
  </Panel>
)}
```

- [ ] **Step 7: Add CSS**

In `styles.css`, add:

```css
.macro-score-card {
  display: grid;
  gap: 12px;
}

.macro-score-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.temporal-summary {
  color: var(--text-muted);
}

.subscore-grid {
  display: grid;
  gap: 8px;
}

.subscore-row {
  display: grid;
  grid-template-columns: 120px minmax(120px, 1fr) 64px;
  align-items: center;
  gap: 10px;
}

.subscore-row meter {
  width: 100%;
}

.risk-list {
  margin: 0;
  padding-left: 18px;
}
```

- [ ] **Step 8: Run frontend source tests and build**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. python3 -m unittest stock_screener.tests.test_code_screening_frontend
cd stock_screener/web_frontend && npm run build
```

Expected: unittest passes and Vite build succeeds.

- [ ] **Step 9: Commit**

```bash
git add stock_screener/web_frontend/src/main.tsx stock_screener/web_frontend/src/styles.css stock_screener/tests/test_code_screening_frontend.py
git commit -m "feat: display macro score details"
```

## Task 10: End-to-End Verification and Browser Patrol

**Files:**
- Modify only files required by failures found during verification.

- [ ] **Step 1: Run backend focused tests**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. python3 -m unittest \
  stock_screener.tests.test_market_intel_models \
  stock_screener.tests.test_market_intel_repository \
  stock_screener.tests.test_market_intel_macro_scoring \
  stock_screener.tests.test_rule_engine \
  stock_screener.tests.test_screen_service_strategy_gate \
  stock_screener.tests.test_web_platform
```

Expected: all selected backend tests pass.

- [ ] **Step 2: Run full backend suite**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. python3 -m unittest discover -s stock_screener/tests -p 'test_*.py'
```

Expected: full suite passes. If unrelated environment tests fail because credentials or live providers are unavailable, capture the exact failing test names and error text, then run the closest deterministic subset listed in Step 1 before reporting.

- [ ] **Step 3: Build frontend**

Run:

```bash
cd stock_screener/web_frontend && npm run build
```

Expected: Vite build exits with code 0.

- [ ] **Step 4: Restart local backend and frontend**

Use the repo's existing commands or scripts identified from current running processes. If the services are already running from this checkout, stop only those processes and restart them.

Backend expected URL:

```text
http://127.0.0.1:8000
```

Frontend expected URL:

```text
http://127.0.0.1:5173
```

- [ ] **Step 5: Browser patrol with in-app browser**

Open:

```text
http://127.0.0.1:5173/
```

Verify:

- Rules page lists `Market Intel 宏观评分`.
- Code screener can select a rule chain containing `market_intel_macro_score_link`.
- Result table shows technical score, macro score, and final score.
- Selecting a result row shows macro score details.
- Macro details show temporal summary and evidence time fields.
- Failed macro score still shows score details.
- Layout has no overlapping text on desktop width.

- [ ] **Step 6: Commit verification fixes**

If verification required code fixes, commit them:

```bash
git status --short
git add stock_screener/market_intel/models.py stock_screener/market_intel/repository.py stock_screener/market_intel/macro_scoring.py stock_screener/market_intel/macro_llm.py stock_screener/macro_strategies.py stock_screener/rule_engine.py stock_screener/api/screen_service.py stock_screener/web/single_stock.py stock_screener/web/main.py stock_screener/web_frontend/src/main.tsx stock_screener/web_frontend/src/styles.css stock_screener/db.py stock_screener/sql/001_screening_rules.sql stock_screener/sql/017_market_intel.sql stock_screener/tests/test_market_intel_models.py stock_screener/tests/test_market_intel_repository.py stock_screener/tests/test_market_intel_macro_scoring.py stock_screener/tests/test_rule_engine.py stock_screener/tests/test_screen_service_strategy_gate.py stock_screener/tests/test_web_platform.py stock_screener/tests/test_code_screening_frontend.py
git commit -m "fix: harden macro score verification flow"
```

If no fixes were required, do not create an empty commit.

## Self-Review Checklist

- Spec coverage:
  - Aggregate macro rule: Tasks 5 and 6.
  - Market-intel primary evidence: Tasks 2, 5, and 7.
  - On-demand stock intel refresh: Task 5.
  - Score range `-100` to `100`: Tasks 3 and 4.
  - Threshold pass/fail: Tasks 3 and 5.
  - Failed score details visible: Tasks 5, 8, and 9.
  - Evidence time and signal reversal: Tasks 1, 2, 4, and 9.
  - Technical/macro final weighting `60/40`: Tasks 3 and 8.
- Placeholder scan:
  - The plan contains concrete file paths, commands, and snippets for every implementation task.
- Type consistency:
  - `market_intel_macro_score_link` maps to `MarketIntelMacroScoreStrategizer`.
  - Runtime context keys are `market_intel_service` and `macro_score_scorer`.
  - Frontend score fields are `technical_score`, `macro_score`, `final_score`, and `score_details`.
