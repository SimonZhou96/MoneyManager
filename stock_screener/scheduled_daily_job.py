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
    ENABLE_LLM_ANALYSIS  是否自动启用搜索+模型辅助分析，默认 1
    TAVILY_API_KEY       搜索 provider key（不配置则跳过联网检索）
    LLM_PROVIDER=openai_compatible|codex_responses|deepseek  AI 分析模型 provider
    LLM_API_BASE, LLM_API_KEY, LLM_MODEL  OpenAI-compatible 模型配置
    CODEX_API_BASE, CODEX_API_KEY, CODEX_LLM_MODEL, CODEX_REASONING_EFFORT  Codex Responses 配置
    DEEPSEEK_API_BASE, DEEPSEEK_API_KEY, DEEPSEEK_LLM_MODEL  DeepSeek Chat Completions 配置
    SECTOR_SYNC_MEMBERSHIPS  是否同步完整行业板块成分，默认 1
    SECTOR_ENABLE_EXTERNAL_ENRICHMENT  是否对通过股票调用外部板块补齐，默认 1
"""

import argparse
import csv
import json
import os
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
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
from api.screen_service import get_strategy_condition_labels, run_screening_task
from signal_analysis.service import env_flag, run_signal_analysis_for_market
from signal_analysis.chain import write_analysis_columns_to_csv
from sector_resolver import (
    SectorInfo,
    SectorResolver,
    sector_info_to_membership_rows,
    sector_info_to_stock_row,
)
from timeframe import parse_timeframe


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


def sync_sector_memberships_for_markets(db: MarketDatabase, fetcher, markets: List[str]) -> None:
    """Best-effort sync of complete industry memberships from the quote API."""
    if not env_flag("SECTOR_SYNC_MEMBERSHIPS", True):
        print("板块成分同步: 已关闭")
        return
    if not hasattr(fetcher, "fetch_industry_memberships"):
        return

    try:
        db.init_sector_schema()
    except Exception as exc:
        print(f"板块成分表初始化失败，跳过同步: {type(exc).__name__}: {exc}")
        return

    for market in markets:
        market = normalize_market(market)
        try:
            rows = fetcher.fetch_industry_memberships(market)
        except Exception as exc:
            print(f"{market_label(market)} 板块成分同步失败: {type(exc).__name__}: {exc}")
            continue
        if not rows:
            print(f"{market_label(market)} 未获取到完整行业板块成分")
            continue
        membership_rows = []
        stock_rows_by_code = {}
        for item in rows:
            code = (item.get("code") or "").strip()
            sector_name = (item.get("sector_name") or item.get("industry_name") or "").strip()
            if not code or not sector_name:
                continue
            membership_rows.append({
                "market": market,
                "code": code,
                "sector_type": item.get("sector_type") or "industry",
                "sector_code": item.get("sector_code") or item.get("industry_code"),
                "sector_name": sector_name,
                "source": item.get("source") or "futu_plate",
            })
            stock_rows_by_code.setdefault(code, {
                "code": code,
                "name": item.get("name") or code,
                "sector": sector_name,
                "sector_code": item.get("sector_code") or item.get("industry_code"),
                "industry": item.get("industry_name") or sector_name,
                "industry_code": item.get("industry_code") or item.get("sector_code"),
                "source": item.get("source") or "futu_plate",
            })
        try:
            db.upsert_stock_sector_memberships(membership_rows)
            db.upsert_stocks(market, stock_rows_by_code.values(), source="futu_plate")
            print(f"✓ {market_label(market)} 板块成分已同步: {len(membership_rows)} 条")
        except Exception as exc:
            print(f"{market_label(market)} 板块成分写入失败: {type(exc).__name__}: {exc}")


# ------------------------------------------------------------------
# 2. 合并股票池 + 筛选
# ------------------------------------------------------------------


def get_merged_pool_stocks(db: MarketDatabase, market: str) -> List[dict]:
    """
    合并指定市场所有股票池类型，按 code 聚合并补齐板块字段。
    返回格式兼容 screen_service 的 watchlist。
    """
    pool_types = ["best", "index", "industry", "ipo", "etf"]
    merged = {}
    for pool_type in pool_types:
        stocks = db.get_stock_pool(market, pool_type, limit=None)
        for s in stocks:
            code = (s.get("code") or "").strip()
            if not code:
                continue
            current = merged.setdefault(code, {"code": code})
            current["name"] = current.get("name") or s.get("name") or code
            current["market_cap"] = current.get("market_cap") or s.get("market_cap")
            current["pe_ratio"] = current.get("pe_ratio") or s.get("pe_ratio")

            industry = s.get("industry_name")
            if industry:
                current["industry"] = current.get("industry") or industry
                current["sector"] = current.get("sector") or industry

            if pool_type == "etf":
                current["industry"] = current.get("industry") or "ETF"
                current["sector"] = current.get("sector") or "ETF"

    resolver = SectorResolver.default(db=db, include_external=False)
    sector_map = resolver.resolve(market, list(merged.keys()))
    _apply_sector_info_to_records(list(merged.values()), sector_map)
    return list(merged.values())


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


def get_market_etf_codes(mysql_config: MySqlConfig, market: str) -> set[str]:
    """使用独立连接读取指定市场 ETF 代码集合。"""
    db = MarketDatabase(mysql_config)
    try:
        return get_etf_codes(db, market)
    finally:
        db.close()


def _apply_sector_info_to_records(records: List[dict], sector_map: dict[str, SectorInfo]) -> List[dict]:
    """Fill missing sector/industry fields on records in place."""
    for record in records:
        code = (record.get("code") or "").strip()
        info = sector_map.get(code)
        if not info:
            continue
        sector = info.sector or info.industry
        industry = info.industry or info.sector
        if sector and not record.get("sector"):
            record["sector"] = sector
        if industry and not record.get("industry"):
            record["industry"] = industry
        if info.source:
            record["sector_source"] = info.source
    return records


def enrich_records_with_sectors(
    mysql_config: MySqlConfig,
    task_id: str,
    market: str,
    records: List[dict],
) -> List[dict]:
    """
    Best-effort sector enrichment for passed records.

    External providers are applied only to the passed list to avoid slowing down
    the full screening universe. Failures here must not affect CSV/Feishu.
    """
    if not records:
        return records

    db = MarketDatabase(mysql_config)
    try:
        try:
            db.init_sector_schema()
        except Exception:
            pass
        resolver = SectorResolver.default(
            db=db,
            include_external=env_flag("SECTOR_ENABLE_EXTERNAL_ENRICHMENT", True),
        )
        sector_map = resolver.resolve(market, [(record.get("code") or "").strip() for record in records])
        _apply_sector_info_to_records(records, sector_map)

        update_rows = []
        membership_rows = []
        stock_rows = []
        name_by_code = {record.get("code"): record.get("name") for record in records}
        for code, info in sector_map.items():
            sector = info.sector or info.industry
            industry = info.industry or info.sector
            if not (sector or industry):
                continue
            update_rows.append({"code": code, "sector": sector, "industry": industry})
            membership_rows.extend(sector_info_to_membership_rows(market, info))
            stock_rows.append(sector_info_to_stock_row(info, name=name_by_code.get(code) or code))

        if update_rows:
            db.update_screening_result_sectors(task_id, market, update_rows)
        if membership_rows:
            db.upsert_stock_sector_memberships(membership_rows)
        if stock_rows:
            db.upsert_stocks(market, stock_rows)
    except Exception as exc:
        print(f"[板块补齐] {market_label(market)} 失败但不影响 CSV/飞书发送: {type(exc).__name__}: {exc}")
    finally:
        db.close()
    return records


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
                        if d.get("result") == "pass":
                            conditions.extend(
                                get_strategy_condition_labels(
                                    d.get("filter_name", ""),
                                    d.get("details") if isinstance(d.get("details"), dict) else {},
                                )
                            )
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
    enrich_records_with_sectors(mysql_config, task_id, market, passed)
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


@dataclass
class MarketScreeningResult:
    market: str
    task_id: Optional[str] = None
    passed: List[dict] = field(default_factory=list)
    csv_paths: List[str] = field(default_factory=list)
    skipped: bool = False
    error: Optional[str] = None


def get_default_screening_params() -> dict:
    """默认筛选参数：EMA 突破 + RSI 等策略"""
    return {
        "use_db_rule_engine": True,
        "use_ema_breakout": True,
        "ema_short": 10,
        "ema_long": 150,
        "use_zuoyi_strategy": True,
        "zuoyi_signal_window": 3,
        "rsi_period": 14,
        "rsi_oversold_threshold": 30.0,
        "rsi_overbought_threshold": 70.0,
    }


def run_market_screening_worker(
    mysql_config: MySqlConfig,
    market: str,
    timeframe: str,
    default_params: dict,
    csv_base: str,
    today_str: str,
    verbose: bool = False,
    enable_ai_analysis: bool = False,
) -> MarketScreeningResult:
    """
    Execute screening and CSV export for one market.

    This helper owns its MarketDatabase connection for stock-pool checks and
    ETF lookup. It intentionally does not send Feishu messages so notification
    side effects stay in the main thread.
    """
    print(f"\n--- 开始筛选 {market_label(market)} ---")
    db = None
    try:
        db = MarketDatabase(mysql_config)
        if not has_merged_pool_stocks(db, market):
            print(f"  {market_label(market)} 无可用股票池数据，跳过")
            return MarketScreeningResult(market=market, skipped=True)
        db.close()
        db = None

        task_id, passed = run_screening_for_market(
            mysql_config=mysql_config,
            market=market,
            timeframe=timeframe,
            default_params=default_params,
            verbose=verbose,
        )
        if not task_id:
            print(f"  {market_label(market)} 未创建筛选任务，跳过")
            return MarketScreeningResult(market=market, skipped=True)

        print(f"  {market_label(market)}: {len(passed)} 只通过")

        csv_paths: List[str] = []
        if passed:
            csv_path = f"{csv_base}_{today_str}_{market}.csv"
            write_screening_csv(passed, csv_path)
            csv_paths.append(csv_path)
            print(f"✓ CSV 已导出: {csv_path}")

            no_etf_path, etf_only_path = write_split_screening_csvs(
                records=passed,
                csv_base_path=csv_path,
                etf_codes=get_market_etf_codes(mysql_config, market),
            )
            csv_paths.extend([no_etf_path, etf_only_path])
            print(f"✓ 非 ETF CSV 已导出: {no_etf_path}")
            print(f"✓ ETF CSV 已导出: {etf_only_path}")

            if enable_ai_analysis:
                try:
                    analysis_result = run_signal_analysis_for_market(
                        mysql_config=mysql_config,
                        task_id=task_id,
                        market=market,
                        csv_path=csv_path,
                        check_date=date.fromisoformat(today_str),
                        enabled=True,
                    )
                    for warning in analysis_result.warnings:
                        print(f"[AI分析] {market_label(market)}: {warning}")
                    results_by_code = getattr(analysis_result, "results_by_code", {}) or {}
                    if results_by_code:
                        try:
                            write_analysis_columns_to_csv(no_etf_path, results_by_code)
                            write_analysis_columns_to_csv(etf_only_path, results_by_code)
                            print(f"✓ AI 辅助分析已同步到拆分 CSV: {no_etf_path}, {etf_only_path}")
                        except Exception as e:
                            print(f"[AI分析] {market_label(market)} 拆分 CSV 写回失败但不影响飞书发送: {type(e).__name__}: {e}")
                    if analysis_result.artifact_paths:
                        csv_paths.extend(analysis_result.artifact_paths)
                        for artifact_path in analysis_result.artifact_paths:
                            print(f"✓ AI 辅助分析文件已导出: {artifact_path}")
                    elif analysis_result.skipped_reason:
                        print(f"[AI分析] {market_label(market)} 跳过: {analysis_result.skipped_reason}")
                except Exception as e:
                    print(f"[AI分析] {market_label(market)} 失败但不影响 CSV/飞书发送: {type(e).__name__}: {e}")
        else:
            print(f"  {market_label(market)} 无满足条件的股票，不生成 CSV")

        return MarketScreeningResult(
            market=market,
            task_id=task_id,
            passed=passed,
            csv_paths=csv_paths,
        )
    except Exception as e:
        return MarketScreeningResult(
            market=market,
            skipped=True,
            error=f"{type(e).__name__}: {e}",
        )
    finally:
        if db is not None:
            db.close()


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
    parser.add_argument("--market-workers", type=int, default=3, help="并行筛选市场的 worker 数量")
    ai_group = parser.add_mutually_exclusive_group()
    ai_group.add_argument("--ai-analysis", dest="enable_ai_analysis", action="store_true", default=None, help="启用搜索+模型辅助分析")
    ai_group.add_argument("--no-ai-analysis", dest="enable_ai_analysis", action="store_false", help="关闭搜索+模型辅助分析")
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
            sync_sector_memberships_for_markets(db, fetcher, markets)
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

    db.close()

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
    market_workers = max(1, args.market_workers)
    enable_ai_analysis = (
        env_flag("ENABLE_LLM_ANALYSIS", True)
        if args.enable_ai_analysis is None
        else bool(args.enable_ai_analysis)
    )
    if enable_ai_analysis:
        print("搜索+模型辅助分析: 已启用（失败不会影响原始 CSV/飞书发送）")
    else:
        print("搜索+模型辅助分析: 已关闭")

    with ThreadPoolExecutor(max_workers=market_workers) as executor:
        future_to_market = {
            executor.submit(
                run_market_screening_worker,
                mysql_config,
                market,
                timeframe,
                params,
                csv_base,
                today_str,
                False,
                enable_ai_analysis,
            ): market
            for market in markets
        }

        for future in as_completed(future_to_market):
            market = future_to_market[future]
            try:
                result = future.result()
            except Exception as e:
                result = MarketScreeningResult(
                    market=market,
                    skipped=True,
                    error=f"{type(e).__name__}: {e}",
                )

            if result.error:
                skipped_markets.append(result.market)
                print(f"✗ {market_label(result.market)} 筛选失败: {result.error}")
                continue

            if result.skipped or not result.task_id:
                skipped_markets.append(result.market)
                continue

            processed_markets.append(result.market)

            # 4. 发送该市场飞书消息
            if not args.no_feishu and webhook_url:
                summary_lines = [
                    f"【定时筛选】{date.today()} - {market_label(result.market)}",
                    f"通过: {len(result.passed)} 只",
                ]
                if result.passed:
                    summary_lines.extend(["CSV 文件:", *result.csv_paths])
                    sent_ok = send_screening_result(webhook_url, "\n".join(summary_lines), result.csv_paths)
                else:
                    sent_ok = send_feishu_text(webhook_url, "\n".join(summary_lines))
                if sent_ok:
                    print(f"✓ 已发送 {market_label(result.market)} 飞书消息")
                else:
                    print(f"✗ {market_label(result.market)} 飞书消息发送不完整，请检查上方 [Feishu] 日志")

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
