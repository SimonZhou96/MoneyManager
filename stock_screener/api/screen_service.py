#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
筛选服务 - 封装 run_screening + 进度回调
"""

import time
import uuid
from datetime import date
from typing import Callable, Optional

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
from strategizers import (
    StrategizerChain,
    ZuoYiStrategizer,
    EMABreakoutStrategizer,
    RSIOversoldStrategizer,
    RSIOverboughtStrategizer,
    TodayVolumeExceedsPrior3MaxStrategizer,
    DailyPctChangeBandStrategizer,
)
from timeframe import parse_timeframe
from universe import fetch_stock_list_akshare
from universe_filter import UniverseFilterFactory


STRATEGY_NAME_MAP = {
    "EMABreakoutStrategizer": "EMA突破",
    "RSIOversoldStrategizer": "RSI超卖",
    "RSIOverboughtStrategizer": "RSI超买",
    "TodayVolumeExceedsPrior3MaxStrategizer": "放量超前三日",
    "DailyDrop6To65Strategizer": "当日跌6%~6.5%",
    "DailyRise4To45Strategizer": "当日涨4%~4.5%",
}


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

    label = STRATEGY_NAME_MAP.get(filter_name)
    return [label] if label else []


def _json_safe_value(value):
    """递归转换为 JSON 可序列化的值，保留 list/dict 明细。"""
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, (str, int, float, bool)):
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
    根据参数创建策略器链。任意一个策略器满足即视为股票满足策略条件。
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
            signal_window=params.get("zuoyi_signal_window", 3),
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

    # 量价/涨跌幅：与 EMA、RSI 并列，任一满足即可（StrategizerChain）
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


def run_screening_task(
    mysql_config: MySqlConfig,
    task_id: str,
    market: str,
    timeframe: str,
    params: dict,
    verbose: bool = False,
    watchlist: Optional[list] = None,
    progress_log: bool = False,
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
        
        # 创建筛选器链与策略器链
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

        # 创建 K 线获取器（尝试连接 Futu OpenD）
        fetchers = None
        quote_ctx = None
        if needs_kline:
            # 尝试连接 Futu OpenD
            try:
                from futu import OpenQuoteContext
                quote_ctx = OpenQuoteContext(host='127.0.0.1', port=11111)
                if verbose:
                    print("✓ 已连接 Futu OpenD")
            except Exception as e:
                if verbose:
                    print(f"⚠️  无法连接 Futu OpenD: {e}，将使用其他数据源")
                quote_ctx = None

            # 创建获取器链（传入 quote_ctx，如果可用）
            fetchers = KlineFetcherFactory.create_fetcher_chain(quote_ctx=quote_ctx)
        
        # 创建筛选器上下文
        context = FilterContext(
            check_date=date.today(),
            market=market,
            db=db,
            verbose=verbose,
        )
        # 注入 timeframe 供 AvgDailyVolumeFilter 使用
        context.timeframe = timeframe
        
        # 主循环：遍历每只股票，在同一个循环中完成以下步骤
        # 步骤1: 获取K线数据
        # 步骤2: 应用筛选器链（判断是否满足突破策略和筛选条件）
        # 步骤3: 打印详细日志
        # 步骤4: 写入数据库
        results = []
        passed_stocks = []  # 记录满足条件的股票
        
        if verbose:
            print(f"\n{'='*80}")
            print(f"开始逐个处理 {len(stock_infos)} 只股票")
            print(f"流程: 获取K线 → 筛选判断 → 打印日志 → 写入数据库")
            print(f"{'='*80}\n")
        
        for i, si in enumerate(stock_infos, 1):
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
            
            # 步骤2: 筛选器 -> 策略器；合并为单一 result（filter_outputs 含筛选器+策略器，passed=筛选通过且任意策略满足）
            result = filter_chain.apply(si, context)
            strategy_result = strategy_chain.apply(si, context)
            for out in strategy_result.outputs:
                result.add_output(FilterOutput(
                    filter_name=out.name,
                    result=FilterResult.PASS if out.satisfied else FilterResult.FAIL,
                    reason=out.reason or "",
                    details=dict(out.details) if out.details else {},
                ))
            result.passed = result.passed and strategy_result.any_satisfied
            results.append(result)

            # 步骤3: 详细日志输出（所有股票都打印，不论是否满足条件）
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

                    # 提取满足的策略
                    satisfied_strategies = []
                    for output in result.filter_outputs:
                        if output.result.value == "pass":
                            satisfied_strategies.extend(
                                get_strategy_condition_labels(output.filter_name, output.details)
                            )

                    passed_stocks.append({
                        "code": si.code,
                        "name": si.name or si.code,
                        "satisfied_strategies": satisfied_strategies
                    })
                else:
                    failed_filters = result.get_failed_filters()
                    skipped_filters = [o for o in result.filter_outputs if o.result.value == "skip"]
                    
                    if failed_filters:
                        print(f"❌ 未通过的筛选器 ({len(failed_filters)}): {', '.join([f.filter_name for f in failed_filters])}")
                    if skipped_filters:
                        print(f"⊝  跳过的筛选器 ({len(skipped_filters)}): {', '.join([f.filter_name for f in skipped_filters])}")
                    
                    # 打印关键失败原因
                    if failed_filters:
                        print(f"\n关键失败原因:")
                        for f in failed_filters:
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
                filter_details.append({
                    "filter_name": o.filter_name,
                    "result": o.result.value,
                    "reason": o.reason or "",
                    "details": details,
                })
            
            db_record = {
                "task_id": task_id,
                "market": market,
                "code": stock.code,
                "name": stock.name or "",
                "is_passed": result.passed,
                "filter_summary": result.get_summary(),
                "filter_details": filter_details,
                "sector": stock.sector,
                "industry": stock.industry,
                "market_cap": stock.market_cap,
                "pe_ratio": stock.pe_ratio,
                "close_price": close_price,
            }
            
            # 立即写入数据库
            db.upsert_screening_results(check_date=context.check_date, results=[db_record])
        
        # 所有股票处理完成后的总结
        if verbose:
            print(f"✓ 筛选结果已实时写入 screening_results 表")
        
        # 获取通过筛选的股票数量
        passed_count = sum(1 for r in results if r.passed)
        
        if verbose:
            print(f"\n{'='*60}")
            print(f"筛选完成：{passed_count}/{total_count} 只股票通过")
            print(f"{'='*60}\n")
        
        # 更新任务状态为完成
        db.update_task_status(task_id, "completed")
    
    except Exception as e:
        if verbose:
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
        params_json=params,
        check_date=date.today(),
    )

    db.close()

    background_runner(
        lambda: run_screening_task(
            mysql_config=mysql_config,
            task_id=task_id,
            market=market,
            timeframe=timeframe,
            params=params,
            watchlist=watchlist,
            verbose=True,
        )
    )

    return task_id
