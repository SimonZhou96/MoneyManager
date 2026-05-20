#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import os
import time
import uuid
from datetime import date
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import urlparse

import requests

from custom_list import CustomListScreeningRunner, is_custom_list_job
from db import MarketDatabase
from feishu_notifier import send_screening_result
from fetch_stock_pools import get_db_config
from kline_fetcher import FutuKlineFetcher
from market import market_label, normalize_market
from scheduled_daily_job import get_default_screening_params, run_market_screening_worker
from stock_pool import CANONICAL_POOL_TYPES, POOL_RESULT_KEY_BY_TYPE, StockPoolCriteria, StockPoolFetcher, normalize_pool_types
from web.config import load_dotenv
from web.single_stock import SingleStockRequest, run_single_stock_analysis


POOL_MAP = dict(POOL_RESULT_KEY_BY_TYPE)


def main() -> int:
    env_args = argparse.ArgumentParser(add_help=False)
    env_args.add_argument("--env-file")
    known_env, _ = env_args.parse_known_args()
    if known_env.env_file:
        load_dotenv(Path(known_env.env_file).expanduser())
    load_dotenv()
    parser = argparse.ArgumentParser(description="Local Futu OpenD Agent -> cloud Web API sync/worker")
    parser.add_argument("--env-file", help="Optional env file, for example .agent.env")
    parser.add_argument("--mode", choices=["sync", "worker", "worker-once"], default=os.getenv("AGENT_MODE", "sync"))
    parser.add_argument("--cloud-api-base", default=(os.getenv("AGENT_API_BASE_URL") or os.getenv("CLOUD_API_BASE", "")).rstrip("/"))
    parser.add_argument("--agent-token", default=os.getenv("AGENT_API_TOKEN") or os.getenv("AGENT_TOKEN", ""))
    parser.add_argument("--agent-id", default=os.getenv("AGENT_ID", "local-opend-agent"))
    parser.add_argument("--markets", default=os.getenv("AGENT_MARKETS", "HK,US,A"))
    parser.add_argument("--timeframes", default=os.getenv("AGENT_TIMEFRAMES", "1d"))
    parser.add_argument("--futu-host", default=os.getenv("FUTU_HOST", "127.0.0.1"))
    parser.add_argument("--futu-port", type=int, default=int(os.getenv("FUTU_PORT", "11111")))
    parser.add_argument("--batch-size", type=int, default=int(os.getenv("AGENT_BATCH_SIZE", "1000")))
    parser.add_argument("--max-codes-per-market", type=int, default=int(os.getenv("AGENT_MAX_CODES_PER_MARKET", "100")))
    parser.add_argument("--max-kline-count", type=int, default=int(os.getenv("AGENT_MAX_KLINE_COUNT", "500")))
    parser.add_argument("--timeout-sec", type=int, default=int(os.getenv("AGENT_API_TIMEOUT_SEC", "120")))
    parser.add_argument("--api-retries", type=int, default=int(os.getenv("AGENT_API_RETRIES", "3")))
    parser.add_argument("--poll-interval-sec", type=int, default=int(os.getenv("AGENT_POLL_INTERVAL_SEC", "30")))
    parser.add_argument("--screening-csv", default=os.getenv("AGENT_SCREENING_CSV", "logs/agent_screening_result.csv"))
    parser.add_argument("--result-batch-size", type=int, default=int(os.getenv("AGENT_RESULT_BATCH_SIZE", "1000")))
    parser.add_argument("--result-batch-max-bytes", type=int, default=int(os.getenv("AGENT_RESULT_BATCH_MAX_BYTES", str(2 * 1024 * 1024))))
    parser.add_argument("--allow-http", action="store_true", help="Allow http:// cloud API URL for local tests")
    parser.add_argument("--dry-run", action="store_true", help="Fetch local data and print upload counts without POSTing")
    args = parser.parse_args()

    if not args.cloud_api_base:
        raise SystemExit("AGENT_API_BASE_URL or CLOUD_API_BASE is required")
    if not args.agent_token:
        raise SystemExit("AGENT_API_TOKEN or AGENT_TOKEN is required")
    if urlparse(args.cloud_api_base).scheme != "https" and not args.allow_http:
        raise SystemExit("cloud API base must use https unless --allow-http is set")

    client = CloudClient(
        args.cloud_api_base,
        args.agent_token,
        timeout_sec=args.timeout_sec,
        dry_run=args.dry_run,
        max_retries=args.api_retries,
    )

    if args.mode in {"worker", "worker-once"}:
        return run_worker(args, client)

    markets = [normalize_market(item) for item in args.markets.split(",") if item.strip()]
    timeframes = [item.strip() for item in args.timeframes.split(",") if item.strip()]

    import futu as ft

    quote_ctx = ft.OpenQuoteContext(host=args.futu_host, port=args.futu_port)
    sync_run_id = str(uuid.uuid4())

    stock_pool_rows = 0
    sector_rows = 0
    kline_rows = 0
    try:
        client.create_sync_run(sync_run_id, args.agent_id, markets, timeframes)
        fetcher = StockPoolFetcher(quote_ctx=quote_ctx)
        kline_fetcher = FutuKlineFetcher(quote_ctx)

        for market in markets:
            pools = fetcher.fetch_all_pools(
                market,
                best_criteria=StockPoolCriteria(
                    market_cap_min=5_000_000_000,
                    price_min=5.0,
                    pe_min=5.0,
                    avg_volume_min=20_000_000,
                ),
            )
            for pool_type, key in POOL_MAP.items():
                rows = pools.get(key) or []
                if rows:
                    client.push_stock_pool(sync_run_id, market, pool_type, rows)
                    stock_pool_rows += len(rows)

            memberships = fetcher.fetch_industry_memberships(market)
            if memberships:
                for row in memberships:
                    row["market"] = market
                    row["as_of_date"] = date.today().isoformat()
                for chunk in chunks(memberships, args.batch_size):
                    client.push_sector_memberships(sync_run_id, chunk)
                    sector_rows += len(chunk)

            codes = collect_codes_from_pools(pools)[: args.max_codes_per_market]
            for timeframe in timeframes:
                for code in codes:
                    df = kline_fetcher.fetch(code, market=market, timeframe=timeframe, max_count=args.max_kline_count)
                    if df is None or df.empty:
                        continue
                    rows = dataframe_to_kline_rows(df, sync_run_id, market, code, timeframe)
                    for chunk in chunks(rows, args.batch_size):
                        client.push_klines(sync_run_id, chunk)
                        kline_rows += len(chunk)
                    time.sleep(0.05)

        client.complete_sync_run(
            sync_run_id,
            status="success",
            stock_pool_rows=stock_pool_rows,
            sector_rows=sector_rows,
            kline_rows=kline_rows,
        )
        print(f"sync completed: {sync_run_id}, pools={stock_pool_rows}, sectors={sector_rows}, klines={kline_rows}")
        return 0
    except Exception as exc:
        client.complete_sync_run(
            sync_run_id,
            status="failed",
            error_message=f"{type(exc).__name__}: {exc}",
            stock_pool_rows=stock_pool_rows,
            sector_rows=sector_rows,
            kline_rows=kline_rows,
        )
        raise
    finally:
        quote_ctx.close()


