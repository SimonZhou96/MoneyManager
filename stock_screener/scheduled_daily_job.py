#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
定时任务 - 天级别执行

流程：
1. 捞取港股、A股、美股股票池数据，写入数据库
2. 对每个市场的股票池分别执行筛选逻辑
3. 将满足条件的股票导出为 CSV，并额外拆分非 ETF / ETF 两份 CSV
4. 通过飞书 Webhook 发送结果

用法:
    python3 scheduled_daily_job.py
    python3 scheduled_daily_job.py --no-fetch   # 跳过捞取，仅筛选+导出+飞书
    python3 scheduled_daily_job.py --no-feishu  # 不发送飞书
    python3 scheduled_daily_job.py --require-fresh-pools  # 抓池失败时直接退出，不回退旧池数据

cron 示例（每天 18:00 执行，收盘后）:
    0 18 * * * cd /path/to/stock_screener && python3 scheduled_daily_job.py

环境变量:
    MYSQL_HOST, MYSQL_PORT, MYSQL_USER, MYSQL_PASSWORD, MYSQL_DATABASE
    FEISHU_WEBHOOK_URL  飞书机器人 Webhook 地址（不配置则跳过飞书发送）
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

# 添加项目根目录到 path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _load_dotenv():
    """加载同目录 .env"""
    env_path = Path(__file__).parent / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

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
from api.screen_service import run_screening_task
from timeframe import parse_timeframe


# 策略名映射（用于 CSV 中「满足的条件」）
STRATEGY_NAME_MAP = {
    "EMABreakoutStrategizer": "EMA突破",
    "RSIOversoldStrategizer": "RSI超卖",
    "RSIOverboughtStrategizer": "RSI超买",
    "TodayVolumeExceedsPrior3MaxStrategizer": "放量超前三日",
    "DailyDrop6To65Strategizer": "当日跌6%~6.5%",
    "DailyRise4To45Strategizer": "当日涨4%~4.5%",
}


# ------------------------------------------------------------------
# 1. 股票池捞取
# ------------------------------------------------------------------


def fetch_all_markets_pools(
    db: MarketDatabase,
    fetcher,
    markets: List[str],
    pools: List[str],
) -> None:
    """捞取多市场股票池并更新到数据库"""
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


# ------------------------------------------------------------------
# 2. 合并股票池 + 筛选
# ------------------------------------------------------------------


def get_merged_pool_stocks(db: MarketDatabase, market: str) -> List[dict]:
    """
    合并指定市场所有股票池类型，按 code 去重。
    返回格式兼容 screen_service 的 watchlist。
    """
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
            # 映射字段: industry_name -> industry
            result.append({
                "code": code,
                "name": s.get("name") or code,
                "market_cap": s.get("market_cap"),
                "pe_ratio": s.get("pe_ratio"),
                "industry": s.get("industry_name"),
                "sector": s.get("industry_name"),  # 股票池无 sector，用 industry 代替
            })
    return result


def has_merged_pool_stocks(db: MarketDatabase, market: str) -> bool:
    """检查指定市场是否存在可用于筛选的合并股票池数据。"""
    return len(get_merged_pool_stocks(db, market)) > 0


def get_etf_codes(db: MarketDatabase, market: str) -> set[str]:
    """获取指定市场 ETF 股票池中的代码集合，用于导出拆分。"""
    return {
        (s.get("code") or "").strip()
        for s in db.get_stock_pool(market, "etf", limit=None)
        if (s.get("code") or "").strip()
    }


def run_screening_for_market(
    mysql_config: MySqlConfig,
    market: str,
    timeframe: str,
    default_params: dict,
    verbose: bool = False,
) -> Tuple[Optional[str], List[dict]]:
    """
    对指定市场的合并股票池执行筛选。

    Returns:
        (task_id, passed_stocks)  # passed_stocks 为通过的股票列表
    """
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

    # 查询通过筛选的股票
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


# ------------------------------------------------------------------
# 3. CSV 导出
# ------------------------------------------------------------------


def write_screening_csv(records: List[dict], csv_path: str) -> None:
    """
    将满足条件的股票写入 CSV。
    列：code, 市场, 名称, pe, 市值, 所属板块, 满足的条件
    """
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
                    val = f"{val:.4g}" if val == val else ""  # 避免 nan
                row.append(str(val) if val != "" else "")
            w.writerow(row)


def write_split_screening_csvs(records: List[dict], csv_base_path: str, etf_codes: set[str]) -> Tuple[str, str]:
    """
    按 ETF 归属拆分写入两份 CSV。

    Returns:
        (no_etf_csv_path, etf_only_csv_path)
    """
    no_etf_records = [r for r in records if (r.get("code") or "").strip() not in etf_codes]
    etf_records = [r for r in records if (r.get("code") or "").strip() in etf_codes]

    if csv_base_path.endswith(".csv"):
        csv_base_path = csv_base_path[:-4]
    no_etf_path = f"{csv_base_path}_no_etf.csv"
    etf_only_path = f"{csv_base_path}_etf_only.csv"

    write_screening_csv(no_etf_records, no_etf_path)
    write_screening_csv(etf_records, etf_only_path)
    return no_etf_path, etf_only_path


