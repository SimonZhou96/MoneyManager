from __future__ import annotations

import asyncio
import hashlib
import json
import os
import threading
import time
import uuid
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from typing import Any, Dict, List, Optional

from fastapi import BackgroundTasks, Depends, FastAPI, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from db import MarketDatabase
from custom_list import CustomListCodeParser, CustomListJobService, CustomListScreeningRunner
from feishu_notifier import send_screening_result
from market import market_label, normalize_market
from rule_engine import RuleRepository
from stock_pool import CANONICAL_POOL_TYPES, normalize_pool_types, pool_scope_from_types
from .auth import (
    CurrentUser,
    authenticate,
    clear_login_session,
    client_ip,
    create_login_session,
    get_db,
    optional_user,
    require_agent,
    require_user,
)
from .business import BusinessError
from .config import artifact_dir, load_dotenv, mysql_config_from_env, session_cookie_name
from .errors import (
    business_error_handler,
    http_exception_handler,
    request_validation_exception_handler,
)
from .market_intel import router as market_intel_router
from .jobs import run_web_screening_job
from .options import router as options_router
from .quant import router as quant_router
from .sectors import router as sectors_router
from .stock_terminal import router as stock_terminal_router
from .rate_limit import (
    ARTIFACT_DOWNLOAD_RULE,
    CREATE_TASK_RULE,
    LOGIN_REQUEST_RULE,
    SINGLE_STOCK_RULE,
    WEB_READ_RULE,
    enforce_rate_limit,
)
from .rule_chains import parse_rule_expression, resolve_rule_chain, validate_rule_expression_against_market
from .single_stock import normalize_stock_code
from .validation import (
    validate_agent_artifact_size,
    validate_agent_bulk_size,
    validate_agent_json_payload_size,
    validate_markets,
    validate_rule_chain_key,
    validate_rule_chain_timeframe,
    validate_timeframe,
)


load_dotenv()

app = FastAPI(title="MoneyManager Stock Screener", version="0.1.0")
app.add_exception_handler(BusinessError, business_error_handler)
app.add_exception_handler(StarletteHTTPException, http_exception_handler)
app.add_exception_handler(RequestValidationError, request_validation_exception_handler)
app.include_router(options_router)
app.include_router(quant_router)
app.include_router(market_intel_router)
app.include_router(sectors_router)
app.include_router(stock_terminal_router)

SCREENING_RESULT_SCORE_FIELDS = ("technical_score", "macro_score", "final_score", "score_details")


class LoginRequest(BaseModel):
    username: str
    password: str


class ScreeningTaskRequest(BaseModel):
    markets: List[str] = Field(default_factory=lambda: ["HK", "US", "A"])
    timeframe: str = "1d"
    pool_types: List[str] = Field(default_factory=lambda: list(CANONICAL_POOL_TYPES))
    chain_key: Optional[str] = None
    enable_ai_analysis: bool = True
    send_feishu: bool = False


class SingleStockApiRequest(BaseModel):
    market: str
    code: str
    timeframe: str = "1d"
    chain_key: Optional[str] = None
    mode: Optional[str] = None  # "web" = run in background thread immediately; None/absent = queued for agent


class CustomListScreeningTaskRequest(BaseModel):
    market: str
    codes: List[str]
    timeframe: str = "1d"
    chain_key: Optional[str] = None
    enable_ai_analysis: bool = True
    send_feishu: bool = False


def run_custom_list_backend_job(job_id: str) -> None:
    mysql_config = mysql_config_from_env()
    db = MarketDatabase(mysql_config)
    try:
        job = db.get_web_screening_job(job_id)
        if not job:
            return
        options = job.get("options") or {}
        task_ids = job.get("task_ids") or []
        task_id = task_ids[0] if task_ids else options.get("task_id")
        market = normalize_market((job.get("markets") or [None])[0] or options.get("market") or "HK")
        timeframe = str(job.get("timeframe") or "1d")
        result = CustomListScreeningRunner(mysql_config).run(
            job=job,
            csv_base=str(artifact_dir() / "screening_result"),
            today_str=date.today().strftime("%Y-%m-%d"),
            enable_ai_analysis=bool(options.get("enable_ai_analysis", True)),
            task_id=task_id,
        )
        summary = {
            "markets": [market],
            "timeframe": timeframe,
            "chain_key": options.get("chain_key"),
            "chain_timeframe": options.get("chain_timeframe"),
            "chain_name": options.get("chain_name"),
            "result_upload_scope": options.get("result_upload_scope"),
            "input_summary": options.get("input_summary") or {},
            "task_ids": [result.task_id],
            "market_statuses": {market: {"status": "completed", "task_id": result.task_id}},
            "passed_count": len(result.passed),
            "total_count": result.total_count,
        }
        db.update_web_screening_job(job_id, "completed", task_ids=[result.task_id], finished=True, summary=summary)
        webhook_url = os.getenv("FEISHU_WEBHOOK_URL", "").strip()
        if options.get("send_feishu") and webhook_url and result.csv_paths:
            try:
                send_screening_result(
                    webhook_url,
                    f"【Web自定义列表筛选】{date.today()} - {market_label(market)}\n通过: {len(result.passed)} 只",
                    result.csv_paths,
                )
            except Exception as exc:
                summary["warnings"] = [f"飞书发送失败：{type(exc).__name__}: {exc}"]
                db.update_web_screening_job(job_id, "completed", task_ids=[result.task_id], summary=summary)
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"
        try:
            job = db.get_web_screening_job(job_id) or {}
            options = job.get("options") or {}
            task_ids = job.get("task_ids") or ([options.get("task_id")] if options.get("task_id") else None)
            task_id = task_ids[0] if task_ids else None
            if task_id:
                db.update_task_status(task_id, "failed")
            db.update_web_screening_job(
                job_id,
                "failed",
                task_ids=task_ids,
                error_message=message,
                finished=True,
                summary={"warnings": [message]},
            )
        except Exception:
            pass
    finally:
        db.close()


