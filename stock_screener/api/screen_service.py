#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
筛选服务 - 封装 run_screening + 进度回调
"""

import time
import uuid
import os
import math
from datetime import date
from typing import Any, Callable, Dict, Optional

from db import MarketDatabase, MySqlConfig
from filters import (
    FilterChain,
    FilterContext,
    FilterOutput,
    FilterResult,
    StockInfo,
    MarketCapFilter,
    PEFilter,
    PriceFilter,
    AvgDailyVolumeFilter,
    ProfitabilityFilter,
    stocks_to_stock_infos,
)
from kline_fetcher import KlineFetcherFactory
from market import normalize_market, market_label
from market_intel.macro_scoring import aggregate_rule_scores
from rule_engine import RuleEngine, RuleRegistry, RuleRepository
from strategizers import (
    StrategizerChain,
    ZuoYiStrategizer,
    EMABreakoutStrategizer,
    RSIOversoldStrategizer,
    RSIOverboughtStrategizer,
    TechnicalPatternStrategizer,
    TodayVolumeExceedsPrior3MaxStrategizer,
    DailyPctChangeBandStrategizer,
)
from signal_analysis.models import ScreeningSignalRow
from signal_analysis.service import build_macro_score_scorer, build_market_intel_service, run_signal_analysis_for_row
from timeframe import parse_timeframe
from universe import fetch_stock_list_akshare
from universe_filter import UniverseFilterFactory
from scoring.market_cache import MarketCache
from scoring.entry_scorer import EntryScorer
from scoring.holding_scorer import HoldingScorer


UNIFIED_BULLISH_TOP20_CHAIN_KEY = "unified_bullish_top20"
UNIFIED_BULLISH_TOP_N = 20


STRATEGY_NAME_MAP = {
    "EMABreakoutStrategizer": "EMA突破",
    "RSIOversoldStrategizer": "RSI超卖",
    "RSIOverboughtStrategizer": "RSI超买",
    "TodayVolumeExceedsPrior3MaxStrategizer": "放量超前三日",
    "DailyDrop6To65Strategizer": "当日跌6%~6.5%",
    "DailyRise4To45Strategizer": "当日涨4%~4.5%",
}


def select_unified_bullish_top_candidates(candidates: list[dict], top_n: int = UNIFIED_BULLISH_TOP_N) -> list[dict]:
    """Pick the strongest bullish technical candidates by entry_score with deterministic tie-breaking."""
    eligible = [
        item for item in candidates
        if int(item.get("total_match_count") or item.get("bullish_match_count") or 0) > 0
    ]
    ranked = sorted(
        eligible,
        key=lambda item: (-item.get("entry_score", 0), str(item.get("code") or "")),
    )
    return ranked[:max(0, int(top_n))]


def _zuoyi_direction_label(direction: str) -> str:
    if direction == "bullish":
        return "左一战法-看涨"
    if direction == "bearish":
        return "左一战法-看跌"
    return "左一战法"


def get_strategy_condition_labels(filter_name: str, details: Optional[dict] = None) -> list[str]:
    """根据策略器输出生成展示用命中条件。"""
    details = details or {}
    if filter_name == "ZuoYiStrategizer":
        signals = details.get("signals")
        if isinstance(signals, list):
            labels = []
            for signal in signals:
                if isinstance(signal, dict):
                    label = _zuoyi_direction_label(str(signal.get("direction") or ""))
                    if label not in labels:
                        labels.append(label)
            if labels:
                return labels

        direction = details.get("direction")
        if isinstance(direction, str) and direction:
            return [_zuoyi_direction_label(d) for d in direction.split("|") if d]
        return ["左一战法"]

    if filter_name == "TechnicalPatternStrategizer":
        label = details.get("pattern_label")
        return [str(label)] if label else []

    label = STRATEGY_NAME_MAP.get(filter_name)
    return [label] if label else []


def _json_safe_value(value):
    """递归转换为 JSON 可序列化的值，保留 list/dict 明细。"""
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _json_safe_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe_value(v) for v in value]
    return str(value)


def create_filter_chain_from_params(params: dict) -> FilterChain:
    """
    根据参数创建筛选器链（不含策略逻辑，EMA 突破已迁移到策略器）
    """
    chain = FilterChain(mode="all", early_stop=False)

    # 市值筛选器
    if params.get("market_cap_min") is not None or params.get("market_cap_max") is not None:
        chain.add_filter(MarketCapFilter(
            min_cap=params.get("market_cap_min"),
            max_cap=params.get("market_cap_max"),
        ))
    
    # 每日平均交易量筛选器
    if params.get("avg_daily_volume_min") is not None or params.get("avg_daily_volume_max") is not None:
        chain.add_filter(AvgDailyVolumeFilter(
            min_volume=params.get("avg_daily_volume_min"),
            max_volume=params.get("avg_daily_volume_max"),
        ))
    
    # 股票价格筛选器
    if params.get("price_min") is not None or params.get("price_max") is not None:
        chain.add_filter(PriceFilter(
            min_price=params.get("price_min"),
            max_price=params.get("price_max"),
        ))
    
    # PE 筛选器
    if params.get("pe_min") is not None or params.get("pe_max") is not None:
        chain.add_filter(PEFilter(
            min_pe=params.get("pe_min"),
            max_pe=params.get("pe_max"),
            allow_negative=False,
        ))
    
    # 公司盈利筛选器
    if params.get("require_profitable") is True:
        chain.add_filter(ProfitabilityFilter(require_profitable=True))

    return chain


def create_strategizer_chain_from_params(params: dict) -> StrategizerChain:
    """
    根据参数创建策略器链。
    最终通过逻辑在 evaluate_strategy_gate 中统一判断。
    """
    chain = StrategizerChain()

    # EMA 向上突破策略器
    use_ema = params.get("use_ema_breakout", True)
    if use_ema:
        chain.add_strategizer(EMABreakoutStrategizer(
            ema_short=params.get("ema_short", 10),
            ema_long=params.get("ema_long", 150),
        ))

    # 左一战法：当前 timeframe 内多空都筛，任一方向命中即满足策略条件
    if params.get("use_zuoyi_strategy", True):
        chain.add_strategizer(ZuoYiStrategizer(
            signal_window=params.get("zuoyi_signal_window", 15),
            include_bullish=True,
            include_bearish=True,
        ))

    # RSI(14) <= 30 超卖策略器
    chain.add_strategizer(RSIOversoldStrategizer(
        period=params.get("rsi_period", 14),
        threshold=params.get("rsi_oversold_threshold", 30.0),
    ))

    # RSI(14) >= 70 超买策略器
    chain.add_strategizer(RSIOverboughtStrategizer(
        period=params.get("rsi_period", 14),
        threshold=params.get("rsi_overbought_threshold", 70.0),
    ))

    # 量价/涨跌幅：与 EMA、RSI 并列，作为左一战法之外的策略条件
    if params.get("use_volume_spike_vs_prior3", True):
        chain.add_strategizer(TodayVolumeExceedsPrior3MaxStrategizer())
    if params.get("use_daily_drop_band", True):
        chain.add_strategizer(DailyPctChangeBandStrategizer(
            pct_min=-6.5,
            pct_max=-6.0,
            name="DailyDrop6To65Strategizer",
        ))
    if params.get("use_daily_rise_band", True):
        chain.add_strategizer(DailyPctChangeBandStrategizer(
            pct_min=4.0,
            pct_max=4.5,
            name="DailyRise4To45Strategizer",
        ))

    return chain


def evaluate_strategy_gate(strategy_result, require_zuoyi_strategy: bool = True) -> bool:
    """
    判断策略组合是否满足最终通过条件。

    默认逻辑：左一战法 && 任一其他策略。
    当显式关闭左一战法时，保持旧逻辑：任一策略满足即可。
    """
    if not require_zuoyi_strategy:
        return strategy_result.any_satisfied

    zuoyi_satisfied = False
    other_strategy_satisfied = False
    for output in strategy_result.outputs:
        if output.name == "ZuoYiStrategizer":
            zuoyi_satisfied = output.satisfied
        elif output.satisfied:
            other_strategy_satisfied = True
    return zuoyi_satisfied and other_strategy_satisfied


def create_rule_engine_from_db(
    db: MarketDatabase,
    market: str,
    timeframe: str = "1d",
    chain_key: Optional[str] = None,
) -> RuleEngine:
    """按市场和周期加载数据库规则引擎。"""
    repository = RuleRepository(db)
    metadata = repository.load_metadata(market)
    chain_config = (
        repository.load_chain(market, chain_key, timeframe)
        if chain_key else repository.load_active_chain(market, timeframe)
    )
    return RuleEngine(
        metadata=metadata,
        chain_config=chain_config,
        registry=RuleRegistry.default(),
    )


def _build_macro_signal_row(stock: StockInfo, market: str) -> ScreeningSignalRow:
    return ScreeningSignalRow(
        index=0,
        code=stock.code,
        market=market,
        market_label=market_label(market),
        name=stock.name or stock.code,
        pe_ratio="" if stock.pe_ratio is None else str(stock.pe_ratio),
        market_cap="" if stock.market_cap is None else str(stock.market_cap),
        sector=stock.sector or stock.industry or "",
        conditions_met="",
        raw={},
    )


def _rule_metadata_for_output(rule_engine: Optional[RuleEngine], output: FilterOutput):
    if rule_engine is None:
        return None
    details = output.details if isinstance(output.details, dict) else {}
    rule_key = str(details.get("rule_key") or "")
    if rule_key:
        metadata = getattr(rule_engine, "metadata_by_key", {}).get(rule_key)
        if metadata:
            return metadata
    pattern_key = str(details.get("pattern_key") or "")
    if output.filter_name == "TechnicalPatternStrategizer" and pattern_key:
        metadata = getattr(rule_engine, "metadata_by_key", {}).get(pattern_key)
        if metadata and metadata.implementation == "TechnicalPatternStrategizer":
            return metadata
    for metadata in getattr(rule_engine, "metadata", []):
        if metadata.implementation == output.filter_name:
            return metadata
    return None


def _score_weights_from_filter_details(filter_details: list[dict]) -> dict:
    for item in filter_details:
        details = item.get("details") if isinstance(item, dict) else {}
        if not isinstance(details, dict):
            continue
        if "macro_score" in details:
            return {
                "technical_weight": details.get("technical_weight", 0.6),
                "macro_weight": details.get("macro_weight", 0.4),
            }
    return {}


def run_screening_task(
    mysql_config: MySqlConfig,
    task_id: str,
    market: str,
    timeframe: str,
    params: dict,
    verbose: bool = False,
    watchlist: Optional[list] = None,
    progress_log: bool = False,
    chain_key: Optional[str] = None,
):
    """
    执行筛选任务（后台运行）

    Args:
        mysql_config: MySQL 配置
        task_id: 任务ID
        market: 市场
        timeframe: 时间周期
        params: 筛选参数
        verbose: 是否输出详细日志
        watchlist: 自选股列表 [{"code", "name", ...}]，非空时仅筛选此列表不查 DB
        progress_log: 是否输出进度日志（当前处理到哪只股票）
    """
    db = None
    try:
        db = MarketDatabase(mysql_config)
        db.init_schema(timeframe)

        # 股票列表：自选股非空则直接用，否则从 DB 拉取
        if watchlist and len(watchlist) > 0:
            stocks = []
            for item in watchlist:
                code = (item.get("code") or "").strip()
                if not code:
                    continue
                stocks.append({
                    "code": code,
                    "name": item.get("name") or code,
                    "sector": item.get("sector"),
                    "industry": item.get("industry"),
                    "market_cap": item.get("market_cap"),
                    "pe_ratio": item.get("pe_ratio"),
                    "pb_ratio": item.get("pb_ratio"),
                })
            if not stocks:
                if verbose:
                    print("✗ 自选股列表无有效股票")
                db.update_task_status(task_id, "failed")
                return
            # 自选股补全基本面（从 stocks 表按 code 查）
            try:
                fund_list = db.get_stocks_by_codes(market, [s["code"] for s in stocks], include_fundamentals=True)
                fund_by_code = {r["code"]: r for r in fund_list}
                for s in stocks:
                    f = fund_by_code.get(s["code"])
                    if f:
                        s["sector"] = s["sector"] or f.get("sector")
                        s["industry"] = s["industry"] or f.get("industry")
                        s["market_cap"] = s["market_cap"] if s.get("market_cap") is not None else f.get("market_cap")
                        s["pe_ratio"] = s["pe_ratio"] if s.get("pe_ratio") is not None else f.get("pe_ratio")
                        s["pb_ratio"] = s["pb_ratio"] if s.get("pb_ratio") is not None else f.get("pb_ratio")
                if verbose:
                    print(f"✓ 已从数据库补全 {len([s for s in stocks if s.get('market_cap')])} 只股票的基本面数据")
            except Exception as e:
                if verbose:
                    print(f"⚠️  补全基本面数据失败: {e}")
                pass
        else:
            # 全量股票兜底：优先从 API 拉取并写入 DB，API 无数据则用 DB，都没有再报错
            stocks = []
            try:
                stocks = db.get_stocks(market, include_fundamentals=True)
                if not stocks or len(stocks) == 0:
                    api_stocks = fetch_stock_list_akshare(market)
                    db.upsert_stocks(market, api_stocks, source="AKShare")
                    stocks = api_stocks
                    if verbose:
                        print(f"✓ 从 API 获取{market_label(market)}股票 {len(api_stocks)} 只并已更新至 DB")

            except Exception as e:
                if verbose:
                    print(f"⚠ API 获取股票失败: {e}，尝试使用 DB 数据")
            if not stocks:
                stocks = db.get_stocks(market, include_fundamentals=True)
                if stocks and verbose:
                    print(f"✓ 使用 DB 中{market_label(market)}股票 {len(stocks)} 只")
            if not stocks:
                if verbose:
                    print(f"✗ 未获取到{market_label(market)}股票列表（API 与 DB 均无数据）")
                db.update_task_status(task_id, "failed")
                return

        total_count = len(stocks)
        
        # 更新任务总数
        db.update_task_progress(task_id, 0, None, None)
        
        if verbose:
            print(f"\n{'='*60}")
            print(f"开始筛选 {total_count} 只{market_label(market)}股票 (timeframe={timeframe})")
            print(f"{'='*60}\n")
        
        # 转换为 StockInfo 列表
        stock_infos = stocks_to_stock_infos(stocks, market, db)
        
        # 创建数据库规则引擎或回退到参数驱动链路
        use_db_rule_engine = params.get("use_db_rule_engine", True)
        requested_chain_key = chain_key or params.get("chain_key")
        rule_engine = None
        filter_chain = None
        strategy_chain = None
        if use_db_rule_engine:
            rule_engine = create_rule_engine_from_db(db, market, timeframe, requested_chain_key)
            if not rule_engine.has_rules():
                if verbose:
                    print("警告：数据库规则链未引用任何规则")
                db.update_task_status(task_id, "completed")
                return
            is_unified_bullish_top20 = rule_engine.chain_config.chain_key == UNIFIED_BULLISH_TOP20_CHAIN_KEY
            needs_kline = rule_engine.requires_kline()
            if is_unified_bullish_top20:
                needs_kline = True
            if verbose:
                print(f"✓ 已加载数据库规则链: {rule_engine.chain_config.chain_key} ({rule_engine.chain_config.timeframe})")
        else:
            is_unified_bullish_top20 = False
            filter_chain = create_filter_chain_from_params(params)
            strategy_chain = create_strategizer_chain_from_params(params)

            has_filters = len(filter_chain.list_filters()) > 0
            has_strategizers = len(strategy_chain.list_strategizers()) > 0
            if not has_filters and not has_strategizers:
                if verbose:
                    print("警告：没有启用任何筛选器或策略器")
                db.update_task_status(task_id, "completed")
                return

            # 检查是否需要 K 线：筛选器或策略器任一需要则拉取
            needs_kline = any(
                f.__class__.__name__ in ["PriceFilter", "AvgDailyVolumeFilter"]
                for f in filter_chain._filters if f.enabled
            ) or len(strategy_chain.list_strategizers()) > 0

        # 创建 K 线获取器。云端优先读 DB 缓存；本地脚本可通过环境变量继续连接 Futu OpenD。
        fetchers = None
        quote_ctx = None
        if needs_kline:
            use_futu = os.getenv("KLINE_USE_FUTU_OPEND", "1").strip().lower() in {"1", "true", "yes", "on"}
            if use_futu:
                try:
                    from futu import OpenQuoteContext
                    futu_host = os.getenv("FUTU_HOST", "127.0.0.1")
                    futu_port = int(os.getenv("FUTU_PORT", "11111"))
                    quote_ctx = OpenQuoteContext(host=futu_host, port=futu_port)
                    if verbose:
                        print(f"✓ 已连接 Futu OpenD ({futu_host}:{futu_port})")
                except Exception as e:
                    if verbose:
                        print(f"⚠️  无法连接 Futu OpenD: {e}，将使用 DB/YFinance/AKShare")
                    quote_ctx = None

            fetchers = KlineFetcherFactory.create_fetcher_chain(quote_ctx=quote_ctx, db=db)
        
        # 创建筛选器上下文
        context = FilterContext(
            check_date=date.today(),
            market=market,
            db=db,
            verbose=verbose,
        )
        # 注入 timeframe 供 AvgDailyVolumeFilter 使用
        context.timeframe = timeframe
        requires_market_intel_macro_score = (
            rule_engine is not None
            and hasattr(rule_engine, "requires_market_intel_macro_score")
            and rule_engine.requires_market_intel_macro_score()
        )
        requires_enterprise_potential = (
            rule_engine is not None
            and hasattr(rule_engine, "requires_enterprise_potential")
            and rule_engine.requires_enterprise_potential()
        )
        if requires_market_intel_macro_score or requires_enterprise_potential:
            # MarketIntel service + scorer 注入（供宏观评分和 EnterprisePotential 策略使用）
            market_intel_service = build_market_intel_service(mysql_config, enabled=True)
            macro_score_scorer = build_macro_score_scorer()
            context.set_cache("market_intel_service", market_intel_service)
            context.set_cache("macro_score_scorer", macro_score_scorer)
            # 注入 Futu 连接，供 EnterprisePotential yfinance 失败后兜底
            context.set_cache("futu_quote_ctx", quote_ctx)

        if requires_enterprise_potential:
            # EnterprisePotential 批量预取
            from potential_analysis.service import EnterprisePotentialService
            service = EnterprisePotentialService()
            codes = [s.code for s in stock_infos]
            print(f"📊 批量预取企业潜力数据: {len(codes)} 只股票 ({market})")
            report = service.prefetch_batch(market, codes, context)
            print(f"  ✅ 预取完成: {report.ok_count} 成功, {report.fail_count} 失败, "
                  f"{report.duration_ms}ms")
            context.set_cache("enterprise_service", service)

            # 将 YFinance PE/市值写回 StockInfo，确保 CSV 中包含基本面数据
            pe_merged = 0
            market_cap_merged = 0
            for stock in stock_infos:
                company_snap = context.get_cache("enterprise:company:" + stock.code)
                valuation_snap = context.get_cache("enterprise:valuation:" + stock.code)
                pe_trailing = getattr(valuation_snap, "pe_trailing", None)
                market_cap = (
                    getattr(valuation_snap, "market_cap", None)
                    or getattr(company_snap, "market_cap", None)
                )
                if pe_trailing is not None:
                    stock.pe_ratio = pe_trailing
                    pe_merged += 1
                if market_cap is not None:
                    stock.market_cap = market_cap
                    market_cap_merged += 1
            if verbose:
                print(f"✓ 已回填企业潜力基本面: PE {pe_merged} 只, 市值 {market_cap_merged} 只")
        
        # 主循环：遍历每只股票，在同一个循环中完成以下步骤
        # 步骤1: 获取K线数据
        # 步骤2: 应用筛选器链（判断是否满足突破策略和筛选条件）
        # 步骤3: 打印详细日志
        # 步骤4: 写入数据库
        results = []
        passed_stocks = []  # 记录满足条件的股票
        failed_stocks = []  # 记录未通过的股票及原因
        pending_screening_records = []
        unified_candidates = []
        live_stocks: Dict[str, StockInfo] = {}
        macro_analysis_cache: Dict[str, object] = {}
        macro_warning_cache: Dict[str, list[str]] = {}

        # ── Pre-compute MarketTemperature for scoring ──
        market_cache = MarketCache()
        market_temp = market_cache.get_or_compute(market)
        entry_scorer = EntryScorer()
        holding_scorer = HoldingScorer()

        if verbose:
            print(f"\n{'='*80}")
            print(f"开始逐个处理 {len(stock_infos)} 只股票")
            print(f"流程: 获取K线 → 筛选判断 → 打印日志 → 写入数据库")
            print(f"{'='*80}\n")
        
        for i, si in enumerate(stock_infos, 1):
            live_stocks[si.code] = si
            if progress_log:
                print(f"[{i}/{total_count}] 处理中: {si.code} - {si.name or si.code}")
            # 更新进度
            db.update_task_progress(
                task_id,
                completed_count=i,
                current_stock_code=si.code,
                current_stock_name=si.name or si.code
            )
            
            # 步骤1: 获取 K 线数据（如果需要）
            kline_fetched = False
            if needs_kline and fetchers:
                for fetcher in fetchers:
                    try:
                        df = fetcher.fetch(si.code, market=si.market, timeframe=timeframe)
                        if df is not None and not df.empty:
                            si.kline_df = df
                            kline_fetched = True
                            if verbose:
                                print(f"[{i}/{total_count}] 📊 {si.code} - 获取 K 线成功 ({fetcher.get_name()}, {len(df)} 根)")
                            break
                    except Exception as e:
                        continue
                
                if not kline_fetched and verbose:
                    print(f"[{i}/{total_count}] ⚠️  {si.code} - 获取 K 线失败（所有数据源）")
                
                # 限速
                time.sleep(0.2)
            
            # 步骤2: 应用规则。默认由 DB 规则引擎计算；兼容模式保留旧链路。
            if rule_engine is not None:
                signal_analysis_loader = None
                if not is_unified_bullish_top20 and rule_engine.requires_signal_analysis():
                    def load_signal_analysis(current_stock: StockInfo):
                        cached = macro_analysis_cache.get(current_stock.code)
                        if cached is not None:
                            return cached
                        analysis, warnings = run_signal_analysis_for_row(
                            mysql_config=mysql_config,
                            task_id=f"{task_id}:macro:{current_stock.code}",
                            row=_build_macro_signal_row(current_stock, market),
                            market=market,
                            check_date=context.check_date,
                            timeframe=timeframe,
                            enabled=True,
                        )
                        macro_analysis_cache[current_stock.code] = analysis
                        macro_warning_cache[current_stock.code] = list(warnings or [])
                        if verbose and warnings:
                            print(f"[{i}/{total_count}] ℹ️  {current_stock.code} - 宏观分析告警: {' | '.join(warnings)}")
                        return analysis
                    signal_analysis_loader = load_signal_analysis
                if is_unified_bullish_top20:
                    result = rule_engine.evaluate_bullish_technical_rules(si, context)
                else:
                    result = rule_engine.evaluate_stock(
                        si,
                        context,
                        signal_analysis_loader=signal_analysis_loader,
                    )
            else:
                # 策略通过条件：左一战法 && 任一其他策略（关闭左一时退回任一策略命中）
                result = filter_chain.apply(si, context)
                strategy_result = strategy_chain.apply(si, context)
                for out in strategy_result.outputs:
                    result.add_output(FilterOutput(
                        filter_name=out.name,
                        result=FilterResult.PASS if out.satisfied else FilterResult.FAIL,
                        reason=out.reason or "",
                        details=dict(out.details) if out.details else {},
                    ))
                strategy_gate_passed = evaluate_strategy_gate(
                    strategy_result,
                    require_zuoyi_strategy=params.get("use_zuoyi_strategy", True),
                )
                result.passed = result.passed and strategy_gate_passed
            results.append(result)

            # 步骤3: 收集结果摘要；详细日志只在 verbose 下展开。
            if result.passed:
                satisfied_strategies = []
                if is_unified_bullish_top20:
                    satisfied_strategies.extend(getattr(result, "bullish_condition_labels", []) or [])
                else:
                    for output in result.filter_outputs:
                        if output.result.value == "pass":
                            satisfied_strategies.extend(
                                get_strategy_condition_labels(output.filter_name, output.details)
                            )
                passed_stocks.append({
                    "code": si.code,
                    "name": si.name or si.code,
                    "satisfied_strategies": satisfied_strategies,
                })
                if is_unified_bullish_top20:
                    unified_candidates.append({
                        "code": si.code,
                        "bullish_match_count": getattr(result, "bullish_match_count", 0),
                        "rebound_match_count": getattr(result, "rebound_match_count", 0),
                        "zuoyi_bullish_match_count": getattr(result, "zuoyi_bullish_match_count", 0),
                        "total_match_count": getattr(result, "total_match_count", 0),
                    })
            else:
                blocking_outputs = [
                    output for output in result.filter_outputs
                    if output.result in (FilterResult.FAIL, FilterResult.ERROR)
                ]
                if not blocking_outputs:
                    blocking_outputs = [
                        output for output in result.filter_outputs
                        if output.result == FilterResult.SKIP
                    ]
                fail_names = [output.filter_name for output in blocking_outputs]
                fail_reasons = [
                    f"{output.filter_name}: {output.reason}"
                    for output in blocking_outputs
                    if output.reason
                ]
                failed_stocks.append({
                    "code": si.code,
                    "name": si.name or si.code,
                    "failed_filters": fail_names,
                    "reasons": fail_reasons,
                    "summary": result.get_summary(),
                })

            if progress_log and not verbose:
                status = "✅" if result.passed else "❌"
                reason_tail = result.get_summary()
                if not result.passed and failed_stocks and failed_stocks[-1].get("reasons"):
                    reason_tail = f"{reason_tail} | {failed_stocks[-1]['reasons'][0]}"
                print(f"[{i}/{total_count}] {status} {si.code} {si.name or ''} | {reason_tail}")

            if verbose:
                status = "✅" if result.passed else "❌"
                print(f"\n{'='*80}")
                print(f"[{i}/{total_count}] {status} {si.code} - {si.name}")
                print(f"{'='*80}")
                
                # 打印股票基本信息
                if si.market_cap:
                    print(f"市值: {si.market_cap / 1e8:.2f}亿")
                else:
                    print(f"市值: N/A")
                    
                if si.pe_ratio:
                    print(f"PE: {si.pe_ratio:.2f}")
                else:
                    print(f"PE: N/A")
                
                # 检查是否有 K 线数据
                if si.kline_df is None or si.kline_df.empty:
                    print(f"⚠️  警告: 未获取到 K 线数据")
                else:
                    print(f"K线数据: {len(si.kline_df)} 根")
                
                # 打印每个输出（筛选器 + 策略器，已合并到 result.filter_outputs）
                for output in result.filter_outputs:
                    result_icon = {
                        "pass": "✅",
                        "fail": "❌",
                        "skip": "⊝",
                        "error": "⚠️"
                    }.get(output.result.value, "?")
                    
                    print(f"\n  {result_icon} {output.filter_name}: {output.result.value}")
                    print(f"     原因: {output.reason}")
                    
                    # 打印详细数值
                    if output.details:
                        details_str = ", ".join([f"{k}={v}" for k, v in output.details.items()])
                        print(f"     数值: {details_str}")
                
                # 总结
                print(f"\n总结: {result.get_summary()}")
                
                if result.passed:
                    print(f"🎉 满足所有条件！")
                else:
                    failed = [
                        output for output in result.filter_outputs
                        if output.result in (FilterResult.FAIL, FilterResult.ERROR)
                    ]
                    if failed:
                        print(f"\n关键失败原因:")
                        for f in failed:
                            print(f"  • {f.filter_name}: {f.reason}")
                print(f"{'='*80}\n")
            
            # 步骤4: 立即写入筛选结果到数据库（每只股票处理后立即写入）
            stock = result.stock
            close_price = None
            # 从输出中提取 close_price（PriceFilter 或策略器可能提供）
            for output in result.filter_outputs:
                if output.filter_name == "PriceFilter" and output.details:
                    close_price = output.details.get("price")
                    break
                if close_price is None and output.details:
                    close_price = output.details.get("latest_close")
            
            # 构建 filter_details（筛选器 + 策略器已合并到 result.filter_outputs）
            filter_details = []
            for o in result.filter_outputs:
                details = _json_safe_value(o.details or {})
                metadata = _rule_metadata_for_output(rule_engine, o)
                filter_details.append({
                    "rule_key": metadata.rule_key if metadata else o.filter_name,
                    "rule_name": metadata.rule_name if metadata else o.filter_name,
                    "rule_type": metadata.rule_type if metadata else "",
                    "strategy_category": metadata.strategy_category if metadata else "",
                    "filter_name": o.filter_name,
                    "result": o.result.value,
                    "reason": o.reason or "",
                    "details": details,
                })
            score_summary = aggregate_rule_scores(
                filter_details,
                **_score_weights_from_filter_details(filter_details),
            )

            # ── Compute EntryScore ──
            try:
                entry_bd = entry_scorer.compute(filter_details)
                entry_score_val = entry_bd.entry_score
                entry_decision_val = entry_bd.entry_decision
            except Exception:
                entry_score_val = 50.0
                entry_decision_val = "NO_BUY"
            # Update unified candidate dict for sorting
            if is_unified_bullish_top20:
                for cand in unified_candidates:
                    if cand["code"] == stock.code:
                        cand["entry_score"] = entry_score_val
                        break


            db_record = {
                "task_id": task_id,
                "market": market,
                "code": stock.code,
                "name": stock.name or "",
                "is_passed": result.passed,
                "filter_summary": result.get_summary(),
                "filter_details": filter_details,
                "technical_score": score_summary.get("technical_score"),
                "macro_score": score_summary.get("macro_score"),
                "final_score": score_summary.get("final_score"),
                "score_details": score_summary,
                "entry_score": entry_score_val,
                "entry_decision": entry_decision_val,
                "sector": stock.sector,
                "industry": stock.industry,
                "market_cap": stock.market_cap,
                "pe_ratio": stock.pe_ratio,
                "close_price": close_price,
            }
            
            if is_unified_bullish_top20:
                db_record["bullish_match_count"] = getattr(result, "bullish_match_count", 0)
                db_record["rebound_match_count"] = getattr(result, "rebound_match_count", 0)
                db_record["zuoyi_bullish_match_count"] = getattr(result, "zuoyi_bullish_match_count", 0)
                db_record["total_match_count"] = getattr(result, "total_match_count", 0)
                db_record["bullish_condition_labels"] = list(getattr(result, "bullish_condition_labels", []) or [])
                db_record["categorized_condition_matches"] = list(getattr(result, "categorized_condition_matches", []) or [])
                pending_screening_records.append(db_record)
            else:
                # 立即写入数据库
                db.upsert_screening_results(check_date=context.check_date, results=[db_record])

        if is_unified_bullish_top20:
            selected = select_unified_bullish_top_candidates(unified_candidates, top_n=UNIFIED_BULLISH_TOP_N)
            selected_codes = {item["code"] for item in selected}

            # ── 为 Top20 股票注入 signal_analysis_loader ──
            # unified 链的 requires_signal_analysis() 基于 expression(=ema_breakout) 返回 False，
            # 但 Top20 后置宏观规则中的 company_event_* 需要 signal_analysis 结果，因此在此强制注入。
            # 该 loader 供 CompanyEventHotSectorStrategizer / CompanyEventHotNewsStrategizer 复用。
            def _make_top20_signal_loader():
                def loader(current_stock: StockInfo) -> Any:
                    cached = macro_analysis_cache.get(current_stock.code)
                    if cached is not None:
                        return cached
                    analysis, warnings = run_signal_analysis_for_row(
                        mysql_config=mysql_config,
                        task_id=f"{task_id}:macro:{current_stock.code}",
                        row=_build_macro_signal_row(current_stock, market),
                        market=market,
                        check_date=context.check_date,
                        timeframe=timeframe,
                        enabled=True,
                    )
                    macro_analysis_cache[current_stock.code] = analysis
                    macro_warning_cache[current_stock.code] = list(warnings or [])
                    if verbose and warnings:
                        print(f"  ℹ️  {current_stock.code} - 宏观分析告警: {' | '.join(warnings)}")
                    return analysis
                return loader

            context.set_cache("signal_analysis_loader", _make_top20_signal_loader())

            # ── 对 Top20 股票执行 4 条宏观规则 ──
            for record in pending_screening_records:
                if record["code"] not in selected_codes:
                    continue
                si = live_stocks.get(record["code"])
                if si is None:
                    continue

                macro_result = rule_engine.evaluate_macro_rules_for_top20(si, context, market_cache=market_cache)

                # 合并 macro outputs 到 filter_details
                for o in macro_result.filter_outputs:
                    meta = _rule_metadata_for_output(rule_engine, o)
                    record["filter_details"].append({
                        "rule_key": meta.rule_key if meta else o.filter_name,
                        "rule_type": meta.rule_type if meta else "",
                        "strategy_category": meta.strategy_category if meta else "",
                        "filter_name": o.filter_name,
                        "result": o.result.value,
                        "reason": o.reason or "",
                        "details": _json_safe_value(o.details or {}),
                    })

                # 重算评分（现在 filter_details 包含技术规则 + 宏观规则）
                score_summary = aggregate_rule_scores(
                    record["filter_details"],
                    **_score_weights_from_filter_details(record["filter_details"]),
                )
                record["technical_score"] = score_summary.get("technical_score")
                record["macro_score"] = score_summary.get("macro_score")
                record["final_score"] = score_summary.get("final_score")
                record["score_details"] = score_summary

                # ── Compute HoldingScore for Top20 ──
                try:
                    holding_bd = holding_scorer.compute(
                        record["filter_details"],
                        market_temp,
                        None,  # llm_result - optional, Phase 2+ may populate
                    )
                    record["holding_score"] = holding_bd.holding_score
                    record["holding_decision"] = holding_bd.holding_decision
                    record["holding_period"] = holding_bd.holding_period
                    record["position_suggestion"] = holding_bd.position_suggestion
                    record["risk_level"] = holding_bd.risk_level
                except Exception:
                    record["holding_score"] = 50.0
                    record["holding_decision"] = "NOT_RECOMMENDED"
                    record["holding_period"] = "短线"
                    record["position_suggestion"] = "轻仓试探"
                    record["risk_level"] = "medium"

                if verbose:
                    print(f"  ✓ {record['code']} 宏观评分: technical={record.get('technical_score')}, "
                          f"macro={record.get('macro_score')}, final={record.get('final_score')}")

            # 清除 signal_analysis_loader，避免影响后续非 unified 链使用同一个 context
            context.set_cache("signal_analysis_loader", None)

            passed_stocks = []
            for record in pending_screening_records:
                is_selected = record["code"] in selected_codes
                record["is_passed"] = is_selected
                if not is_selected:
                    match_count = int(record.get("total_match_count") or record.get("bullish_match_count") or 0)
                    if match_count > 0:
                        record["filter_summary"] = (
                            f"命中 {match_count} 条规则"
                            f"（看涨 {int(record.get('bullish_match_count') or 0)} / "
                            f"准备反弹 {int(record.get('rebound_match_count') or 0)} / "
                            f"左一看涨 {int(record.get('zuoyi_bullish_match_count') or 0)}），"
                            f"但未进入Top{UNIFIED_BULLISH_TOP_N}"
                        )
                    else:
                        record["filter_summary"] = "未命中启用的看涨/准备反弹/左一看涨技术规则"
                else:
                    labels = list(record.get("bullish_condition_labels") or [])
                    record["filter_summary"] = (
                        f"统一看涨Top{UNIFIED_BULLISH_TOP_N}: 命中 {int(record.get('total_match_count') or len(labels))} 条规则"
                        f"（看涨 {int(record.get('bullish_match_count') or 0)} / "
                        f"准备反弹 {int(record.get('rebound_match_count') or 0)} / "
                        f"左一看涨 {int(record.get('zuoyi_bullish_match_count') or 0)}）"
                    )
                    passed_stocks.append({
                        "code": record["code"],
                        "name": record.get("name") or record["code"],
                        "satisfied_strategies": labels,
                    })
            db.upsert_screening_results(check_date=context.check_date, results=pending_screening_records)
        
        # 所有股票处理完成后的总结
        if verbose:
            print(f"✓ 筛选结果已实时写入 screening_results 表")
        
        # 获取通过筛选的股票数量
        passed_count = len(passed_stocks) if is_unified_bullish_top20 else sum(1 for r in results if r.passed)
        failed_count = total_count - passed_count

        # Always print summary
        print(f"\n{'='*60}")
        print(f"筛选完成：{passed_count}/{total_count} 只通过, {failed_count} 只未通过")
        print(f"{'='*60}")

        # Always print failed stock details
        if failed_stocks:
            print(f"\n❌ 未通过股票详情 ({len(failed_stocks)} 只):")
            # Group by top failure reason
            from collections import Counter
            reason_counter = Counter()
            for fs in failed_stocks:
                for r in fs.get("reasons", [])[:1]:  # first (most important) failure
                    reason_counter[r] += 1
            print(f"\n失败原因分布:")
            for reason, count in reason_counter.most_common(10):
                print(f"  {count}只: {reason}")
            detail_limit = 50
            print(f"\n明细样例（前 {min(detail_limit, len(failed_stocks))} 只）:")
            for fs in failed_stocks[:detail_limit]:
                print(f"  {fs['code']} {fs['name']} | {fs['summary']} | {'; '.join(fs.get('reasons', [])[:2])}")
            if len(failed_stocks) > detail_limit:
                print(f"  ... 其余 {len(failed_stocks) - detail_limit} 只略")
            print()
        
        # 更新任务状态为完成
        db.update_task_status(task_id, "completed")
    
    except Exception as e:
        print(f"筛选任务失败: {str(e)}")
        import traceback
        traceback.print_exc()
        if db:
            try:
                db.update_task_status(task_id, "failed")
            except Exception:
                pass
    finally:
        # 关闭 Futu quote_ctx
        if 'quote_ctx' in locals() and quote_ctx is not None:
            try:
                quote_ctx.close()
            except Exception:
                pass

        if 'context' in locals():
            market_intel_service = context.get_cache("market_intel_service")
            repository = getattr(market_intel_service, "repository", None)
            close = getattr(repository, "close", None)
            if callable(close):
                close()

        # 关闭数据库连接
        if db:
            db.close()


def create_and_start_screening_task(
    mysql_config: MySqlConfig,
    market: str,
    timeframe: str,
    params: dict,
    background_runner: Callable[[Callable], None],
    watchlist: Optional[list] = None,
    chain_key: Optional[str] = None,
) -> str:
    """
    创建并启动筛选任务

    Args:
        mysql_config: MySQL 配置
        market: 市场
        timeframe: 时间周期
        params: 筛选参数
        background_runner: 后台运行函数（接收一个可调用对象）
        watchlist: 自选股列表 [{"code": "...", "name": "..."}, ...]，非空时仅筛选此列表

    Returns:
        task_id: 任务ID
    """
    task_id = str(uuid.uuid4())
    params_for_task = dict(params or {})
    if chain_key:
        params_for_task["chain_key"] = chain_key

    db = MarketDatabase(mysql_config)
    db.init_schema(timeframe)

    if watchlist and len(watchlist) > 0:
        total_count = len(watchlist)
    else:
        total_count = db.stock_count(market)

    db.create_screening_task(
        task_id=task_id,
        market=market,
        timeframe=timeframe,
        total_count=total_count,
        params_json=params_for_task,
        check_date=date.today(),
    )

    db.close()

    background_runner(
        lambda: run_screening_task(
            mysql_config=mysql_config,
            task_id=task_id,
            market=market,
            timeframe=timeframe,
            params=params_for_task,
            watchlist=watchlist,
            verbose=True,
            chain_key=chain_key,
        )
    )

    return task_id
