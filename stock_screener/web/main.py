from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from db import MarketDatabase
from market import normalize_market
from rule_engine import RuleRepository
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
from .jobs import run_web_screening_job
from .queue import enqueue_or_thread
from .rate_limit import (
    ARTIFACT_DOWNLOAD_RULE,
    CREATE_TASK_RULE,
    LOGIN_REQUEST_RULE,
    SINGLE_STOCK_RULE,
    WEB_READ_RULE,
    enforce_rate_limit,
)
from .single_stock import SingleStockRequest, run_single_stock_analysis
from .validation import validate_agent_bulk_size, validate_markets, validate_timeframe


load_dotenv()

app = FastAPI(title="MoneyManager Stock Screener", version="0.1.0")
app.add_exception_handler(BusinessError, business_error_handler)
app.add_exception_handler(StarletteHTTPException, http_exception_handler)
app.add_exception_handler(RequestValidationError, request_validation_exception_handler)


class LoginRequest(BaseModel):
    username: str
    password: str


class ScreeningTaskRequest(BaseModel):
    markets: List[str] = Field(default_factory=lambda: ["HK", "US", "A"])
    timeframe: str = "1d"
    enable_ai_analysis: bool = True
    send_feishu: bool = False


class SingleStockApiRequest(BaseModel):
    market: str
    code: str
    timeframe: str = "1d"


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


def require_read_user(user: CurrentUser = Depends(require_user)) -> CurrentUser:
    enforce_rate_limit(f"user:{user.id}:read", WEB_READ_RULE)
    return user


@app.on_event("startup")
def startup() -> None:
    db = MarketDatabase(mysql_config_from_env())
    try:
        db.init_web_schema()
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
    user: CurrentUser = Depends(require_user),
    db: MarketDatabase = Depends(get_db),
):
    try:
        markets = validate_markets(payload.markets)
        timeframe = validate_timeframe(payload.timeframe)
    except ValueError as exc:
        raise BusinessError("INVALID_SCREENING_TASK", str(exc)) from exc
    enforce_rate_limit(f"user:{user.id}:create_task", CREATE_TASK_RULE)
    job_id = str(uuid.uuid4())
    db.create_web_screening_job(job_id=job_id, user_id=user.id, markets=markets, timeframe=timeframe)
    runner = enqueue_or_thread(
        run_web_screening_job,
        mysql_config=mysql_config_from_env(),
        job_id=job_id,
        markets=markets,
        timeframe=timeframe,
        artifact_root=str(artifact_dir()),
        enable_ai_analysis=payload.enable_ai_analysis,
        send_feishu=payload.send_feishu,
    )
    return {"job_id": job_id, "status": "queued", "runner": runner, "markets": markets, "timeframe": timeframe}


@app.get("/api/screening/tasks")
def list_tasks(_: CurrentUser = Depends(require_read_user), db: MarketDatabase = Depends(get_db)):
    return {
        "jobs": db.list_web_screening_jobs(limit=50),
        "tasks": db.list_screening_tasks(limit=50),
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
    return {
        "rows": db.get_screening_results_by_task(
            task_id,
            limit=limit,
            offset=offset,
            passed_only=passed_only,
        ),
        "total_count": db.count_screening_results_by_task(task_id),
        "passed_count": db.count_screening_results_by_task(task_id, passed_only=True),
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
def single_stock(payload: SingleStockApiRequest, user: CurrentUser = Depends(require_user)):
    enforce_rate_limit(f"user:{user.id}:single_stock", SINGLE_STOCK_RULE)
    try:
        return run_single_stock_analysis(
            mysql_config=mysql_config_from_env(),
            request=SingleStockRequest(
                market=payload.market,
                code=payload.code,
                timeframe=payload.timeframe,
                user_id=user.id,
            ),
        )
    except Exception as exc:
        raise BusinessError("SINGLE_STOCK_FAILED", f"单股分析失败：{type(exc).__name__}: {exc}") from exc


@app.get("/api/rules")
def get_rules(market: str = "HK", _: CurrentUser = Depends(require_read_user), db: MarketDatabase = Depends(get_db)):
    market = normalize_market(market)
    repository = RuleRepository(db)
    return {
        "market": market,
        "metadata": [item.__dict__ for item in repository.load_metadata(market)],
        "chain": repository.load_active_chain(market).__dict__,
    }


@app.get("/api/system/data-freshness")
def data_freshness(_: CurrentUser = Depends(require_read_user), db: MarketDatabase = Depends(get_db)):
    return {"sync_runs": db.latest_data_sync_runs(limit=10)}


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
    rows = [dict(row, sync_run_id=payload.sync_run_id) for row in payload.rows]
    db.upsert_stock_pool(normalize_market(payload.market), payload.pool_type, rows)
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