class CloudClient:
    def __init__(self, base_url: str, token: str, timeout_sec: int = 120, dry_run: bool = False, max_retries: int = 3):
        self.base_url = base_url.rstrip("/")
        self.timeout_sec = int(timeout_sec)
        self.dry_run = bool(dry_run)
        self.max_retries = max(1, int(max_retries))
        self.session = requests.Session()
        self.session.headers.update({"Authorization": f"Bearer {token}"})

    def post(self, path: str, payload: dict):
        if self.dry_run:
            rows = payload.get("rows") or []
            print(f"[dry-run] POST {path}: rows={len(rows)}")
            return {"ok": True, "dry_run": True}
        last_error: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                response = self.session.post(f"{self.base_url}{path}", json=payload, timeout=self.timeout_sec)
                if response.status_code < 400:
                    data = response.json()
                    if isinstance(data, dict) and data.get("ok") is False:
                        raise RuntimeError(data.get("message") or data.get("error_code") or f"{path} failed")
                    return data
                if response.status_code in (401, 403) or attempt >= self.max_retries:
                    raise RuntimeError(f"{path} failed: HTTP {response.status_code} {response.text[:300]}")
                last_error = RuntimeError(f"{path} failed: HTTP {response.status_code} {response.text[:300]}")
            except requests.RequestException as exc:
                last_error = exc
                if attempt >= self.max_retries:
                    break
            time.sleep(min(2 ** (attempt - 1), 8))
        raise RuntimeError(f"{path} failed after {self.max_retries} attempts: {last_error}")

    def get(self, path: str, params: Optional[dict] = None):
        if self.dry_run:
            print(f"[dry-run] GET {path}: {params or {}}")
            return {"jobs": []}
        response = self.session.get(f"{self.base_url}{path}", params=params or {}, timeout=self.timeout_sec)
        if response.status_code >= 400:
            raise RuntimeError(f"{path} failed: HTTP {response.status_code} {response.text[:300]}")
        data = response.json()
        if isinstance(data, dict) and data.get("ok") is False:
            raise RuntimeError(data.get("message") or data.get("error_code") or f"{path} failed")
        return data

    def post_bytes(self, path: str, data: bytes, params: dict):
        if self.dry_run:
            print(f"[dry-run] POST {path}: bytes={len(data)}, params={params}")
            return {"ok": True, "dry_run": True}
        last_error: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                response = self.session.post(
                    f"{self.base_url}{path}",
                    params=params,
                    data=data,
                    timeout=self.timeout_sec,
                    headers={"Content-Type": params.get("content_type") or "application/octet-stream"},
                )
                if response.status_code < 400:
                    payload = response.json()
                    if isinstance(payload, dict) and payload.get("ok") is False:
                        raise RuntimeError(payload.get("message") or payload.get("error_code") or f"{path} failed")
                    return payload
                if response.status_code in (401, 403) or attempt >= self.max_retries:
                    raise RuntimeError(f"{path} failed: HTTP {response.status_code} {response.text[:300]}")
                last_error = RuntimeError(f"{path} failed: HTTP {response.status_code} {response.text[:300]}")
            except requests.RequestException as exc:
                last_error = exc
                if attempt >= self.max_retries:
                    break
            time.sleep(min(2 ** (attempt - 1), 8))
        raise RuntimeError(f"{path} failed after {self.max_retries} attempts: {last_error}")

    def pending_jobs(self, limit: int = 5):
        return self.get("/api/agent/jobs/pending", {"limit": limit}).get("jobs") or []

    def claim_job(self, job_id: str, agent_id: str):
        return self.post(f"/api/agent/jobs/{job_id}/claim", {"agent_id": agent_id})

    def heartbeat_job(self, job_id: str, agent_id: str, progress: dict):
        return self.post(f"/api/agent/jobs/{job_id}/heartbeat", {"agent_id": agent_id, "progress": progress})

    def complete_job(self, job_id: str, status: str, task_ids: List[str], summary: dict, error_message: str | None = None):
        return self.post(f"/api/agent/jobs/{job_id}/complete", {
            "status": status,
            "task_ids": task_ids,
            "summary": summary,
            "error_message": error_message,
        })

    def push_screening_task(self, job_id: str, task: dict):
        return self.post("/api/agent/screening-tasks/upsert", {"job_id": job_id, "task": task})

    def push_screening_results(self, task_id: str, check_date: str, rows: List[dict]):
        return self.post("/api/agent/screening-results/bulk-upsert", {
            "task_id": task_id,
            "check_date": check_date,
            "rows": rows,
        })

    def push_signal_analysis(self, rows: List[dict]):
        return self.post("/api/agent/signal-analysis/bulk-upsert", {"rows": rows})

    def upload_artifact(self, task_id: str, market: str, path: str):
        file_path = Path(path)
        data = file_path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        suffix = file_path.suffix.lower()
        artifact_type = "report" if suffix == ".md" else "csv"
        content_type = mimetypes.guess_type(file_path.name)[0] or (
            "text/markdown; charset=utf-8" if suffix == ".md" else "text/csv; charset=utf-8"
        )
        return self.post_bytes("/api/agent/artifacts/upload", data, {
            "task_id": task_id,
            "market": market,
            "artifact_type": artifact_type,
            "file_name": file_path.name,
            "content_type": content_type,
            "checksum": digest,
        })

    def complete_single_stock(self, run_id: str, result: dict, rule_details: List[dict]):
        return self.post(f"/api/agent/single-stock-runs/{run_id}/complete", {
            "result": result,
            "rule_details": rule_details,
        })

    def create_sync_run(self, sync_run_id: str, agent_id: str, markets: List[str], timeframes: List[str]):
        return self.post("/api/agent/sync-runs", {
            "sync_run_id": sync_run_id,
            "agent_id": agent_id,
            "markets": markets,
            "timeframes": timeframes,
            "metadata": {"source": "local_agent"},
        })

    def push_stock_pool(self, sync_run_id: str, market: str, pool_type: str, rows: List[dict]):
        return self.post("/api/agent/stock-pools/bulk-upsert", {
            "sync_run_id": sync_run_id,
            "market": market,
            "pool_type": pool_type,
            "rows": rows,
        })

    def push_sector_memberships(self, sync_run_id: str, rows: List[dict]):
        return self.post("/api/agent/sector-memberships/bulk-upsert", {
            "sync_run_id": sync_run_id,
            "rows": rows,
        })

    def push_klines(self, sync_run_id: str, rows: List[dict]):
        return self.post("/api/agent/klines/bulk-upsert", {
            "sync_run_id": sync_run_id,
            "rows": rows,
            "prune": True,
        })

    def complete_sync_run(
        self,
        sync_run_id: str,
        status: str,
        error_message: str | None = None,
        stock_pool_rows: int = 0,
        sector_rows: int = 0,
        kline_rows: int = 0,
    ):
        return self.post(f"/api/agent/sync-runs/{sync_run_id}/complete", {
            "status": status,
            "error_message": error_message,
            "stock_pool_rows": stock_pool_rows,
            "sector_rows": sector_rows,
            "kline_rows": kline_rows,
        })


