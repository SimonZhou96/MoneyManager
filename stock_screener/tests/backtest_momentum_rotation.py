#!/usr/bin/env python3
"""
动量轮动策略回测 — 真实历史K线数据
=====================================

策略：每月买过去6个月涨最好的5只股票，等权轮动，大盘EMA200下方空仓。

用法：
  python3 tests/backtest_momentum_rotation.py              # HK+US
  python3 tests/backtest_momentum_rotation.py --market US  # 仅美股
  python3 tests/backtest_momentum_rotation.py --market HK --no-filter --top-n 3 --momentum-months 3
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Dict, List

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from db import MarketDatabase, MySqlConfig
from quant_lab.strategies.momentum_rotation import (
    MomentumRotationConfig, MomentumRotationRunner,
)

# ══════════════════════════════════════════════════════════════════
# 股票池
# ══════════════════════════════════════════════════════════════════

MARKET_UNIVERSE = {
    "HK": [
        "HK.00700", "HK.09988", "HK.00388", "HK.01299", "HK.00005",
        "HK.02318", "HK.00941", "HK.01398", "HK.03988", "HK.00883",
        "HK.02020", "HK.01211", "HK.02269", "HK.09633", "HK.02382",
        "HK.01093", "HK.00175", "HK.02628", "HK.06618",
    ],
    "US": [
        "US.NVDA", "US.AMZN", "US.META", "US.TSLA", "US.AVGO",
        "US.AMD", "US.NFLX", "US.ADBE", "US.CRM", "US.ORCL",
        "US.UBER", "US.DIS", "US.INTC", "US.CRWD",
    ],
}


# ══════════════════════════════════════════════════════════════════
# 数据加载
# ══════════════════════════════════════════════════════════════════

def load_data(db: MarketDatabase, market: str, symbols: List[str]) -> Dict[str, pd.DataFrame]:
    """从 DB 加载日线数据。"""
    data = {}
    for code in symbols:
        df = db.get_kline_cache(market=market, code=code, timeframe="1d", max_count=2000)
        if df is None or df.empty:
            bare = code.replace(f"{market}.", "")
            df = db.get_kline_cache(market=market, code=bare, timeframe="1d", max_count=2000)
        if df is not None and not df.empty:
            df = df.copy()
            df["date"] = pd.to_datetime(df["date"])
            df = df.sort_values("date").reset_index(drop=True)
            df["close"] = df["close"].astype(float)
            data[code] = df
    return data


def compute_market_index(data: Dict[str, pd.DataFrame]) -> pd.DataFrame:
    """等权平均构建市场代理指数。"""
    all_dates = sorted(set().union(*[set(df["date"].tolist()) for df in data.values()]))
    rows = []
    for d in all_dates:
        closes = [float(df[df["date"] == d]["close"].iloc[0])
                  for df in data.values() if not df[df["date"] == d].empty]
        if closes:
            rows.append({"date": d, "close": np.mean(closes)})
    return pd.DataFrame(rows).sort_values("date").reset_index(drop=True)


# ══════════════════════════════════════════════════════════════════
# 报告
# ══════════════════════════════════════════════════════════════════

def print_report(market: str, result, benchmark_cagr: float, benchmark_dd: float):
    m = result.metrics
    print(f"\n{'='*70}")
    print(f"  📊 动量轮动策略回测报告 — {market}")
    print(f"{'='*70}")
    print(f"  回测区间: {m['start_date']} → {m['end_date']} ({m['years']}年)")
    print()

    print(f"  ┌──────────────────────────────────────────────────────────┐")
    print(f"  │  收益对比                                               │")
    print(f"  ├──────────────────────────────────────────────────────────┤")
    print(f"  │                策略          等权基准(Buy&Hold)          │")
    print(f"  │  总收益率:  {m['total_return']*100:>+10.2f}%    —                            │")
    print(f"  │  年化收益:  {m['cagr']*100:>+10.2f}%    {benchmark_cagr*100:>+10.2f}%                       │")
    print(f"  │  最大回撤:  {m['max_drawdown']*100:>10.2f}%    {benchmark_dd*100:>+10.2f}%                       │")
    print(f"  └──────────────────────────────────────────────────────────┘")
    print(f"  📈 超额收益: {(m['cagr']-benchmark_cagr)*100:+.2f}%/年")

    print(f"\n  ┌──────────────────────────────────────────────────────────┐")
    print(f"  │  风险指标                                               │")
    print(f"  ├──────────────────────────────────────────────────────────┤")
    print(f"  │  年化波动:  {m['annual_volatility']*100:>10.2f}%                                  │")
    print(f"  │  Sharpe:    {m['sharpe']:>10.2f}                                    │")
    print(f"  │  Sortino:   {m['sortino']:>10.2f}                                    │")
    print(f"  │  Calmar:    {m['calmar']:>10.2f}                                    │")
    print(f"  └──────────────────────────────────────────────────────────┘")

    print(f"\n  ┌──────────────────────────────────────────────────────────┐")
    print(f"  │  交易统计                                               │")
    print(f"  ├──────────────────────────────────────────────────────────┤")
    print(f"  │  交易次数:  {m['trade_count']:>8d}                                      │")
    print(f"  │  胜率:      {m['win_rate']*100:>10.1f}%                                    │")
    print(f"  │  平均盈利:  {m['avg_win']*100:>+10.2f}%                                   │")
    print(f"  │  平均亏损:  {m['avg_loss']*100:>+10.2f}%                                   │")
    print(f"  └──────────────────────────────────────────────────────────┘")


# ══════════════════════════════════════════════════════════════════
# 主入口
# ══════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="动量轮动策略回测")
    parser.add_argument("--market", choices=["HK", "US", "ALL"], default="ALL")
    parser.add_argument("--no-filter", action="store_true", help="关闭大盘EMA200过滤")
    parser.add_argument("--top-n", type=int, default=5, help="持仓数量（默认5）")
    parser.add_argument("--momentum-months", type=int, default=6, help="动量回看月数（默认6）")
    parser.add_argument("--skip-months", type=int, default=1, help="跳过最近月数（默认1）")
    parser.add_argument("--frequency", choices=["daily", "weekly", "monthly"], default="monthly", help="调仓频率（默认monthly）")
    parser.add_argument("--consecutive-days", type=int, default=1, help="连续确认天数（默认1，防whipsaw）")
    args = parser.parse_args()

    config_db = MySqlConfig(
        host=os.getenv("MYSQL_HOST", "127.0.0.1"),
        port=int(os.getenv("MYSQL_PORT", "3306")),
        user=os.getenv("MYSQL_USER", "root"),
        password=os.getenv("MYSQL_PASSWORD", "123456"),
        database=os.getenv("MYSQL_DATABASE", "market_data"),
    )
    db = MarketDatabase(config_db)

    config = MomentumRotationConfig(
        momentum_months=args.momentum_months,
        skip_months=args.skip_months,
        top_n=args.top_n,
        use_market_filter=not args.no_filter,
        rebalance_frequency=args.frequency,
        consecutive_days=args.consecutive_days,
    )

    markets = ["HK", "US"] if args.market == "ALL" else [args.market]

    for market in markets:
        symbols = MARKET_UNIVERSE.get(market, [])
        print(f"\n📡 加载 {market} 数据...")
        data = load_data(db, market, symbols)
        print(f"   有效股票: {len(data)}/{len(symbols)}")
        if len(data) < 3:
            print(f"   ⚠️  股票不足3只，跳过")
            continue

        market_index = compute_market_index(data)
        print(f"   大盘指数: {len(market_index)} 个交易日")

        # ── 运行回测 ──
        runner = MomentumRotationRunner(config)
        result = runner.run(data, market_index)

        # ── 基准收益（等权指数买入持有） ──
        eq = result.equity_curve
        if eq:
            start_d = pd.Timestamp(eq[0]["date"])
            end_d = pd.Timestamp(eq[-1]["date"])
            days = (end_d - start_d).days
            years = max(days / 365.25, 0.01)

            idx_df = compute_market_index(data)
            s = idx_df[idx_df["date"] <= start_d]
            e = idx_df[idx_df["date"] <= end_d]
            if not s.empty and not e.empty:
                bench_ret = float(e["close"].iloc[-1]) / float(s["close"].iloc[0]) - 1
                bench_cagr = ((1 + bench_ret) ** (1 / years) - 1) if years > 0 else 0
            else:
                bench_cagr = 0.0

            # 回撤
            bench_dd = 0.0
            if not idx_df.empty:
                peak_val = 0.0
                for _, row in idx_df.iterrows():
                    rd = pd.Timestamp(row["date"])
                    if rd < start_d:
                        continue
                    if rd > end_d:
                        break
                    peak_val = max(peak_val, float(row["close"]))
                    dd_val = (float(row["close"]) - peak_val) / peak_val
                    bench_dd = min(bench_dd, dd_val)
        else:
            bench_cagr, bench_dd = 0.0, 0.0

        print_report(market, result, bench_cagr, bench_dd)

    db.close()


if __name__ == "__main__":
    main()
