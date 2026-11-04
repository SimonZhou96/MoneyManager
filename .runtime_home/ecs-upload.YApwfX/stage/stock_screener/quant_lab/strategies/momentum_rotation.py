"""
动量轮动策略 (Momentum Rotation Strategy)
==========================================

跨截面动量策略：每月买入过去N个月涨幅最大的前K只股票，等权持有。

策略逻辑：
  - 每月最后一个交易日调仓
  - 对每只股票计算过去 momentum_months 个月的收益率（跳过最近 skip_months 个月）
  - 按收益率从高到低排序，取前 top_n 只
  - 等权买入，卖出跌出排名或不在 top_n 的持仓
  - 可选：大盘 EMA200 过滤（大盘在线下时空仓）

学术依据：
  - Jegadeesh & Titman (1993): 动量效应在 3-12 个月窗口持续有效
  - 美股科技股 2021-2026 回测：年化 33.7%，超额基准 +20.8%/年

适用市场：美股（港股 2021-2026 动量失效，需谨慎）
适用周期：日线
最少K线数：momentum_months + skip_months + 1 个月的交易日（约 170 根）

Author: quant_lab
Date: 2026-06-22
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from quant_lab.models import Signal


# ═══════════════════════════════════════════════════════════════════════════
# 策略参数
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class MomentumRotationConfig:
    """动量轮动策略配置"""
    momentum_months: int = 6           # 动量回看月数
    skip_months: int = 1              # 跳过最近月数（避免短期反转）
    top_n: int = 5                    # 持仓数量
    ema_period: int = 200             # 大盘 EMA 周期
    use_market_filter: bool = True    # 大盘 EMA200 过滤
    min_data_months: int = 12         # 最少数据月数
    rebalance_frequency: str = "monthly"  # 调仓频率: daily | weekly | monthly
    consecutive_days: int = 1         # 连续确认天数（防 whipsaw），1=立即触发

    @staticmethod
    def param_definitions() -> dict:
        return {
            "momentum_months": {"type": "int", "default": 6, "min": 1, "max": 12, "label": "动量回看月数"},
            "skip_months": {"type": "int", "default": 1, "min": 0, "max": 3, "label": "跳过最近月数"},
            "top_n": {"type": "int", "default": 5, "min": 2, "max": 10, "label": "持仓数量"},
            "ema_period": {"type": "int", "default": 200, "min": 50, "max": 250, "label": "大盘EMA周期"},
            "use_market_filter": {"type": "bool", "default": True, "label": "大盘EMA200过滤"},
            "rebalance_frequency": {"type": "select", "default": "monthly", "options": ["daily", "weekly", "monthly"], "label": "调仓频率"},
            "consecutive_days": {"type": "int", "default": 1, "min": 1, "max": 20, "label": "连续确认天数"},
        }

    @staticmethod
    def name() -> str:
        return "动量轮动"

    @staticmethod
    def strategy_type() -> str:
        return "momentum_rotation"


# ═══════════════════════════════════════════════════════════════════════════
# 策略引擎
# ═══════════════════════════════════════════════════════════════════════════

class MomentumRotationStrategy:
    """动量轮动策略引擎。

    与单股票 BaseStrategy 不同，这是一个跨截面策略，
    需要在所有股票的上下文中运行。

    用法:
        strategy = MomentumRotationStrategy(MomentumRotationConfig(top_n=5))
        strategy.fit(universe_data)  # 预计算所有股票的动量排名日期
        signals = strategy.generate_signals(symbol, df)  # 单股票信号
        # 或者
        portfolio_signals = strategy.rank_stocks(data, as_of_date)  # 跨截面排名
    """

    def __init__(self, config: Optional[MomentumRotationConfig] = None):
        self.config = config or MomentumRotationConfig()
        self._monthly_rankings: Dict[date, List[Tuple[str, float]]] = {}
        self._market_index: Optional[pd.DataFrame] = None

    # ── 公开 API ──

    def rank_stocks(
        self,
        data: Dict[str, pd.DataFrame],
        as_of_date,
    ) -> List[Tuple[str, float, float]]:
        """对股票池按动量排名。

        Args:
            data: {symbol: DataFrame with date, close columns}
            as_of_date: 排名日期

        Returns:
            [(symbol, momentum_return, current_price), ...] 按动量从高到低排序
        """
        momentums = []
        for sym, df in data.items():
            mom = self._compute_momentum(df, as_of_date)
            if mom is None:
                continue
            price = self._latest_price(df, as_of_date)
            if price is None:
                continue
            momentums.append((sym, mom, price))

        momentums.sort(key=lambda x: x[1], reverse=True)
        return momentums

    def top_picks(
        self,
        data: Dict[str, pd.DataFrame],
        as_of_date,
    ) -> List[Tuple[str, float, float]]:
        """返回 top N 股票。"""
        ranked = self.rank_stocks(data, as_of_date)
        return ranked[:self.config.top_n]

    def market_ok(self, market_index: pd.DataFrame, as_of_date) -> bool:
        """检查大盘是否在 EMA 上方。"""
        if not self.config.use_market_filter:
            return True

        idx = market_index[market_index["date"] <= pd.Timestamp(as_of_date)]
        if idx.empty:
            return True

        row = idx.iloc[-1]
        close = float(row["close"])
        ema200 = row.get("ema200")
        if ema200 is None or pd.isna(ema200):
            return True

        return close > float(ema200)

    def generate_signals_for_universe(
        self,
        data: Dict[str, pd.DataFrame],
        market_index: Optional[pd.DataFrame] = None,
        as_of_date=None,
    ) -> Dict[str, List[Signal]]:
        """为整个股票池生成所有调仓日的买卖信号。

        Args:
            as_of_date: 可选，参考日期。传入时只使用已完成的月份（排除当前月）。

        Returns:
            {symbol: [Signal, ...]} 每只股票的买卖信号列表
        """
        # ── 获取每月最后一个交易日 ──
        all_dates = sorted(set().union(*[set(df["date"].tolist()) for df in data.values()]))
        rebalance_dates = self._rebalance_dates(all_dates, frequency=self.config.rebalance_frequency, as_of_date=as_of_date)

        # ── 构建大盘 EMA ──
        if market_index is not None and self.config.use_market_filter:
            market_index = market_index.copy()
            market_index["ema200"] = market_index["close"].ewm(
                span=self.config.ema_period, adjust=False
            ).mean()

        # ── 逐日/周/月生成信号 ──
        signals_by_symbol: Dict[str, List[Signal]] = {sym: [] for sym in data}
        out_of_top_streak: Dict[str, int] = {}  # 持仓股 → 连续不在 top_n 的天数
        in_top_streak: Dict[str, int] = {}      # 候选股 → 连续在 top_n 的天数
        conf = self.config.consecutive_days

        for rb_date in rebalance_dates:
            # 大盘过滤
            if market_index is not None:
                if not self.market_ok(market_index, rb_date):
                    # 空仓信号：重置所有 streak
                    out_of_top_streak.clear()
                    in_top_streak.clear()
                    continue

            # 排名
            ranked = self.rank_stocks(data, rb_date)
            if not ranked:
                continue

            top_symbols = {s for s, _, _ in ranked[:self.config.top_n]}

            # ── 买入信号：连续在 top_n 达到 consecutive_days ──
            for sym, mom, price in ranked[:self.config.top_n]:
                # 检查是否已持有（有买入但未卖出）
                prev_buys = [s for s in signals_by_symbol[sym] if s.direction == "buy"]
                prev_sells = [s for s in signals_by_symbol[sym] if s.direction == "sell"]
                is_held = len(prev_buys) > len(prev_sells)

                if is_held:
                    in_top_streak.pop(sym, None)  # 已持有，不再追踪
                    out_of_top_streak[sym] = 0     # 回到 top_n，重置卖出计数
                    continue

                in_top_streak[sym] = in_top_streak.get(sym, 0) + 1
                if in_top_streak.get(sym, 0) >= conf:
                    signals_by_symbol[sym].append(Signal(
                        signal_id=str(uuid.uuid4()),
                        ts=rb_date,
                        market="",
                        symbol=sym,
                        direction="buy",
                        reason=f"连续{conf}天动量{mom*100:.1f}% Top{self.config.top_n}",
                        strength=min(1.0, max(0.3, mom * 2.0 + 0.5)),
                    ))
                    in_top_streak.pop(sym, None)

            # ── 卖出信号：连续不在 top_n 达到 consecutive_days ──
            for sym in data:
                prev_buys = [s for s in signals_by_symbol[sym] if s.direction == "buy"]
                prev_sells = [s for s in signals_by_symbol[sym] if s.direction == "sell"]
                is_held = len(prev_buys) > len(prev_sells)

                if not is_held:
                    out_of_top_streak.pop(sym, None)
                    continue

                if sym not in top_symbols:
                    out_of_top_streak[sym] = out_of_top_streak.get(sym, 0) + 1
                else:
                    out_of_top_streak[sym] = 0

                if out_of_top_streak.get(sym, 0) >= conf:
                    price = self._latest_price(data[sym], rb_date)
                    if price:
                        signals_by_symbol[sym].append(Signal(
                            signal_id=str(uuid.uuid4()),
                            ts=rb_date,
                            market="",
                            symbol=sym,
                            direction="sell",
                            reason=f"连续{conf}天跌出Top{self.config.top_n}",
                            strength=1.0,
                        ))
                    out_of_top_streak.pop(sym, None)

            # ── 清理不再在 top_n 的候选 streak ──
            for sym in list(in_top_streak.keys()):
                if sym not in top_symbols:
                    in_top_streak.pop(sym, None)

        return signals_by_symbol

    # ── 内部方法 ──

    def _compute_momentum(self, df: pd.DataFrame, as_of_date) -> Optional[float]:
        """计算截至 as_of_date 的动量收益率。

        t - momentum_months - skip_months 到 t - skip_months 的收益率
        """
        as_of = pd.Timestamp(as_of_date)
        end_date = as_of - pd.DateOffset(months=self.config.skip_months) + pd.DateOffset(days=1)
        start_date = as_of - pd.DateOffset(months=self.config.momentum_months + self.config.skip_months)

        before_end = df[df["date"] <= end_date]
        before_start = df[df["date"] >= start_date]

        if before_end.empty or before_start.empty:
            return None

        end_close = float(before_end["close"].iloc[-1])
        start_close = float(before_start["close"].iloc[0])

        if start_close <= 0:
            return None

        return (end_close - start_close) / start_close

    @staticmethod
    def _latest_price(df: pd.DataFrame, as_of_date) -> Optional[float]:
        match = df[df["date"] <= pd.Timestamp(as_of_date)]
        if match.empty:
            return None
        return float(match["close"].iloc[-1])

    @staticmethod
    def _rebalance_dates(dates: List, frequency: str = "monthly", as_of_date=None) -> List[date]:
        """获取调仓日列表。

        Args:
            dates: 所有可用的交易日列表
            frequency: "daily" | "weekly" | "monthly"
            as_of_date: 可选，参考日期。传入时排除此日期所在周期及之后的日期。
                       例如：6月22日 + monthly → 只返回5月及之前的月末日。
        """
        df = pd.DataFrame({"date": pd.to_datetime(list(dates))})
        df = df.sort_values("date")

        if as_of_date is not None:
            ref = pd.Timestamp(as_of_date)
            df = df[df["date"] < ref]

        if df.empty:
            return []

        if frequency == "daily":
            snapshots = df["date"].tolist()
        elif frequency == "weekly":
            df["yw"] = df["date"].dt.isocalendar().week.astype(int)
            snapshots = df.groupby("yw")["date"].max().sort_values().tolist()
        elif frequency == "monthly":
            df["ym"] = df["date"].dt.to_period("M")
            snapshots = df.groupby("ym")["date"].max().sort_values().tolist()
        else:
            raise ValueError(f"Unknown rebalance_frequency: {frequency}")

        return [d.date() if hasattr(d, "date") else d for d in snapshots]


# ═══════════════════════════════════════════════════════════════════════════
# 回测运行器
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class PortfolioBacktestResult:
    """组合回测结果"""
    equity_curve: List[dict] = field(default_factory=list)
    trades: List[dict] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)


class MomentumRotationRunner:
    """动量轮动策略回测运行器。

    处理组合层面的逻辑：资金管理、仓位分配、净值计算。
    策略引擎（MomentumRotationStrategy）只负责排名和信号生成。
    """

    def __init__(self, config: Optional[MomentumRotationConfig] = None):
        self.config = config or MomentumRotationConfig()
        self.strategy = MomentumRotationStrategy(self.config)

    def run(
        self,
        data: Dict[str, pd.DataFrame],
        market_index: Optional[pd.DataFrame] = None,
        initial_cash: float = 100_000.0,
        commission_rate: float = 0.001,
        slippage_rate: float = 0.001,
        as_of_date=None,
    ) -> PortfolioBacktestResult:
        """运行组合回测。

        Args:
            as_of_date: 可选，参考日期。传入时只调仓到已完成月份为止。
                       例如：6月22日传入 → 最新调仓日为5月最后一个交易日。
        """
        cfg = self.config

        # ── 调仓日期 ──
        all_dates = sorted(set().union(*[set(df["date"].tolist()) for df in data.values()]))
        rebalance_dates = self.strategy._rebalance_dates(all_dates, frequency=cfg.rebalance_frequency, as_of_date=as_of_date)

        # ── 大盘 EMA ──
        if market_index is not None:
            market_index = market_index.copy()
            market_index["ema200"] = (
                market_index["close"].ewm(span=cfg.ema_period, adjust=False).mean()
            )

        # ── 状态 ──
        cash = initial_cash
        positions: Dict[str, float] = {}  # symbol -> shares
        entry_prices: Dict[str, float] = {}
        equity_curve: List[dict] = []
        trades_log: List[dict] = []

        # 连续确认追踪（防 whipsaw）
        out_of_top_streak: Dict[str, int] = {}  # 持仓股 → 连续不在 top_n 的天数
        in_top_streak: Dict[str, int] = {}      # 候选股 → 连续在 top_n 的天数
        conf = cfg.consecutive_days

        for rb_date in rebalance_dates:
            # ── 市场过滤 ──
            market_ok = self.strategy.market_ok(
                market_index, rb_date
            ) if market_index is not None else True
            use_filter = cfg.use_market_filter

            if use_filter and not market_ok:
                # 清仓（重置所有 streak）
                for sym, shares in list(positions.items()):
                    px = self._get_price(data, sym, rb_date)
                    if px:
                        cash += shares * px
                        pnl = (px - entry_prices.get(sym, px)) / entry_prices.get(sym, px)
                        trades_log.append({
                            "date": rb_date, "symbol": sym, "action": "sell",
                            "reason": "大盘破EMA200清仓", "price": px, "pnl_pct": pnl,
                        })
                positions.clear()
                entry_prices.clear()
                out_of_top_streak.clear()
                in_top_streak.clear()
                equity_curve.append({
                    "date": rb_date, "equity": cash, "cash": cash,
                    "positions": 0, "market_ok": market_ok,
                })
                continue

            # ── 当前价格 ──
            prices = {sym: self._get_price(data, sym, rb_date) for sym in data}
            prices = {k: v for k, v in prices.items() if v is not None}

            # ── 总资产 ──
            pos_value = sum(positions.get(sym, 0) * prices.get(sym, 0) for sym in positions)
            total_equity = cash + pos_value

            # ── 排名 ──
            ranked = self.strategy.rank_stocks(data, rb_date)
            top_picks = ranked[:cfg.top_n]
            top_symbols = {s for s, _, _ in top_picks}

            # ── 卖出判定：连续不在 top_n 达到 consecutive_days ──
            for sym in list(positions.keys()):
                if sym not in top_symbols:
                    out_of_top_streak[sym] = out_of_top_streak.get(sym, 0) + 1
                else:
                    out_of_top_streak[sym] = 0  # 回到 top_n，重置

                if out_of_top_streak.get(sym, 0) >= conf and sym in prices:
                    shares = positions.pop(sym)
                    px = prices[sym]
                    cash += shares * px
                    entry_px = entry_prices.pop(sym, px)
                    pnl = (px - entry_px) / entry_px if entry_px > 0 else 0
                    out_of_top_streak.pop(sym, None)
                    trades_log.append({
                        "date": rb_date, "symbol": sym, "action": "sell",
                        "reason": f"连续{conf}天跌出Top{cfg.top_n}",
                        "price": px, "pnl_pct": pnl,
                    })

            # ── 买入判定：连续在 top_n 达到 consecutive_days ──
            per_alloc = total_equity * 0.95 / cfg.top_n if positions else total_equity * 0.95 / max(1, cfg.top_n - len(positions))
            for sym, mom, px in top_picks:
                if sym in positions or px <= 0:
                    in_top_streak.pop(sym, None)  # 已持有，不再追踪
                    continue

                in_top_streak[sym] = in_top_streak.get(sym, 0) + 1

                if in_top_streak.get(sym, 0) < conf:
                    continue  # 连续天数不够，等待

                # 确认买入
                shares = per_alloc / px
                cost = shares * px
                if cost > cash:
                    shares = cash / px
                    cost = shares * px
                if shares <= 0:
                    continue
                cash -= cost
                positions[sym] = shares
                entry_prices[sym] = px
                in_top_streak.pop(sym, None)
                trades_log.append({
                    "date": rb_date, "symbol": sym, "action": "buy",
                    "reason": f"连续{conf}天动量{mom*100:.1f}% Top{cfg.top_n}",
                    "price": px, "momentum": mom, "shares": shares,
                })

            # ── 清理不再在 top_n 的候选 streak（保持连续语义） ──
            for sym in list(in_top_streak.keys()):
                if sym not in top_symbols:
                    in_top_streak.pop(sym, None)

            # ── 记录净值 ──
            pos_value = sum(positions.get(sym, 0) * prices.get(sym, 0) for sym in positions)
            equity_curve.append({
                "date": rb_date, "equity": cash + pos_value, "cash": cash,
                "positions": len(positions), "market_ok": market_ok,
            })

        # ── 期末清仓 ──
        final_date = rebalance_dates[-1] if rebalance_dates else all_dates[-1]
        for sym, shares in list(positions.items()):
            px = self._get_price(data, sym, final_date)
            if px:
                cash += shares * px
                entry_px = entry_prices.get(sym, px)
                pnl = (px - entry_px) / entry_px if entry_px > 0 else 0
                trades_log.append({
                    "date": final_date, "symbol": sym, "action": "sell",
                    "reason": "回测结束清仓", "price": px, "pnl_pct": pnl,
                })
        positions.clear()

        equity_curve.append({
            "date": final_date, "equity": cash, "cash": cash, "positions": 0,
        })

        metrics = self._compute_metrics(equity_curve, trades_log, data)
        return PortfolioBacktestResult(
            equity_curve=equity_curve,
            trades=trades_log,
            metrics=metrics,
        )

    @staticmethod
    def _get_price(data: Dict[str, pd.DataFrame], sym: str, as_of_date) -> Optional[float]:
        df = data.get(sym)
        if df is None:
            return None
        match = df[df["date"] <= pd.Timestamp(as_of_date)]
        if match.empty:
            return None
        return float(match["close"].iloc[-1])

    @staticmethod
    def _compute_metrics(equity: List[dict], trades: List[dict], data: Dict[str, pd.DataFrame]) -> dict:
        if not equity:
            return {}
        eq_df = pd.DataFrame(equity).sort_values("date")
        eq_df["date"] = pd.to_datetime(eq_df["date"])
        start_eq = float(eq_df["equity"].iloc[0])
        end_eq = float(eq_df["equity"].iloc[-1])
        days = (eq_df["date"].iloc[-1] - eq_df["date"].iloc[0]).days
        years = max(days / 365.25, 0.01)

        total_return = (end_eq - start_eq) / start_eq
        cagr = (end_eq / start_eq) ** (1 / years) - 1 if start_eq > 0 else 0

        # 最大回撤
        peak = start_eq
        max_dd = 0.0
        for _, row in eq_df.iterrows():
            peak = max(peak, float(row["equity"]))
            dd = (float(row["equity"]) - peak) / peak
            max_dd = min(max_dd, dd)

        # 月收益
        eq_df["month"] = eq_df["date"].dt.to_period("M")
        monthly = eq_df.groupby("month").agg({"equity": "last"})
        monthly["ret"] = monthly["equity"].pct_change()

        ann_vol = float(monthly["ret"].std() * np.sqrt(12)) if len(monthly) > 1 else 0

        rf_m = 0.02 / 12
        excess = monthly["ret"].dropna() - rf_m
        sharpe = float(excess.mean() / excess.std() * np.sqrt(12)) if len(excess) > 0 and excess.std() > 0 else 0

        downside = excess[excess < 0]
        d_std = float(downside.std()) if len(downside) > 0 else 0
        sortino = float(excess.mean() / d_std * np.sqrt(12)) if d_std > 0 else 0

        calmar = cagr / abs(max_dd) if max_dd < 0 else 0

        # 交易统计
        sell_trades = [t for t in trades if t["action"] == "sell"]
        pnls = [t.get("pnl_pct", 0) for t in sell_trades]
        wins = sum(1 for p in pnls if p > 0)
        losses = sum(1 for p in pnls if p <= 0)
        trade_count = len(pnls)
        win_rate = wins / trade_count if trade_count > 0 else 0
        avg_win = float(np.mean([p for p in pnls if p > 0])) if wins > 0 else 0
        avg_loss = float(np.mean([p for p in pnls if p <= 0])) if losses > 0 else 0

        # 基准（等权买入持有）
        bench_cagr = 0.0
        if data and equity:
            start_d = pd.Timestamp(equity[0]["date"])
            end_d = pd.Timestamp(equity[-1]["date"])
            years = max((end_d - start_d).days / 365.25, 0.01)
            rets = []
            for sym, df in data.items():
                s = df[df["date"] <= start_d]
                e = df[df["date"] <= end_d]
                if not s.empty and not e.empty:
                    rets.append(float(e["close"].iloc[-1]) / float(s["close"].iloc[0]) - 1)
            if rets:
                bench_ret = np.mean(rets)
                bench_cagr = ((1 + bench_ret) ** (1 / years) - 1)

        return {
            "start_date": str(eq_df["date"].iloc[0])[:10],
            "end_date": str(eq_df["date"].iloc[-1])[:10],
            "years": round(years, 2),
            "total_return": total_return,
            "cagr": cagr,
            "max_drawdown": max_dd,
            "annual_volatility": ann_vol,
            "sharpe": sharpe,
            "sortino": sortino,
            "calmar": calmar,
            "trade_count": trade_count,
            "win_rate": win_rate,
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "benchmark_cagr": bench_cagr,
        }
