#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
策略器模块：在筛选通过后对股票做策略检查

流程：筛选股票 -> 股票经过筛选器 -> 通过后股票经过策略器 -> 任意策略器满足则视为满足条件 -> 写入 MySQL

策略器与筛选器的区别：
- 筛选器：硬性条件（市值、PE、价格等），须全部通过才进入策略器。
- 策略器：策略信号（如 EMA 突破、RSI 超买超卖），任意一个满足即视为该股票满足策略条件。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, List, Optional

import pandas as pd

from filters import FilterContext, StockInfo
from strategy import (
    check_ema_breakout,
    check_zuoyi_strategy,
    EMABreakoutResult,
    compute_daily_volume_vs_prior3_and_pct_change,
    get_latest_rsi,
)


@dataclass
class StrategizerOutput:
    """单个策略器的输出"""
    name: str
    satisfied: bool  # 是否满足该策略
    reason: str = ""
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class StrategyChainResult:
    """策略器链对单只股票的结果"""
    stock: StockInfo
    any_satisfied: bool  # 是否有任意一个策略器满足
    outputs: List[StrategizerOutput] = field(default_factory=list)


class Strategizer(ABC):
    """策略器基类：判断股票是否满足某一策略条件"""

    def __init__(self, name: Optional[str] = None, enabled: bool = True):
        self._name = name or self.__class__.__name__
        self._enabled = enabled

    @property
    def name(self) -> str:
        return self._name

    @property
    def enabled(self) -> bool:
        return self._enabled

    @abstractmethod
    def apply(self, stock: StockInfo, context: FilterContext) -> StrategizerOutput:
        """
        应用策略判断。

        Args:
            stock: 股票信息（应已通过筛选器，且如需 K 线则已注入 stock.kline_df）
            context: 上下文（与筛选器共用 FilterContext）

        Returns:
            StrategizerOutput: 是否满足该策略及原因、详情
        """
        pass


class StrategizerChain:
    """
    策略器链：依次执行多个策略器，任意一个满足则整链视为满足。
    """

    def __init__(self, strategizers: Optional[List[Strategizer]] = None):
        self._strategizers: List[Strategizer] = strategizers or []

    def add_strategizer(self, s: Strategizer) -> "StrategizerChain":
        self._strategizers.append(s)
        return self

    def list_strategizers(self) -> List[str]:
        return [s.name for s in self._strategizers]

    def apply(self, stock: StockInfo, context: FilterContext) -> StrategyChainResult:
        """
        对单只股票应用所有已启用的策略器。
        任意一个策略器 satisfied=True 则 any_satisfied=True。
        """
        result = StrategyChainResult(stock=stock, any_satisfied=False)
        for s in self._strategizers:
            if not s.enabled:
                continue
            try:
                out = s.apply(stock, context)
                result.outputs.append(out)
                if out.satisfied:
                    result.any_satisfied = True
            except Exception as e:
                result.outputs.append(StrategizerOutput(
                    name=s.name,
                    satisfied=False,
                    reason=str(e),
                    details={"error": True},
                ))
        return result


# =============================================================================
# 具体策略器实现
# =============================================================================


class ZuoYiStrategizer(Strategizer):
    """左一战法策略器：当前周期内筛选看涨突破与看跌跌破信号。"""

    def __init__(
        self,
        signal_window: int = 3,
        include_bullish: bool = True,
        include_bearish: bool = True,
        name: str = "ZuoYiStrategizer",
        enabled: bool = True,
    ):
        super().__init__(name=name, enabled=enabled)
        self.signal_window = signal_window
        self.include_bullish = include_bullish
        self.include_bearish = include_bearish

    def apply(self, stock: StockInfo, context: FilterContext) -> StrategizerOutput:
        df = stock.kline_df
        if df is None or df.empty:
            return StrategizerOutput(
                name=self.name,
                satisfied=False,
                reason="K线数据不足，无法判断左一战法",
                details={"kline_available": False},
            )

        analysis = check_zuoyi_strategy(
            df=df,
            check_date=context.check_date,
            signal_window=self.signal_window,
            include_bullish=self.include_bullish,
            include_bearish=self.include_bearish,
        )
        signals = [s.to_dict() for s in analysis.signals]
        directions = [s["direction"] for s in signals]
        details = {
            "result_type": analysis.result_type,
            "direction": "|".join(directions) if directions else None,
            "signals": signals,
            "signal_window": self.signal_window,
            "data_rows": analysis.data_rows,
            "latest_close": analysis.latest_close,
        }
        return StrategizerOutput(
            name=self.name,
            satisfied=analysis.satisfied,
            reason=analysis.reason,
            details=details,
        )


class EMABreakoutStrategizer(Strategizer):
    """EMA 向上突破策略器：EMA_short 在 T-1 或 T-2 向上突破 EMA_long 则满足。"""

    def __init__(
        self,
        ema_short: int = 10,
        ema_long: int = 150,
        lookback_days: int = 2,
        name: str = "EMABreakoutStrategizer",
        enabled: bool = True,
    ):
        super().__init__(name=name, enabled=enabled)
        self.ema_short = ema_short
        self.ema_long = ema_long
        self.lookback_days = lookback_days

    def apply(self, stock: StockInfo, context: FilterContext) -> StrategizerOutput:
        df = stock.kline_df
        if df is None or df.empty:
            return StrategizerOutput(
                name=self.name,
                satisfied=False,
                reason="K线数据不足",
                details={"kline_available": False},
            )
        result_enum, breakout_date, ema_short_val, ema_long_val = check_ema_breakout(
            df=df,
            check_date=context.check_date,
            ema_short=self.ema_short,
            ema_long=self.ema_long,
        )
        details = {
            "result_type": result_enum.value,
            "breakout_date": str(breakout_date) if breakout_date else None,
            f"ema{self.ema_short}": ema_short_val,
            f"ema{self.ema_long}": ema_long_val,
        }
        satisfied = result_enum.is_satisfied()
        return StrategizerOutput(
            name=self.name,
            satisfied=satisfied,
            reason=result_enum.get_description(),
            details=details,
        )


