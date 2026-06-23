#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""节点行情聚合：名称/板块/市值/涨跌幅 + size 计算（不含 LLM）。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from web.single_stock import normalize_stock_code

# 市值分桶边界（单位：元）
_SIZE_BUCKETS = [
    50 * 1e8,    # 50亿
    200 * 1e8,   # 200亿
    1000 * 1e8,  # 1000亿
    3000 * 1e8,  # 3000亿
    1 * 1e12,    # 1万亿
]


def compute_size_level(market_cap: Optional[float]) -> int:
    """市值 → size_level 1-6。None → 1（最小）。"""
    if market_cap is None:
        return 1
    for i, boundary in enumerate(_SIZE_BUCKETS):
        if market_cap < boundary:
            return i + 1
    return 6


def format_market_cap(market_cap: Optional[float]) -> str:
    """市值 → 中文格式化字符串。"""
    if market_cap is None:
        return "--"
    if market_cap >= 1e12:
        return f"{market_cap / 1e12:.1f}万亿"
    if market_cap >= 1e8:
        return f"{market_cap / 1e8:.0f}亿"
    if market_cap >= 1e4:
        return f"{market_cap / 1e4:.0f}万"
    return str(int(market_cap))


class NodeResolver:
    """聚合单只股票的行情/板块/市值/涨跌幅。行情失败不抛，返回 None 字段。"""

    def __init__(self, db: Any):
        self.db = db

    def resolve(self, code: str, market: str) -> Dict[str, Any]:
        market = _normalize_market(market)
        norm_code = normalize_stock_code(market, code)
        rows: List[dict] = []
        if hasattr(self.db, "get_stocks_by_codes"):
            rows = self.db.get_stocks_by_codes(market, [norm_code], include_fundamentals=True)
        base = rows[0] if rows else {"code": norm_code, "name": "", "sector": "", "market_cap": None}
        pct_chg = self._fetch_pct_chg(norm_code, market)
        return {
            "code": base.get("code") or norm_code,
            "name": base.get("name") or "",
            "market": market,
            "sector": base.get("sector") or base.get("industry") or "--",
            "market_cap": base.get("market_cap"),
            "pct_chg": pct_chg,
        }

    def resolve_many(self, codes: List[str], market: str) -> Dict[str, Dict[str, Any]]:
        market = _normalize_market(market)
        norm = []
        for c in codes:
            try:
                norm.append(normalize_stock_code(market, c))
            except ValueError:
                continue
        rows: List[dict] = []
        if hasattr(self.db, "get_stocks_by_codes"):
            rows = self.db.get_stocks_by_codes(market, norm, include_fundamentals=True) if norm else []
        by_code = {r["code"]: r for r in rows}
        result = {}
        for c in norm:
            base = by_code.get(c, {"code": c, "name": "", "sector": "", "market_cap": None})
            result[c] = {
                "code": c,
                "name": base.get("name") or "",
                "market": market,
                "sector": base.get("sector") or base.get("industry") or "--",
                "market_cap": base.get("market_cap"),
                "pct_chg": self._fetch_pct_chg(c, market),
            }
        return result

    def _fetch_pct_chg(self, code: str, market: str) -> Optional[float]:
        """取最近一日涨跌幅（%）。失败返回 None，不抛。"""
        try:
            from kline_fetcher import KlineFetcherFactory
            fetchers = KlineFetcherFactory.create_fetcher_chain(db=self.db)
            for fetcher in fetchers:
                df = fetcher.fetch(code, market=market, timeframe="1d", max_count=2)
                if df is not None and not df.empty and "change_rate" in df.columns:
                    last = df["change_rate"].dropna()
                    if len(last):
                        return round(float(last.iloc[-1]), 2)
                    break
            return None
        except Exception:
            return None


def _normalize_market(market: str) -> str:
    m = (market or "").strip().upper()
    return m if m in ("HK", "US", "A") else "A"
