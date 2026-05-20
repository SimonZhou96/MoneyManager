#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
定时任务 - 天级别执行

流程：
1. 捞取港股、A股、美股股票池数据，写入数据库
2. 对每个市场的股票池分别执行筛选逻辑
3. 将满足条件的股票导出为主 CSV，并标注标的类型（股票 / ETF）
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
    SIGNAL_COMPANY_SEARCH_QUERY_MAX_CHARS  Tavily 公司批量搜索 query 长度上限，默认 390
    LLM_PROVIDER=openai_compatible|codex_responses|deepseek  AI 分析模型 provider
    LLM_PROVIDER_ORDER   模型 fallback 顺序，默认 openai_compatible,codex_responses,deepseek
    LLM_API_BASE, LLM_API_KEY, LLM_MODEL  OpenAI-compatible 模型配置
    CODEX_API_BASE, CODEX_API_KEY, CODEX_LLM_MODEL, CODEX_REASONING_EFFORT  Codex Responses 配置
    DEEPSEEK_API_BASE, DEEPSEEK_API_KEY, DEEPSEEK_LLM_MODEL  DeepSeek Chat Completions 配置
    ENABLE_MAIN_FORCE_RISK_ANALYSIS 是否启用主力流出风险分析，默认 1
    MAIN_FORCE_ENABLE_EXTERNAL_DATA 是否启用资金/盘口/龙虎榜/筹码外部数据，默认 1
    STOCK_NAME_ENABLE_EXTERNAL_ENRICHMENT  是否对通过股票调用外部名称补齐，默认 1
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
    fetch_and_save_all_etf,
    fetch_and_save_major_index_constituents,
    fetch_and_save_industry_top5,
    fetch_and_save_recent_ipo_2y,
    get_db_config,
)
from kline_fetcher import KlineFetcherFactory
from main_force_risk import MainForceRiskServiceFactory
from market import market_label, normalize_market
from api.screen_service import get_strategy_condition_labels, run_screening_task
from report_naming import market_signal_report_path
from signal_analysis.service import env_flag, run_signal_analysis_for_market
from signal_analysis.chain import write_analysis_columns_to_csv
from sector_resolver import (
    SectorInfo,
    SectorResolver,
    sector_info_to_membership_rows,
    sector_info_to_stock_row,
)
from stock_name_resolver import StockNameResolver
from stock_pool import (
    CANONICAL_POOL_TYPES,
    DEFAULT_POOL_TYPES_TEXT,
    POOL_TYPE_ALL_ETF,
    POOL_TYPE_BEST,
    POOL_TYPE_INDUSTRY_TOP5,
    POOL_TYPE_MAJOR_INDEX,
    POOL_TYPE_RECENT_IPO_2Y,
    normalize_pool_types,
    parse_pool_types,
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
    pools = normalize_pool_types(pools)
    for market in markets:
        market = normalize_market(market)
        if POOL_TYPE_BEST in pools:
            fetch_and_save_best_stocks(fetcher, db, market)
        if POOL_TYPE_MAJOR_INDEX in pools:
            fetch_and_save_major_index_constituents(fetcher, db, market)
        if POOL_TYPE_INDUSTRY_TOP5 in pools:
            fetch_and_save_industry_top5(fetcher, db, market, top_n=5)
        if POOL_TYPE_RECENT_IPO_2Y in pools:
            fetch_and_save_recent_ipo_2y(fetcher, db, market, days=730)
        if POOL_TYPE_ALL_ETF in pools:
            fetch_and_save_all_etf(fetcher, db, market)


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


def get_merged_pool_stocks(
    db: MarketDatabase,
    market: str,
    pool_types: Optional[List[str]] = None,
) -> List[dict]:
    """
    合并指定市场所有股票池类型，按 code 聚合并补齐板块字段。
    返回格式兼容 screen_service 的 watchlist。
    """
    pool_types = normalize_pool_types(pool_types or CANONICAL_POOL_TYPES)
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

            if pool_type == POOL_TYPE_ALL_ETF:
                current["industry"] = current.get("industry") or "ETF"
                current["sector"] = current.get("sector") or "ETF"

    resolver = SectorResolver.default(db=db, include_external=False)
    sector_map = resolver.resolve(market, list(merged.keys()))
    _apply_sector_info_to_records(list(merged.values()), sector_map)
    return list(merged.values())


def has_merged_pool_stocks(db: MarketDatabase, market: str, pool_types: Optional[List[str]] = None) -> bool:
    """检查指定市场是否存在可用于筛选的合并股票池数据。"""
    return len(get_merged_pool_stocks(db, market, pool_types=pool_types)) > 0


def get_etf_codes(db: MarketDatabase, market: str) -> set[str]:
    """获取指定市场 ETF 股票池中的代码集合，用于标注标的类型。"""
    return {
        (s.get("code") or "").strip()
        for s in db.get_stock_pool(market, POOL_TYPE_ALL_ETF, limit=None)
        if (s.get("code") or "").strip()
    }


def get_market_etf_codes(mysql_config: MySqlConfig, market: str) -> set[str]:
    """使用独立连接读取指定市场 ETF 代码集合。"""
    db = MarketDatabase(mysql_config)
    try:
        return get_etf_codes(db, market)
    finally:
        db.close()


ETF_NAME_KEYWORDS = (
    "ETF",
    "ETN",
    "基金",
    "指数基金",
    "交易型开放式指数基金",
    "Exchange Traded Fund",
)


def infer_instrument_type(record: dict, etf_codes: Optional[set[str]] = None) -> str:
    """Infer whether the screening record is a stock or ETF/fund-like target."""
    code = (record.get("code") or record.get("股票代码") or "").strip()
    if etf_codes and code in etf_codes:
        return "ETF"

    explicit = (record.get("instrument_type") or record.get("标的类型") or "").strip().upper()
    if explicit in {"ETF", "基金", "FUND"}:
        return "ETF"
    if explicit in {"股票", "STOCK"}:
        return "股票"

    sector_text = " ".join(
        str(record.get(key) or "")
        for key in ("sector", "industry", "所属板块")
    ).upper()
    if sector_text.strip() == "ETF" or " ETF" in f" {sector_text} ":
        return "ETF"

    name_text = str(record.get("name") or record.get("名称") or "")
    name_upper = name_text.upper()
    if any(keyword.upper() in name_upper for keyword in ETF_NAME_KEYWORDS):
        return "ETF"
    return "股票"


def annotate_records_with_instrument_type(records: List[dict], etf_codes: Optional[set[str]] = None) -> List[dict]:
    """Fill records with a stable CSV-facing instrument type."""
    for record in records:
        record["instrument_type"] = infer_instrument_type(record, etf_codes)
    return records


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


def enrich_records_with_names(
    mysql_config: MySqlConfig,
    task_id: str,
    market: str,
    records: List[dict],
) -> List[dict]:
    """
    Best-effort display-name enrichment for passed records.

    HK/A names are shown in Chinese when a Chinese source is available; the
    original provider name is retained when no Chinese name can be found.
    """
    if not records:
        return records
    if normalize_market(market) not in {"A", "HK"}:
        return records

    db = MarketDatabase(mysql_config)
    try:
        resolver = StockNameResolver.default(
            db=db,
            include_external=env_flag("STOCK_NAME_ENABLE_EXTERNAL_ENRICHMENT", True),
        )
        before = {record.get("code"): record.get("name") for record in records}
        resolver.enrich_records(market, records)

        update_rows = []
        stock_rows = []
        for record in records:
            code = (record.get("code") or "").strip()
            name = (record.get("name") or "").strip()
            if not code or not name or name == before.get(code):
                continue
            update_rows.append({"code": code, "name": name})
            stock_rows.append({"code": code, "name": name, "source": record.get("name_source") or "name_resolver"})

        if update_rows:
            db.update_screening_result_names(task_id, market, update_rows)
            db.upsert_stocks(market, stock_rows)
            print(f"[名称补齐] {market_label(market)} 已补齐中文名称: {len(update_rows)} 条")
    except Exception as exc:
        print(f"[名称补齐] {market_label(market)} 失败但不影响 CSV/飞书发送: {type(exc).__name__}: {exc}")
    finally:
        db.close()
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


def enrich_records_with_main_force_risks(
    mysql_config: MySqlConfig,
    task_id: str,
    market: str,
    timeframe: str,
    records: List[dict],
    csv_path: str,
    check_date: date,
) -> List[dict]:
    """Best-effort main-force outflow risk enrichment for passed records."""
    if not records:
        return records
    if not env_flag("ENABLE_MAIN_FORCE_RISK_ANALYSIS", True):
        return records

    db = MarketDatabase(mysql_config)
    service = None
    try:
        db.init_main_force_risk_schema()
        fetchers = KlineFetcherFactory.create_fetcher_chain(db=db)
        service = MainForceRiskServiceFactory.from_env(kline_fetchers=fetchers, market=market)
        results = service.analyze_records(
            market=market,
            timeframe=timeframe,
            records=records,
        )
        detail_rows = [
            result.to_db_row(
                task_id=task_id,
                check_date=check_date,
                csv_path=csv_path,
            )
            for result in results
        ]
        db.upsert_main_force_risk_results(detail_rows)
        db.update_screening_result_main_force_risks(
            task_id,
            market,
            [
                {
                    "code": result.code,
                    "risk_level": result.risk_level,
                    "risk_score": result.risk_score,
                    "risk_summary": result.risk_summary,
                    "triggered_signals": [signal.to_dict() for signal in result.triggered_signals],
                    "provider_status": {
                        key: status.to_dict() for key, status in result.data_status.items()
                    },
                }
                for result in results
            ],
        )
        print(f"[主力风险] {market_label(market)} 已分析: {len(results)} 条")
    except Exception as exc:
        print(f"[主力风险] {market_label(market)} 失败但不影响 CSV/飞书发送: {type(exc).__name__}: {exc}")
    finally:
        if service is not None:
            service.close()
        db.close()
    return records


def load_passed_screening_records(mysql_config: MySqlConfig, task_id: str, market: str) -> List[dict]:
    """Load passed screening rows and convert rule details into CSV-facing fields."""
    db = MarketDatabase(mysql_config)
    sql = """
        SELECT code, name, filter_details, sector, industry, market_cap, pe_ratio
        FROM screening_results
        WHERE task_id=%s AND is_passed=1
        ORDER BY code
    """
    try:
        with db.conn.cursor() as cursor:
            cursor.execute(sql, (task_id,))
            rows = cursor.fetchall() or []
    finally:
        db.close()

    passed = []
    for row in rows:
        code, name, fd_raw, sector, industry, market_cap, pe_ratio = row
        conditions = []
        zuoyi_summary = {}
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
                        if d.get("filter_name") == "ZuoYiStrategizer":
                            extracted_zuoyi = _extract_zuoyi_csv_fields(d)
                            if extracted_zuoyi:
                                zuoyi_summary = extracted_zuoyi
            except Exception:
                pass
        record = {
            "code": code,
            "name": name or code,
            "market": market,
            "sector": sector or industry or "",
            "industry": industry or "",
            "market_cap": market_cap,
            "pe_ratio": pe_ratio,
            "conditions_met": "|".join(conditions) if conditions else "",
        }
        record.update(zuoyi_summary)
        passed.append(record)
    return passed


def run_screening_for_market(
    mysql_config: MySqlConfig,
    market: str,
    timeframe: str,
    default_params: dict,
    verbose: bool = False,
    chain_key: Optional[str] = None,
    pool_types: Optional[List[str]] = None,
) -> Tuple[Optional[str], List[dict]]:
    """
    对指定市场的合并股票池执行筛选。

    Returns:
        (task_id, passed_stocks)  # passed_stocks 为通过的股票列表
    """
    db = MarketDatabase(mysql_config)
    db.init_schema(timeframe)

    pool_types = normalize_pool_types(pool_types or CANONICAL_POOL_TYPES)
    watchlist = get_merged_pool_stocks(db, market, pool_types=pool_types)
    if not watchlist:
        db.close()
        return None, []

    task_id = str(uuid.uuid4())
    task_params = dict(default_params or {})
    task_params["pool_types"] = pool_types
    if chain_key:
        task_params["chain_key"] = chain_key
    db.create_screening_task(
        task_id=task_id,
        market=market,
        timeframe=timeframe,
        total_count=len(watchlist),
        params_json=task_params,
        check_date=date.today(),
    )
    db.close()

    run_screening_task(
        mysql_config=mysql_config,
        task_id=task_id,
        market=market,
        timeframe=timeframe,
        params=task_params,
        verbose=verbose,
        watchlist=watchlist,
        progress_log=True,
        chain_key=chain_key,
    )

    return task_id, load_passed_screening_records(mysql_config, task_id, market)


# ------------------------------------------------------------------
# 3. CSV 导出
# ------------------------------------------------------------------


ZUOYI_CSV_COLUMNS = [
    ("左一方向", "zuoyi_direction"),
    ("左一日期", "zuoyi_left_one_date"),
    ("左一顶", "zuoyi_left_one_high"),
    ("左一底", "zuoyi_left_one_low"),
    ("左一支撑区间", "zuoyi_support_zone"),
    ("左一中位线日期", "zuoyi_median_date"),
    ("左一突破日期", "zuoyi_breakout_date"),
    ("左一突破用时", "zuoyi_bars_to_breakout"),
]


MAIN_FORCE_CSV_COLUMNS = [
    ("主力流出风险", "main_force_risk_level_text"),
    ("主力风险分", "main_force_risk_score_text"),
    ("主力风险信号", "main_force_risk_signals_text"),
    ("主力风险说明", "main_force_risk_summary"),
    ("资金与盘面观察", "main_force_market_data_observation_text"),
    ("资金流向数据", "main_force_fund_flow_data_text"),
    ("盘口数据", "main_force_order_book_data_text"),
    ("龙虎榜数据", "main_force_lhb_data_text"),
    ("成交量分布数据", "main_force_chip_data_text"),
]


def _format_zuoyi_number(value) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return ""
    if number != number:
        return ""
    return f"{number:.4g}"


def _zuoyi_direction_text(direction: str) -> str:
    if direction == "bullish":
        return "看涨"
    if direction == "bearish":
        return "看跌"
    return direction or ""


def _extract_zuoyi_csv_fields(filter_detail: dict) -> dict:
    """
    从 ZuoYiStrategizer 的通过明细中提取 CSV 展示字段。

    只有左一策略本身通过且存在 signals 时才返回字段；未启用左一、左一未命中、
    或规则链未执行左一时保持空 dict，CSV 不会出现左一相关列。
    """
    if not isinstance(filter_detail, dict):
        return {}
    if filter_detail.get("filter_name") != "ZuoYiStrategizer":
        return {}
    if filter_detail.get("result") != "pass":
        return {}

    details = filter_detail.get("details")
    if not isinstance(details, dict):
        return {}
    signals = details.get("signals")
    if not isinstance(signals, list) or not signals:
        return {}

    extracted = []
    for signal in signals:
        if not isinstance(signal, dict):
            continue
        left_high = _format_zuoyi_number(signal.get("left_one_high"))
        left_low = _format_zuoyi_number(signal.get("left_one_low"))
        extracted.append({
            "direction": _zuoyi_direction_text(str(signal.get("direction") or "")),
            "left_one_date": str(signal.get("left_one_date") or ""),
            "left_one_high": left_high,
            "left_one_low": left_low,
            "support_zone": f"{left_low}~{left_high}" if left_low and left_high else "",
            "median_date": str(signal.get("median_date") or ""),
            "breakout_date": str(signal.get("breakout_date") or ""),
            "bars_to_breakout": str(signal.get("bars_to_breakout") or ""),
        })

    extracted = [item for item in extracted if item.get("left_one_high") or item.get("left_one_low")]
    if not extracted:
        return {}

    return {
        "zuoyi_direction": "；".join(item["direction"] for item in extracted if item["direction"]),
        "zuoyi_left_one_date": "；".join(item["left_one_date"] for item in extracted if item["left_one_date"]),
        "zuoyi_left_one_high": "；".join(item["left_one_high"] for item in extracted if item["left_one_high"]),
        "zuoyi_left_one_low": "；".join(item["left_one_low"] for item in extracted if item["left_one_low"]),
        "zuoyi_support_zone": "；".join(item["support_zone"] for item in extracted if item["support_zone"]),
        "zuoyi_median_date": "；".join(item["median_date"] for item in extracted if item["median_date"]),
        "zuoyi_breakout_date": "；".join(item["breakout_date"] for item in extracted if item["breakout_date"]),
        "zuoyi_bars_to_breakout": "；".join(item["bars_to_breakout"] for item in extracted if item["bars_to_breakout"]),
    }


def write_screening_csv(records: List[dict], csv_path: str) -> None:
    """
    将满足条件的股票写入 CSV。
    列：code, 市场, 名称, 标的类型, pe, 市值, 所属板块, 满足的条件。
    若本次 records 中存在左一战法命中明细，则追加左一支撑区间列。
    """
    os.makedirs(os.path.dirname(csv_path) or ".", exist_ok=True)
    columns = [
        ("股票代码", "code"),
        ("市场", "market_label"),
        ("名称", "name"),
        ("标的类型", "instrument_type"),
        ("pe", "pe_ratio"),
        ("市值", "market_cap"),
        ("所属板块", "sector"),
        ("满足的条件", "conditions_met"),
    ]
    columns.extend(MAIN_FORCE_CSV_COLUMNS)
    if any(r.get("zuoyi_support_zone") for r in records):
        columns.extend(ZUOYI_CSV_COLUMNS)
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow([c[0] for c in columns])
        for r in records:
            row = []
            for _, key in columns:
                if key == "market_label":
                    val = market_label(r.get("market", ""))
                elif key == "instrument_type":
                    val = infer_instrument_type(r)
                else:
                    val = r.get(key)
                if val is None:
                    val = ""
                elif isinstance(val, float):
                    val = f"{val:.4g}" if val == val else ""  # 避免 nan
                row.append(str(val) if val != "" else "")
            w.writerow(row)


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
        "zuoyi_signal_window": 15,
        "rsi_period": 14,
        "rsi_oversold_threshold": 30.0,
        "rsi_overbought_threshold": 70.0,
    }


