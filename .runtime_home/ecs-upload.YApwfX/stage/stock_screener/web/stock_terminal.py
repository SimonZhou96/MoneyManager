from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Query

from market import normalize_market
from stock_terminal.providers.factory import build_stock_terminal_providers
from stock_terminal.repository import MySqlStockTerminalRepository
from stock_terminal.service import StockTerminalService
from timeframe import parse_timeframe

from .auth import CurrentUser, get_db, require_user
from .business import BusinessError
from .single_stock import normalize_stock_code


router = APIRouter(prefix="/api/stock-terminal", tags=["stock-terminal"])


def get_stock_terminal_service(db=Depends(get_db)) -> StockTerminalService:
    repository = MySqlStockTerminalRepository(db)
    return StockTerminalService(repository, build_stock_terminal_providers(db=db))


@router.get("/{market}/{code}/summary")
def get_summary(
    market: str,
    code: str,
    user: CurrentUser = Depends(require_user),
    service: StockTerminalService = Depends(get_stock_terminal_service),
) -> Dict[str, Any]:
    _ = user
    normalized_market, normalized_code = _normalize_stock(market, code)
    return service.get_summary(normalized_market, normalized_code)


@router.get("/{market}/{code}/klines")
def get_klines(
    market: str,
    code: str,
    timeframe: str = Query(default="1d"),
    limit: int = Query(default=120, ge=1, le=500),
    before: Optional[str] = Query(default=None),
    user: CurrentUser = Depends(require_user),
    service: StockTerminalService = Depends(get_stock_terminal_service),
) -> Dict[str, Any]:
    _ = user
    try:
        normalized_market, normalized_code = _normalize_stock(market, code)
        normalized_timeframe = parse_timeframe(timeframe)
        before_dt = datetime.fromisoformat(before) if before else None
    except ValueError as exc:
        raise BusinessError("STOCK_TERMINAL_INVALID_REQUEST", str(exc)) from exc
    return service.get_klines(
        normalized_market, normalized_code, normalized_timeframe, limit,
        before=before_dt,
    )


@router.get("/{market}/{code}/minute")
def get_minute(
    market: str,
    code: str,
    user: CurrentUser = Depends(require_user),
    service: StockTerminalService = Depends(get_stock_terminal_service),
) -> Dict[str, Any]:
    _ = user
    normalized_market, normalized_code = _normalize_stock(market, code)
    return service.get_minute(normalized_market, normalized_code)


@router.get("/{market}/{code}/fund-flow")
def get_fund_flow(
    market: str,
    code: str,
    user: CurrentUser = Depends(require_user),
    service: StockTerminalService = Depends(get_stock_terminal_service),
) -> Dict[str, Any]:
    _ = user
    normalized_market, normalized_code = _normalize_stock(market, code)
    return service.get_fund_flow(normalized_market, normalized_code)


def _normalize_stock(market: str, code: str) -> tuple[str, str]:
    try:
        normalized_market = normalize_market(market)
        normalized_code = normalize_stock_code(normalized_market, code)
    except ValueError as exc:
        raise BusinessError("STOCK_TERMINAL_INVALID_REQUEST", str(exc)) from exc
    return normalized_market, normalized_code
