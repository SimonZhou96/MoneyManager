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
    EMABreakoutStrategizer,
    RSIOversoldStrategizer,
    RSIOverboughtStrategizer,
)
from timeframe import parse_timeframe
from universe_filter import UniverseFilterFactory


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

    return chain


def run_screening_task(
    mysql_config: MySqlConfig,
    task_id: str,
    market: str,
    timeframe: str,
    params: dict,
    verbose: bool = False,
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
    """
    db = None
    try:
        db = MarketDatabase(mysql_config)
        db.init_schema(timeframe)
        
        # 获取股票列表
        stocks = db.get_stocks(market, include_fundamentals=True)
        if not stocks:
            if verbose:
                print(f"✗ 未获取到{market_label(market)}股票列表")
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

        if not filter_chain.list_filters():
            if verbose:
                print("警告：没有启用任何筛选器")
            db.update_task_status(task_id, "completed")
            return

        # 检查是否需要 K 线：筛选器或策略器任一需要则拉取
        needs_kline = any(
            f.__class__.__name__ in ["PriceFilter", "AvgDailyVolumeFilter"]
            for f in filter_chain._filters if f.enabled
        ) or len(strategy_chain.list_strategizers()) > 0
        
        # 创建 K 线获取器
        fetchers = KlineFetcherFactory.create_fetcher_chain() if needs_kline else None
        
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
                    passed_stocks.append({"code": si.code, "name": si.name or si.code})
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
            
            # 构建 filter_details（筛选器 + 策略器已合并到 result.filter_outputs）
            filter_details = []
            for o in result.filter_outputs:
                details = {}
                for k, v in (o.details or {}).items():
                    if v is None:
                        details[k] = None
                    elif hasattr(v, "isoformat"):
                        details[k] = v.isoformat() if v else None
                    elif isinstance(v, (str, int, float, bool)):
                        details[k] = v
                    else:
                        details[k] = str(v)
                filter_details.append({
                    "filter_name": o.filter_name,
                    "result": o.result.value,
                    "reason": o.reason or "",
                    "details": details,
                })
            
            db_record = {
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
        if db:
            db.close()


def create_and_start_screening_task(
    mysql_config: MySqlConfig,
    market: str,
    timeframe: str,
    params: dict,
    background_runner: Callable[[Callable], None],
) -> str:
    """
    创建并启动筛选任务
    
    Args:
        mysql_config: MySQL 配置
        market: 市场
        timeframe: 时间周期
        params: 筛选参数
        background_runner: 后台运行函数（接收一个可调用对象）
        
    Returns:
        task_id: 任务ID
    """
    # 生成任务ID
    task_id = str(uuid.uuid4())
    
    # 创建数据库连接，获取股票总数
    db = MarketDatabase(mysql_config)
    db.init_schema(timeframe)
    
    total_count = db.stock_count(market)
    
    # 创建任务记录
    db.create_screening_task(
        task_id=task_id,
        market=market,
        timeframe=timeframe,
        total_count=total_count,
        params_json=params,
        check_date=date.today(),
    )
    
    db.close()
    
    # 启动后台任务
    background_runner(
        lambda: run_screening_task(
            mysql_config=mysql_config,
            task_id=task_id,
            market=market,
            timeframe=timeframe,
            params=params,
            verbose=True,
        )
    )
    
    return task_id
