#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
策略模块：EMA突破策略判断
支持判断 EMA10 向上突破 EMA150 的策略
"""

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import List, Optional, Tuple

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
    signal_window: int = 3,
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
