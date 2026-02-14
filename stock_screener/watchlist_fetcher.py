#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
自选股列表获取：Futu OpenAPI get_user_security + DB 兜底

市场与 Futu 分组名：港股=HK, A股=CN, 美股=US
参考: https://openapi.futunn.com/futu-api-doc/quote/get-user-security.html
"""

from typing import Any, List, Optional

from market import normalize_market


# Futu 每 30 秒最多 10 次，内存缓存避免频繁调用
_cache: dict = {}
_cache_ttl_sec = 25


def _market_to_group_name(market: str) -> str:
    """内部市场 -> Futu 自选股分组名"""
    m = normalize_market(market)
    return {"HK": "HK", "A": "CN", "US": "US"}.get(m, "HK")


def _normalize_code_from_futu(code: str, market: str) -> str:
    """Futu code 转内部格式。HK.00700 -> HK.00700, SH.600000 -> 600000.SS"""
    if not code:
        return code
    market = normalize_market(market)
    if market == "A":
        if code.startswith("SH."):
            return code[3:] + ".SS"
        if code.startswith("SZ."):
            return code[3:] + ".SZ"
    return code


def fetch_watchlist_from_futu(
    quote_ctx: Any,
    market: str,
) -> List[dict]:
    """
    通过 Futu get_user_security 获取自选股列表。

    Args:
        quote_ctx: futu OpenQuoteContext
        market: HK / A / US

    Returns:
        [{"code": "...", "name": "..."}, ...]，内部 code 格式
    """
    import time as _time

    try:
        import futu as ft
    except Exception:
        return []

    market = normalize_market(market)
    group_name = _market_to_group_name(market)

    try:
        ret, data = quote_ctx.get_user_security(group_name)
        if ret != ft.RET_OK or data is None:
            return []
        if data.empty:
            return []
        if "code" not in data.columns or "name" not in data.columns:
            return []
        out = []
        for _, row in data.iterrows():
            raw_code = str(row.get("code", ""))
            name = row.get("name")
            if name is None or (isinstance(name, float) and str(name) == "nan"):
                name = raw_code
            else:
                name = str(name).strip()
            code = _normalize_code_from_futu(raw_code, market)
            out.append({"code": code, "name": name or code})
        return out
    except Exception:
        return []


def get_watchlist(
    market: str,
    quote_ctx: Optional[Any] = None,
    db: Optional[Any] = None,
    use_cache: bool = True,
) -> tuple[List[dict], bool]:
    """
    获取自选股列表：先尝试 Futu，失败则从 DB 兜底。

    Args:
        market: HK / A / US
        quote_ctx: Futu OpenQuoteContext，None 则只用 DB
        db: MarketDatabase，用于读写 watchlist_cache
        use_cache: 是否使用内存缓存（减少 Futu 调用频率）

    Returns:
        (stocks, from_futu): 列表与是否来自 Futu（否则来自 DB 缓存）
    """
    import time as _time

    market = normalize_market(market)
    if use_cache and market in _cache:
        t, data, from_futu = _cache[market]
        if _time.time() - t < _cache_ttl_sec:
            return list(data), from_futu

    stocks: List[dict] = []
    from_futu = False

    if quote_ctx:
        stocks = fetch_watchlist_from_futu(quote_ctx, market)
        if stocks:
            from_futu = True
            if db:
                try:
                    db.upsert_watchlist_cache(market, stocks)
                except Exception:
                    pass

    if not stocks and db:
        try:
            stocks = db.get_watchlist_cache(market)
        except Exception:
            pass

    if use_cache:
        _cache[market] = (_time.time(), stocks, from_futu)

    return stocks, from_futu