class ScreeningPostProcessor:
    """Shared passed-stock CSV, main-force risk, and AI report processing."""

    def __init__(
        self,
        mysql_config: MySqlConfig,
        csv_base: str,
        today_str: str,
        enable_ai_analysis: bool = False,
    ):
        self.mysql_config = mysql_config
        self.csv_base = csv_base
        self.today_str = today_str
        self.enable_ai_analysis = bool(enable_ai_analysis)

    def process(
        self,
        market: str,
        timeframe: str,
        task_id: str,
        passed: List[dict],
    ) -> List[str]:
        csv_paths: List[str] = []
        if not passed:
            print(f"  {market_label(market)} 无满足条件的股票，不生成 CSV")
            return csv_paths

        enrich_records_with_names(self.mysql_config, task_id, market, passed)
        enrich_records_with_sectors(self.mysql_config, task_id, market, passed)
        etf_codes = get_market_etf_codes(self.mysql_config, market)
        annotate_records_with_instrument_type(passed, etf_codes)
        csv_path = market_signal_report_path(self.csv_base, market, timeframe, self.today_str, ".csv")
        check_date = date.fromisoformat(self.today_str)
        enrich_records_with_main_force_risks(
            mysql_config=self.mysql_config,
            task_id=task_id,
            market=market,
            timeframe=timeframe,
            records=passed,
            csv_path=csv_path,
            check_date=check_date,
        )
        write_screening_csv(passed, csv_path)
        csv_paths.append(csv_path)
        print(f"✓ CSV 已导出: {csv_path}")

        if self.enable_ai_analysis:
            self._run_ai_analysis(market, timeframe, task_id, csv_path, check_date, csv_paths)
        return csv_paths

    def _run_ai_analysis(
        self,
        market: str,
        timeframe: str,
        task_id: str,
        csv_path: str,
        check_date: date,
        csv_paths: List[str],
    ) -> None:
        try:
            analysis_result = run_signal_analysis_for_market(
                mysql_config=self.mysql_config,
                task_id=task_id,
                market=market,
                csv_path=csv_path,
                check_date=check_date,
                timeframe=timeframe,
                enabled=True,
            )
            for warning in analysis_result.warnings:
                print(f"[AI分析] {market_label(market)}: {warning}")
            results_by_code = getattr(analysis_result, "results_by_code", {}) or {}
            if results_by_code:
                try:
                    write_analysis_columns_to_csv(csv_path, results_by_code)
                    print(f"✓ AI 辅助分析已写回主 CSV: {csv_path}")
                except Exception as e:
                    print(f"[AI分析] {market_label(market)} 主 CSV 写回失败但不影响飞书发送: {type(e).__name__}: {e}")
            if analysis_result.artifact_paths:
                csv_paths.extend(analysis_result.artifact_paths)
                for artifact_path in analysis_result.artifact_paths:
                    print(f"✓ AI 辅助分析文件已导出: {artifact_path}")
            elif analysis_result.skipped_reason:
                print(f"[AI分析] {market_label(market)} 跳过: {analysis_result.skipped_reason}")
        except Exception as e:
            print(f"[AI分析] {market_label(market)} 失败但不影响 CSV/飞书发送: {type(e).__name__}: {e}")


