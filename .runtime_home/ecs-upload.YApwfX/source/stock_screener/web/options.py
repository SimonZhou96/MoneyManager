from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from market import normalize_market
from option_lab.market_data import build_default_option_market_data_provider
from option_lab.models import RiskProfile
from option_lab.service import OptionLabService

from .auth import CurrentUser, get_db, require_user
from .business import BusinessError
from .single_stock import normalize_stock_code


router = APIRouter(prefix="/api/options", tags=["options"])


class OptionEvaluateRequest(BaseModel):
    market: str
    code: str
    risk_profile: str = "均衡"
    capital: Optional[float] = None
    max_loss: Optional[float] = None
    planned_holding_days: Optional[int] = None
    strategy_scope: List[str] = Field(default_factory=list)
    enable_macro_analysis: bool = False
    force_macro_refresh: bool = False
    macro_cache_ttl_minutes: int = Field(default=60, ge=1, le=1440)


class OptionBatchEvaluateRequest(BaseModel):
    market: str
    codes: List[str]
    risk_profile: str = "均衡"
    capital: Optional[float] = None
    max_loss: Optional[float] = None
    planned_holding_days: Optional[int] = None
    strategy_scope: List[str] = Field(default_factory=list)
    enable_macro_analysis: bool = False
    force_macro_refresh: bool = False
    macro_cache_ttl_minutes: int = Field(default=60, ge=1, le=1440)


class SaveOrderPlanRequest(BaseModel):
    candidate_id: str


class FillOrderPlanRequest(BaseModel):
    filled_price: float
    quantity: int = 1
    filled_at: str
    fee: Optional[float] = None


def risk_profile_from_request(payload: OptionEvaluateRequest | OptionBatchEvaluateRequest) -> RiskProfile:
    return RiskProfile.from_input(payload.risk_profile)


def get_option_service(db=Depends(get_db)) -> OptionLabService:
    return OptionLabService(repository=db, market_data_provider=build_default_option_market_data_provider())


@router.post("/evaluate")
def evaluate_options(
    payload: OptionEvaluateRequest,
    user: CurrentUser = Depends(require_user),
    service: OptionLabService = Depends(get_option_service),
) -> Dict[str, Any]:
    try:
        market = normalize_market(payload.market)
        code = normalize_stock_code(market, payload.code)
        result = service.evaluate_single(
            market=market,
            code=code,
            risk_profile=risk_profile_from_request(payload),
            user_id=user.id,
            max_loss=payload.max_loss,
            capital=payload.capital,
            planned_holding_days=payload.planned_holding_days,
            strategy_scope=payload.strategy_scope,
            enable_macro_analysis=payload.enable_macro_analysis,
            force_macro_refresh=payload.force_macro_refresh,
            macro_cache_ttl_minutes=payload.macro_cache_ttl_minutes,
        )
    except ValueError as exc:
        raise BusinessError("OPTION_EVALUATE_INVALID", str(exc)) from exc
    except Exception as exc:
        raise BusinessError("OPTION_EVALUATE_FAILED", f"期权评估失败: {exc}") from exc
    return option_result_to_response(result)


@router.post("/evaluate-batch")
def evaluate_options_batch(
    payload: OptionBatchEvaluateRequest,
    user: CurrentUser = Depends(require_user),
    service: OptionLabService = Depends(get_option_service),
) -> Dict[str, Any]:
    try:
        market = normalize_market(payload.market)
        profile = risk_profile_from_request(payload)
    except ValueError as exc:
        raise BusinessError("OPTION_EVALUATE_INVALID", str(exc)) from exc

    parent_run_id = str(uuid.uuid4())
    repo = service.repository
    if hasattr(repo, "save_run"):
        repo.save_run(parent_run_id, {
            "run_id": parent_run_id,
            "mode": "batch",
            "user_id": user.id,
            "market": market,
            "code": None,
            "risk_profile": profile.label,
            "request": model_to_dict(payload),
            "status": "running",
        })
    items = []
    for raw_code in payload.codes:
        code = normalize_stock_code(market, raw_code)
        try:
            result = service.evaluate_single(
                market=market,
                code=code,
                risk_profile=profile,
                user_id=user.id,
                max_loss=payload.max_loss,
                capital=payload.capital,
                planned_holding_days=payload.planned_holding_days,
                strategy_scope=payload.strategy_scope,
                enable_macro_analysis=payload.enable_macro_analysis,
                force_macro_refresh=payload.force_macro_refresh,
                macro_cache_ttl_minutes=payload.macro_cache_ttl_minutes,
            )
            best = result.candidates[0].to_dict() if result.candidates else None
            items.append({
                "run_id": result.run_id,
                "market": market,
                "code": code,
                "status": result.status,
                "最佳策略": best.get("策略名称") if best else "",
                "评分": best.get("评分") if best else None,
                "期权评分": best.get("期权评分") if best else None,
                "宏观分析评分": best.get("宏观分析评分") if best else None,
                "综合评分": best.get("综合评分") if best else None,
                "macro_analysis": result.macro_analysis.to_dict() if result.macro_analysis is not None else None,
                "best_candidate": best,
                "candidates": [item.to_dict() for item in result.candidates],
            })
        except Exception as exc:
            items.append({
                "run_id": "",
                "market": market,
                "code": code,
                "status": "failed",
                "错误": f"期权评估失败: {exc}",
                "best_candidate": None,
                "candidates": [],
            })
    if hasattr(repo, "insert_option_evaluation_items"):
        repo.insert_option_evaluation_items([
            {
                "run_id": parent_run_id,
                "market": item["market"],
                "code": item["code"],
                "status": item["status"],
                "best_strategy": item.get("best_candidate") or {},
                "child_run_id": item.get("run_id"),
                "candidates": item.get("candidates") or [],
                "error_message": item.get("错误"),
            }
            for item in items
        ])
    if hasattr(repo, "finish_option_evaluation_run"):
        repo.finish_option_evaluation_run(parent_run_id, "completed", [], {"status": "ok"})
    return {"run_id": parent_run_id, "status": "completed", "items": items}


