from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional

from market_intel.models import EvidencePack, IntelItem
from signal_analysis.models import SearchDocument


def flatten_bundle_items(bundle: Any) -> List[IntelItem]:
    """Return IntelItem objects from a MarketIntelService bundle payload."""
    payload = _bundle_payload(bundle)
    groups = payload.get("groups") if isinstance(payload, dict) else None
    if not isinstance(groups, dict):
        return []

    items: List[IntelItem] = []
    for values in groups.values():
        if not isinstance(values, list):
            continue
        for value in values:
            item = _coerce_intel_item(value)
            if item is not None:
                items.append(item)
    return items


def intel_items_to_search_documents(items: Iterable[IntelItem]) -> List[SearchDocument]:
    documents: List[SearchDocument] = []
    for item in items:
        content_parts = [
            f"source={item.source}",
            f"type={item.item_type}",
        ]
        if item.summary:
            content_parts.append(item.summary)
        elif item.title:
            content_parts.append(item.title)
        documents.append(
            SearchDocument(
                title=item.title,
                url=item.url,
                content="\n".join(content_parts),
                query=f"market_intel:{item.scope_type}:{item.market}:{item.code}",
            )
        )
    return documents


class EvidencePackBuilder:
    def __init__(self, service: Any = None):
        self.service = service

    def build(
        self,
        *,
        market: str,
        code: str = "",
        stock_bundle: Any = None,
        market_bundle: Any = None,
        search_documents: Optional[Iterable[SearchDocument]] = None,
        manual_items: Optional[Iterable[Any]] = None,
        source_status: Optional[Dict[str, Any]] = None,
        data_gaps: Optional[Iterable[str]] = None,
        citations: Optional[Iterable[Dict[str, Any]]] = None,
        force_refresh: bool = False,
    ) -> EvidencePack:
        stock_bundle = self._resolve_stock_bundle(market, code, stock_bundle, force_refresh)
        market_bundle = self._resolve_market_bundle(market, market_bundle, force_refresh)
        stock_items = flatten_bundle_items(stock_bundle)
        market_items = flatten_bundle_items(market_bundle)
        manual_intel_items = [
            item for item in (_coerce_intel_item(value) for value in (manual_items or []))
            if item is not None
        ]
        search_intel_items = [
            _search_document_to_intel_item(document, market=market, code=code)
            for document in (search_documents or [])
        ]
        manual_intel_items = _rank_and_dedupe_items(manual_intel_items)
        structured_items = _rank_and_dedupe_items([*stock_items, *market_items])
        structured_keys = {_evidence_key(item) for item in [*manual_intel_items, *structured_items]}
        search_intel_items = _rank_and_dedupe_items([
            item for item in search_intel_items
            if _evidence_key(item) not in structured_keys
        ])

        merged_source_status: Dict[str, Any] = {}
        merged_source_status.update(_bundle_source_status(stock_bundle))
        merged_source_status.update(_bundle_source_status(market_bundle))
        merged_source_status.update(dict(source_status or {}))
        merged_data_gaps = _dedupe_strings([
            *_bundle_data_gaps(stock_bundle, "stock intel", code or market),
            *_bundle_data_gaps(market_bundle, "market intel", market),
            *[str(item) for item in (data_gaps or []) if str(item).strip()],
        ])
        merged_citations = _dedupe_citations([
            *_item_citations([*structured_items, *manual_intel_items, *search_intel_items]),
            *[dict(item) for item in (citations or [])],
        ])

        return EvidencePack(
            market=market,
            code=code,
            structured_items=structured_items,
            search_documents=search_intel_items,
            manual_items=manual_intel_items,
            stock_context={"items": [item.to_dict() for item in _rank_and_dedupe_items(stock_items)]},
            market_context={"items": [item.to_dict() for item in _rank_and_dedupe_items(market_items)]},
            source_status=merged_source_status,
            data_gaps=merged_data_gaps,
            citations=merged_citations,
        )

    def _resolve_stock_bundle(self, market: str, code: str, bundle: Any, force_refresh: bool) -> Any:
        if bundle is not None or self.service is None or not code:
            return bundle
        getter = getattr(self.service, "get_stock_intel", None)
        if not callable(getter):
            return bundle
        return getter(market, code, force_refresh=force_refresh)

    def _resolve_market_bundle(self, market: str, bundle: Any, force_refresh: bool) -> Any:
        if bundle is not None or self.service is None:
            return bundle
        getter = getattr(self.service, "get_market_digest", None)
        if not callable(getter):
            return bundle
        return getter(market, force_refresh=force_refresh)


def _bundle_payload(bundle: Any) -> Dict[str, Any]:
    if bundle is None:
        return {}
    if isinstance(bundle, dict):
        return bundle
    if hasattr(bundle, "to_dict"):
        return bundle.to_dict()
    return {}