# In-memory progress store for web-mode single-stock screening.
# Keyed by run_id, each value is a dict with: step, pct, status, detail, updated_at.
_single_stock_progress: Dict[str, dict] = {}
_single_stock_progress_lock = threading.Lock()


def _set_progress(run_id: str, step: str, pct: int, status: str = "running", detail: str = "") -> None:
    with _single_stock_progress_lock:
        _single_stock_progress[run_id] = {
            "step": step, "pct": pct, "status": status,
            "detail": detail, "updated_at": datetime.now(ZoneInfo("UTC")).isoformat(),
        }


def _clear_progress(run_id: str) -> None:
    with _single_stock_progress_lock:
        _single_stock_progress.pop(run_id, None)


def run_single_stock_web_job(run_id: str) -> None:
    """在 web 后台线程中执行单股筛选，并更新 single_stock_runs 表和进度。"""
    mysql_config = mysql_config_from_env()
    db = MarketDatabase(mysql_config)
    try:
        run = db.get_single_stock_run(run_id)
        if not run:
            return

        market = str(run.get("market") or "HK")
        code = str(run.get("code") or "")
        normalized_code = str(run.get("normalized_code") or code)
        timeframe = str(run.get("timeframe") or "1d")
        chain_key = run.get("chain_key")

        # Step 1: init
        _set_progress(run_id, "init", 5, detail="初始化数据库表结构")
        db.init_schema(timeframe)

        # Step 2: fetch stock info
        _set_progress(run_id, "stock_lookup", 10, detail=f"查询股票: {code}")
        stock_rows = db.get_stocks_by_codes(market, [code], include_fundamentals=True)
        stock_info = stock_rows[0] if stock_rows else {"code": code, "name": code}
        name = stock_info.get("name") or code
        sector = stock_info.get("sector") or ""
        industry = stock_info.get("industry") or ""

        # Step 3: K-line fetch
        _set_progress(run_id, "kline_fetch", 20, detail=f"获取 {name} K线数据")
        from api.screen_service import run_screening_task, create_rule_engine_from_db
        from scheduled_daily_job import get_default_screening_params, load_passed_screening_records

        params = get_default_screening_params()
        if chain_key:
            params["chain_key"] = chain_key

        task_id = f"web_single_{run_id[:12]}"
        db.create_screening_task(
            task_id=task_id,
            market=market,
            timeframe=timeframe,
            total_count=1,
            params_json=params,
            check_date=date.today(),
        )

        # Step 4: rule evaluation (run_screening_task with 1-stock watchlist)
        _set_progress(run_id, "rule_eval", 40, detail=f"执行筛选规则: {name}")

        # Patch: override progress update to track single-stock progress
        original_update = db.update_task_progress
        def _patched_update(tid, completed_count=0, current_stock_code=None, current_stock_name=None, **kw):
            if tid == task_id:
                pct = min(40 + int(40 * (completed_count / max(1, 1))), 80)
                _set_progress(run_id, "rule_eval", pct,
                              detail=f"规则评估完成: {current_stock_name or code}")
            return original_update(tid, completed_count=completed_count,
                                   current_stock_code=current_stock_code,
                                   current_stock_name=current_stock_name, **kw)
        db.update_task_progress = _patched_update

        watchlist = [{
            "code": code,
            "name": name,
            "sector": sector,
            "industry": industry,
            "market_cap": stock_info.get("market_cap"),
            "pe_ratio": stock_info.get("pe_ratio"),
            "pb_ratio": stock_info.get("pb_ratio"),
        }]

        try:
            run_screening_task(
                mysql_config=mysql_config,
                task_id=task_id,
                market=market,
                timeframe=timeframe,
                params=params,
                verbose=False,
                watchlist=watchlist,
                progress_log=True,
                chain_key=chain_key,
            )
        finally:
            db.update_task_progress = original_update

        # Step 5: read result
        _set_progress(run_id, "read_result", 85, detail="读取筛选结果")
        all_records = load_passed_screening_records(mysql_config, task_id, market)

        # Also load all records (including failed) via existing method
        result_rows = db.get_screening_results_by_task(task_id, limit=10, offset=0)
        stock_result = None
        for r in result_rows:
            if r.get("code") == code:
                stock_result = r
                break
        if not stock_result and result_rows:
            stock_result = result_rows[0]

        if not stock_result:
            stock_result = {
                "code": code, "name": name, "is_passed": False,
                "filter_summary": "未找到筛选结果",
                "filter_details": [],
            }

        passed = bool(stock_result.get("is_passed"))
        filter_details = stock_result.get("filter_details") or []
        filter_summary = stock_result.get("filter_summary") or ""

        # Step 6: save to single_stock_runs
        _set_progress(run_id, "save_result", 95, detail="保存筛选报告")
        result_data = {
            "passed": passed,
            "status": "completed",
            "data_source": "web_backend",
            "warnings": [],
            "name": name,
            "sector": sector,
            "industry": industry,
            "filter_summary": filter_summary,
            "final_score": stock_result.get("final_score"),
            "technical_score": stock_result.get("technical_score"),
            "macro_score": stock_result.get("macro_score"),
            "close_price": stock_result.get("close_price"),
            "market_cap": stock_result.get("market_cap"),
            "pe_ratio": stock_result.get("pe_ratio"),
            "rule_chain": {
                "chain_key": chain_key or "",
                "chain_name": run.get("chain_name") or chain_key or "",
            },
        }
        rule_details = []
        # 构建 rule_key → 中文 rule_name 查找表
        rule_name_map: dict[str, str] = {}
        try:
            metadata_rows = db.get_screening_rule_metadata(market)
            for row in metadata_rows:
                rk = str(row.get("rule_key") or "")
                rn = str(row.get("rule_name") or "")
                if rk and rn:
                    rule_name_map[rk] = rn
        except Exception:
            pass
        for idx, fd in enumerate(filter_details):
            rk = fd.get("rule_key") or fd.get("filter_name") or ""
            # 优先用中文 rule_name（从元数据表查找），其次用 details 中的 rule_key，
            # 再次用 filter_name，最后兜底用 rule_key
            cn_name = rule_name_map.get(rk) or rule_name_map.get(
                str(fd.get("details", {}).get("rule_key") or "")
            )
            rule_details.append({
                "rule_key": rk,
                "rule_name": cn_name or fd.get("filter_name") or fd.get("rule_key") or "",
                "rule_type": fd.get("rule_type") or fd.get("strategy_category") or "",
                "result": fd.get("result") or "",
                "reason": fd.get("reason") or "",
                "details": fd.get("details", {}),
                "display_order": idx,
            })
        db.complete_single_stock_run_from_agent(run_id, result_data, rule_details)

        _set_progress(run_id, "done", 100, status="completed", detail="筛选完成 ✓")
        # Keep progress for 60s so late-arriving SSE clients can read final state
        threading.Timer(60, lambda: _clear_progress(run_id)).start()

    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"
        try:
            db.fail_single_stock_run(run_id, message)
        except Exception:
            pass
        _set_progress(run_id, "error", 0, status="failed", detail=message)
        threading.Timer(60, lambda: _clear_progress(run_id)).start()
    finally:
        db.close()


