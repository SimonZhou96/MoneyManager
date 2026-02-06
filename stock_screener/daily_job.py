#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
每日数据同步任务：股票列表 + K线历史入库
"""

import argparse
import json
import os
import time
from datetime import date, datetime, timedelta

import pandas as pd

from db import MarketDatabase, MySqlConfig
from kline_fetcher import KlineFetcherFactory
from market import market_label, parse_markets, normalize_market
from strategy import analyze_stock_ema_breakout, EMABreakoutResult
from universe import fetch_stock_list_akshare, fetch_stock_list_futu


def _previous_business_day(today: date) -> date:
    # 简化版：仅按周末回退（不处理交易所节假日）
    d = today - timedelta(days=1)
    while d.weekday() >= 5:  # 5=Saturday, 6=Sunday
        d -= timedelta(days=1)
    return d


def _should_fetch(last_date: str, target_end: date) -> bool:
    """
    是否需要拉取增量：
    - last_date 为空：需要（首次建库）
    - last_date < target_end：需要补齐到 target_end
    - last_date >= target_end：跳过
    """
    if not last_date:
        return True
    try:
        last_dt = pd.to_datetime(last_date).date()
    except Exception:
        return True
    return last_dt < target_end


def _normalize_kline_df(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    if "date" not in df.columns and "time_key" in df.columns:
        df = df.rename(columns={"time_key": "date"})
    if "date" in df.columns:
        df = df.copy()
        df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
    return df


def _load_kline_cache(code: str, market: str) -> pd.DataFrame | None:
    """
    复用 `cache/kline_data` 的本地文件（parquet 优先，csv fallback）。
    文件名规则与 `KlineDataManager` 保持一致：{MARKET}_{CODE_SAFE}.parquet
    例如：HK.00001 -> HK_HK_00001.parquet（注意：当前缓存实际为 HK_00001.parquet）
    """
    base_dir = os.path.dirname(os.path.abspath(__file__))
    cache_dir = os.path.join(base_dir, "cache", "kline_data")
    if not os.path.isdir(cache_dir):
        return None

    market_tag = str(market).upper()
    safe_code = str(code).replace(".", "_").replace("/", "_")

    # 兼容当前缓存命名：HK.00001 -> HK_00001.parquet（即去掉 market 前缀的 HK.）
    candidates = []
    if safe_code.startswith(f"{market_tag}_"):
        candidates.append(os.path.join(cache_dir, f"{safe_code}.parquet"))
        candidates.append(os.path.join(cache_dir, f"{safe_code}.csv"))
        candidates.append(os.path.join(cache_dir, f"{market_tag}_{safe_code}.parquet"))
        candidates.append(os.path.join(cache_dir, f"{market_tag}_{safe_code}.csv"))
    else:
        candidates.append(os.path.join(cache_dir, f"{market_tag}_{safe_code}.parquet"))
        candidates.append(os.path.join(cache_dir, f"{market_tag}_{safe_code}.csv"))

    file_path = next((p for p in candidates if os.path.exists(p)), None)
    if not file_path:
        return None

    try:
        if file_path.endswith(".parquet"):
            df = pd.read_parquet(file_path)
        else:
            df = pd.read_csv(file_path, encoding="utf-8")
        if df is None or df.empty:
            return None
        if "date" in df.columns:
            df = df.copy()
            df["date"] = pd.to_datetime(df["date"], errors="coerce")
            df = df[df["date"].notna()]
        return df
    except Exception:
        return None


def _build_log_record(market, code, name, status, reason, rows, last_date):
    return {
        "market": market,
        "code": code,
        "name": name,
        "status": status,
        "reason": reason,
        "rows": rows,
        "last_date": last_date,
        "timestamp": datetime.utcnow().isoformat(),
    }


def check_and_save_ema_breakout(
    db: MarketDatabase,
    market: str,
    code: str,
    check_date: date,
    verbose: bool = True,
) -> None:
    """
    检查单只股票的 EMA 突破情况并立即写入数据库
    
    设计原则：
    - 高内聚：所有 EMA 突破检查逻辑集中在此函数
    - 低耦合：只依赖 db 和 strategy 模块，不依赖具体的数据获取方式
    - 可复用：可被 run_once、sync_single_stock 或其他函数调用
    
    Args:
        db: 数据库连接对象
        market: 市场（HK/US）
        code: 股票代码
        check_date: 检查日期（一般是今天）
        verbose: 是否输出详细信息
    """
    # 从数据库获取 K 线数据（需要至少 152 天来计算 EMA150 + 2天回溯）
    # 为安全起见，获取更多数据
    start_date = (check_date - timedelta(days=365)).strftime("%Y-%m-%d")
    end_date = check_date.strftime("%Y-%m-%d")
    
    df = db.get_klines(market, code, start_date=start_date, end_date=end_date)
    
    # 执行策略分析
    signal = analyze_stock_ema_breakout(
        market=market,
        code=code,
        df=df,
        check_date=check_date,
    )
    
    # 立即写入数据库
    db.upsert_ema_breakout_signal(
        market=signal.market,
        code=signal.code,
        check_date=signal.check_date,
        result_type=signal.result.value,
        is_satisfied=signal.result.is_satisfied(),
        breakout_date=signal.breakout_date,
        ema10=signal.ema10,
        ema150=signal.ema150,
        close_price=signal.close_price,
        data_rows=signal.data_rows,
        result_desc=signal.result.get_description(),
    )
    
    if verbose:
        satisfied_mark = "✅" if signal.result.is_satisfied() else "❌"
        print(f"  {satisfied_mark} EMA突破检查: {code} -> {signal.result.value} ({signal.result.get_description()})")


def _init_futu_context(host: str, port: int):
    try:
        import futu as ft
    except Exception:
        return None, None
    try:
        quote_ctx = ft.OpenQuoteContext(host=host, port=port)
        return quote_ctx, ft
    except Exception:
        return None, None


def _parse_stock_code(stock_code: str) -> tuple[str, str] | None:
    """
    解析股票代码，返回 (market, code) 元组
    例如: "HK.00700" -> ("HK", "HK.00700")
          "US.AAPL" -> ("US", "US.AAPL")
    """
    code = str(stock_code).strip().upper()
    if code.startswith("HK."):
        return ("HK", code)
    elif code.startswith("US."):
        return ("US", code)
    elif code.isdigit() and len(code) == 5:
        # 假设5位数字是港股代码
        return ("HK", f"HK.{code}")
    else:
        # 尝试作为美股代码
        return ("US", f"US.{code}")


def sync_single_stock(
    mysql: MySqlConfig,
    stock_code: str,
    use_futu: bool,
    futu_host: str,
    futu_port: int,
    log_path: str,
):
    """
    同步单个股票的数据：
    1. 如果股票不在 stocks 表中，则插入
    2. 获取近5年的K线数据
    3. 存储K线数据到 kline_daily 表
    
    高内聚：所有单个股票同步逻辑集中在此函数
    低耦合：独立于 run_once，不影响现有批量同步功能
    """
    # 解析股票代码
    parsed = _parse_stock_code(stock_code)
    if not parsed:
        print(f"❌ 无法解析股票代码: {stock_code}")
        return
    market, code = parsed
    
    db = MarketDatabase(mysql)
    db.init_schema()
    
    # 使用工厂模式创建获取器链
    futu_ctx = None
    if use_futu:
        futu_ctx, _ = _init_futu_context(futu_host, futu_port)
    
    fetchers = KlineFetcherFactory.create_fetcher_chain(quote_ctx=futu_ctx)
    
    # 1. 检查股票是否在 stocks 表中，如果不存在则插入
    existing_stocks = db.get_stocks(market)
    stock_exists = any(s["code"] == code for s in existing_stocks)
    
    if not stock_exists:
        print(f"📋 股票 {code} 不在列表中，正在获取股票信息...")
        # 尝试从 AKShare 获取股票信息
        stocks = fetch_stock_list_akshare(market)
        target_stock = next((s for s in stocks if s["code"] == code), None)
        
        if not target_stock and futu_ctx:
            # 如果 AKShare 没有，尝试 Futu
            stocks = fetch_stock_list_futu(futu_ctx, market)
            target_stock = next((s for s in stocks if s["code"] == code), None)
        
        if target_stock:
            db.upsert_stocks(market, [target_stock], source="AKShare" if not futu_ctx else "Futu")
            print(f"✓ 股票 {code} ({target_stock.get('name', 'N/A')}) 已添加到列表")
        else:
            # 如果找不到股票信息，创建一个基本记录
            db.upsert_stocks(market, [{"code": code, "name": code}], source="Manual")
            print(f"⚠️  未找到股票 {code} 的详细信息，已创建基本记录")
    else:
        stock_info = next((s for s in existing_stocks if s["code"] == code), None)
        print(f"✓ 股票 {code} ({stock_info.get('name', 'N/A') if stock_info else 'N/A'}) 已在列表中")
    
    # 2. 获取近5年的K线数据
    today = date.today()
    target_end = _previous_business_day(today)
    start_date = (target_end - timedelta(days=365 * 5)).strftime("%Y-%m-%d")
    end_date = target_end.strftime("%Y-%m-%d")
    
    print(f"📊 正在获取 {code} 的K线数据 ({start_date} 至 {end_date})...")
    
    # 优先使用缓存
    df_cache = _load_kline_cache(code, market)
    df = None
    source = None
    
    # 如果缓存存在且覆盖所需日期范围，直接使用缓存
    if df_cache is not None and not df_cache.empty and "date" in df_cache.columns:
        try:
            cache_min = pd.to_datetime(df_cache["date"]).dt.date.min()
            cache_max = pd.to_datetime(df_cache["date"]).dt.date.max()
            start_dt = pd.to_datetime(start_date).date()
            end_dt = pd.to_datetime(end_date).date()
            
            if cache_min <= start_dt and cache_max >= end_dt:
                # 缓存完全覆盖所需范围
                df_cache_norm = df_cache.copy()
                df_cache_norm["date"] = pd.to_datetime(df_cache_norm["date"]).dt.strftime("%Y-%m-%d")
                df_cache_dates = pd.to_datetime(df_cache_norm["date"]).dt.date
                df_cache_norm = df_cache_norm[
                    (df_cache_dates >= start_dt) & (df_cache_dates <= end_dt)
                ]
                if not df_cache_norm.empty:
                    df_cache_norm = _normalize_kline_df(df_cache_norm)
                    db.upsert_klines(market, code, df_cache_norm, source="Cache", adj_type="qfq")
                    print(f"✓ 使用缓存数据: {len(df_cache_norm)} 条记录")
                    df = df_cache_norm
                    source = "Cache"
        except Exception as e:
            print(f"⚠️  处理缓存数据时出错: {e}")
    
    # 如果缓存不可用，从数据源获取
    if df is None or df.empty:
        # 按优先级尝试各个数据源
        for fetcher in fetchers:
            try:
                df = fetcher.fetch(
                    code,
                    market=market,
                    start_date=start_date,
                    end_date=end_date,
                    max_count=5000,
                )
                if df is not None and not df.empty:
                    source = fetcher.get_name()
                    break
            except Exception:
                continue
        
        if df is None or df.empty:
            print(f"❌ 无法获取 {code} 的K线数据")
            if futu_ctx:
                futu_ctx.close()
            db.close()
            return
        
        # 标准化并存储
        df = _normalize_kline_df(df)
        if df is not None and not df.empty:
            db.upsert_klines(market, code, df, source=source, adj_type="qfq")
            print(f"✓ 从 {source} 获取并存储: {len(df)} 条记录")
    
    # 3. 执行 EMA 突破策略检查
    try:
        check_and_save_ema_breakout(db, market, code, today, verbose=True)
    except Exception as e:
        print(f"⚠️ EMA突破检查异常: {code} - {e}")
    
    # 4. 记录日志
    last_date = db.last_kline_date(market, code)
    stock_info = next((s for s in db.get_stocks(market) if s["code"] == code), None)
    name = stock_info.get("name") if stock_info else code
    
    log_record = _build_log_record(
        market, code, name, "updated", f"{source} 同步成功", len(df) if df is not None else 0, last_date
    )
    
    if log_path:
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(log_record, ensure_ascii=False) + "\n")
        print(f"✓ 日志已追加到: {log_path}")
    
    if futu_ctx:
        futu_ctx.close()
    db.close()
    
    print(f"✅ 股票 {code} 同步完成！")


def run_once(
    mysql: MySqlConfig,
    markets,
    use_futu: bool,
    futu_host: str,
    futu_port: int,
    log_path: str,
    limit: int | None,
):
    db = MarketDatabase(mysql)
    db.init_schema()
    
    # 使用工厂模式创建获取器链
    futu_ctx = None
    if use_futu:
        futu_ctx, _ = _init_futu_context(futu_host, futu_port)
    
    fetchers = KlineFetcherFactory.create_fetcher_chain(quote_ctx=futu_ctx)

    log_records = []
    today = date.today()
    target_end = _previous_business_day(today)
    # 仅保留近五年数据（含 target_end 当日）
    cutoff_date = target_end - timedelta(days=365 * 5)
    end_date = target_end.strftime("%Y-%m-%d")

    for market in markets:
        market = normalize_market(market)
        if db.stock_count(market) == 0:
            stocks_source = None
            stocks = fetch_stock_list_akshare(market)
            if stocks:
                stocks_source = "AKShare"
            elif futu_ctx:
                stocks = fetch_stock_list_futu(futu_ctx, market)
                if stocks:
                    stocks_source = "Futu"
            if stocks:
                db.upsert_stocks(market, stocks, source=stocks_source)
                print(f"✓ {market_label(market)}股票列表已入库 ({len(stocks)} 只)")
        stocks = db.get_stocks(market)
        if not stocks:
            print(f"✗ 未获取到{market_label(market)}股票列表，跳过")
            continue
        if limit:
            stocks = stocks[:limit]

        for stock in stocks:
            code = stock["code"]
            name = stock.get("name")
            last_date = db.last_kline_date(market, code)
            if not _should_fetch(last_date, target_end):
                log_records.append(
                    _build_log_record(
                        market, code, name, "skipped", "目标交易日已入库", 0, last_date
                    )
                )
                # 即使跳过数据同步，也需要执行 EMA 突破检查
                try:
                    check_and_save_ema_breakout(db, market, code, today, verbose=True)
                except Exception as e:
                    print(f"  ⚠️ EMA突破检查异常: {code} - {e}")
                continue

            start_date = cutoff_date.strftime("%Y-%m-%d")
            if last_date:
                try:
                    next_day = pd.to_datetime(last_date).date() + timedelta(days=1)
                    if next_day > cutoff_date:
                        start_date = next_day.strftime("%Y-%m-%d")
                except Exception:
                    start_date = cutoff_date.strftime("%Y-%m-%d")

            # 1) 优先复用本地 cache/kline_data
            df_cache = _load_kline_cache(code, market)
            df = None
            source = None

            start_dt = pd.to_datetime(start_date).date()
            end_dt = pd.to_datetime(end_date).date()

            cache_min = None
            cache_max = None
            if df_cache is not None and not df_cache.empty and "date" in df_cache.columns:
                try:
                    cache_min = pd.to_datetime(df_cache["date"]).dt.date.min()
                    cache_max = pd.to_datetime(df_cache["date"]).dt.date.max()
                except Exception:
                    cache_min, cache_max = None, None

            # 2) 用 cache 覆盖可用区间
            cache_used_rows = 0
            if cache_min and cache_max:
                # 过滤近五年 + 目标区间
                try:
                    df_cache_norm = df_cache.copy()
                    df_cache_norm["date"] = pd.to_datetime(df_cache_norm["date"]).dt.strftime("%Y-%m-%d")
                    df_cache_dates = pd.to_datetime(df_cache_norm["date"]).dt.date
                    df_cache_norm = df_cache_norm[(df_cache_dates >= cutoff_date) & (df_cache_dates >= start_dt) & (df_cache_dates <= end_dt)]
                    if not df_cache_norm.empty:
                        df_cache_norm = _normalize_kline_df(df_cache_norm)
                        db.upsert_klines(market, code, df_cache_norm, source="Cache", adj_type="qfq")
                        cache_used_rows = len(df_cache_norm)
                except Exception:
                    cache_used_rows = 0

            # 3) 若 cache 无法覆盖完整缺口，再按缺口区间调用外部数据源补齐
            # 缺口A：cache_min > start_dt，需要补 [start_dt, cache_min-1]
            # 缺口B：cache_max < end_dt，需要补 [cache_max+1, end_dt]
            fetch_ranges: list[tuple[date, date]] = []
            if cache_min is None or cache_max is None:
                fetch_ranges = [(start_dt, end_dt)]
            else:
                if start_dt < cache_min:
                    fetch_ranges.append((start_dt, min(end_dt, cache_min - timedelta(days=1))))
                if cache_max < end_dt:
                    fetch_ranges.append((max(start_dt, cache_max + timedelta(days=1)), end_dt))

            fetched_rows = 0
            part_source = None
            for r_start, r_end in fetch_ranges:
                if r_start > r_end:
                    continue
                
                # 按优先级尝试各个数据源
                df_part = None
                for fetcher in fetchers:
                    try:
                        df_part = fetcher.fetch(
                            code,
                            market=market,
                            start_date=r_start.strftime("%Y-%m-%d"),
                            end_date=r_end.strftime("%Y-%m-%d"),
                            max_count=5000,
                        )
                        if df_part is not None and not df_part.empty:
                            part_source = fetcher.get_name()
                            break
                    except Exception:
                        continue
                
                if df_part is None or df_part.empty:
                    continue

                df_part = _normalize_kline_df(df_part)
                if df_part is None or df_part.empty:
                    continue
                try:
                    df_dates = pd.to_datetime(df_part["date"]).dt.date
                    df_part = df_part[df_dates >= cutoff_date]
                except Exception:
                    pass
                db.upsert_klines(market, code, df_part, source=part_source or "Unknown", adj_type="qfq")
                fetched_rows += len(df_part)

            # 4) 日志用：若 cache/外部都没拿到任何数据，则认为失败
            if cache_used_rows == 0 and fetched_rows == 0:
                df = None
                source = "Cache/AKShare/Futu"
            else:
                df = pd.DataFrame()
                source = f"cache={cache_used_rows}, fetched={fetched_rows}"

            if df is None or df.empty:
                log_records.append(
                    _build_log_record(
                        market, code, name, "failed", f"{source} 无数据", 0, last_date
                    )
                )
                # 即使数据获取失败，也尝试用现有数据库数据进行 EMA 突破检查
                try:
                    check_and_save_ema_breakout(db, market, code, today, verbose=True)
                except Exception as e:
                    print(f"  ⚠️ EMA突破检查异常: {code} - {e}")
                continue

            pruned = db.prune_old_klines(market, code, cutoff_date, adj_type="qfq")
            log_records.append(
                _build_log_record(
                    market,
                    code,
                    name,
                    "updated",
                    f"{source} 写入成功; prune<{cutoff_date}={pruned}",
                    0,
                    last_date,
                )
            )
            
            # 数据同步成功后，执行 EMA 突破策略检查并写入数据库
            try:
                check_and_save_ema_breakout(db, market, code, today, verbose=True)
            except Exception as e:
                print(f"  ⚠️ EMA突破检查异常: {code} - {e}")

    if futu_ctx:
        futu_ctx.close()
    db.close()

    if log_path:
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        with open(log_path, "w", encoding="utf-8") as f:
            for record in log_records:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        print(f"✓ 日志已保存到: {log_path}")


def run_loop(
    interval_hours: int,
    **kwargs,
):
    while True:
        run_once(**kwargs)
        time.sleep(interval_hours * 3600)


def main():
    parser = argparse.ArgumentParser(description="每日股票/行情入库任务")
    parser.add_argument("--mysql-host", default=os.getenv("MYSQL_HOST", "127.0.0.1"), help="MySQL Host")
    parser.add_argument("--mysql-port", type=int, default=int(os.getenv("MYSQL_PORT", "3306")), help="MySQL Port")
    parser.add_argument("--mysql-user", default=os.getenv("MYSQL_USER", "root"), help="MySQL User")
    parser.add_argument("--mysql-password", default=os.getenv("MYSQL_PASSWORD", "123456"), help="MySQL Password")
    parser.add_argument("--mysql-database", default=os.getenv("MYSQL_DATABASE", "market_data"), help="MySQL Database")
    parser.add_argument("--mysql-charset", default=os.getenv("MYSQL_CHARSET", "utf8mb4"), help="MySQL Charset")
    parser.add_argument("--markets", default="HK,US", help="市场列表: HK,US")
    parser.add_argument("--use-futu", action="store_true", default=False, help="允许使用 Futu OpenD 作为备用数据源")
    parser.add_argument("--futu-host", default="127.0.0.1", help="Futu OpenD Host")
    parser.add_argument("--futu-port", type=int, default=11111, help="Futu OpenD Port")
    parser.add_argument("--log", default="logs/daily_sync.jsonl", help="日志输出文件(JSONL)")
    parser.add_argument("--limit", type=int, default=None, help="限制股票数量")
    parser.add_argument("--loop", action="store_true", help="循环执行(默认单次)")
    parser.add_argument("--interval-hours", type=int, default=24, help="循环间隔小时")
    parser.add_argument("--stock-code", default=os.getenv("STOCK_CODE", ""), help="单个股票代码，例如: HK.00700 或 US.AAPL。如果提供，将只同步该股票")
    args = parser.parse_args()

    mysql = MySqlConfig(
        host=args.mysql_host,
        port=args.mysql_port,
        user=args.mysql_user,
        password=args.mysql_password,
        database=args.mysql_database,
        charset=args.mysql_charset,
    )
    
    # 如果提供了单个股票代码，执行单股票同步模式
    if args.stock_code:
        sync_single_stock(
            mysql=mysql,
            stock_code=args.stock_code,
            use_futu=args.use_futu,
            futu_host=args.futu_host,
            futu_port=args.futu_port,
            log_path=args.log,
        )
        return
    
    # 否则执行原有的批量同步逻辑
    markets = parse_markets(args.markets)
    if args.loop:
        run_loop(
            interval_hours=args.interval_hours,
            mysql=mysql,
            markets=markets,
            use_futu=args.use_futu,
            futu_host=args.futu_host,
            futu_port=args.futu_port,
            log_path=args.log,
            limit=args.limit,
        )
    else:
        run_once(
            mysql=mysql,
            markets=markets,
            use_futu=args.use_futu,
            futu_host=args.futu_host,
            futu_port=args.futu_port,
            log_path=args.log,
            limit=args.limit,
        )


if __name__ == "__main__":
    main()
