from __future__ import annotations

import csv
import tempfile
import uuid
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

from api.screen_service import create_rule_engine_from_db, get_strategy_condition_labels
from db import MarketDatabase, MySqlConfig
from filters import FilterContext, StockInfo
from kline_fetcher import KlineFetcherFactory
from market import normalize_market
from signal_analysis.service import run_signal_analysis_for_market
from timeframe import parse_timeframe


@dataclass
class SingleStockRequest:
    market: str
    code: str
    timeframe: str
    chain_key: Optional[str] = None
    user_id: Optional[int] = None
    run_id: Optional[str] = None


def normalize_stock_code(market: str, code: str) -> str:
    market = normalize_market(market)
    value = str(code or "").strip().upper()
    if not value:
        raise ValueError("股票代码不能为空")
    if market == "HK":
        if value.startswith("HK."):
            return f"HK.{value[3:].zfill(5)}"
        if value.isdigit():
            return f"HK.{value.zfill(5)}"
        return value
    if market == "US":
        return value if value.startswith("US.") else f"US.{value}"
    if market == "A":
        if value.startswith(("SH.", "SZ.", "BJ.")):
            return value
        if value.endswith(".SS"):
            return f"SH.{value[:-3]}"
        if value.endswith(".SZ"):
            return f"SZ.{value[:-3]}"
        if value.isdigit() and len(value) == 6:
            if value.startswith(("6", "9")):
                return f"SH.{value}"
            if value.startswith(("8", "4")):
                return f"BJ.{value}"
            return f"SZ.{value}"
    return value


def run_single_stock_analysis(mysql_config: MySqlConfig, request: SingleStockRequest) -> Dict[str, Any]:
    market = normalize_market(request.market)
    timeframe = parse_timeframe(request.timeframe)
    normalized_code = normalize_stock_code(market, request.code)
    run_id = request.run_id or str(uuid.uuid4())
    warnings: List[str] = []
    ai_analysis: Optional[dict] = None
    data_source = ""

    db = MarketDatabase(mysql_config)
    db.init_web_schema()
    db.create_single_stock_run({
        "run_id": run_id,
        "user_id": request.user_id,
        "market": market,
        "code": request.code,
        "normalized_code": normalized_code,
        "timeframe": timeframe,
        "chain_key": request.chain_key,
        "status": "running",
    })

    try:
        stock = _load_stock_info(db, market, normalized_code)
        stock.kline_df, data_source = _fetch_single_kline(db, stock, timeframe)
        if stock.kline_df is None or stock.kline_df.empty:
            warnings.append("未获取到可用 K 线数据")

        context = FilterContext(check_date=date.today(), market=market, db=db, verbose=False)
        context.timeframe = timeframe
        rule_engine = create_rule_engine_from_db(db, market, request.chain_key)
        result = rule_engine.evaluate_stock(stock, context)
        rule_details = _rule_details(rule_engine, result.filter_outputs)

        db.insert_single_stock_rule_details(run_id, rule_details)

        if result.passed:
            ai_analysis, ai_warnings = _run_single_ai(mysql_config, run_id, market, stock, result.filter_outputs)
            warnings.extend(ai_warnings)

        db.finish_single_stock_run(
            run_id=run_id,
            passed=result.passed,
            status="completed",
            data_source=data_source,
            warnings=warnings,
            ai_analysis=ai_analysis,
        )

        return {
            "run_id": run_id,
            "market": market,
            "code": normalized_code,
            "name": stock.name or normalized_code,
            "sector": stock.sector,
            "industry": stock.industry,
            "market_cap": stock.market_cap,
            "pe_ratio": stock.pe_ratio,
            "timeframe": timeframe,
            "passed": bool(result.passed),
            "data_source": data_source,
            "rule_chain": {
                "chain_key": rule_engine.chain_config.chain_key,
                "chain_name": rule_engine.chain_config.chain_name,
                "passed": bool(result.passed),
                "details": rule_details,
            },
            "conditions_met": _conditions_met(result.filter_outputs),
            "zuoyi": _zuoyi_summary(result.filter_outputs),
            "ai_analysis": ai_analysis,
            "warnings": warnings,
        }
    except Exception as exc:
        warnings.append(f"{type(exc).__name__}: {exc}")
        db.finish_single_stock_run(
            run_id=run_id,
            passed=False,
            status="failed",
            data_source=data_source,
            warnings=warnings,
            ai_analysis=None,
        )
        raise
    finally:
        db.close()


