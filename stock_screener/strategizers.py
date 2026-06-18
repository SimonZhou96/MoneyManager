#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
策略器模块：在筛选通过后对股票做策略检查

流程：筛选股票 -> 股票经过筛选器 -> 通过后股票经过策略器 -> 由 screen_service 统一判断策略组合 -> 写入 MySQL

策略器与筛选器的区别：
- 筛选器：硬性条件（市值、PE、价格等），须全部通过才进入策略器。
- 策略器：策略信号（如左一战法、EMA 突破、RSI 超买超卖），每个策略器只输出自身是否满足。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, List, Optional

import pandas as pd


def _coalesce(explicit: Any, defaults: Dict[str, Any], key: str, fallback: Any) -> Any:
    """解析参数：显式值 > 市场预设中的值 > 默认回退值。"""
    if explicit is not None:
        return explicit
    if key in defaults:
        return defaults[key]
    return fallback


if __package__:
    from .filters import FilterContext, StockInfo
    from .strategy import (
        check_ema_breakout,
        check_zuoyi_strategy,
        EMABreakoutResult,
        analyze_technical_pattern,
        compute_daily_volume_vs_prior3_and_pct_change,
        get_latest_rsi,
        analyze_energy_phases,
        EnergyPhaseAnalysis,
        get_market_energy_params,
    )
else:
    from filters import FilterContext, StockInfo
    from strategy import (
        check_ema_breakout,
        check_zuoyi_strategy,
        EMABreakoutResult,
        analyze_technical_pattern,
        compute_daily_volume_vs_prior3_and_pct_change,
        get_latest_rsi,
        analyze_energy_phases,
        EnergyPhaseAnalysis,
        get_market_energy_params,
    )


@dataclass
class StrategizerOutput:
    """单个策略器的输出"""
    name: str
    satisfied: bool  # 是否满足该策略
    result: str = ""
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
        signal_window: int = 15,
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