class SyncRunRequest(BaseModel):
    sync_run_id: Optional[str] = None
    agent_id: Optional[str] = None
    markets: List[str] = Field(default_factory=list)
    timeframes: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class BulkStockPoolRequest(BaseModel):
    sync_run_id: str
    market: str
    pool_type: str
    rows: List[Dict[str, Any]]


class BulkSectorRequest(BaseModel):
    sync_run_id: str
    rows: List[Dict[str, Any]]


class BulkKlineRequest(BaseModel):
    sync_run_id: str
    rows: List[Dict[str, Any]]
    prune: bool = True
    max_bars: int = 500


class CompleteSyncRunRequest(BaseModel):
    status: str = "success"
    error_message: Optional[str] = None
    stock_pool_rows: Optional[int] = None
    sector_rows: Optional[int] = None
    kline_rows: Optional[int] = None


class AgentJobClaimRequest(BaseModel):
    agent_id: str


class AgentHeartbeatRequest(BaseModel):
    agent_id: str
    progress: Dict[str, Any] = Field(default_factory=dict)


class AgentJobCompleteRequest(BaseModel):
    status: str = "completed"
    task_ids: List[str] = Field(default_factory=list)
    summary: Dict[str, Any] = Field(default_factory=dict)
    error_message: Optional[str] = None


class AgentTaskSummaryRequest(BaseModel):
    job_id: str
    task: Dict[str, Any]


class BulkScreeningResultsRequest(BaseModel):
    task_id: str
    check_date: str
    rows: List[Dict[str, Any]]


class BulkSignalAnalysisRequest(BaseModel):
    rows: List[Dict[str, Any]]


class AgentSingleStockCompleteRequest(BaseModel):
    result: Dict[str, Any]
    rule_details: List[Dict[str, Any]] = Field(default_factory=list)


class RuleChainUpsertRequest(BaseModel):
    market: str
    timeframe: str = "1d"
    chain_key: str
    chain_name: str
    expression_json: Dict[str, Any]
    enabled: bool = True
    priority: int = 100
    description: Optional[str] = None


def require_read_user(user: CurrentUser = Depends(require_user)) -> CurrentUser:
    enforce_rate_limit(f"user:{user.id}:read", WEB_READ_RULE)
    return user


def _run_date() -> Any:
    return datetime.now(ZoneInfo("Asia/Shanghai")).date()


def _safe_file_name(file_name: str) -> str:
    value = Path(str(file_name or "artifact")).name
    if not value or value in {".", ".."}:
        value = "artifact"
    return value


@app.on_event("startup")
def startup() -> None:
    db = MarketDatabase(mysql_config_from_env())
    try:
        db.init_web_schema()
        db.init_market_intel_schema()
        db.init_stock_terminal_schema()
        # 初始化核心 schema（stocks/kline 表 + 规则元数据/规则链表）
        db.init_schema("1d")
        username = os.getenv("WEB_BOOTSTRAP_USERNAME", "").strip()
        password = os.getenv("WEB_BOOTSTRAP_PASSWORD", "").strip()
        if username and password and not db.get_web_user_by_username(username):
            db.upsert_web_user(username=username, password=password, role="admin", is_active=True)
    finally:
        db.close()


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.post("/api/auth/login")
def login(payload: LoginRequest, request: Request, response: Response, db: MarketDatabase = Depends(get_db)):
    ip_address = client_ip(request)
    enforce_rate_limit(f"ip:{ip_address}:login", LOGIN_REQUEST_RULE)
    ok, user, message = authenticate(db, payload.username.strip(), payload.password, ip_address)
    if not ok or not user:
        raise BusinessError("LOGIN_FAILED", message or "登录失败，请检查账号或密码")
    create_login_session(db, response, int(user["id"]))
    return {"user": {"id": user["id"], "username": user["username"], "role": user["role"]}}


