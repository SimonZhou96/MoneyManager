#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
使用筛选器执行链进行股票筛选的示例

这个脚本演示了如何使用新的筛选器架构来筛选股票。
筛选器执行链支持：
- EMA突破筛选器：检查 EMA10 向上突破 EMA150
- 市值筛选器：按市值范围筛选
- PE筛选器：按市盈率范围筛选
- 板块筛选器：按板块/行业筛选
- 换手率筛选器：按换手率范围筛选
- 成交量筛选器：按成交量范围筛选
- 自定义筛选器：支持用户自定义筛选逻辑

使用示例：
    python screen_with_filters.py --market HK --use-ema --min-pe 0 --max-pe 50
"""

import argparse
import os
import time
from datetime import date

from db import MarketDatabase, MySqlConfig
from filters import (
    FilterChain,
    FilterContext,
    StockInfo,
    EMABreakoutFilter,
    MarketCapFilter,
    PEFilter,
    SectorFilter,
    TurnoverRateFilter,
    VolumeFilter,
    CustomFilter,
    FilterOutput,
    FilterResult,
    stocks_to_stock_infos,
)
from kline_fetcher import KlineFetcherFactory
from market import normalize_market, market_label
from timeframe import parse_timeframe
from universe_filter import UniverseFilterFactory


def _filter_output_to_dict(output: FilterOutput) -> dict:
    """将 FilterOutput 转为 JSON 可序列化的字典"""
    details = {}
    for k, v in (output.details or {}).items():
        if v is None:
            details[k] = None
        elif hasattr(v, "isoformat"):  # date/datetime
            details[k] = v.isoformat() if v else None
        elif isinstance(v, (str, int, float, bool)):
            details[k] = v
        else:
            details[k] = str(v)
    return {
        "filter_name": output.filter_name,
        "result": output.result.value,
        "reason": output.reason or "",
        "details": details,
    }


def create_filter_chain_from_args(args) -> FilterChain:
    """
    根据命令行参数创建筛选器链
    
    Args:
        args: argparse 解析后的参数
        
    Returns:
        FilterChain: 配置好的筛选器链
    """
    chain = FilterChain(mode="all", early_stop=args.early_stop)
    
    # EMA 突破筛选器
    if args.use_ema:
        chain.add_filter(EMABreakoutFilter(
            ema_short=args.ema_short,
            ema_long=args.ema_long,
        ))
    
    # 市值筛选器
    if args.min_market_cap is not None or args.max_market_cap is not None:
        chain.add_filter(MarketCapFilter(
            min_cap=args.min_market_cap,
            max_cap=args.max_market_cap,
        ))
    
    # PE 筛选器
    if args.min_pe is not None or args.max_pe is not None:
        chain.add_filter(PEFilter(
            min_pe=args.min_pe,
            max_pe=args.max_pe,
            allow_negative=args.allow_negative_pe,
        ))
    
    # 板块筛选器
    if args.include_sectors or args.exclude_sectors:
        include_sectors = args.include_sectors.split(',') if args.include_sectors else None
        exclude_sectors = args.exclude_sectors.split(',') if args.exclude_sectors else None
        chain.add_filter(SectorFilter(
            include_sectors=include_sectors,
            exclude_sectors=exclude_sectors,
        ))
    
    # 换手率筛选器
    if args.min_turnover_rate is not None or args.max_turnover_rate is not None:
        chain.add_filter(TurnoverRateFilter(
            min_rate=args.min_turnover_rate,
            max_rate=args.max_turnover_rate,
        ))
    
    return chain


def run_screening(
    mysql: MySqlConfig,
    market: str,
    filter_chain: FilterChain,
    timeframe: str = "1d",
    limit: int = None,
    verbose: bool = True,
    save_to_db: bool = True,
    universe_reducer=None,
):
    """
    执行股票筛选

    Args:
        mysql: MySQL 配置
        market: 市场
        filter_chain: 筛选器链
        timeframe: K 线周期
        limit: 限制股票数量
        verbose: 是否输出详细日志
        save_to_db: 是否将筛选结果存储到 MySQL
        universe_reducer: 股票池缩减器（可选）
    """
    db = MarketDatabase(mysql)
    db.init_schema(timeframe)

    # 获取股票列表
    stocks = db.get_stocks(market, include_fundamentals=True)
    if not stocks:
        print(f"✗ 未获取到{market_label(market)}股票列表")
        db.close()
        return []

    if limit:
        stocks = stocks[:limit]

    print(f"\n{'='*60}")
    print(f"开始筛选 {len(stocks)} 只{market_label(market)}股票 (timeframe={timeframe})")
    print(f"筛选器: {', '.join(filter_chain.list_filters())}")
    print(f"{'='*60}\n")

    # 转换为 StockInfo 列表
    stock_infos = stocks_to_stock_infos(stocks, market, db)

    # 股票池缩减
    if universe_reducer is not None:
        before = len(stock_infos)
        stock_infos = universe_reducer.reduce(stock_infos)
        if verbose and before > len(stock_infos):
            print(f"股票池缩减: {before} -> {len(stock_infos)} ({universe_reducer.get_name()})")

    # 预获取 K 线数据到 stock.kline_df（EMABreakoutFilter 依赖此数据）
    fetchers = KlineFetcherFactory.create_fetcher_chain()
    if fetchers and any(isinstance(f, EMABreakoutFilter) for f in filter_chain._filters):
        print(f"预获取 K 线数据 ({len(stock_infos)} 只)...")
        for i, si in enumerate(stock_infos, 1):
            for fetcher in fetchers:
                try:
                    df = fetcher.fetch(si.code, market=si.market, timeframe=timeframe)
                    if df is not None and not df.empty:
                        si.kline_df = df
                        break
                except Exception:
                    continue
            if verbose and i % 50 == 0:
                print(f"  已获取 {i}/{len(stock_infos)}")
            time.sleep(0.2)
        print(f"  K 线获取完成")

    # 创建筛选器上下文
    context = FilterContext(
        check_date=date.today(),
        market=market,
        db=db,
        verbose=verbose,
    )

    # 执行筛选
    results = filter_chain.execute(stock_infos, context)
    
    # 获取通过筛选的股票
    passed = filter_chain.get_passed(results)
    
    # 将筛选结果存储到 MySQL
    if save_to_db and results:
        check_date = context.check_date
        db_records = []
        for result in results:
            stock = result.stock
            close_price = None
            for output in result.filter_outputs:
                if output.filter_name == "EMABreakoutFilter" and output.details:
                    close_price = output.details.get("close_price")
                    break
            filter_details = [_filter_output_to_dict(o) for o in result.filter_outputs]
            db_records.append({
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
            })
        db.upsert_screening_results(check_date=check_date, results=db_records)
        print(f"✓ 已将 {len(db_records)} 只股票的筛选结果写入 screening_results 表")
    
    print(f"\n{'='*60}")
    print(f"筛选结果：{len(passed)}/{len(stocks)} 只股票通过")
    print(f"{'='*60}\n")
    
    # 输出通过筛选的股票
    if passed:
        print("通过筛选的股票：")
        print("-" * 80)
        print(f"{'代码':<15} {'名称':<20} {'板块':<15} {'收盘价':<10} {'PE':<10}")
        print("-" * 80)
        
        for result in passed:
            stock = result.stock
            close_price = "-"
            pe = "-"
            
            # 从筛选器输出中获取更多信息
            for output in result.filter_outputs:
                if output.filter_name == "EMABreakoutFilter" and output.details:
                    # 可以从 output.details 获取更多数据
                    pass
            
            # 使用股票信息
            if stock.pe_ratio is not None:
                pe = f"{stock.pe_ratio:.2f}"
            
            sector = stock.sector or stock.industry or "-"
            print(f"{stock.code:<15} {stock.name:<20} {sector:<15} {close_price:<10} {pe:<10}")
        
        print("-" * 80)
    
    db.close()
    return passed


def main():
    parser = argparse.ArgumentParser(description="使用筛选器执行链进行股票筛选")
    
    # MySQL 配置
    parser.add_argument("--mysql-host", default=os.getenv("MYSQL_HOST", "127.0.0.1"), help="MySQL Host")
    parser.add_argument("--mysql-port", type=int, default=int(os.getenv("MYSQL_PORT", "3306")), help="MySQL Port")
    parser.add_argument("--mysql-user", default=os.getenv("MYSQL_USER", "root"), help="MySQL User")
    parser.add_argument("--mysql-password", default=os.getenv("MYSQL_PASSWORD", "123456"), help="MySQL Password")
    parser.add_argument("--mysql-database", default=os.getenv("MYSQL_DATABASE", "market_data"), help="MySQL Database")
    
    # 市场和通用选项
    parser.add_argument("--market", default="HK", help="市场: HK、US 或 A")
    parser.add_argument("--timeframe", default="1d",
                        help="K 线周期: 1m,5m,15m,30m,60m,1h,1d,5d,1wk,1mo,3mo")
    parser.add_argument("--limit", type=int, default=None, help="限制股票数量")
    parser.add_argument("--verbose", action="store_true", default=True, help="输出详细日志")
    parser.add_argument("--early-stop", action="store_true", default=False, help="遇到第一个失败就停止")
    
    # EMA 突破筛选器选项
    parser.add_argument("--use-ema", action="store_true", default=True, help="使用 EMA 突破筛选器")
    parser.add_argument("--no-ema", action="store_false", dest="use_ema", help="禁用 EMA 突破筛选器")
    parser.add_argument("--ema-short", type=int, default=10, help="短期 EMA 周期")
    parser.add_argument("--ema-long", type=int, default=150, help="长期 EMA 周期")
    
    # 市值筛选器选项
    parser.add_argument("--min-market-cap", type=float, default=None, help="最小市值")
    parser.add_argument("--max-market-cap", type=float, default=None, help="最大市值")
    
    # PE 筛选器选项
    parser.add_argument("--min-pe", type=float, default=None, help="最小 PE")
    parser.add_argument("--max-pe", type=float, default=None, help="最大 PE")
    parser.add_argument("--allow-negative-pe", action="store_true", default=False, help="允许负 PE")
    
    # 板块筛选器选项
    parser.add_argument("--include-sectors", type=str, default=None, help="包含的板块（逗号分隔）")
    parser.add_argument("--exclude-sectors", type=str, default=None, help="排除的板块（逗号分隔）")
    
    # 换手率筛选器选项
    parser.add_argument("--min-turnover-rate", type=float, default=None, help="最小换手率(%)")
    parser.add_argument("--max-turnover-rate", type=float, default=None, help="最大换手率(%)")

    # 股票池缩减选项（在筛选链前快速缩小待遍历数量）
    parser.add_argument("--universe-min-cap", type=float, default=None, help="股票池最小市值")
    parser.add_argument("--universe-max-cap", type=float, default=None, help="股票池最大市值")
    parser.add_argument("--universe-min-pe", type=float, default=None, help="股票池最小PE")
    parser.add_argument("--universe-max-pe", type=float, default=None, help="股票池最大PE")
    parser.add_argument("--universe-include-sectors", type=str, default=None, help="股票池包含板块（逗号分隔）")
    parser.add_argument("--universe-exclude-sectors", type=str, default=None, help="股票池排除板块（逗号分隔）")
    
    # 存储选项
    parser.add_argument("--no-save-to-db", action="store_true", help="不将筛选结果保存到 MySQL")
    
    args = parser.parse_args()
    
    # 创建 MySQL 配置
    mysql = MySqlConfig(
        host=args.mysql_host,
        port=args.mysql_port,
        user=args.mysql_user,
        password=args.mysql_password,
        database=args.mysql_database,
    )
    
    # 创建筛选器链
    filter_chain = create_filter_chain_from_args(args)
    
    if not filter_chain.list_filters():
        print("警告：没有启用任何筛选器！")
        return

    # 创建股票池缩减器（可选）
    universe_reducer = UniverseFilterFactory.create_composite(
        min_cap=args.universe_min_cap,
        max_cap=args.universe_max_cap,
        min_pe=args.universe_min_pe,
        max_pe=args.universe_max_pe,
        include_sectors=args.universe_include_sectors.split(",") if args.universe_include_sectors else None,
        exclude_sectors=args.universe_exclude_sectors.split(",") if args.universe_exclude_sectors else None,
    )

    timeframe = parse_timeframe(args.timeframe)

    # 执行筛选
    run_screening(
        mysql=mysql,
        market=normalize_market(args.market),
        filter_chain=filter_chain,
        timeframe=timeframe,
        limit=args.limit,
        verbose=args.verbose,
        save_to_db=not args.no_save_to_db,
        universe_reducer=universe_reducer,
    )


if __name__ == "__main__":
    main()