def run_worker(args, client: CloudClient) -> int:
    print(f"agent worker started: agent_id={args.agent_id}, mode={args.mode}")
    while True:
        processed = process_pending_jobs_once(args, client)
        if args.mode == "worker-once":
            return 0 if processed >= 0 else 1
        if processed == 0:
            time.sleep(max(5, int(args.poll_interval_sec)))


def process_pending_jobs_once(args, client: CloudClient) -> int:
    try:
        jobs = client.pending_jobs(limit=5)
    except Exception as exc:
        print(f"cloud API is not reachable, will retry: {type(exc).__name__}: {exc}")
        return 0
    if not jobs:
        print("no pending cloud jobs")
        return 0
    processed = 0
    for job in jobs:
        job_id = job.get("job_id") or job.get("run_id")
        if not job_id:
            continue
        try:
            claim_result = client.claim_job(job_id, args.agent_id)
            claimed_job = claim_result.get("job") if isinstance(claim_result, dict) else None
            if isinstance(claimed_job, dict):
                job = {**job, **claimed_job}
        except Exception as exc:
            print(f"claim skipped: {job_id}: {type(exc).__name__}: {exc}")
            continue
        if job.get("job_type") == "single_stock":
            process_single_stock_job(args, client, job)
        elif is_custom_list_job(job):
            try:
                process_custom_list_job(args, client, job)
            except Exception as exc:
                message = f"{type(exc).__name__}: {exc}"
                print(f"custom list job {job_id} failed: {message}")
                client.complete_job(job_id, "failed", [], {"warnings": [message]}, error_message=message)
        else:
            try:
                process_screening_job(args, client, job)
            except Exception as exc:
                message = f"{type(exc).__name__}: {exc}"
                print(f"screening job {job_id} failed: {message}")
                client.complete_job(job_id, "failed", [], {"warnings": [message]}, error_message=message)
        processed += 1
    return processed


