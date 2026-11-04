#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Best-effort stock display name enrichment.

The screener receives names from multiple data providers. Some providers return
English names for HK/A shares even when a Chinese display name is available.
This module keeps the rule centralized: prefer Chinese names for HK/A; fall back
to the existing English name when no Chinese name can be found.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional

from market import normalize_market


_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")


@dataclass(frozen=True)
class StockNameInfo:
    code: str
    name: str
    source: str = ""


class StockNameProvider(ABC):
    """Boundary for any source that can provide stock display names."""

    @abstractmethod
    def resolve(self, market: str, codes: List[str]) -> Dict[str, StockNameInfo]:
        """Return name info keyed by normalized screener code."""


class DatabaseStockNameProvider(StockNameProvider):
    """Resolve names from local `stocks` and `stock_pools` tables."""

    def __init__(self, db: Any):
        self.db = db

    def resolve(self, market: str, codes: List[str]) -> Dict[str, StockNameInfo]:
        market = normalize_market(market)
        query_codes = _expand_code_aliases(market, codes)
        resolved: Dict[str, StockNameInfo] = {}

        if hasattr(self.db, "get_stocks_by_codes"):
            for item in self.db.get_stocks_by_codes(market, query_codes, include_fundamentals=False):
                _put_candidate(resolved, market, item.get("code"), item.get("name"), "stocks")

        if hasattr(self.db, "get_stock_pool_records_by_codes"):
            for item in self.db.get_stock_pool_records_by_codes(market, query_codes):
                _put_candidate(resolved, market, item.get("code"), item.get("name"), "stock_pools")

        return resolved


class AkshareStockNameProvider(StockNameProvider):
    """Resolve HK/A Chinese names from AkShare when available."""

    def resolve(self, market: str, codes: List[str]) -> Dict[str, StockNameInfo]:
        market = normalize_market(market)
        if market not in {"A", "HK"}:
            return {}

        resolved: Dict[str, StockNameInfo] = {}
        if market == "A":
            resolved.update(_fetch_a_code_names_from_akshare())
            resolved.update(_fetch_a_etf_names_from_akshare())
            wanted = {_normalize_code(market, code) for code in codes}
            return {code: info for code, info in resolved.items() if code in wanted}

        try:
            from universe import fetch_stock_list_akshare
        except Exception:
            return {}

        try:
            for item in fetch_stock_list_akshare(market):
                _put_candidate(resolved, market, item.get("code"), item.get("name"), "akshare")
        except Exception:
            pass

        wanted = {_normalize_code(market, code) for code in codes}
        return {code: info for code, info in resolved.items() if code in wanted}


class StockNameResolver:
    """Apply provider results to records with Chinese-name preference."""

    def __init__(self, providers: Iterable[StockNameProvider]):
        self.providers = list(providers)

    @classmethod
    def default(cls, db: Optional[Any] = None, include_external: bool = True) -> "StockNameResolver":
        providers: List[StockNameProvider] = []
        if db is not None:
            providers.append(DatabaseStockNameProvider(db))
        if include_external:
            providers.append(AkshareStockNameProvider())
        return cls(providers)

    def resolve(self, market: str, codes: List[str]) -> Dict[str, StockNameInfo]:
        market = normalize_market(market)
        merged: Dict[str, StockNameInfo] = {}
        for provider in self.providers:
            for code, info in provider.resolve(market, codes).items():
                if not info.name:
                    continue
                current = merged.get(code)
                if current is None or _is_better_name(info.name, current.name):
                    merged[code] = info
        return merged

    def enrich_records(self, market: str, records: List[dict]) -> List[dict]:
        market = normalize_market(market)
        if market not in {"A", "HK"}:
            return records

        codes = [(record.get("code") or "").strip() for record in records]
        name_map = self.resolve(market, codes)
        for record in records:
            code = _normalize_code(market, record.get("code"))
            current_name = str(record.get("name") or "").strip()
            if _contains_chinese(current_name):
                continue
            info = name_map.get(code)
            if info and info.name and (_contains_chinese(info.name) or not current_name):
                record["name"] = info.name
                record["name_source"] = info.source
        return records


def _contains_chinese(value: str) -> bool:
    return bool(_CJK_RE.search(value or ""))