def run_market_screening_worker(
    mysql_config: MySqlConfig,
    market: str,
    timeframe: str,
    default_params: dict,
    csv_base: str,
    today_str: str,
    verbose: bool = False,
    enable_ai_analysis: bool = False,
    chain_key: Optional[str] = None,
    pool_types: Optional[List[str]] = None,
) -> MarketScreeningResult:
    """
    Execute screening and CSV export for one market.

    This helper owns its MarketDatabase connection for stock-pool checks and
    ETF lookup. It intentionally does not send Feishu messages so notification
    side effects stay in the main thread.
    """
    print(f"\n--- 开始筛选 {market_label(market)} ---")
    pool_types = normalize_pool_types(pool_types or CANONICAL_POOL_TYPES)
    db = None
    try:
        db = MarketDatabase(mysql_config)
        if not has_merged_pool_stocks(db, market, pool_types=pool_types):
            print(f"  {market_label(market)} 无可用股票池数据，跳过")
            return MarketScreeningResult(market=market, skipped=True)
        db.close()
        db = None

        screening_kwargs = dict(
            mysql_config=mysql_config,
            market=market,
            timeframe=timeframe,
            default_params=default_params,
            verbose=verbose,
            pool_types=pool_types,
        )
        if chain_key:
            screening_kwargs["chain_key"] = chain_key
        task_id, passed = run_screening_for_market(**screening_kwargs)
        if not task_id:
            print(f"  {market_label(market)} 未创建筛选任务，跳过")
            return MarketScreeningResult(market=market, skipped=True)

        print(f"  {market_label(market)}: {len(passed)} 只通过")

        csv_paths = ScreeningPostProcessor(
            mysql_config=mysql_config,
            csv_base=csv_base,
            today_str=today_str,
            enable_ai_analysis=enable_ai_analysis,
        ).process(
            market=market,
            timeframe=timeframe,
            task_id=task_id,
            passed=passed,
        )

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
    parser.add_argument("--pools", default=DEFAULT_POOL_TYPES_TEXT, help=f"股票池类型: {DEFAULT_POOL_TYPES_TEXT}")
    parser.add_argument("--timeframe", default="1d", help="K线周期")
    parser.add_argument("--csv", default="logs/screening_result.csv", help="CSV 输出目录基准（会生成 {股票类型}市场信号{timeframe}{date}复核报告.csv，主表内用“标的类型”区分股票/ETF）")
    parser.add_argument("--market-workers", type=int, default=3, help="并行筛选市场的 worker 数量")
    parser.add_argument("--chain-key", default=None, help="筛选规则链 key（默认使用各市场启用的默认链）")
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
    try:
        pools = parse_pool_types(args.pools)
    except ValueError as exc:
        parser.error(str(exc))
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
    if args.chain_key:
        params["chain_key"] = args.chain_key
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
                args.chain_key,
                pools,
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