# ------------------------------------------------------------------
# 4. 主流程
# ------------------------------------------------------------------


def get_default_screening_params() -> dict:
    """默认筛选参数：EMA 突破 + RSI 等策略"""
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
    parser.add_argument(
        "--require-fresh-pools",
        action="store_true",
        help="抓池失败时直接退出；默认会回退使用数据库中的已有股票池数据",
    )
    parser.add_argument("--markets", default="HK,A,US", help="市场列表，逗号分隔")
    parser.add_argument("--pools", default="best,index,industry,ipo,etf", help="股票池类型")
    parser.add_argument("--timeframe", default="1d", help="K线周期")
    parser.add_argument("--csv", default="logs/screening_result.csv", help="CSV 输出路径（会按日期+市场拆分为 screening_result_2026-02-27_HK.csv，并额外生成 _no_etf/_etf_only 两份）")
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

    # 1. 捞取股票池（可选）
    using_stale_pools = bool(args.no_fetch)
    fetch_failed = False
    if not args.no_fetch:
        try:
            import futu as ft
            from stock_pool import StockPoolFetcher
            quote_ctx = ft.OpenQuoteContext(host=args.futu_host, port=args.futu_port)
            fetcher = StockPoolFetcher(quote_ctx=quote_ctx, db=db)
            fetch_all_markets_pools(db, fetcher, markets, pools)
            quote_ctx.close()
        except Exception as e:
            fetch_failed = True
            print(f"捞取股票池失败: {e}")
            if args.require_fresh_pools:
                db.close()
                return 1
            print("继续使用数据库中已有股票池数据")
            using_stale_pools = True
    else:
        print("跳过捞取，使用已有股票池数据")

    # 2. 对每个市场筛选 + 分市场导出 CSV + 分市场发送飞书
    params = get_default_screening_params()
    webhook_url = os.getenv("FEISHU_WEBHOOK_URL", "").strip()

    # 解析 CSV 输出路径：logs/screening_result.csv -> logs/screening_result
    # 命名格式：{base}_{date}_{market}.csv，区分不同天、不同市场
    csv_base = args.csv
    if csv_base.endswith(".csv"):
        csv_base = csv_base[:-4]
    today_str = date.today().strftime("%Y-%m-%d")
    processed_markets: List[str] = []
    skipped_markets: List[str] = []

    for market in markets:
        print(f"\n--- 开始筛选 {market_label(market)} ---")
        if not has_merged_pool_stocks(db, market):
            skipped_markets.append(market)
            print(f"  {market_label(market)} 无可用股票池数据，跳过")
            continue

        task_id, passed = run_screening_for_market(
            mysql_config=mysql_config,
            market=market,
            timeframe=timeframe,
            default_params=params,
            verbose=False,
        )
        if not task_id:
            skipped_markets.append(market)
            print(f"  {market_label(market)} 未创建筛选任务，跳过")
            continue

        processed_markets.append(market)
        print(f"  {market_label(market)}: {len(passed)} 只通过")

        # 3. 导出该市场 CSV（含日期，避免天级运行时互相覆盖）
        csv_path = f"{csv_base}_{today_str}_{market}.csv"
        csv_paths: List[str] = []
        if passed:
            write_screening_csv(passed, csv_path)
            csv_paths.append(csv_path)
            print(f"✓ CSV 已导出: {csv_path}")
            no_etf_path, etf_only_path = write_split_screening_csvs(
                records=passed,
                csv_base_path=csv_path,
                etf_codes=get_etf_codes(db, market),
            )
            csv_paths.extend([no_etf_path, etf_only_path])
            print(f"✓ 非 ETF CSV 已导出: {no_etf_path}")
            print(f"✓ ETF CSV 已导出: {etf_only_path}")
        else:
            print(f"  {market_label(market)} 无满足条件的股票，不生成 CSV")

        # 4. 发送该市场飞书消息
        if not args.no_feishu and webhook_url:
            summary_lines = [
                f"【定时筛选】{date.today()} - {market_label(market)}",
                f"通过: {len(passed)} 只",
            ]
            if passed:
                summary_lines.extend(["CSV 文件:", *csv_paths])
                send_screening_result(webhook_url, "\n".join(summary_lines), csv_paths)
            else:
                send_feishu_text(webhook_url, "\n".join(summary_lines))
            print(f"✓ 已发送 {market_label(market)} 飞书消息")

    db.close()

    if not args.no_feishu and not webhook_url:
        print("未配置 FEISHU_WEBHOOK_URL，跳过飞书发送")

    if skipped_markets:
        skipped_labels = ", ".join(market_label(m) for m in skipped_markets)
        print(f"跳过的市场: {skipped_labels}")

    if fetch_failed and using_stale_pools:
        print("本次任务在抓池失败后回退使用了数据库中的已有股票池数据")

    if not processed_markets:
        print("没有任何市场完成筛选，任务失败")
        return 1

    print(f"[{datetime.now()}] 定时任务结束")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
