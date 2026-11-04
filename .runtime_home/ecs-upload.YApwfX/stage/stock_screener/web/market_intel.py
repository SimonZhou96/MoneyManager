from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from market import normalize_market
from market_intel.evidence import EvidencePackBuilder
from market_intel.providers.factory import build_market_intel_providers
from market_intel.providers.source_registry import source_status_payload
from market_intel.repository import MySqlMarketIntelRepository
from market_intel.service import MarketIntelService

from .auth import CurrentUser, get_db, require_user
from .business import BusinessError
from .single_stock import normalize_stock_code


router = APIRouter(prefix="/api/market-intel", tags=["market-intel"])


class RefreshRequest(BaseModel):
    include_search: bool = False
    max_items_per_group: int = Field(default=20, ge=1, le=100)


class EvidencePackPreviewRequest(BaseModel):
    market: str
    code: str
    include_search: bool = False
    force_refresh: bool = False


def get_market_intel_service(db=Depends(get_db)) -> MarketIntelService:
    return MarketIntelService(
        MySqlMarketIntelRepository(db),
        build_market_intel_providers(),
    )


@router.get("/sources")
def list_sources(user: CurrentUser = Depends(require_user)) -> Dict[str, Any]:
    _ = user
    return {"sources": source_status_payload()}


@router.get("/stocks/{market}/{code}")
def get_stock_intel(
    market: str,
    code: str,
    refresh: bool = Query(default=False),
    include_search: bool = Query(default=False),
    max_items_per_group: int = Query(default=20, ge=1, le=100),
    user: CurrentUser = Depends(require_user),
    service: MarketIntelService = Depends(get_market_intel_service),
) -> Dict[str, Any]:
    _ = user
    _reject_include_search(include_search)
    try:
        normalized_market = normalize_market(market)
        normalized_code = normalize_stock_code(normalized_market, code)
        payload = service.get_stock_intel(
            normalized_market,
            normalized_code,
            force_refresh=refresh,
        )
        return _trim_groups(payload, max_items_per_group)
    except ValueError as exc:
        raise BusinessError("MARKET_INTEL_INVALID_REQUEST", str(exc)) from exc
    except Exception as exc:
        raise BusinessError("MARKET_INTEL_STOCK_FAILED", f"获取个股情报失败: {exc}") from exc


@router.post("/stocks/{market}/{code}/refresh")
def refresh_stock_intel(
    market: str,
    code: str,
    payload: RefreshRequest,
    user: CurrentUser = Depends(require_user),
    service: MarketIntelService = Depends(get_market_intel_service),
) -> Dict[str, Any]:
    _ = user
    _reject_include_search(payload.include_search)
    try:
        normalized_market = normalize_market(market)
        normalized_code = normalize_stock_code(normalized_market, code)
        result = service.get_stock_intel(
            normalized_market,
            normalized_code,
            force_refresh=True,
        )
        return _trim_groups(result, payload.max_items_per_group)
    except ValueError as exc:
        raise BusinessError("MARKET_INTEL_INVALID_REQUEST", str(exc)) from exc
    except Exception as exc:
        raise BusinessError("MARKET_INTEL_REFRESH_FAILED", f"刷新个股情报失败: {exc}") from exc


@router.get("/markets/{market}/digest")
def get_market_digest(
    market: str,
    refresh: bool = Query(default=False),
    user: CurrentUser = Depends(require_user),
    service: MarketIntelService = Depends(get_market_intel_service),
) -> Dict[str, Any]:
    _ = user
    try:
        normalized_market = normalize_market(market)
        return service.get_market_digest(normalized_market, force_refresh=refresh)
    except ValueError as exc:
        raise BusinessError("MARKET_INTEL_INVALID_REQUEST", str(exc)) from exc
    except Exception as exc:
        raise BusinessError("MARKET_INTEL_DIGEST_FAILED", f"获取市场摘要失败: {exc}") from exc


@router.get("/provider-runs")
def list_provider_runs(
    provider: Optional[str] = Query(default=None),
    market: Optional[str] = Query(default=None),
    code: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    user: CurrentUser = Depends(require_user),
    service: MarketIntelService = Depends(get_market_intel_service),
) -> Dict[str, Any]:
    _ = user
    if code and not market:
        raise BusinessError("MARKET_INTEL_INVALID_REQUEST", "market is required when code is provided")
    try:
        normalized_market = normalize_market(market) if market else None
        normalized_code = normalize_stock_code(normalized_market, code) if normalized_market and code else code
        return {
            "runs": service.list_provider_runs(
                provider=provider,
                market=normalized_market,
                code=normalized_code,
                status=status,
                limit=limit,
            )
        }
    except ValueError as exc:
        raise BusinessError("MARKET_INTEL_INVALID_REQUEST", str(exc)) from exc
    except Exception as exc:
        raise BusinessError("MARKET_INTEL_PROVIDER_RUNS_FAILED", f"获取 Provider 运行记录失败: {exc}") from exc


@router.post("/evidence-pack/preview")
def preview_evidence_pack(
    payload: EvidencePackPreviewRequest,
    user: CurrentUser = Depends(require_user),
    service: MarketIntelService = Depends(get_market_intel_service),
) -> Dict[str, Any]:
    _ = user
    _reject_include_search(payload.include_search)
    try:
        normalized_market = normalize_market(payload.market)
        normalized_code = normalize_stock_code(normalized_market, payload.code)
        pack = EvidencePackBuilder(service).build(
            market=normalized_market,
            code=normalized_code,
            search_documents=[],
            manual_items=[],
            force_refresh=payload.force_refresh,
        )
        return pack.to_dict()
    except ValueError as exc:
        raise BusinessError("MARKET_INTEL_INVALID_REQUEST", str(exc)) from exc
    except Exception as exc:
        raise BusinessError("MARKET_INTEL_EVIDENCE_PACK_FAILED", f"生成证据包预览失败: {exc}") from exc


def _trim_groups(payload: Dict[str, Any], max_items_per_group: int) -> Dict[str, Any]:
    result = dict(payload or {})
    groups = result.get("groups")
    if not isinstance(groups, dict):
        return result
    result["groups"] = {
        key: values[:max_items_per_group] if isinstance(values, list) else values
        for key, values in groups.items()
    }
    return result


def _reject_include_search(include_search: bool) -> None:
    if include_search:
        raise BusinessError(
            "MARKET_INTEL_INVALID_REQUEST",
            "include_search is not supported in Market Intel v1",
        )
