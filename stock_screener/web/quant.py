from __future__ import annotations

from datetime import date
from typing import Any, Dict, List

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from quant_lab.paper import PaperTradingService
from quant_lab.service import QuantLabService

from .auth import CurrentUser, get_db, require_user
from .business import BusinessError


router = APIRouter(prefix="/api/quant", tags=["quant"])


class QuantBacktestRequest(BaseModel):
    market: str
    symbols: List[str] = Field(min_length=1)
    strategy_source: str = "rule_chain"
    entry_chain_key: str
    exit_policy: Dict[str, Any]
    start: date
    end: date
    initial_cash: float = Field(gt=0)
    quantity: int = Field(default=1, ge=1)
    commission_rate: float = Field(default=0.001, ge=0)
    slippage_rate: float = Field(default=0.001, ge=0)
    max_position_weight: float = Field(default=1.0, gt=0, le=1.0)


class CreatePaperAccountRequest(BaseModel):
    name: str = "默认模拟账户"
    initial_cash: float = Field(gt=0)


def get_quant_service(db=Depends(get_db)) -> QuantLabService:
    return QuantLabService(repository=db)


@router.post("/backtests")
def create_backtest(
    payload: QuantBacktestRequest,
    user: CurrentUser = Depends(require_user),
    service: QuantLabService = Depends(get_quant_service),
) -> Dict[str, Any]:
    if payload.end < payload.start:
        raise BusinessError("QUANT_INVALID_DATE_RANGE", "回测结束日期不能早于开始日期")
    return service.submit_backtest(payload.model_dump(mode="json"), user_id=user.id)


@router.post("/paper/accounts")
def create_paper_account(
    payload: CreatePaperAccountRequest,
    user: CurrentUser = Depends(require_user),
    db=Depends(get_db),
) -> Dict[str, Any]:
    service = PaperTradingService(repository=db)
    return service.create_account(user_id=user.id, name=payload.name, initial_cash=payload.initial_cash)
