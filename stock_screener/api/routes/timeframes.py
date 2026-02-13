#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Timeframe 列表 API
"""

from fastapi import APIRouter

from timeframe import VALID_TIMEFRAMES

router = APIRouter()


@router.get("/timeframes")
async def get_timeframes():
    """
    获取 timeframe 列表
    
    Returns:
        Timeframe 列表，格式：[{"value": "1d", "label": "1日"}, ...]
    """
    # 将 timeframe 转换为更友好的标签
    label_map = {
        "1m": "1分钟", "2m": "2分钟", "5m": "5分钟", "15m": "15分钟",
        "30m": "30分钟", "60m": "60分钟", "90m": "90分钟",
        "1h": "1小时",
        "1d": "1日", "5d": "5日", "1wk": "1周", "1mo": "1月", "3mo": "3月",
    }
    
    timeframes = []
    for tf in sorted(VALID_TIMEFRAMES):
        timeframes.append({
            "value": tf,
            "label": label_map.get(tf, tf)
        })
    
    return {"timeframes": timeframes}