@app.post("/api/auth/logout")
def logout(response: Response, request: Request, db: MarketDatabase = Depends(get_db)):
    token = request.cookies.get(session_cookie_name())
    clear_login_session(db, response, token)
    return {"ok": True}


@app.get("/api/auth/me")
def me(user: Optional[CurrentUser] = Depends(optional_user)):
    if user is None:
        raise BusinessError("AUTH_REQUIRED", "请先登录后再访问")
    return {"id": user.id, "username": user.username, "role": user.role}


@app.post("/api/screening/tasks")
def create_screening_task(
    payload: ScreeningTaskRequest,
    background_tasks: BackgroundTasks,
    user: CurrentUser = Depends(require_user),
    db: MarketDatabase = Depends(get_db),
):
    try:
        markets = validate_markets(payload.markets)
        timeframe = validate_timeframe(payload.timeframe)
        pool_types = normalize_pool_types(payload.pool_types or CANONICAL_POOL_TYPES)
    except ValueError as exc:
        raise BusinessError("INVALID_SCREENING_TASK", str(exc)) from exc
    enforce_rate_limit(f"user:{user.id}:create_task", CREATE_TASK_RULE)
    run_date = _run_date()
    chain = resolve_rule_chain(db, markets, timeframe=timeframe, chain_key=payload.chain_key)
    chain_key = chain["chain_key"]
    pool_scope = pool_scope_from_types(pool_types)
    existing_locks = db.get_screening_run_locks(
        run_date,
        markets,
        timeframe,
        chain_key=chain_key,
        pool_scope=pool_scope,
    )
    completed = [item for item in existing_locks if item.get("status") == "completed"]
    if completed:
        scopes = "、".join(f"{item['market']}/{item['timeframe']}/{item.get('chain_key') or chain_key}" for item in completed)
        raise BusinessError(
            "SCREENING_ALREADY_COMPLETED",
            f"今日 {scopes} 已筛选成功，无需重复运行",
        )
    active = [item for item in existing_locks if item.get("status") in {"queued", "running"}]
    if active:
        job_id = active[0]["job_id"]
        job = db.get_web_screening_job(job_id) or {}
        return {
            "job_id": job_id,
            "status": job.get("status") or active[0].get("status"),
            "runner": job.get("execution_mode") or "web_backend",
            "reused": True,
            "message": "已有任务运行中，已为你复用该任务",
            "markets": job.get("markets") or sorted({item["market"] for item in active}),
            "timeframe": timeframe,
            "pool_types": pool_types,
            "chain_key": chain_key,
            "chain_timeframe": chain.get("chain_timeframe"),
            "chain_name": chain.get("chain_name"),
        }
    job_id = str(uuid.uuid4())
    options = {
        "enable_ai_analysis": bool(payload.enable_ai_analysis),
        "send_feishu": bool(payload.send_feishu),
        "result_upload_scope": "passed_only",
        "pool_types": pool_types,
        "pool_scope": pool_scope,
        "chain_key": chain_key,
        "chain_timeframe": chain.get("chain_timeframe"),
        "chain_name": chain.get("chain_name"),
    }
    db.create_web_screening_job(
        job_id=job_id,
        user_id=user.id,
        markets=markets,
        timeframe=timeframe,
        options=options,
        execution_mode="web_backend",
    )
    try:
        db.create_screening_run_locks(
            job_id=job_id,
            run_date=run_date,
            markets=markets,
            timeframe=timeframe,
            chain_key=chain_key,
            pool_scope=pool_scope,
        )
    except Exception as exc:
        locks = db.get_screening_run_locks(
            run_date,
            markets,
            timeframe,
            chain_key=chain_key,
            pool_scope=pool_scope,
        )
        active_after_race = [item for item in locks if item.get("status") in {"queued", "running"}]
        if active_after_race:
            return {
                "job_id": active_after_race[0]["job_id"],
                "status": active_after_race[0]["status"],
                "runner": "web_backend",
                "reused": True,
                "message": "已有任务运行中，已为你复用该任务",
                "markets": sorted({item["market"] for item in active_after_race}),
                "timeframe": timeframe,
                "pool_types": pool_types,
                "chain_key": chain_key,
                "chain_timeframe": chain.get("chain_timeframe"),
                "chain_name": chain.get("chain_name"),
            }
        raise BusinessError("SCREENING_LOCK_CREATE_FAILED", f"创建筛选锁失败：{type(exc).__name__}: {exc}") from exc
    background_tasks.add_task(
        run_web_screening_job,
        mysql_config_from_env(),
        job_id,
        markets,
        timeframe,
        str(artifact_dir()),
        bool(payload.enable_ai_analysis),
        bool(payload.send_feishu),
        chain_key,
        pool_types,
    )
    return {
        "job_id": job_id,
        "status": "queued",
        "runner": "web_backend",
        "markets": markets,
        "timeframe": timeframe,
        "pool_types": pool_types,
        "chain_key": chain_key,
        "chain_timeframe": chain.get("chain_timeframe"),
        "chain_name": chain.get("chain_name"),
    }