def process_screening_job(args, client: CloudClient, job: dict) -> None:
    job_id = job["job_id"]
    markets = [normalize_market(item) for item in (job.get("markets") or [])]
    timeframe = str(job.get("timeframe") or "1d")
    options = job.get("options") or {}
    enable_ai_analysis = bool(options.get("enable_ai_analysis", True))
    send_feishu = bool(options.get("send_feishu", False))
    chain_key = job.get("chain_key") or options.get("chain_key")
    pool_types = normalize_pool_types(options.get("pool_types") or CANONICAL_POOL_TYPES)
    mysql_config = get_db_config()
    default_params = get_default_screening_params()
    if chain_key:
        default_params["chain_key"] = chain_key
    csv_base = args.screening_csv[:-4] if args.screening_csv.endswith(".csv") else args.screening_csv
    today_str = date.today().strftime("%Y-%m-%d")
    webhook_url = os.getenv("FEISHU_WEBHOOK_URL", "").strip()
    task_ids: List[str] = []
    warnings: List[str] = []
    market_statuses: Dict[str, dict] = {}
    summary: Dict[str, Any] = {
        "markets": markets,
        "timeframe": timeframe,
        "chain_key": chain_key,
        "chain_timeframe": job.get("chain_timeframe") or options.get("chain_timeframe"),
        "chain_name": job.get("chain_name") or options.get("chain_name"),
        "pool_types": pool_types,
        "result_upload_scope": "passed_only",
        "market_statuses": market_statuses,
    }

    for market in markets:
        client.heartbeat_job(job_id, args.agent_id, {"current_market": market, "stage": "screening"})
        result = run_market_screening_worker(
            mysql_config=mysql_config,
            market=market,
            timeframe=timeframe,
            default_params=default_params,
            csv_base=csv_base,
            today_str=today_str,
            verbose=False,
            enable_ai_analysis=enable_ai_analysis,
            chain_key=chain_key,
            pool_types=pool_types,
        )
        if result.error:
            warnings.append(f"{market}: {result.error}")
            market_statuses[market] = {"status": "failed", "error_message": result.error}
            continue
        if not result.task_id:
            warnings.append(f"{market}: 未生成筛选任务")
            market_statuses[market] = {"status": "failed", "error_message": "未生成筛选任务"}
            continue
        try:
            upload_market_result(
                args=args,
                client=client,
                mysql_config=mysql_config,
                job_id=job_id,
                market=market,
                task_id=result.task_id,
                csv_paths=result.csv_paths,
                result_upload_scope="passed_only",
            )
            task_ids.append(result.task_id)
            market_statuses[market] = {"status": "completed", "task_id": result.task_id}
            if send_feishu and webhook_url and result.csv_paths:
                sent = send_screening_result(
                    webhook_url,
                    f"【Web本地Agent筛选】{today_str} - {market_label(market)}\n通过: {len(result.passed)} 只",
                    result.csv_paths,
                )
                if not sent:
                    warnings.append(f"{market}: 飞书发送不完整")
        except Exception as exc:
            message = f"上传云端失败: {type(exc).__name__}: {exc}"
            warnings.append(f"{market}: {message}")
            market_statuses[market] = {"status": "failed", "error_message": message}

    summary["task_ids"] = task_ids
    summary["warnings"] = warnings
    status = "completed" if len(task_ids) == len(markets) else "failed"
    error_message = None if status == "completed" else "部分或全部市场筛选失败"
    client.complete_job(job_id, status, task_ids, summary, error_message=error_message)
    print(f"screening job {job_id} finished: {status}, tasks={task_ids}")


