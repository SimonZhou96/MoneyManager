#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Energy Phase Classifier 参数回测脚本。

对指定股票的历史 K 线逐日滑动调用 analyze_energy_phases()，
统计不同参数集下的信号质量指标：
  - 信号频率（信号天数 / 总天数）
  - RELEASE/TRENDING 触发后 N 日胜率（收益 > 0 的比例）
  - 平均 N 日收益率
  - 最大回撤（信号后 N 日内）
  - 盈亏比（平均盈利 / 平均亏损）
  - 状态分布（六态占比）

用法：
  python3 -m tests.backtest_energy_phase --code HK.800000 --market HK
  python3 -m tests.backtest_energy_phase --code HK.00700 --market HK --compare
"""

import argparse
import sys
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

# Ensure stock_screener is on path
sys.path.insert(0, ".")

from strategy import analyze_energy_phases, get_market_energy_params, MARKET_ENERGY_PARAMS


@dataclass
class SignalRecord:
    """单次信号记录。"""
    signal_date: date
    state: str
    close: float
    ke_signed: float
    pe_norm: float
    delta_e: float
    ke_consistency: float
    # Forward returns
    ret_1d: float = 0.0
    ret_3d: float = 0.0
    ret_5d: float = 0.0
    ret_10d: float = 0.0
    ret_20d: float = 0.0
    # Max adverse excursion
    mae_5d: float = 0.0
    mae_10d: float = 0.0
    mae_20d: float = 0.0


@dataclass
class BacktestResult:
    """回测结果汇总。"""
    label: str
    params: Dict[str, Any] = field(default_factory=dict)
    total_days: int = 0
    signal_days: int = 0
    release_signals: int = 0
    trending_signals: int = 0
    state_counts: Dict[str, int] = field(default_factory=dict)
    signals: List[SignalRecord] = field(default_factory=list)
    # Aggregate metrics
    hit_rate_5d: float = 0.0  # 5日胜率
    hit_rate_10d: float = 0.0  # 10日胜率
    hit_rate_20d: float = 0.0  # 20日胜率
    avg_ret_5d: float = 0.0
    avg_ret_10d: float = 0.0
    avg_ret_20d: float = 0.0
    avg_mae_10d: float = 0.0
    profit_factor_10d: float = 0.0  # 盈亏比（总盈利/总亏损）


def fetch_kline(code: str, market: str, lookback_days: int = 500) -> pd.DataFrame:
    """获取 K 线数据。优先使用 DB 缓存，否则调用 KlineFetcher。"""
    try:
        from db import get_db
        db = get_db()
        rows = db.query_kline_cache(code, market, limit=lookback_days)
        if rows:
            df = pd.DataFrame(rows, columns=[
                "date", "open", "high", "low", "close", "volume",
                "turnover", "pe", "pb", "change_pct",
            ])
            df["date"] = pd.to_datetime(df["date"])
            df = df.sort_values("date").reset_index(drop=True)
            if len(df) >= 60:
                print(f"  DB 缓存: {len(df)} 根 K 线, {df['date'].iloc[0].date()} → {df['date'].iloc[-1].date()}")
                return df
    except Exception as e:
        print(f"  DB 查询失败: {e}")

    # Fallback to yfinance
    try:
        from kline_fetcher import KlineFetcherFactory
        fetcher = KlineFetcherFactory.create_fetcher_chain(market=market, skip_db_cache=True)
        end = date.today()
        start = end - timedelta(days=lookback_days)
        df = fetcher.fetch(code, start, end)
        if df is not None and not df.empty:
            df["date"] = pd.to_datetime(df["date"])
            df = df.sort_values("date").reset_index(drop=True)
            print(f"  yfinance: {len(df)} 根 K 线, {df['date'].iloc[0].date()} → {df['date'].iloc[-1].date()}")
            return df
    except Exception as e:
        print(f"  yfinance 失败: {e}")

    print("  ❌ 无法获取 K 线数据")
    return pd.DataFrame()


def backtest(
    df: pd.DataFrame,
    params: Dict[str, Any],
    label: str,
    warmup: int = 40,
    forward_windows: Tuple[int, ...] = (1, 3, 5, 10, 20),
) -> BacktestResult:
    """在历史数据上滑动回测。

    Args:
        df: K 线 DataFrame，必须有 date/close 列。
        params: 传入 analyze_energy_phases 的参数。
        label: 参数集标签。
        warmup: 预热天数（前 warmup 天不评估信号，确保指标稳定）。
        forward_windows: 前向收益窗口。

    Returns:
        BacktestResult with detailed signal records and aggregate metrics.
    """
    result = BacktestResult(label=label, params=params)
    result.total_days = len(df) - warmup

    closes = df["close"].values
    dates = df["date"].values

    for i in range(warmup, len(df)):
        check_date = pd.Timestamp(dates[i]).date()
        sub_df = df.iloc[: i + 1].copy()

        analysis = analyze_energy_phases(
            sub_df,
            check_date=check_date,
            **params,
        )

        state = analysis.state
        result.state_counts[state] = result.state_counts.get(state, 0) + 1

        if not analysis.satisfied:
            continue

        result.signal_days += 1
        if state == "RELEASE":
            result.release_signals += 1
        elif state == "TRENDING":
            result.trending_signals += 1

        signal = SignalRecord(
            signal_date=check_date,
            state=state,
            close=float(closes[i]),
            ke_signed=analysis.details.get("ke_signed", 0),
            pe_norm=analysis.details.get("pe_norm", 0),
            delta_e=analysis.details.get("delta_e_5", 0),
            ke_consistency=analysis.details.get("ke_consistency", 0),
        )

        # 计算前向收益
        current_close = closes[i]
        for w in forward_windows:
            fwd_idx = min(i + w, len(df) - 1)
            fwd_close = closes[fwd_idx]
            ret = (fwd_close - current_close) / current_close
            pct = round(ret * 100, 2)
            setattr(signal, f"ret_{w}d", pct)

            # 计算最大不利偏移 (MAE)
            if w >= 5:
                segment = closes[i : fwd_idx + 1]
                min_close = segment.min()
                mae = (min_close - current_close) / current_close
                setattr(signal, f"mae_{w}d", round(float(mae) * 100, 2))

        result.signals.append(signal)

    # 计算汇总指标
    if result.signals:
        rets_5 = [s.ret_5d for s in result.signals]
        rets_10 = [s.ret_10d for s in result.signals]
        rets_20 = [s.ret_20d for s in result.signals]

        result.hit_rate_5d = sum(1 for r in rets_5 if r > 0) / len(rets_5)
        result.hit_rate_10d = sum(1 for r in rets_10 if r > 0) / len(rets_10)
        result.hit_rate_20d = sum(1 for r in rets_20 if r > 0) / len(rets_20)
        result.avg_ret_5d = np.mean(rets_5)
        result.avg_ret_10d = np.mean(rets_10)
        result.avg_ret_20d = np.mean(rets_20)
        result.avg_mae_10d = np.mean([s.mae_10d for s in result.signals])

        wins = [r for r in rets_10 if r > 0]
        losses = [abs(r) for r in rets_10 if r < 0]
        if losses:
            result.profit_factor_10d = sum(wins) / sum(losses) if sum(losses) > 0 else float("inf")

    return result


def param_sets() -> List[Tuple[str, Dict[str, Any]]]:
    """定义要对比的参数集。"""
    sets = []

    # 1. 原始默认参数
    sets.append(("默认参数（原始）", {
        "ma_period": 20,
        "pe_threshold": 100.0,
        "ke_threshold": 4.0,
        "epr_release_threshold": 0.1,
        "ke_decay_exhaustion": 0.5,
        "ke_decay_peak": 0.8,
        "consistency_window": 10,
        "delta_window": 5,
        "lookback_pe_days": 3,
        "min_rows": 30,
        "crash_neg_streak": 5,
        "crash_ke_path": -20.0,
        "peak_pe_threshold": 80.0,
        "peak_ke_silence": 1.0,
        "peak_delta_e": -10.0,
        "release_delta_e": 10.0,
        "trending_consistency": 0.7,
        "compress_consistency": 0.3,
        "compress_ke_path": 0.0,
    }))

    # 2. HK 市场预设（降低阈值，适配低波动）
    hk_params = {
        "ma_period": 20,
        "pe_threshold": 100.0,
        "ke_threshold": 2.5,           # 4.0 → 2.5
        "epr_release_threshold": 0.1,
        "ke_decay_exhaustion": 0.5,
        "ke_decay_peak": 0.8,
        "consistency_window": 10,
        "delta_window": 5,
        "lookback_pe_days": 3,
        "min_rows": 30,
        "crash_neg_streak": 4,         # 5 → 4
        "crash_ke_path": -12.0,        # -20 → -12
        "peak_pe_threshold": 80.0,
        "peak_ke_silence": 0.6,        # 1.0 → 0.6
        "peak_delta_e": -6.0,          # -10 → -6
        "release_delta_e": 5.0,        # 10 → 5 (核心优化)
        "trending_consistency": 0.6,   # 0.7 → 0.6 (核心优化)
        "compress_consistency": 0.35,  # 0.3 → 0.35
        "compress_ke_path": 0.0,
    }
    sets.append(("HK 优化参数", hk_params))

    # 3. 进一步调优：更激进的 RELEASE 检测
    aggressive = dict(hk_params)
    aggressive["release_delta_e"] = 3.0     # 5.0 → 3.0
    aggressive["trending_consistency"] = 0.55  # 0.6 → 0.55
    aggressive["epr_release_threshold"] = 0.05  # 0.1 → 0.05
    sets.append(("HK 激进参数", aggressive))

    # 4. 保守参数：过滤噪音，只保留强信号
    conservative = dict(hk_params)
    conservative["release_delta_e"] = 8.0    # 5.0 → 8.0
    conservative["trending_consistency"] = 0.7  # 0.6 → 0.7
    conservative["ke_threshold"] = 3.0       # 2.5 → 3.0
    sets.append(("HK 保守参数", conservative))

    # 5. 短周期参数（更快响应）
    short_ma = dict(hk_params)
    short_ma["ma_period"] = 10            # 20 → 10
    short_ma["consistency_window"] = 5    # 10 → 5
    short_ma["delta_window"] = 3          # 5 → 3
    short_ma["lookback_pe_days"] = 2      # 3 → 2
    short_ma["ke_peak_window"] = 20       # 在函数内是 ma_period*2
    sets.append(("HK 短周期参数", short_ma))

    return sets


def print_comparison(results: List[BacktestResult]):
    """打印对比表格。"""
    print()
    print("=" * 120)
    print("📊 能量相位分类器 — 参数回测对比")
    print("=" * 120)

    # Header
    header = (
        f"{'参数集':<20s} "
        f"{'总天数':>6s} "
        f"{'信号数':>6s} "
        f"{'频率':>6s} "
        f"{'5d胜率':>7s} "
        f"{'10d胜率':>7s} "
        f"{'20d胜率':>7s} "
        f"{'均5d%':>7s} "
        f"{'均10d%':>7s} "
        f"{'均20d%':>7s} "
        f"{'MAE10%':>7s} "
        f"{'盈亏比':>7s} "
        f"{'REL':>5s} "
        f"{'TRD':>5s} "
        f"{'CMP':>5s} "
        f"{'UNK':>5s}"
    )
    print(header)
    print("-" * 120)

    for r in results:
        freq = f"{r.signal_days / r.total_days * 100:.1f}%" if r.total_days else "0%"
        rel = r.state_counts.get("RELEASE", 0)
        trd = r.state_counts.get("TRENDING", 0)
        cmp_ = r.state_counts.get("COMPRESS", 0)
        unk = r.state_counts.get("UNKNOWN", 0)

        row = (
            f"{r.label:<20s} "
            f"{r.total_days:>6d} "
            f"{r.signal_days:>6d} "
            f"{freq:>6s} "
            f"{r.hit_rate_5d:>6.1%} "
            f"{r.hit_rate_10d:>6.1%} "
            f"{r.hit_rate_20d:>6.1%} "
            f"{r.avg_ret_5d:>+6.2f} "
            f"{r.avg_ret_10d:>+6.2f} "
            f"{r.avg_ret_20d:>+6.2f} "
            f"{r.avg_mae_10d:>+6.2f} "
            f"{r.profit_factor_10d:>6.2f} "
            f"{rel:>5d} "
            f"{trd:>5d} "
            f"{cmp_:>5d} "
            f"{unk:>5d}"
        )
        print(row)

    print("-" * 120)
    print("REL=RELEASE TRD=TRENDING CMP=COMPRESS UNK=UNKNOWN")
    print("MAE = 信号后最大不利偏移（负值越大越差）")
    print("盈亏比 = 总盈利/总亏损（10日窗口），>1.0 表示盈利大于亏损")
    print()

    # 最佳推荐
    best = max(results, key=lambda r: r.hit_rate_10d * 0.4 + r.avg_ret_10d * 0.3 + r.profit_factor_10d * 0.3)
    print(f"🏆 综合最佳：{best.label}")
    print(f"   10日胜率={best.hit_rate_10d:.1%}, 均10日收益={best.avg_ret_10d:+.2f}%, 盈亏比={best.profit_factor_10d:.2f}")


def print_signal_details(result: BacktestResult, top_n: int = 5):
    """打印最近 N 个信号详情。"""
    print(f"\n── {result.label} — 最近 {min(top_n, len(result.signals))} 个信号 ──")
    print(f"{'日期':<12s} {'状态':<12s} {'收盘':>8s} {'1d%':>7s} {'5d%':>7s} {'10d%':>7s} {'20d%':>7s} {'KE':>8s} {'PE':>8s}")
    for s in result.signals[-top_n:]:
        print(
            f"{str(s.signal_date):<12s} {s.state:<12s} {s.close:>8.2f} "
            f"{s.ret_1d:>+6.2f} {s.ret_5d:>+6.2f} {s.ret_10d:>+6.2f} {s.ret_20d:>+6.2f} "
            f"{s.ke_signed:>8.2f} {s.pe_norm:>8.2f}"
        )


def main():
    parser = argparse.ArgumentParser(description="Energy Phase 参数回测")
    parser.add_argument("--code", default="HK.800000", help="股票代码（如 HK.800000）")
    parser.add_argument("--market", default="HK", help="市场（HK/US/A）")
    parser.add_argument("--lookback", type=int, default=500, help="回看天数")
    parser.add_argument("--compare", action="store_true", help="对比多组参数")
    parser.add_argument("--details", action="store_true", help="显示信号详情")
    args = parser.parse_args()

    print(f"🔍 获取 {args.code} K 线数据（{args.lookback} 天）...")
    df = fetch_kline(args.code, args.market, args.lookback)

    if df.empty:
        print("❌ 无法获取数据，退出。")
        sys.exit(1)

    if len(df) < 60:
        print(f"⚠️  数据量不足（{len(df)} 根），需要至少 60 根 K 线。")
        sys.exit(1)

    if args.compare:
        sets = param_sets()
        results = []
        for label, params in sets:
            print(f"⏳ 回测: {label} ...")
            result = backtest(df, params, label)
            results.append(result)
            print(f"   信号数={result.signal_days}/{result.total_days} "
                  f"({result.signal_days/result.total_days*100:.1f}%), "
                  f"10日胜率={result.hit_rate_10d:.1%}, "
                  f"均10日={result.avg_ret_10d:+.2f}%")

        print_comparison(results)

        if args.details:
            for r in results:
                print_signal_details(r)
    else:
        # 单参数集：使用市场预设
        market_params = get_market_energy_params(args.market)
        params = {
            "ma_period": 20,
            "pe_threshold": 100.0,
            "ke_threshold": 4.0,
            "epr_release_threshold": 0.1,
            "ke_decay_exhaustion": 0.5,
            "ke_decay_peak": 0.8,
            "consistency_window": 10,
            "delta_window": 5,
            "lookback_pe_days": 3,
            "min_rows": 30,
            "crash_neg_streak": 5,
            "crash_ke_path": -20.0,
            "peak_pe_threshold": 80.0,
            "peak_ke_silence": 1.0,
            "peak_delta_e": -10.0,
            "release_delta_e": 10.0,
            "trending_consistency": 0.7,
            "compress_consistency": 0.3,
            "compress_ke_path": 0.0,
        }
        params.update(market_params)

        print(f"⏳ 回测: {args.market} 市场预设参数 ...")
        result = backtest(df, params, f"{args.market} 市场预设")
        results = [result]
        print_comparison(results)
        if args.details:
            print_signal_details(result, top_n=10)


if __name__ == "__main__":
    main()