@router.get("/evaluations/{run_id}")
def get_evaluation(
    run_id: str,
    user: CurrentUser = Depends(require_user),
    service: OptionLabService = Depends(get_option_service),
) -> Dict[str, Any]:
    repo = service.repository
    if not hasattr(repo, "get_option_evaluation_run"):
        raise BusinessError("OPTION_EVALUATION_NOT_FOUND", "期权评估记录不存在")
    run = repo.get_option_evaluation_run(run_id)
    if not run or run.get("user_id") is None or int(run["user_id"]) != user.id:
        raise BusinessError("OPTION_EVALUATION_NOT_FOUND", "期权评估记录不存在")
    if run.get("mode") == "batch":
        items = repo.list_option_evaluation_items(run_id) if hasattr(repo, "list_option_evaluation_items") else []
        return {"run": run, "items": [evaluation_item_to_response(item) for item in items]}
    candidates = repo.list_option_strategy_candidates(run_id) if hasattr(repo, "list_option_strategy_candidates") else []
    return {"run": run, "candidates": candidates}


@router.post("/order-plans")
def save_order_plan(
    payload: SaveOrderPlanRequest,
    user: CurrentUser = Depends(require_user),
    service: OptionLabService = Depends(get_option_service),
) -> Dict[str, Any]:
    try:
        assert_candidate_owner(service.repository, payload.candidate_id, user.id)
        plan = service.save_order_plan(payload.candidate_id, user_id=user.id)
    except Exception as exc:
        raise BusinessError("OPTION_ORDER_PLAN_FAILED", f"保存订单建议失败: {exc}") from exc
    return plan_to_response(plan)


@router.post("/order-plans/{plan_id}/fills")
def fill_order_plan(
    plan_id: str,
    payload: FillOrderPlanRequest,
    user: CurrentUser = Depends(require_user),
    service: OptionLabService = Depends(get_option_service),
) -> Dict[str, Any]:
    try:
        assert_plan_owner(service.repository, plan_id, user.id)
        return service.record_fill(
            plan_id=plan_id,
            filled_price=payload.filled_price,
            quantity=payload.quantity,
            filled_at=payload.filled_at,
            fee=payload.fee,
        )
    except Exception as exc:
        raise BusinessError("OPTION_FILL_FAILED", f"记录成交失败: {exc}") from exc


@router.get("/positions")
def list_positions(
    user: CurrentUser = Depends(require_user),
    service: OptionLabService = Depends(get_option_service),
) -> Dict[str, Any]:
    repo = service.repository
    if hasattr(repo, "list_option_tracked_positions"):
        return {"positions": repo.list_option_tracked_positions(user_id=user.id)}
    return {"positions": list(getattr(repo, "positions", {}).values())}


@router.get("/positions/{position_id}")
def get_position(
    position_id: str,
    user: CurrentUser = Depends(require_user),
    service: OptionLabService = Depends(get_option_service),
) -> Dict[str, Any]:
    position = service.repository.get_position(position_id)
    if not position or not position_belongs_to_user(position, user.id):
        raise BusinessError("OPTION_POSITION_NOT_FOUND", "期权监控持仓不存在")
    events = []
    if hasattr(service.repository, "list_option_monitor_events"):
        events = service.repository.list_option_monitor_events(position_id=position_id)
    else:
        events = getattr(service.repository, "events", {}).get(position_id, [])
    return {"position": position, "events": events}