class RSIOversoldStrategizer(Strategizer):
    """RSI 超卖策略器：RSI(period) <= threshold_low 时满足（超卖）。"""

    def __init__(
        self,
        period: int = 14,
        threshold: float = 30.0,
        name: str = "RSIOversoldStrategizer",
        enabled: bool = True,
    ):
        super().__init__(name=name, enabled=enabled)
        self.period = period
        self.threshold = threshold

    def apply(self, stock: StockInfo, context: FilterContext) -> StrategizerOutput:
        df = stock.kline_df
        if df is None or df.empty:
            return StrategizerOutput(
                name=self.name,
                satisfied=False,
                reason="K线数据不足，无法计算RSI",
                details={"rsi": None},
            )
        rsi = get_latest_rsi(df, period=self.period, check_date=context.check_date)
        if rsi is None:
            return StrategizerOutput(
                name=self.name,
                satisfied=False,
                reason="RSI数据不足或无效",
                details={"rsi": None, "period": self.period},
            )
        satisfied = rsi <= self.threshold
        return StrategizerOutput(
            name=self.name,
            satisfied=satisfied,
            reason=f"RSI({self.period})={rsi:.2f} {'<=' if satisfied else '>'} {self.threshold}",
            details={"rsi": rsi, "period": self.period, "threshold": self.threshold},
        )


class TodayVolumeExceedsPrior3MaxStrategizer(Strategizer):
    """当日成交量大于前三日（不含当日）单日最大成交量则满足。"""

    def __init__(
        self,
        name: str = "TodayVolumeExceedsPrior3MaxStrategizer",
        enabled: bool = True,
    ):
        super().__init__(name=name, enabled=enabled)

    def apply(self, stock: StockInfo, context: FilterContext) -> StrategizerOutput:
        today_v, max_prior3, _, _, _ = compute_daily_volume_vs_prior3_and_pct_change(
            stock.kline_df, check_date=context.check_date
        )
        if today_v is None or max_prior3 is None:
            return StrategizerOutput(
                name=self.name,
                satisfied=False,
                reason="K线或成交量不足（需至少4根日线）",
                details={"today_volume": today_v, "max_volume_prior3": max_prior3},
            )
        satisfied = today_v > max_prior3
        return StrategizerOutput(
            name=self.name,
            satisfied=satisfied,
            reason=(
                f"当日成交量 {today_v:.0f} {'>' if satisfied else '<='} "
                f"前三日最大 {max_prior3:.0f}"
            ),
            details={
                "today_volume": today_v,
                "max_volume_prior3": max_prior3,
            },
        )


class DailyPctChangeBandStrategizer(Strategizer):
    """当日涨跌幅落在 [pct_min, pct_max]（闭区间，单位：%）则满足。"""

    def __init__(
        self,
        pct_min: float,
        pct_max: float,
        name: str,
        enabled: bool = True,
    ):
        super().__init__(name=name, enabled=enabled)
        self.pct_min = pct_min
        self.pct_max = pct_max

    def apply(self, stock: StockInfo, context: FilterContext) -> StrategizerOutput:
        _, _, pct, prev_c, today_c = compute_daily_volume_vs_prior3_and_pct_change(
            stock.kline_df, check_date=context.check_date
        )
        if pct is None:
            return StrategizerOutput(
                name=self.name,
                satisfied=False,
                reason="无法计算当日涨跌幅",
                details={"pct_change": None, "prev_close": prev_c, "close": today_c},
            )
        satisfied = self.pct_min <= pct <= self.pct_max
        return StrategizerOutput(
            name=self.name,
            satisfied=satisfied,
            reason=(
                f"当日涨跌 {pct:.2f}% {'在' if satisfied else '不在'} "
                f"[{self.pct_min}%, {self.pct_max}%]"
            ),
            details={
                "pct_change": pct,
                "prev_close": prev_c,
                "close": today_c,
                "band_min": self.pct_min,
                "band_max": self.pct_max,
            },
        )


class RSIOverboughtStrategizer(Strategizer):
    """RSI 超买策略器：RSI(period) >= threshold_high 时满足（超买）。"""

    def __init__(
        self,
        period: int = 14,
        threshold: float = 70.0,
        name: str = "RSIOverboughtStrategizer",
        enabled: bool = True,
    ):
        super().__init__(name=name, enabled=enabled)
        self.period = period
        self.threshold = threshold

    def apply(self, stock: StockInfo, context: FilterContext) -> StrategizerOutput:
        df = stock.kline_df
        if df is None or df.empty:
            return StrategizerOutput(
                name=self.name,
                satisfied=False,
                reason="K线数据不足，无法计算RSI",
                details={"rsi": None},
            )
        rsi = get_latest_rsi(df, period=self.period, check_date=context.check_date)
        if rsi is None:
            return StrategizerOutput(
                name=self.name,
                satisfied=False,
                reason="RSI数据不足或无效",
                details={"rsi": None, "period": self.period},
            )
        satisfied = rsi >= self.threshold
        return StrategizerOutput(
            name=self.name,
            satisfied=satisfied,
            reason=f"RSI({self.period})={rsi:.2f} {'>=' if satisfied else '<'} {self.threshold}",
            details={"rsi": rsi, "period": self.period, "threshold": self.threshold},
        )
