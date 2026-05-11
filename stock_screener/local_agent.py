#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import time
import uuid
from datetime import date
from pathlib import Path
from typing import Dict, Iterable, List
from urllib.parse import urlparse

import requests

from kline_fetcher import FutuKlineFetcher
from market import normalize_market
from stock_pool import StockPoolCriteria, StockPoolFetcher
from web.config import load_dotenv


POOL_MAP = {
    "best": "best_stocks",
    "industry": "industry_leaders",
    "ipo": "recent_ipos",
    "etf": "etf_list",
}


def main() -> int:
    env_args = argparse.ArgumentParser(add_help=False)
    env_args.add_argument("--env-file")
    known_env, _ = env_args.parse_known_args()
    if known_env.env_file:
        load_dotenv(Path(known_env.env_file).expanduser())
    load_dotenv()
    parser = argparse.ArgumentParser(description="Local Futu OpenD Agent -> cloud Web API sync")
    parser.add_argument("--env-file", help="Optional env file, for example .agent.env")
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
    parser.add_argument("--allow-http", action="store_true", help="Allow http:// cloud API URL for local tests")
    parser.add_argument("--dry-run", action="store_true", help="Fetch local data and print upload counts without POSTing")
    args = parser.parse_args()

    if not args.cloud_api_base:
        raise SystemExit("AGENT_API_BASE_URL or CLOUD_API_BASE is required")
    if not args.agent_token:
        raise SystemExit("AGENT_API_TOKEN or AGENT_TOKEN is required")
    if urlparse(args.cloud_api_base).scheme != "https" and not args.allow_http:
        raise SystemExit("cloud API base must use https unless --allow-http is set")

    markets = [normalize_market(item) for item in args.markets.split(",") if item.strip()]
    timeframes = [item.strip() for item in args.timeframes.split(",") if item.strip()]

    import futu as ft

    quote_ctx = ft.OpenQuoteContext(host=args.futu_host, port=args.futu_port)
    client = CloudClient(
        args.cloud_api_base,
        args.agent_token,
        timeout_sec=args.timeout_sec,
        dry_run=args.dry_run,
        max_retries=args.api_retries,
    )
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
                    return response.json()
                if response.status_code in (401, 403) or attempt >= self.max_retries:
                    raise RuntimeError(f"{path} failed: HTTP {response.status_code} {response.text[:300]}")
                last_error = RuntimeError(f"{path} failed: HTTP {response.status_code} {response.text[:300]}")
            except requests.RequestException as exc:
                last_error = exc
                if attempt >= self.max_retries:
                    break
            time.sleep(min(2 ** (attempt - 1), 8))
        raise RuntimeError(f"{path} failed after {self.max_retries} attempts: {last_error}")

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


def collect_codes_from_pools(pools: Dict[str, List[dict]]) -> List[str]:
    result = []
    seen = set()
    for key in ("best_stocks", "index_constituents", "industry_leaders", "recent_ipos", "etf_list"):
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
