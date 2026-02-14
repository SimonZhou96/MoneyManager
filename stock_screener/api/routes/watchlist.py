#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
自选股列表 API：Futu get_user_security + DB 兜底
"""

import os
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query

from db import MarketDatabase, MySqlConfig
from market import normalize_market
from watchlist_fetcher import get_watchlist

router = APIRouter()


def _get_mysql_config() -> MySqlConfig:
    return MySqlConfig(
        host=os.getenv("MYSQL_HOST", "127.0.0.1"),
        port=int(os.getenv("MYSQL_PORT", "3306")),
        user=os.getenv("MYSQL_USER", "root"),
        password=os.getenv("MYSQL_PASSWORD", "123456"),
        database=os.getenv("MYSQL_DATABASE", "market_data"),
    )


def _create_quote_ctx():
    """创建 Futu OpenQuoteContext，失败返回 None"""
    try:
        import futu as ft
        host = os.getenv("FUTU_HOST", "127.0.0.1")
        port = int(os.getenv("FUTU_PORT", "11111"))
        return ft.OpenQuoteContext(host=host, port=port)
    except Exception:
        return None


@router.get("/watchlist")
async def api_get_watchlist(
    market: str = Query(..., description="市场: HK, A, US；多市场逗号分隔如 HK,A,US"),
):
    """
    获取自选股列表。先尝试 Futu OpenAPI，失败则返回 DB 缓存。
    返回 from_cache=true 表示来自 DB 兜底。
    """
    markets_raw = [s.strip().upper() for s in market.split(",") if s.strip()]
    if not markets_raw:
        raise HTTPException(status_code=400, detail="market 不能为空")
    valid = {"HK", "A", "US"}
    markets = [normalize_market(m) for m in markets_raw if normalize_market(m) in valid]
    if not markets:
        raise HTTPException(status_code=400, detail="market 需为 HK、A、US 之一或组合")

    quote_ctx = _create_quote_ctx()
    mysql_config = _get_mysql_config()
    db = MarketDatabase(mysql_config)
    try:
        db.init_schema("1d")  # 确保 watchlist_cache 表存在
    except Exception:
        pass

    result = {}
    try:
        for m in markets:
            stocks, from_futu = get_watchlist(
                market=m,
                quote_ctx=quote_ctx,
                db=db,
                use_cache=True,
            )
            result[m] = {
                "stocks": stocks,
                "from_cache": not from_futu,
            }
    finally:
        if quote_ctx:
            try:
                quote_ctx.close()
            except Exception:
                pass
        db.close()

    if len(markets) == 1:
        m = markets[0]
        return {
            "market": m,
            "stocks": result[m]["stocks"],
            "from_cache": result[m]["from_cache"],
        }
    return {"markets": result}