def upload_market_result(
    args,
    client: CloudClient,
    mysql_config,
    job_id: str,
    market: str,
    task_id: str,
    csv_paths: List[str],
    result_upload_scope: str = "passed_only",
) -> None:
    db = MarketDatabase(mysql_config)
    try:
        task = db.get_task_by_id(task_id)
        if not task:
            raise RuntimeError(f"local task not found: {task_id}")
        total_count = int(task.get("total_count") or 0)
        passed_count = db.count_screening_results_by_task(task_id, passed_only=True)
        task["job_id"] = job_id
        task["passed_count"] = passed_count
        task["failed_count"] = max(0, total_count - passed_count)
        task["uploaded_result_scope"] = result_upload_scope
        client.push_screening_task(job_id, task)

        offset = 0
        passed_only = result_upload_scope != "all"
        while True:
            rows = db.get_screening_results_by_task(
                task_id,
                limit=args.result_batch_size,
                offset=offset,
                passed_only=passed_only,
            )
            if not rows:
                break
            for chunk in chunk_rows_by_payload_budget(
                [prepare_screening_row_for_upload(row, task_id) for row in rows],
                {"task_id": task_id, "check_date": task["check_date"]},
                max_rows=args.result_batch_size,
                max_bytes=args.result_batch_max_bytes,
            ):
                client.push_screening_results(task_id, task["check_date"], chunk)
            offset += args.result_batch_size

        analysis_rows = db.get_signal_analysis_results_by_task(task_id)
        for chunk in chunk_rows_by_payload_budget(
            analysis_rows,
            {},
            max_rows=args.result_batch_size,
            max_bytes=args.result_batch_max_bytes,
        ):
            client.push_signal_analysis(chunk)

        for path in csv_paths:
            try:
                client.upload_artifact(task_id, market, path)
            except Exception as exc:
                print(f"artifact upload failed: {path}: {type(exc).__name__}: {exc}")
    finally:
        db.close()


