from __future__ import annotations

import os
import hashlib
import uuid
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo
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
from .rate_limit import (
    ARTIFACT_DOWNLOAD_RULE,
    CREATE_TASK_RULE,
    LOGIN_REQUEST_RULE,
    SINGLE_STOCK_RULE,
    WEB_READ_RULE,
    enforce_rate_limit,
)
from .rule_chains import resolve_rule_chain
from .single_stock import normalize_stock_code
from .validation import (
    validate_agent_artifact_size,
    validate_agent_bulk_size,
    validate_agent_json_payload_size,
    validate_markets,
    validate_timeframe,
)


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
    chain_key: Optional[str] = None
    enable_ai_analysis: bool = True
    send_feishu: bool = False


class SingleStockApiRequest(BaseModel):
    market: str
    code: str
    timeframe: str = "1d"
    chain_key: Optional[str] = None


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
    run_date = _run_date()
    chain = resolve_rule_chain(db, markets, payload.chain_key)
    chain_key = chain["chain_key"]
    existing_locks = db.get_screening_run_locks(run_date, markets, timeframe, chain_key=chain_key)
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
            "runner": "local_agent",
            "reused": True,
            "message": "已有任务运行中，已为你复用该任务",
            "markets": job.get("markets") or sorted({item["market"] for item in active}),
            "timeframe": timeframe,
            "chain_key": chain_key,
            "chain_name": chain.get("chain_name"),
        }
    job_id = str(uuid.uuid4())
    options = {
        "enable_ai_analysis": bool(payload.enable_ai_analysis),
        "send_feishu": bool(payload.send_feishu),
        "result_upload_scope": "passed_only",
        "chain_key": chain_key,
        "chain_name": chain.get("chain_name"),
    }
    db.create_web_screening_job(job_id=job_id, user_id=user.id, markets=markets, timeframe=timeframe, options=options)
    try:
        db.create_screening_run_locks(
            job_id=job_id,
            run_date=run_date,
            markets=markets,
            timeframe=timeframe,
            chain_key=chain_key,
        )
    except Exception as exc:
        locks = db.get_screening_run_locks(run_date, markets, timeframe, chain_key=chain_key)
        active_after_race = [item for item in locks if item.get("status") in {"queued", "running"}]
        if active_after_race:
            return {
                "job_id": active_after_race[0]["job_id"],
                "status": active_after_race[0]["status"],
                "runner": "local_agent",
                "reused": True,
                "message": "已有任务运行中，已为你复用该任务",
                "markets": sorted({item["market"] for item in active_after_race}),
                "timeframe": timeframe,
                "chain_key": chain_key,
                "chain_name": chain.get("chain_name"),
            }
        raise BusinessError("SCREENING_LOCK_CREATE_FAILED", f"创建筛选锁失败：{type(exc).__name__}: {exc}") from exc
    return {
        "job_id": job_id,
        "status": "queued",
        "runner": "local_agent",
        "markets": markets,
        "timeframe": timeframe,
        "chain_key": chain_key,
        "chain_name": chain.get("chain_name"),
    }


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
    return {
        "rows": db.get_screening_results_by_task(
            task_id,
            limit=limit,
            offset=offset,
            passed_only=passed_only,
        ),
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
    chain = resolve_rule_chain(db, [market], payload.chain_key)
    run_id = str(uuid.uuid4())
    db.create_single_stock_run({
        "run_id": run_id,
        "user_id": user.id,
        "market": market,
        "code": payload.code,
        "normalized_code": normalized_code,
        "timeframe": timeframe,
        "chain_key": chain["chain_key"],
        "status": "queued",
    })
    return {
        "run_id": run_id,
        "status": "queued",
        "runner": "local_agent",
        "market": market,
        "code": normalized_code,
        "timeframe": timeframe,
        "chain_key": chain["chain_key"],
        "chain_name": chain.get("chain_name"),
    }


@app.get("/api/screening/single-stock/{run_id}")
def get_single_stock(run_id: str, _: CurrentUser = Depends(require_read_user), db: MarketDatabase = Depends(get_db)):
    result = db.get_single_stock_run(run_id)
    if not result:
        raise BusinessError("SINGLE_STOCK_NOT_FOUND", "单股任务不存在")
    return result


@app.get("/api/rules")
def get_rules(market: str = "HK", _: CurrentUser = Depends(require_read_user), db: MarketDatabase = Depends(get_db)):
    market = normalize_market(market)
    repository = RuleRepository(db)
    chains = repository.load_chains(market)
    return {
        "market": market,
        "metadata": [item.__dict__ for item in repository.load_metadata(market)],
        "chain": repository.load_active_chain(market).__dict__,
        "chains": [item.__dict__ for item in chains],
    }


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
