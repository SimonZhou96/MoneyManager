#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
定时任务 - 天级别执行（实现文件，入口见上级目录 scheduled_daily_job.py）

流程：
1. 捞取港股、A股、美股股票池数据，写入数据库
2. 对每个市场的股票池分别执行筛选逻辑
3. 将满足条件的股票导出为 CSV
4. 通过飞书 Webhook 发送结果

用法:
    cd stock_screener && python3 jobs/scheduled_daily_job.py
    或: python3 scheduled_daily_job.py  （根目录薄入口转发）

cron 示例:
    0 18 * * * cd /path/to/stock_screener && python3 scheduled_daily_job.py
"""

import argparse
import csv
import json
import os
import sys
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import List, Optional, Tuple

# stock_screener 包根目录（本文件在 jobs/ 下）
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


def _load_dotenv():
    """加载 stock_screener 根目录下的 .env"""
    env_path = Path(_ROOT) / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


from api.screen_service import run_screening_task
from db import MarketDatabase, MySqlConfig
from feishu_notifier import send_feishu_text, send_screening_result
from fetch_stock_pools import (
    fetch_and_save_best_stocks,
    fetch_and_save_etf_list,
    fetch_and_save_index_constituents,
    fetch_and_save_industry_leaders,
    fetch_and_save_recent_ipos,
    get_db_config,
)
from market import market_label, normalize_market
from timeframe import parse_timeframe

STRATEGY_NAME_MAP = {
    "EMABreakoutStrategizer": "EMA突破",
    "RSIOversoldStrategizer": "RSI超卖",
    "RSIOverboughtStrategizer": "RSI超买",
}


def fetch_all_markets_pools(
    db: MarketDatabase,
    fetcher,
    markets: List[str],
    pools: List[str],
) -> None:
    for market in markets:
        market = normalize_market(market)
        if "best" in pools:
            fetch_and_save_best_stocks(fetcher, db, market)
        if "index" in pools:
            fetch_and_save_index_constituents(fetcher, db, market)
        if "industry" in pools:
            fetch_and_save_industry_leaders(fetcher, db, market, top_n=5)
        if "ipo" in pools:
            fetch_and_save_recent_ipos(fetcher, db, market, days=730)
        if "etf" in pools:
            fetch_and_save_etf_list(fetcher, db, market)


def get_merged_pool_stocks(db: MarketDatabase, market: str) -> List[dict]:
    pool_types = ["best", "index", "industry", "ipo", "etf"]
    seen = set()
    result = []
    for pool_type in pool_types:
        stocks = db.get_stock_pool(market, pool_type, limit=None)
        for s in stocks:
            code = (s.get("code") or "").strip()
            if not code or code in seen:
                continue
            seen.add(code)
            result.append({
                "code": code,
                "name": s.get("name") or code,
                "market_cap": s.get("market_cap"),
                "pe_ratio": s.get("pe_ratio"),
                "industry": s.get("industry_name"),
                "sector": s.get("industry_name"),
            })
    return result


def run_screening_for_market(
    mysql_config: MySqlConfig,
    market: str,
    timeframe: str,
    default_params: dict,
    verbose: bool = False,
) -> Tuple[Optional[str], List[dict]]:
    db = MarketDatabase(mysql_config)
    db.init_schema(timeframe)

    watchlist = get_merged_pool_stocks(db, market)
    if not watchlist:
        db.close()
        return None, []

    task_id = str(uuid.uuid4())
    db.create_screening_task(
        task_id=task_id,
        market=market,
        timeframe=timeframe,
        total_count=len(watchlist),
        params_json=default_params,
        check_date=date.today(),
    )
    db.close()

    run_screening_task(
        mysql_config=mysql_config,
        task_id=task_id,
        market=market,
        timeframe=timeframe,
        params=default_params,
        verbose=verbose,
        watchlist=watchlist,
        progress_log=True,
    )

    db2 = MarketDatabase(mysql_config)
    sql = """
        SELECT code, name, filter_details, sector, industry, market_cap, pe_ratio
        FROM screening_results
        WHERE task_id=%s AND is_passed=1
        ORDER BY code
    """
    with db2.conn.cursor() as cursor:
        cursor.execute(sql, (task_id,))
        rows = cursor.fetchall() or []
    db2.close()

    passed = []
    for row in rows:
        code, name, fd_raw, sector, industry, market_cap, pe_ratio = row
        conditions = []
        if fd_raw:
            try:
                fd = json.loads(fd_raw) if isinstance(fd_raw, str) else fd_raw
                if isinstance(fd, list):
                    for d in fd:
                        if d.get("result") == "pass" and d.get("filter_name") in STRATEGY_NAME_MAP:
                            conditions.append(STRATEGY_NAME_MAP[d["filter_name"]])
            except Exception:
                pass
        passed.append({
            "code": code,
            "name": name or code,
            "market": market,
            "sector": sector or industry or "",
            "industry": industry or "",
            "market_cap": market_cap,
            "pe_ratio": pe_ratio,
            "conditions_met": "|".join(conditions) if conditions else "",
        })
    return task_id, passed


def write_screening_csv(records: List[dict], csv_path: str) -> None:
    if not records:
        return
    os.makedirs(os.path.dirname(csv_path) or ".", exist_ok=True)
    columns = [
        ("股票代码", "code"),
        ("市场", "market_label"),
        ("名称", "name"),
        ("pe", "pe_ratio"),
        ("市值", "market_cap"),
        ("所属板块", "sector"),
        ("满足的条件", "conditions_met"),
    ]
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow([c[0] for c in columns])
        for r in records:
            row = []
            for _, key in columns:
                if key == "market_label":
                    val = market_label(r.get("market", ""))
                else:
                    val = r.get(key)
                if val is None:
                    val = ""
                elif isinstance(val, float):
                    val = f"{val:.4g}" if val == val else ""
                row.append(str(val) if val != "" else "")
            w.writerow(row)


def get_default_screening_params() -> dict:
    return {
        "use_ema_breakout": True,
        "ema_short": 10,
        "ema_long": 150,
        "rsi_period": 14,
        "rsi_oversold_threshold": 30.0,
        "rsi_overbought_threshold": 70.0,
    }


def main():
    _load_dotenv()
    parser = argparse.ArgumentParser(description="定时任务 - 捞池+筛选+CSV+飞书")
    parser.add_argument("--no-fetch", action="store_true", help="跳过股票池捞取")
    parser.add_argument("--no-feishu", action="store_true", help="不发送飞书消息")
    parser.add_argument("--markets", default="HK,A,US", help="市场列表，逗号分隔")
    parser.add_argument("--pools", default="best,index,industry,ipo,etf", help="股票池类型")
    parser.add_argument("--timeframe", default="1d", help="K线周期")
    parser.add_argument(
        "--csv",
        default="logs/screening_result.csv",
        help="CSV 输出路径（会按日期+市场拆分）",
    )
    parser.add_argument("--futu-host", default="127.0.0.1", help="Futu OpenD 主机")
    parser.add_argument("--futu-port", type=int, default=11111, help="Futu OpenD 端口")
    args = parser.parse_args()

    mysql_config = get_db_config()

    db = MarketDatabase(mysql_config)
    db.init_stock_pool_schema()

    markets = [normalize_market(m) for m in args.markets.split(",") if m.strip()]
    pools = [p.strip().lower() for p in args.pools.split(",") if p.strip()]
    timeframe = parse_timeframe(args.timeframe)

    print(f"[{datetime.now()}] 定时任务开始 | 市场: {markets} | 股票池: {pools}")

    if not args.no_fetch:
        try:
            import futu as ft
            from stock_pool import StockPoolFetcher

            quote_ctx = ft.OpenQuoteContext(host=args.futu_host, port=args.futu_port)
            fetcher = StockPoolFetcher(quote_ctx=quote_ctx, db=db)
            fetch_all_markets_pools(db, fetcher, markets, pools)
            quote_ctx.close()
        except Exception as e:
            print(f"捞取股票池失败: {e}")
            db.close()
            return 1
    else:
        print("跳过捞取，使用已有股票池数据")

    params = get_default_screening_params()
    webhook_url = os.getenv("FEISHU_WEBHOOK_URL", "").strip()

    csv_base = args.csv
    if csv_base.endswith(".csv"):
        csv_base = csv_base[:-4]
    today_str = date.today().strftime("%Y-%m-%d")

    for market in markets:
        print(f"\n--- 开始筛选 {market_label(market)} ---")
        _, passed = run_screening_for_market(
            mysql_config=mysql_config,
            market=market,
            timeframe=timeframe,
            default_params=params,
            verbose=False,
        )
        print(f"  {market_label(market)}: {len(passed)} 只通过")

        csv_path = f"{csv_base}_{today_str}_{market}.csv"
        if passed:
            write_screening_csv(passed, csv_path)
            print(f"✓ CSV 已导出: {csv_path}")
        else:
            print(f"  {market_label(market)} 无满足条件的股票，不生成 CSV")

        if not args.no_feishu and webhook_url:
            summary_lines = [
                f"【定时筛选】{date.today()} - {market_label(market)}",
                f"通过: {len(passed)} 只",
            ]
            if passed:
                summary_lines.append(f"CSV: {csv_path}")
                send_screening_result(webhook_url, "\n".join(summary_lines), csv_path)
            else:
                send_feishu_text(webhook_url, "\n".join(summary_lines))
            print(f"✓ 已发送 {market_label(market)} 飞书消息")

    db.close()

    if not args.no_feishu and not webhook_url:
        print("未配置 FEISHU_WEBHOOK_URL，跳过飞书发送")

    print(f"[{datetime.now()}] 定时任务结束")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