def _is_better_name(candidate: str, current: str) -> bool:
    if _contains_chinese(candidate) and not _contains_chinese(current):
        return True
    if _contains_chinese(candidate) == _contains_chinese(current):
        return len(candidate.strip()) < len(current.strip()) if current else True
    return False


def _normalize_code(market: str, code: Any) -> str:
    market = normalize_market(market)
    text = str(code or "").strip().upper()
    if not text:
        return ""
    if market == "HK":
        if text.startswith("HK."):
            text = text[3:]
        if text.endswith(".HK"):
            text = text[:-3]
        return f"HK.{text.zfill(5)}" if text.isdigit() else text
    if market == "A":
        if text.startswith("SH.") or text.startswith("SZ."):
            return text
        if text.endswith(".SS"):
            return f"SH.{text[:-3]}"
        if text.endswith(".SZ"):
            return f"SZ.{text[:-3]}"
        if text.isdigit() and len(text) == 6:
            if text.startswith(("5", "6", "9")):
                return f"SH.{text}"
            if text.startswith(("0", "1", "2", "3")):
                return f"SZ.{text}"
        return text
    if market == "US" and text.startswith("US."):
        return text
    return text


def _code_aliases(market: str, code: str) -> List[str]:
    normalized = _normalize_code(market, code)
    if not normalized:
        return []
    if market == "HK" and normalized.startswith("HK."):
        bare = normalized[3:]
        return [normalized, bare, f"{bare}.HK"]
    if market == "A" and normalized.startswith(("SH.", "SZ.")):
        bare = normalized[3:]
        suffix = ".SS" if normalized.startswith("SH.") else ".SZ"
        return [normalized, bare, f"{bare}{suffix}"]
    return [normalized]


def _expand_code_aliases(market: str, codes: Iterable[str]) -> List[str]:
    aliases: List[str] = []
    seen = set()
    for code in codes:
        for alias in _code_aliases(market, str(code)):
            if alias and alias not in seen:
                seen.add(alias)
                aliases.append(alias)
    return aliases


def _put_candidate(
    target: Dict[str, StockNameInfo],
    market: str,
    code: Any,
    name: Any,
    source: str,
) -> None:
    normalized = _normalize_code(market, code)
    display_name = str(name or "").strip()
    if not normalized or not display_name:
        return
    candidate = StockNameInfo(code=normalized, name=display_name, source=source)
    current = target.get(normalized)
    if current is None or _is_better_name(candidate.name, current.name):
        target[normalized] = candidate


def _find_column(columns: Iterable[str], keywords: Iterable[str]) -> Optional[str]:
    for column in columns:
        lower = str(column).lower()
        for keyword in keywords:
            if keyword in lower or keyword in str(column):
                return column
    return None


def _fetch_a_etf_names_from_akshare() -> Dict[str, StockNameInfo]:
    try:
        import akshare as ak
    except Exception:
        return {}
    if not hasattr(ak, "fund_etf_spot_em"):
        return {}
    try:
        data = ak.fund_etf_spot_em()
    except Exception:
        return {}
    if data is None or len(data) == 0:
        return {}
    code_col = _find_column(data.columns, ["代码", "code", "基金代码"])
    name_col = _find_column(data.columns, ["名称", "简称", "基金简称", "name"])
    if not code_col or not name_col:
        return {}
    result: Dict[str, StockNameInfo] = {}
    for _, row in data.iterrows():
        _put_candidate(result, "A", row.get(code_col), row.get(name_col), "akshare_etf")
    return result


def _fetch_a_code_names_from_akshare() -> Dict[str, StockNameInfo]:
    try:
        import akshare as ak
    except Exception:
        return {}
    if not hasattr(ak, "stock_info_a_code_name"):
        return {}
    try:
        data = ak.stock_info_a_code_name()
    except Exception:
        return {}
    if data is None or len(data) == 0:
        return {}
    code_col = _find_column(data.columns, ["代码", "code", "A股代码"])
    name_col = _find_column(data.columns, ["名称", "简称", "name", "证券简称", "A股简称"])
    if not code_col or not name_col:
        return {}
    result: Dict[str, StockNameInfo] = {}
    for _, row in data.iterrows():
        _put_candidate(result, "A", row.get(code_col), row.get(name_col), "akshare_name")
    return result
