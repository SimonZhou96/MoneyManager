#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Timeframe 配置模块

支持的周期：
- 分钟级：1m, 2m, 5m, 15m, 30m, 60m, 90m
- 小时级：1h
- 日级及以上：1d, 5d, 1wk, 1mo, 3mo
"""

from __future__ import annotations

# 所有支持的 timeframe
VALID_TIMEFRAMES = {
    "1m", "2m", "5m", "15m", "30m", "60m", "90m",
    "1h",
    "1d", "5d", "1wk", "1mo", "3mo",
}

# timeframe -> yfinance 推荐的 period（最大回溯区间）
# 分钟级数据 yfinance 限制较严格
_YF_PERIOD_MAP = {
    "1m": "7d",
    "2m": "60d",
    "5m": "60d",
    "15m": "60d",
    "30m": "60d",
    "60m": "60d",
    "90m": "60d",
    "1h": "730d",
    "1d": "5y",
    "5d": "5y",
    "1wk": "5y",
    "1mo": "5y",
    "3mo": "5y",
}

# timeframe -> AKShare A 股分钟周期 period 参数
# AKShare stock_zh_a_hist_min_em 只支持 "1","5","15","30","60"
_AKSHARE_MIN_PERIOD_MAP = {
    "1m": "1",
    "5m": "5",
    "15m": "15",
    "30m": "30",
    "60m": "60",
}

# EMA 策略需要的最少 K 线根数（EMA150 + 2 根回溯）
MIN_BARS_FOR_EMA = 152


def parse_timeframe(s: str) -> str:
    """
    校验并标准化 timeframe 字符串

    Args:
        s: 用户传入的 timeframe（如 "1h", "1d"）

    Returns:
        标准化后的 timeframe

    Raises:
        ValueError: 如果不在支持列表中
    """
    tf = str(s).strip().lower()
    if tf not in VALID_TIMEFRAMES:
        raise ValueError(
            f"不支持的 timeframe: '{s}'，可选: {sorted(VALID_TIMEFRAMES)}"
        )
    return tf


def is_intraday(timeframe: str) -> bool:
    """是否为日内级别（分钟 / 小时）"""
    return timeframe in {
        "1m", "2m", "5m", "15m", "30m", "60m", "90m", "1h",
    }


def get_yf_period(timeframe: str) -> str:
    """获取 yfinance 对应的 period 参数"""
    return _YF_PERIOD_MAP.get(timeframe, "5y")


def get_akshare_min_period(timeframe: str) -> str | None:
    """
    获取 AKShare A 股分钟线的 period 参数。
    不支持的 timeframe 返回 None。
    """
    return _AKSHARE_MIN_PERIOD_MAP.get(timeframe)


def timeframe_to_table_suffix(timeframe: str) -> str:
    """
    将 timeframe 转为数据库表名后缀。
    例如 "1d" -> "1d", "1h" -> "1h", "5m" -> "5m"
    """
    return timeframe.replace(" ", "")