def _bundle_source_status(bundle: Any) -> Dict[str, Any]:
    payload = _bundle_payload(bundle)
    source_status = payload.get("source_status") if isinstance(payload, dict) else None
    return dict(source_status or {}) if isinstance(source_status, dict) else {}


def _coerce_intel_item(value: Any) -> Optional[IntelItem]:
    if isinstance(value, IntelItem):
        return value
    if isinstance(value, SearchDocument):
        return _search_document_to_intel_item(value, market="", code="")
    if isinstance(value, dict):
        try:
            return IntelItem.from_dict(value)
        except (KeyError, TypeError, ValueError):
            return None
    return None


def _search_document_to_intel_item(document: SearchDocument, *, market: str, code: str) -> IntelItem:
    now = datetime.now(timezone.utc)
    return IntelItem(
        scope_type="stock" if code else "market",
        market=market,
        code=code,
        source="search",
        provider="signal_analysis",
        item_type="search_document",
        title=document.title,
        summary=document.content,
        url=document.url,
        fetched_at=now,
        expires_at=now + timedelta(hours=2),
        dedupe_key=document.url or document.title,
        raw_json={
            "score": document.score,
            "query": document.query,
        },
    )


def _bundle_data_gaps(bundle: Any, label: str, identity: str) -> List[str]:
    payload = _bundle_payload(bundle)
    if not payload:
        return [f"{label} bundle is missing for {identity}"]

    gaps = []
    freshness = str(payload.get("freshness_status") or "").strip().lower()
    if freshness in {"empty", "stale"}:
        gaps.append(f"{label} bundle is {freshness} for {identity}")
    if not flatten_bundle_items(payload):
        gaps.append(f"{label} bundle has no usable items for {identity}")

    for provider, status in _bundle_source_status(payload).items():
        status_text = str(status.get("status") if isinstance(status, dict) else "").strip().lower()
        if status_text == "failed":
            message = status.get("error_message") if isinstance(status, dict) else ""
            suffix = f": {message}" if message else ""
            gaps.append(f"{label} provider {provider} failed for {identity}{suffix}")
    return gaps


def _item_citations(items: Iterable[IntelItem]) -> List[Dict[str, Any]]:
    citations = []
    for item in items:
        if not item.title and not item.url:
            continue
        citations.append({
            "label": item.title or item.url,
            "url": item.url,
        })
    return citations


def _search_document_citations(documents: Iterable[SearchDocument]) -> List[Dict[str, Any]]:
    citations = []
    for document in documents:
        if not document.title and not document.url:
            continue
        citations.append({
            "label": document.title or document.url,
            "url": document.url,
        })
    return citations


def _dedupe_citations(citations: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows = []
    seen = set()
    for citation in citations:
        label = str(citation.get("label") or citation.get("title") or citation.get("url") or "").strip()
        url = str(citation.get("url") or "").strip()
        if not label and not url:
            continue
        key = (url, label)
        if key in seen:
            continue
        seen.add(key)
        normalized = dict(citation)
        normalized["label"] = label or url
        normalized["url"] = url
        rows.append(normalized)
    return rows


def _dedupe_strings(items: Iterable[str]) -> List[str]:
    rows = []
    seen = set()
    for item in items:
        text = str(item).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        rows.append(text)
    return rows


def _canonical_url(value: str) -> str:
    text = str(value or "").strip()
    if text.endswith("/"):
        text = text[:-1]
    return text


def _canonical_title(value: str) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _evidence_key(item: IntelItem) -> tuple:
    url = _canonical_url(item.url)
    if url:
        return ("url", url)
    title = _canonical_title(item.title)
    published = item.published_at.date().isoformat() if item.published_at else ""
    if published:
        return ("title", title, published)
    return ("title", title)


def _rank_item(item: IntelItem) -> tuple:
    provider_rank = {
        "manual": 0,
        "eastmoney": 1,
        "cailianpress": 2,
        "sina": 3,
        "tradingview": 4,
        "global_index": 5,
        "signal_analysis": 8,
    }.get(item.provider, 6)
    type_rank = {
        "announcement": 0,
        "financial": 1,
        "research_report": 2,
        "market_news": 3,
        "news": 4,
        "search_document": 8,
    }.get(item.item_type, 6)
    published = item.published_at or item.fetched_at
    timestamp = published.timestamp() if published else 0
    return (provider_rank, type_rank, -timestamp, item.title)


def _rank_and_dedupe_items(items: Iterable[IntelItem]) -> List[IntelItem]:
    rows = sorted(list(items), key=_rank_item)
    result: List[IntelItem] = []
    seen = set()
    for item in rows:
        key = _evidence_key(item)
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result