@router.post("/positions/{position_id}/refresh")
def refresh_position(
    position_id: str,
    user: CurrentUser = Depends(require_user),
    service: OptionLabService = Depends(get_option_service),
) -> Dict[str, Any]:
    try:
        position = service.repository.get_position(position_id)
        if not position or not position_belongs_to_user(position, user.id):
            raise BusinessError("OPTION_POSITION_NOT_FOUND", "期权监控持仓不存在")
        return {"events": service.refresh_position(position_id)}
    except BusinessError:
        raise
    except Exception as exc:
        raise BusinessError("OPTION_MONITOR_FAILED", f"刷新监控失败: {exc}") from exc


@router.post("/monitor/run")
def run_monitor(
    user: CurrentUser = Depends(require_user),
    service: OptionLabService = Depends(get_option_service),
) -> Dict[str, Any]:
    repo = service.repository
    if hasattr(repo, "list_option_tracked_positions"):
        positions = repo.list_option_tracked_positions(user_id=user.id, status="active")
    else:
        positions = list(getattr(repo, "positions", {}).values())
    events = []
    for position in positions:
        position_id = position.get("position_id")
        if position_id:
            events.extend(service.refresh_position(position_id, send_feishu=True))
    return {"events": events}


def option_result_to_response(result) -> Dict[str, Any]:
    payload = {
        "run_id": result.run_id,
        "market": result.market,
        "code": result.code,
        "status": result.status,
        "风险偏好": result.risk_profile.label,
        "warnings": result.warnings,
        "data_quality": result.data_quality.to_dict(),
        "candidates": [item.to_dict() for item in result.candidates],
    }
    if result.macro_analysis is not None:
        payload["macro_analysis"] = (
            result.macro_analysis.to_dict()
            if hasattr(result.macro_analysis, "to_dict")
            else result.macro_analysis
        )
    return payload


def plan_to_response(plan: Any) -> Dict[str, Any]:
    if isinstance(plan, dict):
        details = plan.get("contract_details") or plan.get("合约明细") or []
        return {
            "plan_id": plan.get("plan_id"),
            "status": plan.get("status"),
            "合约明细": _display_contract_rows(details),
            "order_suggestion": plan.get("order_suggestion") or {},
        }
    return {
        "plan_id": plan.plan_id,
        "status": plan.status,
        "合约明细": [item.to_display_row() for item in plan.contract_details],
        "order_suggestion": plan.order_suggestion,
    }


def _display_contract_rows(details: List[dict]) -> List[dict]:
    rows = []
    for item in details or []:
        if "合约代码" in item:
            rows.append(item)
        else:
            rows.append({
                "买卖方向": item.get("side_label") or item.get("side"),
                "期权类型": item.get("contract_type_label") or item.get("contract_type"),
                "合约代码": item.get("option_code"),
                "到期日": item.get("expiration_date"),
                "行权价": item.get("strike"),
                "建议价格": item.get("suggested_price"),
                "数量": item.get("quantity"),
                "币种": item.get("currency"),
            })
    return rows


def assert_candidate_owner(repo, candidate_id: str, user_id: int) -> None:
    candidate = repo.get_candidate(candidate_id)
    if not candidate:
        raise BusinessError("OPTION_CANDIDATE_NOT_FOUND", "期权策略候选不存在")
    run_id = candidate.get("run_id") if isinstance(candidate, dict) else getattr(candidate, "run_id", None)
    if hasattr(repo, "get_option_evaluation_run") and run_id:
        run = repo.get_option_evaluation_run(run_id)
        if run and run.get("user_id") is not None and int(run["user_id"]) != user_id:
            raise BusinessError("OPTION_CANDIDATE_NOT_FOUND", "期权策略候选不存在")


def assert_plan_owner(repo, plan_id: str, user_id: int) -> None:
    plan = repo.get_order_plan(plan_id)
    if not plan:
        raise BusinessError("OPTION_ORDER_PLAN_NOT_FOUND", "订单建议不存在")
    owner = plan.get("user_id") if isinstance(plan, dict) else None
    if owner is None or int(owner) != user_id:
        raise BusinessError("OPTION_ORDER_PLAN_NOT_FOUND", "订单建议不存在")


def position_belongs_to_user(position: dict, user_id: int) -> bool:
    owner = position.get("user_id")
    return owner is not None and int(owner) == user_id


def model_to_dict(model: BaseModel) -> dict:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


def evaluation_item_to_response(item: dict) -> dict:
    best = item.get("best_strategy") or {}
    return {
        **item,
        "run_id": best.get("child_run_id") or item.get("run_id"),
        "best_candidate": best,
        "candidates": best.get("candidates") or [],
        "最佳策略": best.get("策略名称") or "",
        "评分": best.get("评分"),
        "期权评分": best.get("期权评分"),
        "宏观分析评分": best.get("宏观分析评分"),
        "综合评分": best.get("综合评分"),
        "macro_analysis": item.get("macro_analysis") or best.get("macro_analysis"),
    }
