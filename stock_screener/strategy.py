#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
策略模块：EMA突破策略判断
支持判断 EMA10 向上突破 EMA150 的策略
"""

from dataclasses import dataclass
from datetime import date, timedelta
from enum import Enum
from typing import Optional, Tuple

import pandas as pd


class EMABreakoutResult(Enum):
    """EMA突破结果枚举
    
    满足条件的情况：
    - BREAKOUT_T1: 前一个交易日刚刚向上突破
    - BREAKOUT_T2: 前两个交易日刚刚向上突破
    
    不满足条件的情况：
    - NO_BREAKOUT_BELOW: EMA10 仍在 EMA150 下方，未发生突破
    - NO_BREAKOUT_ALREADY_ABOVE: EMA10 早已在 EMA150 上方（超过2个交易日）
    - NO_BREAKOUT_DOWNWARD: EMA10 向下跌破（从上方跌到下方）
    - INSUFFICIENT_DATA: 数据不足，无法计算 EMA
    - INVALID_DATA: 数据异常（如 close 价格为空或为负）
    """
    # 满足条件
    BREAKOUT_T1 = "breakout_t1"  # 前一个交易日向上突破
    BREAKOUT_T2 = "breakout_t2"  # 前两个交易日向上突破
    
    # 不满足条件
    NO_BREAKOUT_BELOW = "no_breakout_below"  # EMA10 仍在下方
    NO_BREAKOUT_ALREADY_ABOVE = "no_breakout_already_above"  # 早已在上方
    NO_BREAKOUT_DOWNWARD = "no_breakout_downward"  # 向下跌破
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
            EMABreakoutResult.NO_BREAKOUT_ALREADY_ABOVE: "EMA10早已在EMA150上方（超过2个交易日）",
            EMABreakoutResult.NO_BREAKOUT_DOWNWARD: "EMA10向下跌破EMA150",
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
    check_date: date,
    ema_short: int = 10,
    ema_long: int = 150,
    lookback_days: int = 2,
) -> Tuple[EMABreakoutResult, Optional[date], Optional[float], Optional[float]]:
    """
    检查 EMA 向上突破条件
    
    判断逻辑：
    1. 计算 EMA10 和 EMA150
    2. 检查最近 lookback_days 个交易日内是否发生向上突破
       （即 EMA10 从下方穿越到上方）
    3. 向上突破定义：前一天 EMA10 < EMA150，当天 EMA10 >= EMA150
    
    Args:
        df: K线数据 DataFrame，需包含 'date' 和 'close' 列
        check_date: 检查日期（一般是今天）
        ema_short: 短期 EMA 周期，默认 10
        ema_long: 长期 EMA 周期，默认 150
        lookback_days: 回溯天数，默认 2（检查前1天和前2天）
        
    Returns:
        (result, breakout_date, ema_short_value, ema_long_value)
    """
    # 验证数据
    is_valid, error_msg = _validate_kline_data(df)
    if not is_valid:
        return EMABreakoutResult.INVALID_DATA, None, None, None
    
    # 预处理数据
    df = _prepare_kline_data(df)
    
    # 需要至少 ema_long 个数据点才能计算有意义的 EMA150
    # 实际上 EMA 可以从第一个点开始计算，但前 ema_long 个点的值不够稳定
    # 我们要求至少有 ema_long + lookback_days 个点
    min_required = ema_long + lookback_days
    if len(df) < min_required:
        return EMABreakoutResult.INSUFFICIENT_DATA, None, None, None
    
    # 计算 EMA
    df["ema_short"] = calculate_ema(df["close"], ema_short)
    df["ema_long"] = calculate_ema(df["close"], ema_long)
    
    # 获取最近的数据点（按 check_date 筛选）
    df["date_only"] = df["date"].dt.date
    df_before_check = df[df["date_only"] <= check_date]
    
    if len(df_before_check) < lookback_days + 2:
        # 需要至少 lookback_days + 2 个点来判断趋势
        return EMABreakoutResult.INSUFFICIENT_DATA, None, None, None
    
    # 获取最后几个交易日的数据
    recent_data = df_before_check.tail(lookback_days + 2).reset_index(drop=True)
    
    # 当前最新一天的 EMA 值
    latest = recent_data.iloc[-1]
    ema_short_val = float(latest["ema_short"])
    ema_long_val = float(latest["ema_long"])
    
    # 判断突破情况
    # 检查每一天是否发生突破
    for i in range(len(recent_data) - 1, 0, -1):  # 从最新往前遍历
        current = recent_data.iloc[i]
        previous = recent_data.iloc[i - 1]
        
        current_above = current["ema_short"] >= current["ema_long"]
        previous_above = previous["ema_short"] >= previous["ema_long"]
        
        # 向上突破：前一天在下方，当天在上方或相等
        if current_above and not previous_above:
            breakout_date = current["date_only"]
            days_ago = len(recent_data) - 1 - i
            
            if days_ago == 0:
                # 今天突破（如果 check_date 是最新数据日期）
                return EMABreakoutResult.BREAKOUT_T1, breakout_date, ema_short_val, ema_long_val
            elif days_ago == 1:
                # 前一天突破
                return EMABreakoutResult.BREAKOUT_T1, breakout_date, ema_short_val, ema_long_val
            elif days_ago == 2:
                # 前两天突破
                return EMABreakoutResult.BREAKOUT_T2, breakout_date, ema_short_val, ema_long_val
            else:
                # 超过2天前突破，属于"早已在上方"
                break
        
        # 向下跌破：前一天在上方，当天在下方
        if not current_above and previous_above:
            # 最近发生了向下跌破
            if len(recent_data) - 1 - i <= lookback_days:
                return EMABreakoutResult.NO_BREAKOUT_DOWNWARD, None, ema_short_val, ema_long_val
    
    # 没有在 lookback_days 内发生突破，判断当前位置
    latest_above = recent_data.iloc[-1]["ema_short"] >= recent_data.iloc[-1]["ema_long"]
    
    if latest_above:
        # EMA10 在上方但不是刚刚突破 -> 早已在上方
        return EMABreakoutResult.NO_BREAKOUT_ALREADY_ABOVE, None, ema_short_val, ema_long_val
    else:
        # EMA10 在下方 -> 未突破
        return EMABreakoutResult.NO_BREAKOUT_BELOW, None, ema_short_val, ema_long_val


def analyze_stock_ema_breakout(
    market: str,
    code: str,
    df: pd.DataFrame,
    check_date: date,
) -> EMABreakoutSignal:
    """
    分析单只股票的 EMA 突破情况
    
    Args:
        market: 市场（HK/US）
        code: 股票代码
        df: K线数据
        check_date: 检查日期
        
    Returns:
        EMABreakoutSignal 对象
    """
    result, breakout_date, ema10, ema150 = check_ema_breakout(
        df=df,
        check_date=check_date,
        ema_short=10,
        ema_long=150,
        lookback_days=2,
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