def process_custom_list_job(args, client: CloudClient, job: dict) -> None:
    job_id = job["job_id"]
    options = job.get("options") or {}
    market = normalize_market((job.get("markets") or [None])[0] or "HK")
    timeframe = str(job.get("timeframe") or "1d")
    enable_ai_analysis = bool(options.get("enable_ai_analysis", True))
    send_feishu = bool(options.get("send_feishu", False))
    mysql_config = get_db_config()
    csv_base = args.screening_csv[:-4] if args.screening_csv.endswith(".csv") else args.screening_csv
    today_str = date.today().strftime("%Y-%m-%d")
    webhook_url = os.getenv("FEISHU_WEBHOOK_URL", "").strip()
    summary: Dict[str, Any] = {
        "markets": [market],
        "timeframe": timeframe,
        "chain_key": job.get("chain_key") or options.get("chain_key"),
        "chain_timeframe": job.get("chain_timeframe") or options.get("chain_timeframe"),
        "chain_name": job.get("chain_name") or options.get("chain_name"),
        "result_upload_scope": "all",
        "input_summary": options.get("input_summary") or {},
        "market_statuses": {},
        "warnings": [],
    }

    client.heartbeat_job(job_id, args.agent_id, {"current_market": market, "stage": "custom_list_screening"})
    result = CustomListScreeningRunner(mysql_config).run(
        job=job,
        csv_base=csv_base,
        today_str=today_str,
        enable_ai_analysis=enable_ai_analysis,
    )
    if not result.task_id:
        raise RuntimeError("自定义股票列表未生成筛选任务")

    upload_market_result(
        args=args,
        client=client,
        mysql_config=mysql_config,
        job_id=job_id,
        market=market,
        task_id=result.task_id,
        csv_paths=result.csv_paths,
        result_upload_scope="all",
    )
    summary["task_ids"] = [result.task_id]
    summary["market_statuses"] = {market: {"status": "completed", "task_id": result.task_id}}
    summary["passed_count"] = len(result.passed)
    summary["total_count"] = result.total_count

    if send_feishu and webhook_url and result.csv_paths:
        sent = send_screening_result(
            webhook_url,
            f"【Web自定义列表筛选】{today_str} - {market_label(market)}\n通过: {len(result.passed)} 只",
            result.csv_paths,
        )
        if not sent:
            summary["warnings"].append(f"{market}: 飞书发送不完整")
    client.complete_job(job_id, "completed", [result.task_id], summary, error_message=None)
    print(f"custom list job {job_id} finished: completed, task={result.task_id}")