@app.post("/api/screening/custom-list-tasks")
def create_custom_list_task(
    payload: CustomListScreeningTaskRequest,
    background_tasks: BackgroundTasks,
    user: CurrentUser = Depends(require_user),
    db: MarketDatabase = Depends(get_db),
):
    enforce_rate_limit(f"user:{user.id}:create_task", CREATE_TASK_RULE)
    try:
        market = normalize_market(payload.market)
        timeframe = validate_timeframe(payload.timeframe)
        parsed = CustomListCodeParser().parse(market, payload.codes)
    except ValueError as exc:
        raise BusinessError("INVALID_CUSTOM_LIST_TASK", str(exc)) from exc
    chain = resolve_rule_chain(db, [market], timeframe=timeframe, chain_key=payload.chain_key)
    try:
        result = CustomListJobService(db).create_job(
            user_id=user.id,
            market=market,
            timeframe=timeframe,
            chain=chain,
            parse_result=parsed,
            enable_ai_analysis=payload.enable_ai_analysis,
            send_feishu=payload.send_feishu,
        )
        background_tasks.add_task(run_custom_list_backend_job, result["job_id"])
        return result
    except ValueError as exc:
        raise BusinessError("CUSTOM_LIST_NO_RESOLVED_STOCKS", str(exc)) from exc


@app.get("/api/screening/custom-list-tasks/{job_id}/results")
def get_custom_list_task_results(
    job_id: str,
    _: CurrentUser = Depends(require_read_user),
    db: MarketDatabase = Depends(get_db),
):
    job = db.get_web_screening_job(job_id)
    if not job:
        raise BusinessError("CUSTOM_LIST_JOB_NOT_FOUND", "自定义股票列表筛选任务不存在")
    try:
        return CustomListJobService(db).build_results(job)
    except ValueError as exc:
        raise BusinessError("CUSTOM_LIST_JOB_INVALID", str(exc)) from exc


@app.get("/api/screening/tasks")
def list_tasks(_: CurrentUser = Depends(require_read_user), db: MarketDatabase = Depends(get_db)):
    return {
        "jobs": db.list_web_screening_jobs(limit=50),
        "tasks": db.list_screening_tasks(limit=50),
        "single_stock_runs": db.list_single_stock_runs(limit=50),
    }


@app.get("/api/screening/tasks/{task_id}")
def get_task(task_id: str, _: CurrentUser = Depends(require_read_user), db: MarketDatabase = Depends(get_db)):
    task = db.get_task_by_id(task_id)
    if not task:
        raise BusinessError("TASK_NOT_FOUND", "任务不存在")
    return task


@app.get("/api/screening/tasks/{task_id}/results")
def get_task_results(
    task_id: str,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    passed_only: bool = False,
    _: CurrentUser = Depends(require_read_user),
    db: MarketDatabase = Depends(get_db),
):
    task = db.get_task_by_id(task_id)
    if task and task.get("uploaded_result_scope") == "passed_only":
        total_count = int(task.get("total_count") or 0)
        passed_count = int(task.get("passed_count") or db.count_screening_results_by_task(task_id, passed_only=True))
    else:
        total_count = db.count_screening_results_by_task(task_id)
        passed_count = db.count_screening_results_by_task(task_id, passed_only=True)
    rows = db.get_screening_results_by_task(
        task_id,
        limit=limit,
        offset=offset,
        passed_only=passed_only,
    )
    for row in rows:
        for field in SCREENING_RESULT_SCORE_FIELDS:
            row.setdefault(field, {} if field == "score_details" else None)
    return {
        "rows": rows,
        "total_count": total_count,
        "passed_count": passed_count,
        "uploaded_result_scope": task.get("uploaded_result_scope") if task else None,
        "limit": limit,
        "offset": offset,
    }


@app.get("/api/screening/tasks/{task_id}/artifacts")
def get_task_artifacts(task_id: str, _: CurrentUser = Depends(require_read_user), db: MarketDatabase = Depends(get_db)):
    return {"artifacts": db.list_screening_artifacts(task_id=task_id)}


@app.get("/api/artifacts/{artifact_id}/download")
def download_artifact(artifact_id: str, user: CurrentUser = Depends(require_user), db: MarketDatabase = Depends(get_db)):
    enforce_rate_limit(f"user:{user.id}:artifact_download", ARTIFACT_DOWNLOAD_RULE)
    artifact = db.get_screening_artifact(artifact_id)
    if not artifact:
        raise BusinessError("ARTIFACT_NOT_FOUND", "文件不存在")
    path = Path(artifact["file_path"])
    if not path.exists() or not path.is_file():
        raise BusinessError("ARTIFACT_FILE_MISSING", "文件已丢失")
    return FileResponse(
        path=str(path),
        filename=artifact["file_name"],
        media_type=artifact.get("content_type") or "application/octet-stream",
    )


@app.post("/api/screening/single-stock")
def single_stock(payload: SingleStockApiRequest, user: CurrentUser = Depends(require_user), db: MarketDatabase = Depends(get_db)):
    enforce_rate_limit(f"user:{user.id}:single_stock", SINGLE_STOCK_RULE)
    try:
        market = normalize_market(payload.market)
        timeframe = validate_timeframe(payload.timeframe)
        normalized_code = normalize_stock_code(market, payload.code)
    except Exception as exc:
        raise BusinessError("INVALID_SINGLE_STOCK", f"单股参数不合法：{exc}") from exc
    chain = resolve_rule_chain(db, [market], timeframe=timeframe, chain_key=payload.chain_key)
    run_id = str(uuid.uuid4())
    web_mode = (payload.mode or "").strip().lower() == "web"
    db.create_single_stock_run({
        "run_id": run_id,
        "user_id": user.id,
        "market": market,
        "code": payload.code,
        "normalized_code": normalized_code,
        "timeframe": timeframe,
        "chain_key": chain["chain_key"],
        "status": "running" if web_mode else "queued",
    })
    if web_mode:
        # Start background thread to run screening immediately
        threading.Thread(target=run_single_stock_web_job, args=(run_id,), daemon=True).start()
    return {
        "run_id": run_id,
        "status": "running" if web_mode else "queued",
        "runner": "web_backend" if web_mode else "local_agent",
        "market": market,
        "code": normalized_code,
        "timeframe": timeframe,
        "chain_key": chain["chain_key"],
        "chain_timeframe": chain.get("chain_timeframe"),
        "chain_name": chain.get("chain_name"),
    }


