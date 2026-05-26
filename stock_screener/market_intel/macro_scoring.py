from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

try:
    from market_intel.models import EvidencePack, IntelItem, datetime_to_json
except ModuleNotFoundError:
    from stock_screener.market_intel.models import EvidencePack, IntelItem, datetime_to_json


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
        return bool(
            self.company_events
            or self.hot_sectors
            or self.company_hot_news
            or self.market_hot_news
            or self.other_items
        )

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


class MacroEvidencePreprocessor:
    COMPANY_EVENT_TYPES = {"announcement", "financial", "research_report", "search_document"}
    HOT_SECTOR_TYPES = {"hot_sector", "money_flow", "index_snapshot"}
    COMPANY_HOT_NEWS_TYPES = {"company_hot_news", "hot_news", "news"}
    MARKET_HOT_NEWS_TYPES = {"market_news"}

    def build(self, pack: EvidencePack, as_of: Optional[datetime] = None) -> MacroEvidencePackage:
        as_of = _as_aware_utc(as_of or datetime.now(timezone.utc))
        rows = [self._row_from_item(item, as_of) for item in self._all_items(pack)]
        rows.sort(key=_row_sort_key, reverse=True)

        data_gaps = list(pack.data_gaps or [])
        data_gaps.extend(self._time_data_gaps(rows))

        company_event_types = self.COMPANY_EVENT_TYPES
        hot_sector_types = self.HOT_SECTOR_TYPES
        company_hot_news_types = self.COMPANY_HOT_NEWS_TYPES
        market_hot_news_types = self.MARKET_HOT_NEWS_TYPES
        grouped_types = (
            company_event_types
            | hot_sector_types
            | company_hot_news_types
            | market_hot_news_types
        )

        return MacroEvidencePackage(
            market=pack.market,
            code=pack.code,
            as_of=as_of,
            company_events=[row for row in rows if row.item_type in company_event_types],
            hot_sectors=[row for row in rows if row.item_type in hot_sector_types],
            company_hot_news=[row for row in rows if row.item_type in company_hot_news_types],
            market_hot_news=[row for row in rows if row.item_type in market_hot_news_types],
            other_items=[row for row in rows if row.item_type not in grouped_types],
            temporal_findings=self._temporal_findings(rows),
            source_status=dict(pack.source_status or {}),
            data_gaps=_dedupe_strings(data_gaps),
        )

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

    def _time_data_gaps(self, rows: List[MacroEvidenceRow]) -> List[str]:
        gaps = []
        for row in rows:
            title = row.title or "(untitled)"
            if row.event_time is None and row.published_at is None:
                gaps.append(f"{title} 缺少 event_time/published_at")
            if row.fetched_at is None:
                gaps.append(f"{title} 缺少 fetched_at")
        return gaps

    def _temporal_findings(self, rows: List[MacroEvidenceRow]) -> List[TemporalFinding]:
        company_rows = [row for row in rows if row.item_type in self.COMPANY_EVENT_TYPES]
        if len(company_rows) < 2:
            return []

        reversal = None
        confirmation = None
        for newer_index, newer in enumerate(company_rows):
            newer_text = f"{newer.title} {newer.summary}"
            for older in company_rows[newer_index + 1:]:
                older_text = f"{older.title} {older.summary}"
                if reversal is None and _looks_negative(newer_text) and _looks_positive(older_text):
                    reversal = TemporalFinding(
                        type="newer_event_reverses_older_signal",
                        description="较新的公司事件包含风险或利空表述，可能反转较早利好信号",
                        older_evidence_title=older.title,
                        newer_evidence_title=newer.title,
                    )
                if (
                    confirmation is None
                    and _looks_positive(newer_text)
                    and not _looks_negative(newer_text)
                    and _looks_positive(older_text)
                ):
                    confirmation = TemporalFinding(
                        type="newer_event_confirms_older_signal",
                        description="较新的公司事件延续或确认较早利好信号",
                        older_evidence_title=older.title,
                        newer_evidence_title=newer.title,
                    )
                if reversal is not None and confirmation is not None:
                    return [reversal, confirmation]

        findings = []
        if reversal is not None:
            findings.append(reversal)
        if confirmation is not None:
            findings.append(confirmation)
        return findings


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


def _looks_positive(text: str) -> bool:
    return any(word in text for word in ("利好", "增长", "订单", "中标", "突破", "上调", "回购", "盈利"))


def _looks_negative(text: str) -> bool:
    return any(word in text for word in ("利空", "风险", "延期", "下滑", "处罚", "监管", "亏损", "减持"))