class TechnicalPatternStrategizer(Strategizer):
    """通用技术形态策略器：封装蜡烛图、K线和辅助线原子规则。"""

    def __init__(
        self,
        pattern_key: str,
        name: str = "TechnicalPatternStrategizer",
        enabled: bool = True,
        **params: Any,
    ):
        super().__init__(name=name, enabled=enabled)
        self.pattern_key = str(pattern_key or "").strip()
        self.params = dict(params or {})

    def apply(self, stock: StockInfo, context: FilterContext) -> StrategizerOutput:
        df = stock.kline_df
        if df is None or df.empty:
            return StrategizerOutput(
                name=self.name,
                satisfied=False,
                reason="K线数据不足，无法判断技术形态",
                details={"kline_available": False, "pattern_key": self.pattern_key},
            )

        signal = analyze_technical_pattern(
            df=df,
            pattern_key=self.pattern_key,
            check_date=context.check_date,
            **self.params,
        )
        return StrategizerOutput(
            name=self.name,
            satisfied=signal.satisfied,
            reason=signal.reason,
            details=dict(signal.details),
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


class EnergyPhaseClassifier(Strategizer):
    """六态物理能量相位分类器。

    将价格运动映射为六种物理状态：
        COMPRESS   — 势能积蓄，低动能 → 观望 (WATCH)
        RELEASE    — 势能→动能转化 → 看涨买入 (BUY)
        TRENDING   — 动能持续主导 → 持有 (HOLD)
        EXHAUSTION — 动能衰竭 → 预警 (WARN)
        PEAK       — 动能归零高位 → 卖出 (SELL)
        CRASH      — 空方动能 → 回避 (AVOID)

    当状态为 RELEASE 或 TRENDING 时触发 bullish 看涨信号，
    自动被 unified_bullish_top20 的技术规则发现并参与 Top20 评选。

    支持 market 参数自动加载市场特定预设（HK 低波动降阈值 / A 高波动升阈值）。
    通过 params dict 传入的 market 值会触发 get_market_energy_params()，然后用显式参数覆盖。
    """

    def __init__(
        self,
        *,
        name: str = "EnergyPhaseClassifier",
        enabled: bool = True,
        market: str | None = None,
        ma_period: int = 20,
        pe_threshold: float = 100.0,
        ke_threshold: float | None = None,  # None → 市场预设或默认 4.0
        epr_release_threshold: float = 0.1,
        ke_decay_exhaustion: float = 0.5,
        ke_decay_peak: float = 0.8,
        consistency_window: int = 10,
        delta_window: int = 5,
        lookback_pe_days: int = 3,
        min_rows: int = 30,
        # ── 状态判定参数（None → 市场预设或硬编码默认值） ──
        crash_neg_streak: int | None = None,
        crash_ke_path: float | None = None,
        peak_pe_threshold: float | None = None,
        peak_ke_silence: float | None = None,
        peak_delta_e: float | None = None,
        release_delta_e: float | None = None,
        trending_consistency: float | None = None,
        compress_consistency: float | None = None,
        compress_ke_path: float | None = None,
    ):
        super().__init__(name=name, enabled=enabled)

        # ── 加载市场预设 ──
        market_defaults = get_market_energy_params(market) if market else {}

        self.ma_period = ma_period
        self.pe_threshold = pe_threshold
        self.ke_threshold = _coalesce(ke_threshold, market_defaults, "ke_threshold", 4.0)
        self.epr_release_threshold = epr_release_threshold
        self.ke_decay_exhaustion = ke_decay_exhaustion
        self.ke_decay_peak = ke_decay_peak
        self.consistency_window = consistency_window
        self.delta_window = delta_window
        self.lookback_pe_days = lookback_pe_days
        self.min_rows = min_rows
        # 状态判定参数（显式值 > 市场预设 > 默认值）
        self.crash_neg_streak = _coalesce(crash_neg_streak, market_defaults, "crash_neg_streak", 5)
        self.crash_ke_path = _coalesce(crash_ke_path, market_defaults, "crash_ke_path", -20.0)
        self.peak_pe_threshold = _coalesce(peak_pe_threshold, market_defaults, "peak_pe_threshold", 80.0)
        self.peak_ke_silence = _coalesce(peak_ke_silence, market_defaults, "peak_ke_silence", 1.0)
        self.peak_delta_e = _coalesce(peak_delta_e, market_defaults, "peak_delta_e", -10.0)
        self.release_delta_e = _coalesce(release_delta_e, market_defaults, "release_delta_e", 10.0)
        self.trending_consistency = _coalesce(trending_consistency, market_defaults, "trending_consistency", 0.7)
        self.compress_consistency = _coalesce(compress_consistency, market_defaults, "compress_consistency", 0.3)
        self.compress_ke_path = _coalesce(compress_ke_path, market_defaults, "compress_ke_path", 0.0)

    def apply(self, stock: "StockInfo", context: "FilterContext") -> StrategizerOutput:
        df = stock.kline_df
        if df is None or df.empty:
            return StrategizerOutput(
                name=self.name,
                satisfied=False,
                reason="K-line data unavailable for energy phase analysis",
                details={"kline_available": False},
            )

        analysis = analyze_energy_phases(
            df=df,
            check_date=context.check_date,
            ma_period=self.ma_period,
            pe_threshold=self.pe_threshold,
            ke_threshold=self.ke_threshold,
            epr_release_threshold=self.epr_release_threshold,
            ke_decay_exhaustion=self.ke_decay_exhaustion,
            ke_decay_peak=self.ke_decay_peak,
            consistency_window=self.consistency_window,
            delta_window=self.delta_window,
            lookback_pe_days=self.lookback_pe_days,
            min_rows=self.min_rows,
            crash_neg_streak=self.crash_neg_streak,
            crash_ke_path=self.crash_ke_path,
            peak_pe_threshold=self.peak_pe_threshold,
            peak_ke_silence=self.peak_ke_silence,
            peak_delta_e=self.peak_delta_e,
            release_delta_e=self.release_delta_e,
            trending_consistency=self.trending_consistency,
            compress_consistency=self.compress_consistency,
            compress_ke_path=self.compress_ke_path,
        )

        details = {
            "state": analysis.state,
            "direction": "bullish" if analysis.satisfied else "unknown",
        }
        details.update(analysis.details)

        return StrategizerOutput(
            name=self.name,
            satisfied=analysis.satisfied,
            reason=analysis.reason,
            details=details,
        )


# =============================================================================
# 持有层宏观规则 Strategizer（6 个）
# =============================================================================


class CreditRiskRegimeStrategizer(Strategizer):
    """信用风险环境策略器（市场级）—— 评估信用风险是否上升。

    数据源：YFinance（HK/US: HYG/LQD/JNK/IEF/VIX; A: AKShare 信用债）。
    Phase 1 实现 YFinance ETF 代理方案。
    """

    def __init__(
        self,
        name: str = "CreditRiskRegimeStrategizer",
        enabled: bool = True,
    ):
        super().__init__(name=name, enabled=enabled)

    def apply(self, stock: StockInfo, context: FilterContext) -> StrategizerOutput:
        """委托给 MarketCache，此 Strategizer 仅作为 RuleRegistry 注册占位。
        实际计算在 MarketCache._compute_credit_risk() 中。"""
        # MarketCache 的结果会通过 MarketTemperature.to_filter_details() 注入
        return StrategizerOutput(
            name=self.name,
            satisfied=False,
            reason="CreditRiskRegime 由 MarketCache 统一计算，不逐只股票调用",
            details={"delegated": True},
        )


class MarketBreadthRegimeStrategizer(Strategizer):
    """市场宽度策略器（市场级）—— 判断指数上涨是否健康扩散。

    Phase 2 实现。数据源：Futu OpenD 全市场个股日 K + AKShare 行业涨跌。
    """

    def __init__(
        self,
        name: str = "MarketBreadthRegimeStrategizer",
        enabled: bool = True,
    ):
        super().__init__(name=name, enabled=enabled)

    def apply(self, stock: StockInfo, context: FilterContext) -> StrategizerOutput:
        return StrategizerOutput(
            name=self.name,
            satisfied=False,
            reason="MarketBreadthRegime 由 MarketCache 统一计算，不逐只股票调用",
            details={"delegated": True},
        )


class LiquidityNowcastStrategizer(Strategizer):
    """资金流动性即报策略器（市场级）—— 判断资金环境是否支持继续持有。

    Phase 2 实现。数据源：AKShare（A股融资融券/北向资金/ETF资金）+ Futu OpenD（成交额）。
    """

    def __init__(
        self,
        name: str = "LiquidityNowcastStrategizer",
        enabled: bool = True,
    ):
        super().__init__(name=name, enabled=enabled)

    def apply(self, stock: StockInfo, context: FilterContext) -> StrategizerOutput:
        return StrategizerOutput(
            name=self.name,
            satisfied=False,
            reason="LiquidityNowcast 由 MarketCache 统一计算，不逐只股票调用",
            details={"delegated": True},
        )


class EarningsRevisionMomentumStrategizer(Strategizer):
    """盈利预期修正策略器（个股级）—— 判断公司未来盈利预期是否改善。

    数据源：Tavily（搜索业绩预告/财报/券商观点）+ LLM 结构化。
    Phase 3 实现 LLM 调用，Phase 1 返回中性桩。
    """

    def __init__(
        self,
        name: str = "EarningsRevisionMomentumStrategizer",
        enabled: bool = True,
    ):
        super().__init__(name=name, enabled=enabled)

    def apply(self, stock: StockInfo, context: FilterContext) -> StrategizerOutput:
        # Phase 1 桩实现
        return StrategizerOutput(
            name=self.name,
            satisfied=False,
            reason="EarningsRevision 暂未实现（Phase 3）",
            details={"score": 50.0, "earnings_trend": "stable", "confidence": 0.0},
        )


class PolicyEventRiskStrategizer(Strategizer):
    """政策事件风险策略器（市场级）—— 识别政策/监管/地缘事件影响。

    Phase 3 实现。数据源：Tavily + LLM。
    """

    def __init__(
        self,
        name: str = "PolicyEventRiskStrategizer",
        enabled: bool = True,
    ):
        super().__init__(name=name, enabled=enabled)

    def apply(self, stock: StockInfo, context: FilterContext) -> StrategizerOutput:
        return StrategizerOutput(
            name=self.name,
            satisfied=False,
            reason="PolicyEventRisk 由 MarketCache 统一计算，不逐只股票调用",
            details={"delegated": True},
        )


class CommodityShockStrategizer(Strategizer):
    """大宗商品冲击策略器（市场级）—— 识别商品价格变化对不同行业的影响。

    Phase 2 实现。数据源：YFinance（WTI原油/铜/黄金/天然气/白银期货）。
    """

    def __init__(
        self,
        name: str = "CommodityShockStrategizer",
        enabled: bool = True,
    ):
        super().__init__(name=name, enabled=enabled)

    def apply(self, stock: StockInfo, context: FilterContext) -> StrategizerOutput:
        return StrategizerOutput(
            name=self.name,
            satisfied=False,
            reason="CommodityShock 由 MarketCache 统一计算，不逐只股票调用",
            details={"delegated": True},
        )
