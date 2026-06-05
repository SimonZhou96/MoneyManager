#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
策略模块：EMA突破策略判断
支持判断 EMA10 向上突破 EMA150 的策略
"""

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd


class EMABreakoutResult(Enum):
    """EMA突破结果枚举
    
    满足条件的情况：
    - BREAKOUT_T1: 前一个交易日刚刚向上突破
    - BREAKOUT_T2: 前两个交易日刚刚向上突破
    
    不满足条件的情况：
    - NO_BREAKOUT_BELOW: EMA10 仍在 EMA150 下方
    - NO_BREAKOUT_ALREADY_ABOVE: EMA10 早已在 EMA150 上方（非最近2个交易日突破）
    - INSUFFICIENT_DATA: 数据不足，无法计算 EMA
    - INVALID_DATA: 数据异常（如 close 价格为空或为负）
    """
    # 满足条件
    BREAKOUT_T1 = "breakout_t1"  # 前一个交易日向上突破
    BREAKOUT_T2 = "breakout_t2"  # 前两个交易日向上突破
    
    # 不满足条件
    NO_BREAKOUT_BELOW = "no_breakout_below"  # EMA10 仍在下方
    NO_BREAKOUT_ALREADY_ABOVE = "no_breakout_already_above"  # 早已在上方
    INSUFFICIENT_DATA = "insufficient_data"  # 数据不足
    INVALID_DATA = "invalid_data"  # 数据异常

    def is_satisfied(self) -> bool:
        """判断是否满足突破条件"""
        return self in (EMABreakoutResult.BREAKOUT_T1, EMABreakoutResult.BREAKOUT_T2)
    
    def get_description(self) -> str:
        """获取结果描述"""
        descriptions = {
            EMABreakoutResult.BREAKOUT_T1: "前一个交易日EMA10向上突破EMA150",
            EMABreakoutResult.BREAKOUT_T2: "前两个交易日EMA10向上突破EMA150",
            EMABreakoutResult.NO_BREAKOUT_BELOW: "EMA10仍在EMA150下方，未发生突破",
            EMABreakoutResult.NO_BREAKOUT_ALREADY_ABOVE: "EMA10早已在EMA150上方（超过2个K线柱）",
            EMABreakoutResult.INSUFFICIENT_DATA: "K线数据不足，无法计算EMA",
            EMABreakoutResult.INVALID_DATA: "K线数据异常",
        }
        return descriptions.get(self, "未知状态")


@dataclass
class EMABreakoutSignal:
    """EMA突破信号数据类"""
    market: str
    code: str
    check_date: date  # 检查日期（当天）
    result: EMABreakoutResult
    breakout_date: Optional[date] = None  # 突破发生的日期
    ema10: Optional[float] = None  # 最新的 EMA10 值
    ema150: Optional[float] = None  # 最新的 EMA150 值
    close_price: Optional[float] = None  # 最新收盘价
    data_rows: int = 0  # 用于计算的数据行数


@dataclass
class ZuoYiSignal:
    """左一战法单个方向信号"""
    direction: str  # bullish / bearish
    left_one_date: date
    median_date: date
    breakout_date: date
    bars_to_breakout: int
    left_one_high: float
    left_one_low: float
    median_high: float
    median_low: float
    breakout_close: float
    latest_close: float

    def to_dict(self) -> dict:
        """转换为可 JSON 序列化的字典"""
        return {
            "direction": self.direction,
            "left_one_date": self.left_one_date.isoformat(),
            "median_date": self.median_date.isoformat(),
            "breakout_date": self.breakout_date.isoformat(),
            "bars_to_breakout": self.bars_to_breakout,
            "left_one_high": self.left_one_high,
            "left_one_low": self.left_one_low,
            "median_high": self.median_high,
            "median_low": self.median_low,
            "breakout_close": self.breakout_close,
            "latest_close": self.latest_close,
        }


@dataclass
class ZuoYiAnalysis:
    """左一战法分析结果"""
    satisfied: bool
    result_type: str
    reason: str
    signals: List[ZuoYiSignal] = field(default_factory=list)
    data_rows: int = 0
    latest_close: Optional[float] = None


def calculate_ema(prices: pd.Series, period: int) -> pd.Series:
    """
    计算指数移动平均线 (EMA)
    
    Args:
        prices: 价格序列（通常是收盘价）
        period: EMA 周期
        
    Returns:
        pd.Series: EMA 值序列
    """
    return prices.ewm(span=period, adjust=False).mean()


def calculate_sma(prices: pd.Series, period: int) -> pd.Series:
    """计算简单移动平均线 (SMA)。"""
    return prices.rolling(window=period, min_periods=period).mean()


def calculate_macd(
    prices: pd.Series,
    fast_period: int = 12,
    slow_period: int = 26,
    signal_period: int = 9,
) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """计算 MACD、信号线和柱状图。"""
    fast = calculate_ema(prices, fast_period)
    slow = calculate_ema(prices, slow_period)
    macd = fast - slow
    signal = calculate_ema(macd, signal_period)
    histogram = macd - signal
    return macd, signal, histogram


def calculate_bollinger_bands(
    prices: pd.Series,
    period: int = 20,
    std_multiplier: float = 2.0,
) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """计算布林线中轨、上轨、下轨。"""
    middle = calculate_sma(prices, period)
    std = prices.rolling(window=period, min_periods=period).std()
    upper = middle + std * std_multiplier
    lower = middle - std * std_multiplier
    return middle, upper, lower


def calculate_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """计算平均真实波幅 (ATR)。"""
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    close = df["close"].astype(float)
    prev_close = close.shift(1)
    true_range = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return true_range.rolling(window=period, min_periods=period).mean()


def calculate_vwap(df: pd.DataFrame) -> pd.Series:
    """按已有 K 线窗口计算成交量加权均价。"""
    typical_price = (df["high"].astype(float) + df["low"].astype(float) + df["close"].astype(float)) / 3.0
    volume = df["volume"].astype(float)
    cumulative_volume = volume.cumsum()
    return (typical_price * volume).cumsum() / cumulative_volume


def calculate_kdj(
    df: pd.DataFrame,
    period: int = 9,
    k_period: int = 3,
    d_period: int = 3,
) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """计算 KDJ 指标。"""
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    close = df["close"].astype(float)
    lowest_low = low.rolling(window=period, min_periods=period).min()
    highest_high = high.rolling(window=period, min_periods=period).max()
    rsv = (close - lowest_low) / (highest_high - lowest_low) * 100.0
    k = rsv.ewm(alpha=1.0 / k_period, adjust=False).mean()
    d = k.ewm(alpha=1.0 / d_period, adjust=False).mean()
    j = 3.0 * k - 2.0 * d
    return k, d, j


@dataclass
class TechnicalPatternSignal:
    """通用蜡烛图/辅助线原子规则分析结果。"""

    pattern_key: str
    pattern_label: str
    direction: str
    satisfied: bool
    reason: str
    details: Dict[str, Any] = field(default_factory=dict)
    data_rows: int = 0


TECHNICAL_PATTERN_DEFINITIONS: Dict[str, Dict[str, Any]] = {
    "bullish_engulfing": {"label": "看涨吞没", "direction": "bullish", "min_rows": 2},
    "bearish_engulfing": {"label": "看跌吞没", "direction": "bearish", "min_rows": 2},
    "hammer_reversal": {"label": "锤子线反转", "direction": "bullish", "min_rows": 1},
    "shooting_star_reversal": {"label": "射击之星反转", "direction": "bearish", "min_rows": 1},
    "morning_star": {"label": "早晨之星", "direction": "bullish", "min_rows": 3},
    "evening_star": {"label": "黄昏之星", "direction": "bearish", "min_rows": 3},
    "piercing_line": {"label": "曙光初现", "direction": "bullish", "min_rows": 2},
    "dark_cloud_cover": {"label": "乌云盖顶", "direction": "bearish", "min_rows": 2},
    "three_white_soldiers": {"label": "红三兵", "direction": "bullish", "min_rows": 3},
    "three_black_crows": {"label": "三只乌鸦", "direction": "bearish", "min_rows": 3},
    "doji_indecision": {"label": "十字星", "direction": "neutral", "min_rows": 1},
    "bullish_marubozu": {"label": "看涨光头光脚", "direction": "bullish", "min_rows": 1},
    "bearish_marubozu": {"label": "看跌光头光脚", "direction": "bearish", "min_rows": 1},
    "sma_golden_cross": {"label": "均线金叉", "direction": "bullish", "min_rows": 51},
    "sma_death_cross": {"label": "均线死叉", "direction": "bearish", "min_rows": 51},
    "ema_golden_cross": {"label": "EMA金叉", "direction": "bullish", "min_rows": 51},
    "ema_death_cross": {"label": "EMA死叉", "direction": "bearish", "min_rows": 51},
    "macd_bullish_cross": {"label": "MACD金叉", "direction": "bullish", "min_rows": 35},
    "macd_bearish_cross": {"label": "MACD死叉", "direction": "bearish", "min_rows": 35},
    "bollinger_lower_rebound": {"label": "布林下轨反弹", "direction": "bullish", "min_rows": 21},
    "bollinger_upper_rejection": {"label": "布林上轨回落", "direction": "bearish", "min_rows": 21},
    "vwap_bullish_reclaim": {"label": "成交量加权均价上穿", "direction": "bullish", "min_rows": 2, "volume": True},
    "vwap_bearish_loss": {"label": "成交量加权均价下破", "direction": "bearish", "min_rows": 2, "volume": True},
    "atr_up_breakout": {"label": "ATR向上突破", "direction": "bullish", "min_rows": 16},
    "atr_down_breakdown": {"label": "ATR向下跌破", "direction": "bearish", "min_rows": 16},
    "kdj_bullish_cross": {"label": "KDJ金叉", "direction": "bullish", "min_rows": 12},
    "kdj_low_bullish_cross": {"label": "低位KDJ金叉", "direction": "bullish", "min_rows": 12},
    "kdj_bearish_cross": {"label": "KDJ死叉", "direction": "bearish", "min_rows": 12},
    "rsi_bullish_rebound": {"label": "RSI超卖回升", "direction": "bullish", "min_rows": 16},
    "rsi_bearish_pullback": {"label": "RSI超买回落", "direction": "bearish", "min_rows": 16},
    "volume_price_breakout": {"label": "放量突破", "direction": "bullish", "min_rows": 22, "volume": True},
    "volume_price_breakdown": {"label": "放量跌破", "direction": "bearish", "min_rows": 22, "volume": True},
}


def _validate_kline_data(df: pd.DataFrame) -> Tuple[bool, str]:
    """
    验证 K 线数据的有效性
    
    Returns:
        (是否有效, 错误信息)
    """
    if df is None or df.empty:
        return False, "DataFrame is None or empty"
    
    if "close" not in df.columns:
        return False, "Missing 'close' column"
    
    if "date" not in df.columns:
        return False, "Missing 'date' column"
    
    # 检查是否有有效的 close 价格
    valid_close = df["close"].notna() & (df["close"] > 0)
    if valid_close.sum() == 0:
        return False, "No valid close prices"
    
    return True, ""


def _prepare_kline_data(df: pd.DataFrame) -> pd.DataFrame:
    """
    预处理 K 线数据，确保数据格式正确且按日期排序
    
    Args:
        df: 原始 K 线数据
        
    Returns:
        处理后的 DataFrame
    """
    df = df.copy()
    
    # 确保 date 是 datetime 类型
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    
    # 移除无效日期和无效收盘价
    df = df[df["date"].notna() & df["close"].notna() & (df["close"] > 0)]
    
    # 按日期升序排列
    df = df.sort_values("date").reset_index(drop=True)
    
    return df


def _prepare_technical_pattern_data(
    df: pd.DataFrame,
    check_date: date | None = None,
    require_volume: bool = False,
) -> Tuple[Optional[pd.DataFrame], str]:
    """预处理蜡烛图和辅助线原子规则所需的 OHLCV 数据。"""
    if df is None or df.empty:
        return None, "K线数据为空"

    required_columns = ["date", "open", "high", "low", "close"]
    if require_volume:
        required_columns.append("volume")
    missing = [col for col in required_columns if col not in df.columns]
    if missing:
        return None, f"缺少字段: {', '.join(missing)}"

    work = df.copy()
    work["date"] = pd.to_datetime(work["date"], errors="coerce")
    for col in ["open", "high", "low", "close"] + (["volume"] if require_volume else []):
        work[col] = pd.to_numeric(work[col], errors="coerce")

    valid = (
        work["date"].notna()
        & work["open"].notna()
        & work["high"].notna()
        & work["low"].notna()
        & work["close"].notna()
        & (work["open"] > 0)
        & (work["high"] > 0)
        & (work["low"] > 0)
        & (work["close"] > 0)
        & (work["high"] >= work["low"])
        & (work["high"] >= work["open"])
        & (work["high"] >= work["close"])
        & (work["low"] <= work["open"])
        & (work["low"] <= work["close"])
    )
    if require_volume:
        valid = valid & work["volume"].notna() & (work["volume"] > 0)
    work = work[valid].sort_values("date").reset_index(drop=True)

    if check_date is not None and not work.empty:
        work = work[work["date"].dt.date <= check_date].reset_index(drop=True)

    if work.empty:
        return None, "无有效 OHLC K线"
    return work, ""


def _candle_body(row: pd.Series) -> float:
    return abs(float(row["close"]) - float(row["open"]))


def _candle_range(row: pd.Series) -> float:
    return float(row["high"]) - float(row["low"])


def _upper_shadow(row: pd.Series) -> float:
    return float(row["high"]) - max(float(row["open"]), float(row["close"]))


def _lower_shadow(row: pd.Series) -> float:
    return min(float(row["open"]), float(row["close"])) - float(row["low"])


def _is_bullish(row: pd.Series) -> bool:
    return float(row["close"]) > float(row["open"])


def _is_bearish(row: pd.Series) -> bool:
    return float(row["close"]) < float(row["open"])


def _body_midpoint(row: pd.Series) -> float:
    return (float(row["open"]) + float(row["close"])) / 2.0


def _crossed_up(prev_left: float, prev_right: float, curr_left: float, curr_right: float) -> bool:
    return prev_left <= prev_right and curr_left > curr_right


def _crossed_down(prev_left: float, prev_right: float, curr_left: float, curr_right: float) -> bool:
    return prev_left >= prev_right and curr_left < curr_right


def _latest_value(series: pd.Series, offset: int = 1) -> Optional[float]:
    if len(series) < offset:
        return None
    val = series.iloc[-offset]
    if pd.isna(val):
        return None
    return float(val)


def _format_price(value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    return round(float(value), 4)


def _technical_signal(
    pattern_key: str,
    satisfied: bool,
    reason: str,
    work: Optional[pd.DataFrame],
    details: Optional[Dict[str, Any]] = None,
) -> TechnicalPatternSignal:
    definition = TECHNICAL_PATTERN_DEFINITIONS.get(pattern_key, {})
    label = str(definition.get("label") or pattern_key)
    direction = str(definition.get("direction") or "neutral")
    data_rows = len(work) if work is not None else 0
    payload: Dict[str, Any] = {
        "pattern_key": pattern_key,
        "pattern_label": label,
        "direction": direction,
        "data_rows": data_rows,
    }
    if details:
        payload.update(details)
    return TechnicalPatternSignal(
        pattern_key=pattern_key,
        pattern_label=label,
        direction=direction,
        satisfied=satisfied,
        reason=reason,
        details=payload,
        data_rows=data_rows,
    )


def _analyze_candlestick_pattern(pattern_key: str, work: pd.DataFrame, params: Dict[str, Any]) -> Tuple[bool, str, Dict[str, Any]]:
    body_ratio = float(params.get("body_ratio", 0.1))
    shadow_ratio = float(params.get("shadow_ratio", 2.0))
    small_shadow_ratio = float(params.get("small_shadow_ratio", 0.6))
    min_body_ratio = float(params.get("min_body_range_ratio", 0.45))
    last = work.iloc[-1]
    details = {
        "open": _format_price(float(last["open"])),
        "high": _format_price(float(last["high"])),
        "low": _format_price(float(last["low"])),
        "close": _format_price(float(last["close"])),
    }

    if pattern_key in ("bullish_engulfing", "bearish_engulfing", "piercing_line", "dark_cloud_cover"):
        prev, curr = work.iloc[-2], work.iloc[-1]
        prev_body_low = min(float(prev["open"]), float(prev["close"]))
        prev_body_high = max(float(prev["open"]), float(prev["close"]))
        curr_body_low = min(float(curr["open"]), float(curr["close"]))
        curr_body_high = max(float(curr["open"]), float(curr["close"]))
        details.update({
            "prev_open": _format_price(float(prev["open"])),
            "prev_close": _format_price(float(prev["close"])),
        })
        if pattern_key == "bullish_engulfing":
            ok = _is_bearish(prev) and _is_bullish(curr) and curr_body_low <= prev_body_low and curr_body_high >= prev_body_high
            return ok, "最近两根K线形成看涨吞没" if ok else "未形成看涨吞没", details
        if pattern_key == "bearish_engulfing":
            ok = _is_bullish(prev) and _is_bearish(curr) and curr_body_low <= prev_body_low and curr_body_high >= prev_body_high
            return ok, "最近两根K线形成看跌吞没" if ok else "未形成看跌吞没", details
        if pattern_key == "piercing_line":
            midpoint = _body_midpoint(prev)
            ok = _is_bearish(prev) and _is_bullish(curr) and float(curr["open"]) < float(prev["close"]) and midpoint < float(curr["close"]) < float(prev["open"])
            details["prev_body_midpoint"] = _format_price(midpoint)
            return ok, "当前K线向上刺入前一阴线实体中点上方" if ok else "未形成曙光初现", details
        midpoint = _body_midpoint(prev)
        ok = _is_bullish(prev) and _is_bearish(curr) and float(curr["open"]) > float(prev["close"]) and float(prev["open"]) < float(curr["close"]) < midpoint
        details["prev_body_midpoint"] = _format_price(midpoint)
        return ok, "当前K线跌破前一阳线实体中点" if ok else "未形成乌云盖顶", details

    if pattern_key in ("hammer_reversal", "shooting_star_reversal", "doji_indecision", "bullish_marubozu", "bearish_marubozu"):
        body = max(_candle_body(last), 1e-9)
        rng = max(_candle_range(last), 1e-9)
        upper = _upper_shadow(last)
        lower = _lower_shadow(last)
        details.update({
            "body": _format_price(body),
            "upper_shadow": _format_price(upper),
            "lower_shadow": _format_price(lower),
            "body_range_ratio": round(body / rng, 4),
        })
        if pattern_key == "hammer_reversal":
            ok = lower >= body * shadow_ratio and upper <= body * small_shadow_ratio and max(float(last["open"]), float(last["close"])) >= float(last["low"]) + rng * 0.6
            return ok, "下影线明显长于实体，收盘靠近上方" if ok else "未形成锤子线反转", details
        if pattern_key == "shooting_star_reversal":
            ok = upper >= body * shadow_ratio and lower <= body * small_shadow_ratio and min(float(last["open"]), float(last["close"])) <= float(last["low"]) + rng * 0.4
            return ok, "上影线明显长于实体，收盘靠近下方" if ok else "未形成射击之星反转", details
        if pattern_key == "doji_indecision":
            ok = body <= rng * body_ratio
            return ok, "开收盘价接近，形成十字星" if ok else "未形成十字星", details
        if pattern_key == "bullish_marubozu":
            ok = _is_bullish(last) and body >= rng * 0.8 and upper <= rng * 0.1 and lower <= rng * 0.1
            return ok, "阳线实体占主要波幅，上下影较短" if ok else "未形成看涨光头光脚", details
        ok = _is_bearish(last) and body >= rng * 0.8 and upper <= rng * 0.1 and lower <= rng * 0.1
        return ok, "阴线实体占主要波幅，上下影较短" if ok else "未形成看跌光头光脚", details

    if pattern_key in ("morning_star", "evening_star"):
        first, second, third = work.iloc[-3], work.iloc[-2], work.iloc[-1]
        first_body = max(_candle_body(first), 1e-9)
        second_body = _candle_body(second)
        third_body = _candle_body(third)
        details.update({
            "first_open": _format_price(float(first["open"])),
            "first_close": _format_price(float(first["close"])),
            "second_body": _format_price(second_body),
            "third_body": _format_price(third_body),
        })
        if pattern_key == "morning_star":
            ok = _is_bearish(first) and second_body <= first_body * 0.55 and _is_bullish(third) and float(third["close"]) > _body_midpoint(first)
            return ok, "三根K线形成早晨之星反转" if ok else "未形成早晨之星", details
        ok = _is_bullish(first) and second_body <= first_body * 0.55 and _is_bearish(third) and float(third["close"]) < _body_midpoint(first)
        return ok, "三根K线形成黄昏之星反转" if ok else "未形成黄昏之星", details

    if pattern_key in ("three_white_soldiers", "three_black_crows"):
        rows = [work.iloc[-3], work.iloc[-2], work.iloc[-1]]
        if pattern_key == "three_white_soldiers":
            ok = all(_is_bullish(row) and _candle_body(row) / max(_candle_range(row), 1e-9) >= min_body_ratio for row in rows)
            ok = ok and float(rows[1]["close"]) > float(rows[0]["close"]) and float(rows[2]["close"]) > float(rows[1]["close"])
            return ok, "连续三根较强阳线且收盘逐步抬高" if ok else "未形成红三兵", details
        ok = all(_is_bearish(row) and _candle_body(row) / max(_candle_range(row), 1e-9) >= min_body_ratio for row in rows)
        ok = ok and float(rows[1]["close"]) < float(rows[0]["close"]) and float(rows[2]["close"]) < float(rows[1]["close"])
        return ok, "连续三根较强阴线且收盘逐步走低" if ok else "未形成三只乌鸦", details

    return False, "未实现的蜡烛图形态", details


def _analyze_indicator_pattern(pattern_key: str, work: pd.DataFrame, params: Dict[str, Any]) -> Tuple[bool, str, Dict[str, Any]]:
    close = work["close"].astype(float)
    details: Dict[str, Any] = {"close": _format_price(float(close.iloc[-1]))}

    if pattern_key in ("sma_golden_cross", "sma_death_cross", "ema_golden_cross", "ema_death_cross"):
        short_period = int(params.get("short_period", 20))
        long_period = int(params.get("long_period", 50))
        if pattern_key.startswith("sma"):
            short_line = calculate_sma(close, short_period)
            long_line = calculate_sma(close, long_period)
            line_name = "SMA"
        else:
            short_line = calculate_ema(close, short_period)
            long_line = calculate_ema(close, long_period)
            line_name = "EMA"
        prev_short, curr_short = _latest_value(short_line, 2), _latest_value(short_line, 1)
        prev_long, curr_long = _latest_value(long_line, 2), _latest_value(long_line, 1)
        details.update({
            f"{line_name}{short_period}": _format_price(curr_short),
            f"{line_name}{long_period}": _format_price(curr_long),
        })
        if None in (prev_short, curr_short, prev_long, curr_long):
            return False, f"{line_name}数据不足", details
        if pattern_key.endswith("golden_cross"):
            ok = _crossed_up(prev_short, prev_long, curr_short, curr_long)
            return ok, f"{line_name}{short_period}向上穿越{line_name}{long_period}" if ok else "未形成均线金叉", details
        ok = _crossed_down(prev_short, prev_long, curr_short, curr_long)
        return ok, f"{line_name}{short_period}向下穿越{line_name}{long_period}" if ok else "未形成均线死叉", details

    if pattern_key in ("macd_bullish_cross", "macd_bearish_cross"):
        fast = int(params.get("fast_period", 12))
        slow = int(params.get("slow_period", 26))
        signal_period = int(params.get("signal_period", 9))
        macd, signal, histogram = calculate_macd(close, fast, slow, signal_period)
        prev_macd, curr_macd = _latest_value(macd, 2), _latest_value(macd, 1)
        prev_signal, curr_signal = _latest_value(signal, 2), _latest_value(signal, 1)
        details.update({
            "macd": _format_price(curr_macd),
            "macd_signal": _format_price(curr_signal),
            "macd_histogram": _format_price(_latest_value(histogram, 1)),
        })
        if None in (prev_macd, curr_macd, prev_signal, curr_signal):
            return False, "MACD数据不足", details
        if pattern_key == "macd_bullish_cross":
            ok = _crossed_up(prev_macd, prev_signal, curr_macd, curr_signal)
            return ok, "MACD线向上穿越信号线" if ok else "未形成MACD金叉", details
        ok = _crossed_down(prev_macd, prev_signal, curr_macd, curr_signal)
        return ok, "MACD线向下穿越信号线" if ok else "未形成MACD死叉", details

    if pattern_key in ("bollinger_lower_rebound", "bollinger_upper_rejection"):
        period = int(params.get("period", 20))
        std_multiplier = float(params.get("std_multiplier", 2.0))
        _, upper, lower = calculate_bollinger_bands(close, period, std_multiplier)
        prev_close, curr_close = float(close.iloc[-2]), float(close.iloc[-1])
        prev_upper, curr_upper = _latest_value(upper, 2), _latest_value(upper, 1)
        prev_lower, curr_lower = _latest_value(lower, 2), _latest_value(lower, 1)
        details.update({
            "bollinger_upper": _format_price(curr_upper),
            "bollinger_lower": _format_price(curr_lower),
        })
        if None in (prev_upper, curr_upper, prev_lower, curr_lower):
            return False, "布林线数据不足", details
        if pattern_key == "bollinger_lower_rebound":
            ok = prev_close <= prev_lower and curr_close > curr_lower and curr_close > prev_close
            return ok, "价格从布林下轨下方向上收回" if ok else "未形成布林下轨反弹", details
        ok = prev_close >= prev_upper and curr_close < curr_upper and curr_close < prev_close
        return ok, "价格从布林上轨上方向下回落" if ok else "未形成布林上轨回落", details

    if pattern_key in ("vwap_bullish_reclaim", "vwap_bearish_loss"):
        vwap = calculate_vwap(work)
        prev_close, curr_close = float(close.iloc[-2]), float(close.iloc[-1])
        prev_vwap, curr_vwap = _latest_value(vwap, 2), _latest_value(vwap, 1)
        details["vwap"] = _format_price(curr_vwap)
        if None in (prev_vwap, curr_vwap):
            return False, "成交量加权均价数据不足", details
        if pattern_key == "vwap_bullish_reclaim":
            ok = _crossed_up(prev_close, prev_vwap, curr_close, curr_vwap)
            return ok, "收盘价向上收复成交量加权均价" if ok else "未上穿成交量加权均价", details
        ok = _crossed_down(prev_close, prev_vwap, curr_close, curr_vwap)
        return ok, "收盘价跌破成交量加权均价" if ok else "未下破成交量加权均价", details

    if pattern_key in ("atr_up_breakout", "atr_down_breakdown"):
        period = int(params.get("period", 14))
        multiplier = float(params.get("multiplier", 1.0))
        atr = calculate_atr(work, period=period)
        current_atr = _latest_value(atr, 1)
        lookback_high = float(work["high"].iloc[-period - 1:-1].max())
        lookback_low = float(work["low"].iloc[-period - 1:-1].min())
        curr_close = float(close.iloc[-1])
        prev_close = float(close.iloc[-2])
        details.update({
            "atr": _format_price(current_atr),
            "lookback_high": _format_price(lookback_high),
            "lookback_low": _format_price(lookback_low),
        })
        if current_atr is None or current_atr <= 0:
            return False, "ATR数据不足", details
        if pattern_key == "atr_up_breakout":
            ok = curr_close > lookback_high and (curr_close - prev_close) >= current_atr * multiplier
            return ok, "收盘价放大波幅突破前高" if ok else "未形成ATR向上突破", details
        ok = curr_close < lookback_low and (prev_close - curr_close) >= current_atr * multiplier
        return ok, "收盘价放大波幅跌破前低" if ok else "未形成ATR向下跌破", details

    if pattern_key in ("kdj_bullish_cross", "kdj_low_bullish_cross", "kdj_bearish_cross"):
        period = int(params.get("period", 9))
        low_threshold = float(params.get("low_threshold", 30.0))
        k, d, j = calculate_kdj(work, period=period)
        prev_k, curr_k = _latest_value(k, 2), _latest_value(k, 1)
        prev_d, curr_d = _latest_value(d, 2), _latest_value(d, 1)
        details.update({
            "k": _format_price(curr_k),
            "d": _format_price(curr_d),
            "j": _format_price(_latest_value(j, 1)),
            "low_threshold": low_threshold if pattern_key == "kdj_low_bullish_cross" else None,
        })
        if None in (prev_k, curr_k, prev_d, curr_d):
            return False, "KDJ数据不足", details
        if pattern_key in ("kdj_bullish_cross", "kdj_low_bullish_cross"):
            ok = _crossed_up(prev_k, prev_d, curr_k, curr_d)
            if ok and pattern_key == "kdj_low_bullish_cross":
                ok = min(prev_k, prev_d, curr_k, curr_d) <= low_threshold
                return ok, "低位K线向上穿越D线" if ok else "KDJ金叉不在低位区间", details
            return ok, "K线向上穿越D线" if ok else "未形成KDJ金叉", details
        ok = _crossed_down(prev_k, prev_d, curr_k, curr_d)
        return ok, "K线向下穿越D线" if ok else "未形成KDJ死叉", details

    if pattern_key in ("rsi_bullish_rebound", "rsi_bearish_pullback"):
        period = int(params.get("period", 14))
        low_threshold = float(params.get("low_threshold", 30.0))
        high_threshold = float(params.get("high_threshold", 70.0))
        rsi = calculate_rsi(close, period=period)
        prev_rsi, curr_rsi = _latest_value(rsi, 2), _latest_value(rsi, 1)
        details["rsi"] = _format_price(curr_rsi)
        if None in (prev_rsi, curr_rsi):
            return False, "RSI数据不足", details
        if pattern_key == "rsi_bullish_rebound":
            ok = prev_rsi <= low_threshold and curr_rsi > low_threshold
            return ok, "RSI从超卖区向上回升" if ok else "未形成RSI超卖回升", details
        ok = prev_rsi >= high_threshold and curr_rsi < high_threshold
        return ok, "RSI从超买区向下回落" if ok else "未形成RSI超买回落", details

    if pattern_key in ("volume_price_breakout", "volume_price_breakdown"):
        lookback = int(params.get("lookback", 20))
        volume_multiplier = float(params.get("volume_multiplier", 1.5))
        prior = work.iloc[-lookback - 1:-1]
        avg_volume = float(prior["volume"].astype(float).mean())
        curr_volume = float(work.iloc[-1]["volume"])
        curr_close = float(close.iloc[-1])
        details.update({
            "avg_volume": _format_price(avg_volume),
            "current_volume": _format_price(curr_volume),
            "volume_multiplier": round(curr_volume / avg_volume, 4) if avg_volume > 0 else None,
        })
        if avg_volume <= 0:
            return False, "成交量数据不足", details
        if pattern_key == "volume_price_breakout":
            prior_high = float(prior["high"].max())
            details["prior_high"] = _format_price(prior_high)
            ok = curr_close > prior_high and curr_volume >= avg_volume * volume_multiplier
            return ok, "收盘价突破前高且成交量放大" if ok else "未形成放量突破", details
        prior_low = float(prior["low"].min())
        details["prior_low"] = _format_price(prior_low)
        ok = curr_close < prior_low and curr_volume >= avg_volume * volume_multiplier
        return ok, "收盘价跌破前低且成交量放大" if ok else "未形成放量跌破", details

    return False, "未实现的辅助线策略", details


def analyze_technical_pattern(
    df: pd.DataFrame,
    pattern_key: str,
    check_date: date | None = None,
    **params: Any,
) -> TechnicalPatternSignal:
    """
    分析常见蜡烛图、K线与辅助线原子规则。

    pattern_key 使用 TECHNICAL_PATTERN_DEFINITIONS 中的键。返回统一的中文标签、
    方向、是否命中、原因与关键指标，供规则链和报告复用。
    """
    pattern_key = str(pattern_key or "").strip()
    definition = TECHNICAL_PATTERN_DEFINITIONS.get(pattern_key)
    if not definition:
        return TechnicalPatternSignal(
            pattern_key=pattern_key,
            pattern_label=pattern_key or "未知规则",
            direction="neutral",
            satisfied=False,
            reason=f"未知技术形态规则: {pattern_key}",
            details={"pattern_key": pattern_key, "pattern_label": pattern_key or "未知规则"},
            data_rows=0,
        )

    work, error_msg = _prepare_technical_pattern_data(
        df,
        check_date=check_date,
        require_volume=bool(definition.get("volume")),
    )
    if work is None:
        return _technical_signal(pattern_key, False, error_msg, work)

    min_rows = int(params.get("min_rows", definition.get("min_rows", 1)))
    if len(work) < min_rows:
        return _technical_signal(pattern_key, False, f"K线数据不足，至少需要{min_rows}根", work)

    candlestick_keys = {
        "bullish_engulfing",
        "bearish_engulfing",
        "hammer_reversal",
        "shooting_star_reversal",
        "morning_star",
        "evening_star",
        "piercing_line",
        "dark_cloud_cover",
        "three_white_soldiers",
        "three_black_crows",
        "doji_indecision",
        "bullish_marubozu",
        "bearish_marubozu",
    }
    try:
        if pattern_key in candlestick_keys:
            satisfied, reason, details = _analyze_candlestick_pattern(pattern_key, work, params)
        else:
            satisfied, reason, details = _analyze_indicator_pattern(pattern_key, work, params)
    except Exception as exc:
        return _technical_signal(pattern_key, False, f"技术形态计算失败: {exc}", work, {"error": True})

    return _technical_signal(pattern_key, satisfied, reason, work, details)


def check_ema_breakout(
    df: pd.DataFrame,
    check_date: date | None = None,
    ema_short: int = 10,
    ema_long: int = 150,
) -> Tuple[EMABreakoutResult, Optional[date], Optional[float], Optional[float]]:
    """
    检查 EMA 向上突破条件

    判断逻辑：
    1. 计算 EMA_short 和 EMA_long
    2. 取最近 3 根 K 线，检查是否发生向上突破（T-1 或 T-2）
    3. 向上突破定义：前一根 EMA_short <= EMA_long，当根 EMA_short > EMA_long

    Args:
        df: K 线 DataFrame，需包含 'date' 和 'close' 列（支持日线和分钟线）
        check_date: 截止日期（可选，None 则使用全部数据）
        ema_short: 短期 EMA 周期，默认 10
        ema_long: 长期 EMA 周期，默认 150
    Returns:
        (result, breakout_date, ema_short_value, ema_long_value)
    """
    # 验证数据
    is_valid, error_msg = _validate_kline_data(df)
    if not is_valid:
        return EMABreakoutResult.INVALID_DATA, None, None, None

    # 预处理数据
    df = _prepare_kline_data(df)

    # 需要至少 ema_long 个数据点 + 2 根回溯
    min_required = ema_long + 2
    if len(df) < min_required:
        return EMABreakoutResult.INSUFFICIENT_DATA, None, None, None

    # 计算 EMA
    df["ema_short"] = calculate_ema(df["close"], ema_short)
    df["ema_long"] = calculate_ema(df["close"], ema_long)

    # 按 check_date 截断（兼容分钟线 datetime 和日线 date）
    if check_date is not None:
        df = df[df["date"].dt.date <= check_date]
    if len(df) < 3:
        return EMABreakoutResult.INSUFFICIENT_DATA, None, None, None

    recent = df.tail(3).reset_index(drop=True)  # t-2, t-1, t
    t2, t1, t0 = recent.iloc[0], recent.iloc[1], recent.iloc[2]

    ema_short_val = float(t0["ema_short"])
    ema_long_val = float(t0["ema_long"])

    def _crossed_up(prev, curr) -> bool:
        return prev["ema_short"] <= prev["ema_long"] and curr["ema_short"] > curr["ema_long"]

    if _crossed_up(t1, t0):
        return EMABreakoutResult.BREAKOUT_T1, t0["date"].date(), ema_short_val, ema_long_val
    if _crossed_up(t2, t1):
        return EMABreakoutResult.BREAKOUT_T2, t1["date"].date(), ema_short_val, ema_long_val

    if ema_short_val >= ema_long_val:
        return EMABreakoutResult.NO_BREAKOUT_ALREADY_ABOVE, None, ema_short_val, ema_long_val

    return EMABreakoutResult.NO_BREAKOUT_BELOW, None, ema_short_val, ema_long_val


def analyze_stock_ema_breakout(
    market: str,
    code: str,
    df: pd.DataFrame,
    check_date: date | None = None,
) -> EMABreakoutSignal:
    """
    分析单只股票的 EMA 突破情况

    Args:
        market: 市场（HK/US/A）
        code: 股票代码
        df: K 线数据（日线或分钟线均可）
        check_date: 截止日期（None 则使用 K 线中最新日期）

    Returns:
        EMABreakoutSignal 对象
    """
    # 自动推断 check_date
    if check_date is None:
        if df is not None and not df.empty and "date" in df.columns:
            check_date = pd.to_datetime(df["date"]).dt.date.max()
        else:
            check_date = date.today()

    result, breakout_date, ema10, ema150 = check_ema_breakout(
        df=df,
        check_date=check_date,
        ema_short=10,
        ema_long=150,
    )
    
    # 获取最新收盘价
    close_price = None
    data_rows = 0
    if df is not None and not df.empty and "close" in df.columns:
        data_rows = len(df)
        valid_closes = df[df["close"].notna() & (df["close"] > 0)]
        if not valid_closes.empty:
            close_price = float(valid_closes.iloc[-1]["close"])
    
    return EMABreakoutSignal(
        market=market,
        code=code,
        check_date=check_date,
        result=result,
        breakout_date=breakout_date,
        ema10=ema10,
        ema150=ema150,
        close_price=close_price,
        data_rows=data_rows,
    )


def _prepare_zuoyi_kline_data(df: pd.DataFrame) -> Tuple[Optional[pd.DataFrame], str]:
    """
    预处理左一战法所需 K 线数据。

    左一战法依赖 high / low / close 的区间关系和收盘突破，不能只验证 close。
    """
    if df is None or df.empty:
        return None, "K线数据为空"

    required_columns = ["date", "high", "low", "close"]
    missing = [c for c in required_columns if c not in df.columns]
    if missing:
        return None, f"缺少字段: {', '.join(missing)}"

    work = df.copy()
    work["date"] = pd.to_datetime(work["date"], errors="coerce")
    for col in ["high", "low", "close"]:
        work[col] = pd.to_numeric(work[col], errors="coerce")

    valid = (
        work["date"].notna()
        & work["high"].notna()
        & work["low"].notna()
        & work["close"].notna()
        & (work["high"] > 0)
        & (work["low"] > 0)
        & (work["close"] > 0)
        & (work["high"] >= work["low"])
    )
    work = work[valid].sort_values("date").reset_index(drop=True)
    if work.empty:
        return None, "无有效 high/low/close K线"

    return work, ""


def _has_kline_containment(left: pd.Series, right: pd.Series) -> bool:
    """判断两根 K 线是否存在包含关系。"""
    left_contains_right = left["high"] >= right["high"] and left["low"] <= right["low"]
    right_contains_left = right["high"] >= left["high"] and right["low"] <= left["low"]
    return bool(left_contains_right or right_contains_left)


def _find_left_one_index(df: pd.DataFrame, median_idx: int) -> Optional[int]:
    """从中位线向左寻找第一根与中位线无包含关系的 K 线。"""
    median = df.iloc[median_idx]
    for idx in range(median_idx - 1, -1, -1):
        candidate = df.iloc[idx]
        if not _has_kline_containment(candidate, median):
            return idx
    return None


def _find_bullish_median_candidates(df: pd.DataFrame) -> List[int]:
    """
    寻找下降途中可作为中位线的 K 线。

    下降途中不断寻找更低低点；同低时取高点更高者。
    """
    candidates: List[int] = []
    lowest_low: Optional[float] = None
    same_low_best_high: Optional[float] = None

    for idx, row in df.iterrows():
        low = float(row["low"])
        high = float(row["high"])
        if lowest_low is None:
            lowest_low = low
            same_low_best_high = high
            continue

        if low < lowest_low:
            lowest_low = low
            same_low_best_high = high
            candidates.append(idx)
        elif low == lowest_low and same_low_best_high is not None and high > same_low_best_high:
            same_low_best_high = high
            candidates.append(idx)

    return candidates


def _find_bearish_median_candidates(df: pd.DataFrame) -> List[int]:
    """
    寻找上涨途中可作为中位线的 K 线。

    上涨途中不断寻找更高高点；同高时取低点更低者。
    """
    candidates: List[int] = []
    highest_high: Optional[float] = None
    same_high_best_low: Optional[float] = None

    for idx, row in df.iterrows():
        high = float(row["high"])
        low = float(row["low"])
        if highest_high is None:
            highest_high = high
            same_high_best_low = low
            continue

        if high > highest_high:
            highest_high = high
            same_high_best_low = low
            candidates.append(idx)
        elif high == highest_high and same_high_best_low is not None and low < same_high_best_low:
            same_high_best_low = low
            candidates.append(idx)

    return candidates


def _build_zuoyi_signal(
    df: pd.DataFrame,
    direction: str,
    median_idx: int,
    left_one_idx: int,
    breakout_idx: int,
) -> ZuoYiSignal:
    left_one = df.iloc[left_one_idx]
    median = df.iloc[median_idx]
    breakout = df.iloc[breakout_idx]
    latest = df.iloc[-1]
    return ZuoYiSignal(
        direction=direction,
        left_one_date=left_one["date"].date(),
        median_date=median["date"].date(),
        breakout_date=breakout["date"].date(),
        bars_to_breakout=breakout_idx - median_idx,
        left_one_high=float(left_one["high"]),
        left_one_low=float(left_one["low"]),
        median_high=float(median["high"]),
        median_low=float(median["low"]),
        breakout_close=float(breakout["close"]),
        latest_close=float(latest["close"]),
    )


def _find_recent_zuoyi_signal(
    df: pd.DataFrame,
    direction: str,
    median_candidates: List[int],
    signal_window: int,
) -> Optional[ZuoYiSignal]:
    """
    寻找最近的左一突破信号。

    信号必须满足：
    - 突破发生在中位线之后 signal_window 根 K 线内；
    - 突破本身也发生在最近 signal_window 根 K 线内，避免历史信号长期保留。
    """
    latest_idx = len(df) - 1

    for median_idx in reversed(median_candidates):
        if median_idx >= latest_idx:
            continue

        left_one_idx = _find_left_one_index(df, median_idx)
        if left_one_idx is None:
            continue

        left_one = df.iloc[left_one_idx]
        end_idx = min(latest_idx, median_idx + signal_window)
        for breakout_idx in range(median_idx + 1, end_idx + 1):
            if latest_idx - breakout_idx >= signal_window:
                continue

            close = float(df.iloc[breakout_idx]["close"])
            if direction == "bullish" and close > float(left_one["high"]):
                return _build_zuoyi_signal(df, direction, median_idx, left_one_idx, breakout_idx)
            if direction == "bearish" and close < float(left_one["low"]):
                return _build_zuoyi_signal(df, direction, median_idx, left_one_idx, breakout_idx)

    return None


def check_zuoyi_strategy(
    df: pd.DataFrame,
    check_date: date | None = None,
    signal_window: int = 15,
    include_bullish: bool = True,
    include_bearish: bool = True,
) -> ZuoYiAnalysis:
    """
    检查左一战法信号。

    规则：
    - 上涨途中：中位线取更高高点，同高取低点更低的 K 线，用于看跌跌破左一低点。
    - 下降途中：中位线取更低低点，同低取高点更高的 K 线，用于看涨突破左一高点。
    - 左一 K 线：中位线左侧第一根与中位线不具备包含关系的 K 线。
    - 有效信号：中位线之后 signal_window 根 K 线内，收盘价突破左一高点或跌破左一低点。
    """
    if signal_window <= 0:
        return ZuoYiAnalysis(
            satisfied=False,
            result_type="invalid_data",
            reason="signal_window 必须大于 0",
        )

    work, error_msg = _prepare_zuoyi_kline_data(df)
    if work is None:
        return ZuoYiAnalysis(
            satisfied=False,
            result_type="invalid_data",
            reason=error_msg,
        )

    if check_date is not None:
        work = work[work["date"].dt.date <= check_date].reset_index(drop=True)

    if len(work) < 3:
        return ZuoYiAnalysis(
            satisfied=False,
            result_type="insufficient_data",
            reason="K线数据不足，至少需要左一、中位线、突破K线",
            data_rows=len(work),
        )

    signals: List[ZuoYiSignal] = []
    if include_bullish:
        bullish_signal = _find_recent_zuoyi_signal(
            work,
            direction="bullish",
            median_candidates=_find_bullish_median_candidates(work),
            signal_window=signal_window,
        )
        if bullish_signal is not None:
            signals.append(bullish_signal)

    if include_bearish:
        bearish_signal = _find_recent_zuoyi_signal(
            work,
            direction="bearish",
            median_candidates=_find_bearish_median_candidates(work),
            signal_window=signal_window,
        )
        if bearish_signal is not None:
            signals.append(bearish_signal)

    latest_close = float(work.iloc[-1]["close"])
    if not signals:
        return ZuoYiAnalysis(
            satisfied=False,
            result_type="no_signal",
            reason=f"最近{signal_window}根K线内无左一战法有效突破",
            data_rows=len(work),
            latest_close=latest_close,
        )

    directions = {s.direction for s in signals}
    if directions == {"bullish", "bearish"}:
        result_type = "both_breakout"
        reason = "同时出现左一战法看涨与看跌信号"
    elif "bullish" in directions:
        result_type = "bullish_breakout"
        reason = f"左一战法看涨：收盘价在{signal_window}根K线内突破左一高点"
    else:
        result_type = "bearish_breakdown"
        reason = f"左一战法看跌：收盘价在{signal_window}根K线内跌破左一低点"

    return ZuoYiAnalysis(
        satisfied=True,
        result_type=result_type,
        reason=reason,
        signals=signals,
        data_rows=len(work),
        latest_close=latest_close,
    )


def calculate_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """
    计算 RSI（相对强弱指数），使用 Wilder 平滑。

    Args:
        close: 收盘价序列
        period: RSI 周期，默认 14

    Returns:
        pd.Series: RSI 值序列，范围 [0, 100]
    """
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    # Wilder 平滑: alpha = 1/period
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False).mean()
    rs = avg_gain / avg_loss
    # avg_loss==0 时 RSI=100
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return rsi


def compute_daily_volume_vs_prior3_and_pct_change(
    df: pd.DataFrame,
    check_date: date | None = None,
) -> Tuple[
    Optional[float],
    Optional[float],
    Optional[float],
    Optional[float],
    Optional[float],
]:
    """
    基于日线 K 线（最后一根视为当日）计算：
    - 当日成交量 vs 前三日成交量最大值
    - 当日涨跌幅（相对前一日收盘，百分比）

    Returns:
        (today_vol, max_vol_prior3, pct_change, prev_close, today_close)
        任一步数据不足则对应为 None；pct_change 为百分数如 -6.2 表示跌 6.2%。
    """
    if df is None or df.empty:
        return None, None, None, None, None
    if "close" not in df.columns:
        return None, None, None, None, None
    if "volume" not in df.columns:
        return None, None, None, None, None

    is_valid, _ = _validate_kline_data(df)
    if not is_valid:
        return None, None, None, None, None

    work = _prepare_kline_data(df)
    if check_date is not None:
        work = work[work["date"].dt.date <= check_date]
    if len(work) < 4:
        return None, None, None, None, None

    try:
        vol_series = work["volume"].astype(float)
        close_series = work["close"].astype(float)
    except (ValueError, TypeError):
        return None, None, None, None, None

    prior3_vol = vol_series.iloc[-4:-1].values
    today_vol_raw = float(vol_series.iloc[-1])
    prev_close = float(close_series.iloc[-2])
    today_close = float(close_series.iloc[-1])

    if today_vol_raw <= 0 or any(v <= 0 for v in prior3_vol):
        return None, None, None, prev_close, today_close

    max_prior3 = float(max(prior3_vol))
    pct_change = (today_close - prev_close) / prev_close * 100.0 if prev_close > 0 else None
    return today_vol_raw, max_prior3, pct_change, prev_close, today_close


def get_latest_rsi(df: pd.DataFrame, period: int = 14, check_date: date | None = None) -> Optional[float]:
    """
    从 K 线数据计算最新一根 K 线的 RSI(period)。

    Args:
        df: K 线 DataFrame，需包含 'close'、'date' 列
        period: RSI 周期，默认 14
        check_date: 截止日期（None 则用全部数据取最后一根）

    Returns:
        最新 RSI 值，数据不足或无效时返回 None
    """
    if df is None or df.empty or "close" not in df.columns:
        return None
    df = df.copy()
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        if check_date is not None:
            df = df[df["date"].dt.date <= check_date]
    if len(df) < period + 1:
        return None
    close = df["close"].astype(float)
    rsi_series = calculate_rsi(close, period=period)
    last_val = rsi_series.iloc[-1]
    if pd.isna(last_val):
        return None
    return float(last_val)
