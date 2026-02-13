#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
市场列表 API
"""

from fastapi import APIRouter

from market import MARKET_CONFIG

router = APIRouter()


@router.get("/markets")
async def get_markets():
    """
    获取市场列表
    
    Returns:
        市场列表，格式：[{"value": "HK", "label": "港股"}, ...]
    """
    markets = []
    for code, config in MARKET_CONFIG.items():
        markets.append({
            "value": code,
            "label": config.get("label", code)
        })
    return {"markets": markets}