def process_single_stock_job(args, client: CloudClient, job: dict) -> None:
    run_id = job["run_id"]
    try:
        client.heartbeat_job(run_id, args.agent_id, {"stage": "single_stock"})
        result = run_single_stock_analysis(
            mysql_config=get_db_config(),
            request=SingleStockRequest(
                market=job["market"],
                code=job.get("code") or job.get("normalized_code"),
                timeframe=job.get("timeframe") or "1d",
                chain_key=job.get("chain_key"),
                user_id=job.get("user_id"),
                run_id=run_id,
            ),
        )
        details = result.get("rule_chain", {}).get("details") or []
        client.complete_single_stock(run_id, result, details)
        print(f"single stock job {run_id} finished: passed={result.get('passed')}")
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"
        client.complete_single_stock(run_id, {"status": "failed", "passed": False, "warnings": [message]}, [])
        print(f"single stock job {run_id} failed: {message}")


def prepare_screening_row_for_upload(row: dict, task_id: str) -> dict:
    item = dict(row)
    item["task_id"] = task_id
    item["is_passed"] = bool(item.get("is_passed", True))
    details = item.get("filter_details")
    if details is not None and json_payload_size(details) > 64 * 1024:
        item["filter_details"] = [{
            "details_truncated": True,
            "reason": "filter_details exceeded 64KB during Agent upload",
        }]
    return item


def chunk_rows_by_payload_budget(rows: List[dict], base_payload: dict, max_rows: int, max_bytes: int) -> Iterable[List[dict]]:
    current: List[dict] = []
    for row in rows:
        candidate = current + [row]
        if current and (len(candidate) > max_rows or json_payload_size({**base_payload, "rows": candidate}) > max_bytes):
            yield current
            current = [row]
        else:
            current = candidate
    if current:
        yield current


def json_payload_size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, default=str).encode("utf-8"))


def collect_codes_from_pools(pools: Dict[str, List[dict]]) -> List[str]:
    result = []
    seen = set()
    for key in POOL_MAP.values():
        for item in pools.get(key) or []:
            code = str(item.get("code") or "").strip()
            if code and code not in seen:
                seen.add(code)
                result.append(code)
    return result


def dataframe_to_kline_rows(df, sync_run_id: str, market: str, code: str, timeframe: str) -> List[dict]:
    rows = []
    for _, row in df.iterrows():
        bar_time = _format_bar_time(row.get("date"))
        rows.append({
            "sync_run_id": sync_run_id,
            "market": market,
            "code": code,
            "timeframe": timeframe,
            "bar_time": bar_time,
            "open": _number(row.get("open")),
            "high": _number(row.get("high")),
            "low": _number(row.get("low")),
            "close": _number(row.get("close")),
            "volume": _number(row.get("volume")),
            "turnover": _number(row.get("turnover")),
            "source": "opend_cache",
        })
    return rows


def _format_bar_time(value):
    if hasattr(value, "to_pydatetime"):
        value = value.to_pydatetime()
    if hasattr(value, "strftime"):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    return value


def chunks(items: List[dict], size: int) -> Iterable[List[dict]]:
    for start in range(0, len(items), max(1, size)):
        yield items[start:start + max(1, size)]


def _number(value):
    try:
        if value is None or value != value:
            return None
        return float(value)
    except Exception:
        return None


if __name__ == "__main__":
    raise SystemExit(main())
