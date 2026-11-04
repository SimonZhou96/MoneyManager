#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""节点行情聚合：名称/板块/市值/涨跌幅 + size 计算（不含 LLM）。"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Tuple

from web.single_stock import normalize_stock_code

from .symbols import bare_code, normalize_topology_market, symbol_id

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

    def resolve(self, code: str, market: str, include_quote: bool = False, fallback_name: str = "") -> Dict[str, Any]:
        market = _normalize_market(market)
        norm_code = normalize_stock_code(market, code)
        rows: List[dict] = []
        if hasattr(self.db, "get_stocks_by_codes"):
            rows = self.db.get_stocks_by_codes(market, [norm_code], include_fundamentals=True)
            if not rows:
                bare = bare_code(market, norm_code)
                if bare != norm_code:
                    rows = self.db.get_stocks_by_codes(market, [bare], include_fundamentals=True)
        base = rows[0] if rows else {"code": norm_code, "name": "", "sector": "", "market_cap": None}
        name = base.get("name") or fallback_name or ""
        pct_chg = self._fetch_pct_chg(norm_code, market) if include_quote else None
        return {
            "code": base.get("code") or norm_code,
            "name": name,
            "market": market,
            "sector": base.get("sector") or base.get("industry") or "--",
            "market_cap": base.get("market_cap"),
            "pct_chg": pct_chg,
            "quote_status": "fresh" if pct_chg is not None else "pending",
        }

    def resolve_many(
        self,
        stocks: Iterable[Tuple[str, str]] | List[str],
        market: Optional[str] = None,
        include_quote: bool = False,
    ) -> Dict[str, Dict[str, Any]]:
        grouped: Dict[str, List[str]] = {}
        if market is not None:
            market = _normalize_market(market)
            stock_items = [(market, str(c)) for c in stocks]
        else:
            stock_items = [(str(m), str(c)) for m, c in stocks]  # type: ignore[misc]

        normalized_items: List[Tuple[str, str]] = []
        for raw_market, c in stock_items:
            item_market = _normalize_market(raw_market)
            try:
                norm_code = normalize_stock_code(item_market, c)
            except ValueError:
                continue
            normalized_items.append((item_market, norm_code))
            grouped.setdefault(item_market, []).append(norm_code)
            bare = bare_code(item_market, norm_code)
            if bare != norm_code:
                grouped[item_market].append(bare)

        by_symbol: Dict[str, dict] = {}
        if hasattr(self.db, "get_stocks_by_codes"):
            for item_market, codes in grouped.items():
                rows = self.db.get_stocks_by_codes(item_market, codes, include_fundamentals=True) if codes else []
                for row in rows:
                    key = symbol_id(item_market, bare_code(item_market, row["code"]))
                    if key not in by_symbol:
                        by_symbol[key] = row

        result = {}
        for item_market, c in normalized_items:
            symbol = symbol_id(item_market, c)
            bare_sym = symbol_id(item_market, bare_code(item_market, c))
            base = by_symbol.get(symbol) or by_symbol.get(bare_sym)
            if base is None:
                base = {"code": c, "name": "", "sector": "", "market_cap": None}
            pct_chg = self._fetch_pct_chg(c, item_market) if include_quote else None
            result[symbol] = {
                "code": c,
                "name": base.get("name") or "",
                "market": item_market,
                "sector": base.get("sector") or base.get("industry") or "--",
                "market_cap": base.get("market_cap"),
                "pct_chg": pct_chg,
                "quote_status": "fresh" if pct_chg is not None else "pending",
            }
        return result

    def _fetch_pct_chg(self, code: str, market: str) -> Optional[float]:
        """取最近一日涨跌幅（%）。失败返回 None，不抛。"""
        try:
            from kline_fetcher import managed_fetcher_chain
            with managed_fetcher_chain() as fetchers:
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
    return normalize_topology_market(market)