@app.get("/api/screening/single-stock/{run_id}")
def get_single_stock(run_id: str, _: CurrentUser = Depends(require_read_user), db: MarketDatabase = Depends(get_db)):
    result = db.get_single_stock_run(run_id)
    if not result:
        raise BusinessError("SINGLE_STOCK_NOT_FOUND", "单股任务不存在")
    return result


@app.get("/api/screening/single-stock/{run_id}/progress")
async def single_stock_progress(
    run_id: str,
    _: CurrentUser = Depends(require_read_user),
):
    """SSE 端点：流式推送单股筛选进度。"""
    async def event_stream():
        last_state_hash = ""
        # Keep streaming until terminal state or timeout (120s)
        for _ in range(240):  # max 240 * 0.5s = 120s
            with _single_stock_progress_lock:
                state = _single_stock_progress.get(run_id)
            if state:
                h = hash(json.dumps(state, sort_keys=True, default=str))
                if h != last_state_hash:
                    last_state_hash = h
                    payload = json.dumps(state, default=str)
                    yield f"data: {payload}\n\n"
                if state.get("status") in ("completed", "failed"):
                    return
            else:
                # Progress not found yet — send initial waiting event
                if last_state_hash != "waiting":
                    last_state_hash = "waiting"
                    yield f"data: {json.dumps({'step': 'waiting', 'pct': 0, 'status': 'running', 'detail': '等待任务启动...'})}\n\n"
            await asyncio.sleep(0.5)
    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.get("/api/stocks/search")
def search_stocks(
    q: str = Query(..., min_length=1, description="搜索关键词，支持股票名称或代码"),
    market: str = Query("HK", description="市场 HK/US/A"),
    limit: int = Query(10, ge=1, le=30, description="返回结果数上限"),
    _: CurrentUser = Depends(require_read_user),
    db: MarketDatabase = Depends(get_db),
):
    """股票名称/代码模糊搜索，用于自动补全。按匹配精度 + 市值降序排列。"""
    market = normalize_market(market)
    results = db.search_stocks_by_name(market, q, limit)
    return {"query": q, "market": market, "results": results}


@app.get("/api/screening/single-stock/history/{code}")
def stock_screening_history(
    code: str,
    market: str = Query("HK"),
    limit: int = Query(20, ge=1, le=50),
    _: CurrentUser = Depends(require_read_user),
    db: MarketDatabase = Depends(get_db),
):
    """查询某只股票的历史筛选记录"""
    market = normalize_market(market)
    history = db.list_single_stock_runs_by_code(market, code, limit)
    return {"code": code, "market": market, "history": history}


@app.get("/api/rules")
def get_rules(
    market: str = "HK",
    timeframe: Optional[str] = None,
    _: CurrentUser = Depends(require_read_user),
    db: MarketDatabase = Depends(get_db),
):
    market = normalize_market(market)
    if timeframe:
        timeframe = validate_timeframe(timeframe)
    repository = RuleRepository(db)
    chains = repository.load_chains(market, timeframe)
    metadata = []
    for item in repository.load_metadata(market):
        row = item.__dict__.copy()
        params = row.get("params") if isinstance(row.get("params"), dict) else {}
        direction = str(params.get("direction") or "").strip()
        row["signal_direction"] = direction or None
        row["signal_direction_label"] = {
            "bullish": "看涨",
            "bearish": "看跌",
            "neutral": "中性",
        }.get(direction, "未标明")
        metadata.append(row)
    return {
        "market": market,
        "timeframe": timeframe or "*",
        "metadata": metadata,
        "chain": repository.load_active_chain(market, timeframe or "*").__dict__,
        "chains": [item.__dict__ for item in chains],
    }


@app.post("/api/rules/chains")
def create_rule_chain(
    payload: RuleChainUpsertRequest,
    _: CurrentUser = Depends(require_user),
    db: MarketDatabase = Depends(get_db),
):
    market = normalize_market(payload.market)
    timeframe = validate_rule_chain_timeframe(payload.timeframe)
    chain_key = validate_rule_chain_key(payload.chain_key)
    expression = validate_rule_expression_against_market(db, market, parse_rule_expression(payload.expression_json))
    db.create_screening_rule_chain({
        "market": market,
        "timeframe": timeframe,
        "chain_key": chain_key,
        "chain_name": payload.chain_name.strip(),
        "expression_json": expression,
        "enabled": payload.enabled,
        "priority": payload.priority,
        "description": payload.description,
    })
    return {"ok": True}


