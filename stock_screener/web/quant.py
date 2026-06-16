from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends
from pydantic import BaseModel, Field

from db import InMemoryQuantRepository, MarketDatabase
from quant_lab.paper import PaperTradingService
from quant_lab.service import QuantLabService

from .auth import CurrentUser, get_db, require_user
from .business import BusinessError
from .config import mysql_config_from_env


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
    background_tasks: BackgroundTasks,
    user: CurrentUser = Depends(require_user),
    service: QuantLabService = Depends(get_quant_service),
) -> Dict[str, Any]:
    if payload.end < payload.start:
        raise BusinessError("QUANT_INVALID_DATE_RANGE", "回测结束日期不能早于开始日期")
    result = service.submit_backtest(payload.model_dump(mode="json"), user_id=user.id)
    background_tasks.add_task(_run_backtest_background, result["run_id"], service)
    return result


@router.get("/backtests/{run_id}")
def get_backtest(
    run_id: str,
    user: CurrentUser = Depends(require_user),
    service: QuantLabService = Depends(get_quant_service),
) -> Dict[str, Any]:
    row = service.get_backtest(run_id, user_id=user.id)
    if not row:
        raise BusinessError("QUANT_BACKTEST_NOT_FOUND", "回测任务不存在")
    return row


def _run_backtest_background(run_id: str, service: QuantLabService) -> None:
    if isinstance(service.repository, InMemoryQuantRepository) or service.data_provider is not None or service.strategy is not None:
        service.run_backtest(run_id)
        return

    db = MarketDatabase(mysql_config_from_env())
    try:
        QuantLabService(repository=db).run_backtest(run_id)
    finally:
        db.close()


@router.post("/paper/accounts")
def create_paper_account(
    payload: CreatePaperAccountRequest,
    user: CurrentUser = Depends(require_user),
    db=Depends(get_db),
) -> Dict[str, Any]:
    service = PaperTradingService(repository=db)
    return service.create_account(user_id=user.id, name=payload.name, initial_cash=payload.initial_cash)


# ── 策略回测 ──

class StrategyBacktestRequest(BaseModel):
    market: str
    symbols: List[str] = Field(min_length=1, max_length=1, description="当前仅支持单只标的")
    strategy: Dict[str, Any]              # {"type": "ma_cross", "params": {...}, "entry_side": "long"}
    start: date
    end: date
    initial_cash: float = Field(gt=0, default=100000)
    quantity: int = Field(default=10, ge=1)
    commission_rate: float = Field(default=0.001, ge=0)
    slippage_rate: float = Field(default=0.001, ge=0)
    max_position_weight: float = Field(default=1.0, gt=0, le=1.0)
    risk: Optional[Dict[str, Any]] = None


class OptimizationRequest(BaseModel):
    market: str
    symbol: str
    strategy_type: str
    param_space: Dict[str, List[float]]
    objective: str = "sharpe"
    start: date
    end: date
    initial_cash: float = Field(gt=0, default=100000)
    quantity: int = Field(default=10, ge=1)


@router.post("/backtests/strategy")
def create_strategy_backtest(
    payload: StrategyBacktestRequest,
    background_tasks: BackgroundTasks,
    user: CurrentUser = Depends(require_user),
    service: QuantLabService = Depends(get_quant_service),
) -> Dict[str, Any]:
    if payload.end < payload.start:
        raise BusinessError("QUANT_INVALID_DATE_RANGE", "回测结束日期不能早于开始日期")
    result = service.submit_strategy_backtest(payload.model_dump(mode="json"), user_id=user.id)
    background_tasks.add_task(_run_strategy_backtest_background, result["run_id"], service)
    return result


@router.post("/backtests/optimize")
def create_optimization(
    payload: OptimizationRequest,
    background_tasks: BackgroundTasks,
    user: CurrentUser = Depends(require_user),
    service: QuantLabService = Depends(get_quant_service),
) -> Dict[str, Any]:
    if payload.end < payload.start:
        raise BusinessError("QUANT_INVALID_DATE_RANGE", "优化结束日期不能早于开始日期")
    total_combos = 1
    for v in payload.param_space.values():
        total_combos *= len(v)
    if total_combos > 200:
        raise BusinessError("QUANT_PARAM_SPACE_TOO_LARGE", f"参数组合过多({total_combos})，请缩小搜索空间（≤200组）")
    result = service.submit_optimization(payload.model_dump(mode="json"), user_id=user.id)
    background_tasks.add_task(_run_optimization_background, result["run_id"], service)
    return result


@router.get("/strategies")
def list_strategies() -> List[Dict[str, Any]]:
    from quant_lab.strategies import list_strategies as _list
    return _list()


def _run_strategy_backtest_background(run_id: str, service: QuantLabService) -> None:
    if isinstance(service.repository, InMemoryQuantRepository) or service.data_provider is not None:
        service.run_strategy_backtest(run_id)
        return
    db = MarketDatabase(mysql_config_from_env())
    try:
        QuantLabService(repository=db).run_strategy_backtest(run_id)
    finally:
        db.close()


def _run_optimization_background(run_id: str, service: QuantLabService) -> None:
    if isinstance(service.repository, InMemoryQuantRepository) or service.data_provider is not None:
        service.run_optimization(run_id)
        return
    db = MarketDatabase(mysql_config_from_env())
    try:
        QuantLabService(repository=db).run_optimization(run_id)
    finally:
        db.close()
