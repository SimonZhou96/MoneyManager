#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
参数解析器 - 解析用户输入的筛选指标

支持格式：
- ">50億" -> min_cap = 5e9
- "<100億" -> max_cap = 1e10
- "50~100億" -> min_cap = 5e9, max_cap = 1e10
- "1000>價格>5" -> min=5, max=1000
- "100>PE>10" -> min=10, max=100
- ">2000萬" -> min = 2e7

单位映射：
- 億/亿 = 1e8
- 萬/万 = 1e4
"""

import re
from typing import Optional, Tuple


# 单位映射
UNIT_MAP = {
    "億": 1e8,
    "亿": 1e8,
    "萬": 1e4,
    "万": 1e4,
}


def parse_numeric_with_unit(value: str) -> Optional[float]:
    """
    解析带单位的数值字符串
    
    Args:
        value: 例如 "50億"、"2000萬"、"100"
        
    Returns:
        解析后的数值，如果解析失败返回 None
    """
    if not value or not isinstance(value, str):
        return None
    
    value = value.strip()
    if not value:
        return None
    
    # 尝试匹配数值 + 单位
    for unit, multiplier in UNIT_MAP.items():
        if unit in value:
            # 提取数值部分
            num_part = value.replace(unit, "").strip()
            try:
                return float(num_part) * multiplier
            except (ValueError, TypeError):
                return None
    
    # 没有单位，直接解析数值
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def parse_range_expression(expr: str) -> Tuple[Optional[float], Optional[float]]:
    """
    解析范围表达式
    
    支持格式：
    - ">50億" -> (5e9, None)
    - ">=50億" -> (5e9, None)
    - "<100億" -> (None, 1e10)
    - "<=100億" -> (None, 1e10)
    - "50~100億" -> (5e9, 1e10)
    - "50-100億" -> (5e9, 1e10)
    - "1000>價格>5" -> (5, 1000)
    - "100>PE>10" -> (10, 100)
    
    Args:
        expr: 范围表达式字符串
        
    Returns:
        (min_value, max_value) 元组，没有则为 None
    """
    if not expr or not isinstance(expr, str):
        return None, None
    
    expr = expr.strip()
    if not expr:
        return None, None
    
    # 格式1: "1000>x>5" 或 "100>PE>10"
    pattern1 = r"([\d.]+)\s*>\s*[\u4e00-\u9fa5a-zA-Z]*\s*>\s*([\d.]+)"
    match = re.search(pattern1, expr)
    if match:
        max_val = parse_numeric_with_unit(match.group(1))
        min_val = parse_numeric_with_unit(match.group(2))
        return min_val, max_val
    
    # 格式2: "50~100億" 或 "50-100億"
    pattern2 = r"([\d.]+)\s*[~\-]\s*([\d.]+)"
    match = re.search(pattern2, expr)
    if match:
        min_val = parse_numeric_with_unit(match.group(1) + extract_unit(expr))
        max_val = parse_numeric_with_unit(match.group(2) + extract_unit(expr))
        return min_val, max_val
    
    # 格式3: ">50億" 或 ">=50億"
    pattern3 = r">=?\s*([\d.]+)"
    match = re.search(pattern3, expr)
    if match:
        min_val = parse_numeric_with_unit(match.group(1) + extract_unit(expr))
        return min_val, None
    
    # 格式4: "<100億" 或 "<=100億"
    pattern4 = r"<=?\s*([\d.]+)"
    match = re.search(pattern4, expr)
    if match:
        max_val = parse_numeric_with_unit(match.group(1) + extract_unit(expr))
        return None, max_val
    
    # 格式5: 单个数值（作为精确值或最小值）
    val = parse_numeric_with_unit(expr)
    if val is not None:
        return val, None
    
    return None, None


def extract_unit(expr: str) -> str:
    """
    从表达式中提取单位
    
    Args:
        expr: 表达式字符串
        
    Returns:
        单位字符串，如 "億"、"萬" 或空字符串
    """
    for unit in UNIT_MAP.keys():
        if unit in expr:
            return unit
    return ""


def parse_filter_params(params: dict) -> dict:
    """
    解析前端传来的筛选参数
    
    Args:
        params: 原始参数字典，可能包含字符串格式的指标
        
    Returns:
        解析后的参数字典，数值已转换
    """
    result = {}
    
    # 市场和 timeframe 直接复制
    if "market" in params:
        result["market"] = params["market"]
    if "timeframe" in params:
        result["timeframe"] = params["timeframe"]
    
    # 市值
    if "market_cap" in params and isinstance(params["market_cap"], str):
        min_val, max_val = parse_range_expression(params["market_cap"])
        if min_val is not None:
            result["market_cap_min"] = min_val
        if max_val is not None:
            result["market_cap_max"] = max_val
    else:
        if "market_cap_min" in params:
            val = parse_numeric_with_unit(str(params["market_cap_min"]))
            if val is not None:
                result["market_cap_min"] = val
        if "market_cap_max" in params:
            val = parse_numeric_with_unit(str(params["market_cap_max"]))
            if val is not None:
                result["market_cap_max"] = val
    
    # 每日平均交易量
    if "avg_daily_volume" in params and isinstance(params["avg_daily_volume"], str):
        min_val, max_val = parse_range_expression(params["avg_daily_volume"])
        if min_val is not None:
            result["avg_daily_volume_min"] = min_val
        if max_val is not None:
            result["avg_daily_volume_max"] = max_val
    else:
        if "avg_daily_volume_min" in params:
            val = parse_numeric_with_unit(str(params["avg_daily_volume_min"]))
            if val is not None:
                result["avg_daily_volume_min"] = val
        if "avg_daily_volume_max" in params:
            val = parse_numeric_with_unit(str(params["avg_daily_volume_max"]))
            if val is not None:
                result["avg_daily_volume_max"] = val
    
    # 股票价格
    if "price" in params and isinstance(params["price"], str):
        min_val, max_val = parse_range_expression(params["price"])
        if min_val is not None:
            result["price_min"] = min_val
        if max_val is not None:
            result["price_max"] = max_val
    else:
        if "price_min" in params:
            val = parse_numeric_with_unit(str(params["price_min"]))
            if val is not None:
                result["price_min"] = val
        if "price_max" in params:
            val = parse_numeric_with_unit(str(params["price_max"]))
            if val is not None:
                result["price_max"] = val
    
    # 市盈率
    if "pe" in params and isinstance(params["pe"], str):
        min_val, max_val = parse_range_expression(params["pe"])
        if min_val is not None:
            result["pe_min"] = min_val
        if max_val is not None:
            result["pe_max"] = max_val
    else:
        if "pe_min" in params:
            val = parse_numeric_with_unit(str(params["pe_min"]))
            if val is not None:
                result["pe_min"] = val
        if "pe_max" in params:
            val = parse_numeric_with_unit(str(params["pe_max"]))
            if val is not None:
                result["pe_max"] = val
    
    # 公司有盈利
    if "require_profitable" in params:
        result["require_profitable"] = bool(params["require_profitable"])

    # 策略与其它 API 字段（parse_filter_params 原先未透传，会导致策略开关丢失）
    for key in (
        "use_ema_breakout",
        "ema_short",
        "ema_long",
        "rsi_period",
        "rsi_oversold_threshold",
        "rsi_overbought_threshold",
        "use_volume_spike_vs_prior3",
        "use_daily_drop_band",
        "use_daily_rise_band",
    ):
        if key in params:
            result[key] = params[key]

    return result


def get_default_params() -> dict:
    """
    获取默认筛选参数
    
    Returns:
        默认参数字典
    """
    return {
        "market_cap_min": 5e9,  # 50亿
        "avg_daily_volume_min": 2e7,  # 2000万
        "price_min": 5,
        "price_max": 1000,
        "pe_min": 10,
        "pe_max": 100,
        "require_profitable": True,
    }


# 示例和测试
if __name__ == "__main__":
    # 测试解析
    test_cases = [
        (">50億", (5e9, None)),
        (">=50亿", (5e9, None)),
        ("<100億", (None, 1e10)),
        ("50~100億", (5e9, 1e10)),
        ("50-100亿", (5e9, 1e10)),
        (">2000萬", (2e7, None)),
        ("1000>價格>5", (5.0, 1000.0)),
        ("100>PE>10", (10.0, 100.0)),
        ("100", (100.0, None)),
    ]
    
    print("测试范围表达式解析：")
    for expr, expected in test_cases:
        result = parse_range_expression(expr)
        status = "✓" if result == expected else "✗"
        print(f"{status} {expr:20} -> {result}")
    
    print("\n测试参数解析：")
    params = {
        "market": "HK",
        "timeframe": "1d",
        "market_cap": ">50億",
        "avg_daily_volume": ">2000萬",
        "price": "1000>價格>5",
        "pe": "100>PE>10",
        "require_profitable": True,
    }
    result = parse_filter_params(params)
    print(f"输入: {params}")
    print(f"输出: {result}")