@app.put("/api/rules/chains/{market}/{timeframe}/{chain_key}")
def update_rule_chain(
    market: str,
    timeframe: str,
    chain_key: str,
    payload: RuleChainUpsertRequest,
    _: CurrentUser = Depends(require_user),
    db: MarketDatabase = Depends(get_db),
):
    market = normalize_market(market)
    timeframe = validate_rule_chain_timeframe(timeframe)
    chain_key = validate_rule_chain_key(chain_key)
    expression = validate_rule_expression_against_market(db, market, parse_rule_expression(payload.expression_json))
    active = db.get_active_screening_rule_chain(market, timeframe)
    if active and active["chain_key"] == chain_key and not payload.enabled:
        raise BusinessError("RULE_CHAIN_ACTIVE", "当前生效规则链不能直接禁用，请先启用其他规则链")
    updated = db.update_screening_rule_chain(market, timeframe, chain_key, {
        "chain_name": payload.chain_name.strip(),
        "expression_json": expression,
        "enabled": payload.enabled,
        "priority": payload.priority,
        "description": payload.description,
    })
    if not updated:
        raise BusinessError("RULE_CHAIN_NOT_FOUND", "规则链不存在")
    return {"ok": True}


@app.delete("/api/rules/chains/{market}/{timeframe}/{chain_key}")
def delete_rule_chain(
    market: str,
    timeframe: str,
    chain_key: str,
    _: CurrentUser = Depends(require_user),
    db: MarketDatabase = Depends(get_db),
):
    market = normalize_market(market)
    timeframe = validate_rule_chain_timeframe(timeframe)
    chain_key = validate_rule_chain_key(chain_key)
    active = db.get_active_screening_rule_chain(market, timeframe)
    if active and active["chain_key"] == chain_key:
        raise BusinessError("RULE_CHAIN_ACTIVE", "当前生效规则链不能删除，请先切换到其他规则链")
    deleted = db.delete_screening_rule_chain(market, timeframe, chain_key)
    if not deleted:
        raise BusinessError("RULE_CHAIN_NOT_FOUND", "规则链不存在")
    return {"ok": True}


@app.get("/api/system/data-freshness")
def data_freshness(_: CurrentUser = Depends(require_read_user), db: MarketDatabase = Depends(get_db)):
    return {"sync_runs": db.latest_data_sync_runs(limit=10)}


@app.get("/api/agent/jobs/pending", dependencies=[Depends(require_agent)])
def agent_pending_jobs(
    limit: int = Query(default=5, ge=1, le=20),
    stale_after_seconds: int = Query(default=1800, ge=300, le=86400),
    db: MarketDatabase = Depends(get_db),
):
    return {"jobs": db.list_pending_agent_jobs(limit=limit, stale_after_seconds=stale_after_seconds)}


@app.post("/api/agent/jobs/{job_id}/claim", dependencies=[Depends(require_agent)])
def agent_claim_job(job_id: str, payload: AgentJobClaimRequest, db: MarketDatabase = Depends(get_db)):
    if not payload.agent_id.strip():
        raise BusinessError("AGENT_ID_REQUIRED", "Agent ID 不能为空")
    job = db.claim_agent_job(job_id, payload.agent_id.strip())
    if not job:
        raise BusinessError("AGENT_JOB_NOT_CLAIMED", "任务已被其他 Agent 领取或不再可领取")
    return {"claimed": True, "job": job}


@app.post("/api/agent/jobs/{job_id}/heartbeat", dependencies=[Depends(require_agent)])
def agent_job_heartbeat(job_id: str, payload: AgentHeartbeatRequest, db: MarketDatabase = Depends(get_db)):
    if not payload.agent_id.strip():
        raise BusinessError("AGENT_ID_REQUIRED", "Agent ID 不能为空")
    db.heartbeat_agent_job(job_id, payload.agent_id.strip(), progress=payload.progress)
    return {"job_id": job_id, "status": "heartbeat"}


@app.post("/api/agent/jobs/{job_id}/complete", dependencies=[Depends(require_agent)])
def agent_complete_job(job_id: str, payload: AgentJobCompleteRequest, db: MarketDatabase = Depends(get_db)):
    status = payload.status if payload.status in {"completed", "failed"} else "failed"
    db.complete_agent_screening_job(
        job_id=job_id,
        status=status,
        task_ids=payload.task_ids,
        summary=payload.summary,
        error_message=payload.error_message,
    )
    return {"job_id": job_id, "status": status}


@app.post("/api/agent/screening-tasks/upsert", dependencies=[Depends(require_agent)])
def agent_upsert_screening_task(payload: AgentTaskSummaryRequest, db: MarketDatabase = Depends(get_db)):
    validate_agent_json_payload_size(payload.dict())
    item = dict(payload.task)
    item["job_id"] = payload.job_id
    db.upsert_screening_task_summary(item)
    return {"written": 1, "task_id": item.get("task_id")}


@app.post("/api/agent/screening-results/bulk-upsert", dependencies=[Depends(require_agent)])
def agent_bulk_screening_results(payload: BulkScreeningResultsRequest, db: MarketDatabase = Depends(get_db)):
    validate_agent_bulk_size(payload.rows, max_rows=1000)
    validate_agent_json_payload_size(payload.dict())
    rows = []
    for row in payload.rows:
        item = dict(row)
        item["task_id"] = payload.task_id
        item["is_passed"] = bool(item.get("is_passed", True))
        rows.append(item)
    db.upsert_screening_results(date.fromisoformat(payload.check_date), rows)
    return {"written": len(rows)}


@app.post("/api/agent/signal-analysis/bulk-upsert", dependencies=[Depends(require_agent)])
def agent_bulk_signal_analysis(payload: BulkSignalAnalysisRequest, db: MarketDatabase = Depends(get_db)):
    validate_agent_bulk_size(payload.rows, max_rows=1000)
    validate_agent_json_payload_size(payload.dict())
    db.upsert_signal_analysis_results(payload.rows)
    return {"written": len(payload.rows)}