def _load_stock_info(db: MarketDatabase, market: str, code: str) -> StockInfo:
    rows = db.get_stocks_by_codes(market, [code], include_fundamentals=True)
    item = dict(rows[0]) if rows else {"code": code}
    name = item.get("name") or code
    sector = item.get("sector")
    industry = item.get("industry")
    market_cap = item.get("market_cap")
    pe_ratio = item.get("pe_ratio")
    pb_ratio = item.get("pb_ratio")

    pool_rows = []
    if hasattr(db, "get_stock_pool_records_by_codes"):
        pool_rows = db.get_stock_pool_records_by_codes(market, [code])
    for pool_item in pool_rows or []:
        if (not name or name == code) and pool_item.get("name"):
            name = pool_item.get("name")
        if market_cap is None and pool_item.get("market_cap") is not None:
            market_cap = pool_item.get("market_cap")
        if pe_ratio is None and pool_item.get("pe_ratio") is not None:
            pe_ratio = pool_item.get("pe_ratio")
        industry_name = pool_item.get("industry_name")
        if industry_name:
            industry = industry or industry_name
            sector = sector or industry_name
        if pool_item.get("pool_type") == "etf":
            industry = industry or "ETF"
            sector = sector or "ETF"

    memberships = db.get_sector_memberships_by_codes(market, [code]) if hasattr(db, "get_sector_memberships_by_codes") else {}
    for membership in memberships.get(code, []):
        sector_name = membership.get("sector_name")
        if not sector_name:
            continue
        sector_type = str(membership.get("sector_type") or "").lower()
        if sector_type == "industry":
            industry = industry or sector_name
            sector = sector or sector_name
        elif sector_type == "sector":
            sector = sector or sector_name
        else:
            sector = sector or sector_name

    return StockInfo(
        market=market,
        code=code,
        name=name or code,
        sector=sector,
        industry=industry,
        market_cap=market_cap,
        pe_ratio=pe_ratio,
        pb_ratio=pb_ratio,
    )


def _fetch_single_kline(db: MarketDatabase, stock: StockInfo, timeframe: str):
    fetchers = KlineFetcherFactory.create_fetcher_chain(db=db)
    for fetcher in fetchers:
        df = fetcher.fetch(stock.code, market=stock.market, timeframe=timeframe, max_count=500)
        if df is not None and not df.empty:
            return df, fetcher.get_name()
    return None, "failed"


def _rule_details(rule_engine, outputs) -> List[dict]:
    metadata_by_impl = {}
    for item in rule_engine.metadata:
        metadata_by_impl.setdefault(item.implementation, item)
    rows = []
    for index, output in enumerate(outputs):
        metadata = metadata_by_impl.get(output.filter_name)
        rows.append({
            "rule_key": metadata.rule_key if metadata else output.filter_name,
            "rule_name": metadata.rule_name if metadata else output.filter_name,
            "rule_type": metadata.rule_type if metadata else "",
            "result": output.result.value,
            "reason": output.reason or "",
            "details": output.details or {},
            "display_order": metadata.display_order if metadata else index,
        })
    return rows


def _conditions_met(outputs) -> List[str]:
    labels: List[str] = []
    for output in outputs:
        if output.result.value != "pass":
            continue
        for label in get_strategy_condition_labels(output.filter_name, output.details):
            if label not in labels:
                labels.append(label)
    return labels


def _zuoyi_summary(outputs) -> dict:
    for output in outputs:
        if output.filter_name == "ZuoYiStrategizer" and output.result.value == "pass":
            details = output.details or {}
            return {
                "reason": output.reason,
                "signals": details.get("signals") or [],
            }
    return {}


def _run_single_ai(mysql_config: MySqlConfig, run_id: str, market: str, stock: StockInfo, outputs) -> tuple[Optional[dict], List[str]]:
    warnings: List[str] = []
    with tempfile.TemporaryDirectory(prefix="single_stock_ai_") as tmp_dir:
        csv_path = Path(tmp_dir) / f"{run_id}.csv"
        with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=["股票代码", "市场", "名称", "pe", "市值", "所属板块", "满足的条件"],
            )
            writer.writeheader()
            writer.writerow({
                "股票代码": stock.code,
                "市场": market,
                "名称": stock.name or stock.code,
                "pe": stock.pe_ratio or "",
                "市值": stock.market_cap or "",
                "所属板块": stock.sector or stock.industry or "",
                "满足的条件": "|".join(_conditions_met(outputs)),
            })
        result = run_signal_analysis_for_market(
            mysql_config=mysql_config,
            task_id=run_id,
            market=market,
            csv_path=str(csv_path),
            check_date=date.today(),
            enabled=True,
        )
        warnings.extend(result.warnings or [])
        analysis = (result.results_by_code or {}).get(stock.code)
        if analysis is None:
            if result.skipped_reason:
                warnings.append(result.skipped_reason)
            return None, warnings
        return {
            "code": analysis.code,
            "name": analysis.name,
            "analysis_status": analysis.analysis_status,
            "reliability_score": analysis.reliability_score,
            "confidence_score": analysis.confidence_score,
            "signal_bias": analysis.signal_bias,
            "summary": analysis.summary,
            "positive_factors": analysis.positive_factors,
            "risk_factors": analysis.risk_factors,
            "macro_factors": analysis.macro_factors,
            "company_events": analysis.company_events,
            "market_hot_news": analysis.market_hot_news,
            "company_hot_news": analysis.company_hot_news,
            "news_impact": analysis.news_impact,
            "news_sources": analysis.news_sources,
            "hot_sectors": analysis.hot_sectors,
            "hot_sector_mark": analysis.hot_sector_mark,
            "matched_hot_sectors": analysis.matched_hot_sectors,
            "hot_sector_relevance": analysis.hot_sector_relevance,
            "hot_sector_reason": analysis.hot_sector_reason,
            "hot_sector_sources": analysis.hot_sector_sources,
            "source_urls": analysis.source_urls,
            "model": analysis.model,
            "error_message": analysis.error_message,
        }, warnings
