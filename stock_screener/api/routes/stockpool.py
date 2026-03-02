#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
股票池 API
"""

import os
from fastapi import APIRouter, HTTPException
from typing import Optional

from db import MarketDatabase, MySqlConfig
from market import normalize_market

router = APIRouter()


def get_mysql_config() -> MySqlConfig:
    """获取 MySQL 配置"""
    return MySqlConfig(
        host=os.getenv("MYSQL_HOST", "127.0.0.1"),
        port=int(os.getenv("MYSQL_PORT", "3306")),
        user=os.getenv("MYSQL_USER", "root"),
        password=os.getenv("MYSQL_PASSWORD", "123456"),
        database=os.getenv("MYSQL_DATABASE", "market_data"),
    )


@router.get("/stock-pools")
async def get_stock_pools(market: str, pool_type: str, limit: Optional[int] = None):
    """
    获取股票池数据

    Args:
        market: 市场 (HK/US/A)
        pool_type: 池类型 (best/index/industry/ipo/etf)
        limit: 限制返回数量（可选）

    Returns:
        {
            "stocks": [...],
            "total": 数量,
            "last_update": {...}
        }
    """
    market = normalize_market(market)

    if pool_type not in ["best", "index", "industry", "ipo", "etf"]:
        raise HTTPException(status_code=400, detail=f"Invalid pool_type: {pool_type}")

    db_config = get_mysql_config()
    db = MarketDatabase(db_config)

    try:
        # 获取最后更新信息
        last_update = db.get_pool_last_update(market, pool_type)

        if not last_update:
            # 无数据时返回 200 + 空数组，便于前端展示友好提示
            return {
                "stocks": [],
                "total": 0,
                "last_update": None
            }

        # 获取股票池数据
        stocks = db.get_stock_pool(market, pool_type, limit=limit)

        return {
            "stocks": stocks,
            "total": len(stocks),
            "last_update": last_update
        }

    finally:
        db.close()


@router.get("/stock-pools/types")
async def get_pool_types():
    """
    获取所有股票池类型及其说明

    Returns:
        [
            {"value": "best", "label": "最好股票", "description": "..."},
            ...
        ]
    """
    return [
        {
            "value": "best",
            "label": "最好股票",
            "description": "基于市值、价格、PE、成交量筛选的优质股票"
        },
        {
            "value": "index",
            "label": "指数成份股",
            "description": "主要指数的成份股（如恒生指数、恒生科技等）"
        },
        {
            "value": "industry",
            "label": "行业龙头",
            "description": "各行业按市值排名前5的股票"
        },
        {
            "value": "ipo",
            "label": "新股",
            "description": "最近两年上市的股票"
        },
        {
            "value": "etf",
            "label": "ETF列表",
            "description": "所有ETF和REIT基金"
        }
    ]


@router.get("/stock-pools/stats")
async def get_pool_stats(market: str):
    """
    获取指定市场所有股票池的统计信息

    Args:
        market: 市场 (HK/US/A)

    Returns:
        {
            "market": "HK",
            "pools": [
                {"pool_type": "best", "count": 338, "last_update": "...", "status": "success"},
                ...
            ]
        }
    """
    market = normalize_market(market)

    db_config = get_mysql_config()
    db = MarketDatabase(db_config)

    try:
        pool_types = ["best", "index", "industry", "ipo", "etf"]
        pools = []

        for pool_type in pool_types:
            last_update = db.get_pool_last_update(market, pool_type)
            if last_update:
                pools.append({
                    "pool_type": pool_type,
                    "count": last_update["stock_count"],
                    "last_update": last_update["update_time"],
                    "status": last_update["status"]
                })

        return {
            "market": market,
            "pools": pools
        }

    finally:
        db.close()
