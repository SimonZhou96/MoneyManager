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
    ) -> EvidencePack:
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

        merged_source_status: Dict[str, Any] = {}
        merged_source_status.update(_bundle_source_status(stock_bundle))
        merged_source_status.update(_bundle_source_status(market_bundle))
        merged_source_status.update(dict(source_status or {}))

        return EvidencePack(
            market=market,
            code=code,
            structured_items=[*stock_items, *market_items],
            search_documents=search_intel_items,
            manual_items=manual_intel_items,
            stock_context={"items": [item.to_dict() for item in stock_items]},
            market_context={"items": [item.to_dict() for item in market_items]},
            source_status=merged_source_status,
            data_gaps=[str(item) for item in (data_gaps or []) if str(item).strip()],
            citations=[dict(item) for item in (citations or [])],
        )


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