@app.post("/api/agent/single-stock-runs/{run_id}/complete", dependencies=[Depends(require_agent)])
def agent_complete_single_stock(run_id: str, payload: AgentSingleStockCompleteRequest, db: MarketDatabase = Depends(get_db)):
    validate_agent_json_payload_size(payload.dict())
    db.complete_single_stock_run_from_agent(run_id, payload.result, payload.rule_details)
    return {"run_id": run_id, "status": payload.result.get("status") or "completed"}


@app.post("/api/agent/artifacts/upload", dependencies=[Depends(require_agent)])
async def agent_upload_artifact(
    request: Request,
    task_id: str = Query(...),
    market: Optional[str] = Query(default=None),
    artifact_type: str = Query(default="csv"),
    file_name: str = Query(...),
    content_type: Optional[str] = Query(default=None),
    checksum: Optional[str] = Query(default=None),
    db: MarketDatabase = Depends(get_db),
):
    if artifact_type not in {"csv", "report"}:
        raise BusinessError("AGENT_ARTIFACT_TYPE_INVALID", "导出文件类型不支持")
    safe_name = _safe_file_name(file_name)
    suffix = Path(safe_name).suffix.lower()
    if suffix not in {".csv", ".md"}:
        raise BusinessError("AGENT_ARTIFACT_SUFFIX_INVALID", "只允许上传 CSV 或 Markdown 文件")
    content = await request.body()
    validate_agent_artifact_size(len(content))
    digest = hashlib.sha256(content).hexdigest()
    if checksum and checksum != digest:
        raise BusinessError("AGENT_ARTIFACT_CHECKSUM_MISMATCH", "导出文件校验失败，请重新上传")
    target_dir = artifact_dir() / "agent_uploads" / task_id
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / safe_name
    target_path.write_bytes(content)
    artifact_id = db.create_screening_artifact({
        "task_id": task_id,
        "market": market,
        "artifact_type": artifact_type,
        "file_name": safe_name,
        "file_path": str(target_path.resolve()),
        "content_type": content_type or ("text/markdown; charset=utf-8" if suffix == ".md" else "text/csv; charset=utf-8"),
        "file_size": len(content),
        "checksum": digest,
    })
    return {"artifact_id": artifact_id, "file_name": safe_name, "file_size": len(content), "checksum": digest}


@app.post("/api/agent/sync-runs", dependencies=[Depends(require_agent)])
def create_sync_run(payload: SyncRunRequest, db: MarketDatabase = Depends(get_db)):
    sync_run_id = payload.sync_run_id or str(uuid.uuid4())
    db.create_data_sync_run(
        sync_run_id=sync_run_id,
        agent_id=payload.agent_id,
        markets=payload.markets,
        timeframes=payload.timeframes,
        metadata=payload.metadata,
    )
    return {"sync_run_id": sync_run_id, "status": "running"}


@app.post("/api/agent/stock-pools/bulk-upsert", dependencies=[Depends(require_agent)])
def bulk_stock_pool(payload: BulkStockPoolRequest, db: MarketDatabase = Depends(get_db)):
    validate_agent_bulk_size(payload.rows)
    try:
        pool_types = normalize_pool_types([payload.pool_type])
    except ValueError as exc:
        raise StarletteHTTPException(status_code=400, detail=str(exc)) from exc
    if not pool_types:
        raise StarletteHTTPException(status_code=400, detail="无效股票池类型: 不能为空")
    pool_type = pool_types[0]
    rows = [dict(row, sync_run_id=payload.sync_run_id) for row in payload.rows]
    db.upsert_stock_pool(normalize_market(payload.market), pool_type, rows)
    return {"written": len(rows)}


@app.post("/api/agent/sector-memberships/bulk-upsert", dependencies=[Depends(require_agent)])
def bulk_sector(payload: BulkSectorRequest, db: MarketDatabase = Depends(get_db)):
    validate_agent_bulk_size(payload.rows)
    rows = [dict(row, sync_run_id=payload.sync_run_id) for row in payload.rows]
    db.upsert_stock_sector_memberships(rows)
    return {"written": len(rows)}


@app.post("/api/agent/klines/bulk-upsert", dependencies=[Depends(require_agent)])
def bulk_klines(payload: BulkKlineRequest, db: MarketDatabase = Depends(get_db)):
    validate_agent_bulk_size(payload.rows)
    rows = [dict(row, sync_run_id=payload.sync_run_id, source=row.get("source") or "opend_cache") for row in payload.rows]
    written = db.upsert_kline_cache(rows)
    if payload.prune:
        seen = set()
        for row in rows:
            key = (row.get("market"), row.get("code"), row.get("timeframe"))
            if None in key or key in seen:
                continue
            seen.add(key)
            db.prune_kline_cache(key[0], key[1], key[2], payload.max_bars)
    return {"written": written}


@app.post("/api/agent/sync-runs/{sync_run_id}/complete", dependencies=[Depends(require_agent)])
def complete_sync_run(sync_run_id: str, payload: CompleteSyncRunRequest, db: MarketDatabase = Depends(get_db)):
    db.complete_data_sync_run(
        sync_run_id=sync_run_id,
        status=payload.status,
        error_message=payload.error_message,
        stock_pool_rows=payload.stock_pool_rows,
        sector_rows=payload.sector_rows,
        kline_rows=payload.kline_rows,
    )
    return {"sync_run_id": sync_run_id, "status": payload.status}
