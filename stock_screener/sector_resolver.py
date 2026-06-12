#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Best-effort sector and industry enrichment for screened stocks.

The resolver is intentionally defensive: every provider may fail or return
partial data, and later providers only fill fields that are still empty. Sector
enrichment must never decide whether a stock passes screening.
"""

from __future__ import annotations

import json
import os
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from market import normalize_market
from stock_pool import CANONICAL_POOL_TYPES, POOL_TYPE_ALL_ETF


DEFAULT_POOL_TYPES = CANONICAL_POOL_TYPES


@dataclass(frozen=True)
class SectorInfo:
    code: str
    sector: str = ""
    sector_code: str = ""
    industry: str = ""
    industry_code: str = ""
    source: str = ""

    def has_any(self) -> bool:
        return bool(self.sector or self.industry or self.sector_code or self.industry_code)


class SectorProvider(ABC):
    """Provider interface used by SectorResolver."""

    name = "base"

    @abstractmethod
    def resolve(self, market: str, codes: List[str]) -> Dict[str, SectorInfo]:
        """Return sector information for the requested stock codes."""


class ManualSectorProvider(SectorProvider):
    """Manual sector mapping from env or JSON file.

    Supported JSON examples:
    {
      "HK": {
        "HK.00700": {"sector": "科技", "industry": "互联网"}
      },
      "US.AAPL": "Technology"
    }
    """

    name = "manual"

    def __init__(self, mapping: Optional[Dict[str, Any]] = None):
        self.mapping = mapping or self._load_mapping()

    def resolve(self, market: str, codes: List[str]) -> Dict[str, SectorInfo]:
        market = normalize_market(market)
        market_mapping = self.mapping.get(market, {}) if isinstance(self.mapping.get(market), dict) else {}
        result: Dict[str, SectorInfo] = {}
        for code in codes:
            raw = market_mapping.get(code)
            if raw is None:
                raw = self.mapping.get(code)
            info = _sector_info_from_value(code, raw, self.name)
            if info and info.has_any():
                result[code] = info
        return result

    @staticmethod
    def _load_mapping() -> Dict[str, Any]:
        mapping: Dict[str, Any] = {}
        path = os.getenv("STOCK_SECTOR_MANUAL_FILE", "").strip()
        if path:
            try:
                data = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    mapping.update(data)
            except Exception:
                pass
        raw = os.getenv("STOCK_SECTOR_MANUAL_JSON", "").strip()
        if raw:
            try:
                data = json.loads(raw)
                if isinstance(data, dict):
                    mapping.update(data)
            except Exception:
                pass
        return mapping


class DatabaseSectorProvider(SectorProvider):
    """Read sector fields and sector membership rows from MarketDatabase."""

    name = "database"

    def __init__(self, db: Any):
        self.db = db

    def resolve(self, market: str, codes: List[str]) -> Dict[str, SectorInfo]:
        if self.db is None or not codes:
            return {}

        result: Dict[str, SectorInfo] = {}
        try:
            rows = self.db.get_stocks_by_codes(market, codes, include_fundamentals=True)
        except Exception:
            rows = []
        for row in rows:
            code = _clean(row.get("code"))
            if not code:
                continue
            info = SectorInfo(
                code=code,
                sector=_clean(row.get("sector")),
                sector_code=_clean(row.get("sector_code")),
                industry=_clean(row.get("industry")),
                industry_code=_clean(row.get("industry_code")),
                source=self.name,
            )
            if info.has_any():
                result[code] = info

        if hasattr(self.db, "get_sector_memberships_by_codes"):
            try:
                memberships = self.db.get_sector_memberships_by_codes(market, codes)
            except Exception:
                memberships = {}
            for code, items in memberships.items():
                info = _info_from_memberships(code, items, self.name)
                if info and info.has_any():
                    result[code] = _merge_sector_info(result.get(code), info)
        return result


class StockPoolSectorProvider(SectorProvider):
    """Resolve from stock_pools industry_name and ETF membership."""

    name = "stock_pool"

    def __init__(self, db: Any, pool_types: Iterable[str] = DEFAULT_POOL_TYPES):
        self.db = db
        self.pool_types = tuple(pool_types)

    def resolve(self, market: str, codes: List[str]) -> Dict[str, SectorInfo]:
        if self.db is None or not codes:
            return {}
        wanted = set(codes)
        result: Dict[str, SectorInfo] = {}
        for pool_type in self.pool_types:
            try:
                rows = self.db.get_stock_pool(market, pool_type, limit=None)
            except Exception:
                continue
            for row in rows:
                code = _clean(row.get("code"))
                if code not in wanted:
                    continue
                if pool_type == POOL_TYPE_ALL_ETF:
                    info = SectorInfo(code=code, sector="ETF", industry="ETF", source=self.name)
                else:
                    industry = _clean(row.get("industry_name"))
                    industry_code = _clean(row.get("industry_code"))
                    if not industry:
                        continue
                    info = SectorInfo(
                        code=code,
                        sector=industry,
                        sector_code=industry_code,
                        industry=industry,
                        industry_code=industry_code,
                        source=self.name,
                    )
                result[code] = _merge_sector_info(result.get(code), info)
        return result


class FutuPlateSectorProvider(SectorProvider):
    """Resolve full industry plate memberships from Futu OpenD."""

    name = "futu_plate"

    def __init__(self, quote_ctx: Any = None, max_plates: Optional[int] = None):
        self.quote_ctx = quote_ctx
        self.max_plates = max_plates if max_plates is not None else _env_int("SECTOR_FUTU_MAX_PLATES", 300)

    def resolve(self, market: str, codes: List[str]) -> Dict[str, SectorInfo]:
        if self.quote_ctx is None or not codes:
            return {}
        try:
            import futu as ft
        except Exception:
            return {}

        market = normalize_market(market)
        market_map = {"HK": ft.Market.HK, "US": ft.Market.US, "A": ft.Market.SH}
        market_enum = market_map.get(market)
        if market_enum is None:
            return {}

        ret, plates = self.quote_ctx.get_plate_list(market_enum, ft.Plate.INDUSTRY)
        if ret != ft.RET_OK or plates is None:
            return {}

        wanted = set(codes)
        result: Dict[str, SectorInfo] = {}
        count = 0
        for _, plate in plates.iterrows():
            if count >= self.max_plates:
                break
            count += 1
            industry_code = _clean(plate.get("code"))
            industry_name = _clean(plate.get("plate_name"))
            if not industry_code or not industry_name:
                continue
            try:
                ret, members = self.quote_ctx.get_plate_stock(industry_code)
            except Exception:
                continue
            if ret != ft.RET_OK or members is None:
                continue
            for _, member in members.iterrows():
                code = _clean(member.get("code"))
                if code not in wanted:
                    continue
                result[code] = _merge_sector_info(
                    result.get(code),
                    SectorInfo(
                        code=code,
                        sector=industry_name,
                        sector_code=industry_code,
                        industry=industry_name,
                        industry_code=industry_code,
                        source=self.name,
                    ),
                )
        return result


class AkshareSectorProvider(SectorProvider):
    """Resolve A-share industry/concept board memberships from AKShare."""

    name = "akshare"

    def __init__(self, max_boards: Optional[int] = None, include_concepts: Optional[bool] = None):
        self.max_boards = max_boards if max_boards is not None else _env_int("SECTOR_AKSHARE_MAX_BOARDS", 180)
        self.include_concepts = (
            include_concepts
            if include_concepts is not None
            else _env_flag("SECTOR_AKSHARE_INCLUDE_CONCEPTS", True)
        )

    def resolve(self, market: str, codes: List[str]) -> Dict[str, SectorInfo]:
        if normalize_market(market) != "A" or not codes:
            return {}
        try:
            import akshare as ak
        except Exception:
            return {}

        wanted = {_normalize_a_code(code) for code in codes}
        wanted.discard("")
        code_by_plain = {_normalize_a_code(code): code for code in codes}
        result: Dict[str, SectorInfo] = {}

        board_sources = [
            ("industry", getattr(ak, "stock_board_industry_name_em", None), getattr(ak, "stock_board_industry_cons_em", None)),
        ]
        if self.include_concepts:
            board_sources.append(
                ("concept", getattr(ak, "stock_board_concept_name_em", None), getattr(ak, "stock_board_concept_cons_em", None))
            )

        checked = 0
        for sector_type, name_func, cons_func in board_sources:
            if name_func is None or cons_func is None:
                continue
            try:
                boards = name_func()
            except Exception:
                continue
            name_col = _find_column(getattr(boards, "columns", []), ["板块名称", "名称", "name"])
            code_col = _find_column(getattr(boards, "columns", []), ["板块代码", "代码", "code"])
            if not name_col:
                continue
            for _, board in boards.iterrows():
                if checked >= self.max_boards:
                    break
                checked += 1
                board_name = _clean(board.get(name_col))
                board_code = _clean(board.get(code_col)) if code_col else ""
                if not board_name:
                    continue
                try:
                    members = cons_func(symbol=board_name)
                except Exception:
                    continue
                member_code_col = _find_column(getattr(members, "columns", []), ["代码", "code", "股票代码"])
                if not member_code_col:
                    continue
                for _, member in members.iterrows():
                    plain = _normalize_a_code(member.get(member_code_col))
                    if plain not in wanted:
                        continue
                    original_code = code_by_plain.get(plain, plain)
                    if sector_type == "industry":
                        info = SectorInfo(
                            code=original_code,
                            sector=board_name,
                            sector_code=board_code,
                            industry=board_name,
                            industry_code=board_code,
                            source=self.name,
                        )
                    else:
                        info = SectorInfo(
                            code=original_code,
                            sector=board_name,
                            sector_code=board_code,
                            source=self.name,
                        )
                    result[original_code] = _merge_sector_info(result.get(original_code), info)
            if checked >= self.max_boards:
                break
        return result


class YahooFinanceSectorProvider(SectorProvider):
    """Resolve sector/industry from yfinance for US/HK and some A-share codes."""

    name = "yahoo_finance"

    def __init__(self, max_codes: Optional[int] = None):
        self.max_codes = max_codes if max_codes is not None else _env_int("SECTOR_YAHOO_MAX_CODES", 500)

    def resolve(self, market: str, codes: List[str]) -> Dict[str, SectorInfo]:
        if not codes:
            return {}
        try:
            import yfinance as yf
        except Exception:
            return {}

        from yf_ratelimit import per_ticker_sleep

        result: Dict[str, SectorInfo] = {}
        for code in codes[: self.max_codes]:
            symbol = _to_yahoo_symbol(market, code)
            if not symbol:
                continue
            per_ticker_sleep()  # 逐只 0.3s 延迟，避免触发频控
            try:
                info = yf.Ticker(symbol).info or {}
            except Exception:
                continue
            sector = _clean(info.get("sector"))
            industry = _clean(info.get("industry"))
            if not (sector or industry):
                continue
            result[code] = SectorInfo(
                code=code,
                sector=sector or industry,
                industry=industry,
                source=self.name,
            )
        return result


class SearchLLMSectorProvider(SectorProvider):
    """Weak fallback based on search snippets.

    This provider is disabled unless SECTOR_ENABLE_SEARCH_LLM is truthy. It
    intentionally does not overwrite API-provided sector fields.
    """

    name = "search_llm"

    def __init__(self, max_codes: Optional[int] = None):
        self.max_codes = max_codes if max_codes is not None else _env_int("SECTOR_SEARCH_MAX_CODES", 60)

    def resolve(self, market: str, codes: List[str]) -> Dict[str, SectorInfo]:
        if not _env_flag("SECTOR_ENABLE_SEARCH_LLM", False) or not codes:
            return {}
        try:
            from signal_analysis.factories import SearchProviderFactory
            from signal_analysis.models import AnalysisSettings
        except Exception:
            return {}
        provider = SearchProviderFactory.from_env(AnalysisSettings(search_max_results=2))
        if not provider.is_available:
            return {}

        result: Dict[str, SectorInfo] = {}
        for code in codes[: self.max_codes]:
            try:
                docs = provider.search(f"{code} stock sector industry company profile", 2)
            except Exception:
                continue
            sector = _guess_sector_from_text(" ".join(f"{doc.title} {doc.content}" for doc in docs))
            if sector:
                result[code] = SectorInfo(code=code, sector=sector, industry=sector, source=self.name)
        return result


class SectorResolver:
    """Merge sector information from multiple providers."""

    def __init__(self, providers: List[SectorProvider]):
        self.providers = providers

    @classmethod
    def default(
        cls,
        db: Any = None,
        quote_ctx: Any = None,
        include_external: bool = False,
    ) -> "SectorResolver":
        providers: List[SectorProvider] = [
            ManualSectorProvider(),
        ]
        if db is not None:
            providers.extend([DatabaseSectorProvider(db), StockPoolSectorProvider(db)])
        if quote_ctx is not None:
            providers.append(FutuPlateSectorProvider(quote_ctx))
        if include_external:
            providers.extend([AkshareSectorProvider(), YahooFinanceSectorProvider(), SearchLLMSectorProvider()])
        return cls(providers)

    def resolve(self, market: str, codes: List[str]) -> Dict[str, SectorInfo]:
        market = normalize_market(market)
        clean_codes = _dedupe([_clean(code) for code in codes if _clean(code)])
        result: Dict[str, SectorInfo] = {}
        for provider in self.providers:
            missing = [code for code in clean_codes if not _is_complete(result.get(code))]
            if not missing:
                break
            try:
                partial = provider.resolve(market, missing)
            except Exception:
                continue
            for code, info in partial.items():
                if not info or not info.has_any():
                    continue
                result[code] = _merge_sector_info(result.get(code), info)
        return result


def sector_info_to_stock_row(info: SectorInfo, name: str = "") -> Dict[str, Any]:
    return {
        "code": info.code,
        "name": name or info.code,
        "sector": info.sector or info.industry,
        "sector_code": info.sector_code or info.industry_code,
        "industry": info.industry or info.sector,
        "industry_code": info.industry_code or info.sector_code,
        "source": info.source,
    }


def sector_info_to_membership_rows(market: str, info: SectorInfo) -> List[Dict[str, Any]]:
    rows = []
    source = (info.source or "resolver")[:32]
    if info.sector:
        rows.append(
            {
                "market": market,
                "code": info.code,
                "sector_type": "sector",
                "sector_code": info.sector_code,
                "sector_name": info.sector,
                "source": source,
            }
        )
    if info.industry and info.industry != info.sector:
        rows.append(
            {
                "market": market,
                "code": info.code,
                "sector_type": "industry",
                "sector_code": info.industry_code,
                "sector_name": info.industry,
                "source": source,
            }
        )
    return rows


def _sector_info_from_value(code: str, raw: Any, source: str) -> Optional[SectorInfo]:
    if raw is None:
        return None
    if isinstance(raw, str):
        text = _clean(raw)
        if not text:
            return None
        return SectorInfo(code=code, sector=text, industry=text, source=source)
    if isinstance(raw, dict):
        return SectorInfo(
            code=code,
            sector=_clean(raw.get("sector")),
            sector_code=_clean(raw.get("sector_code")),
            industry=_clean(raw.get("industry")),
            industry_code=_clean(raw.get("industry_code")),
            source=_clean(raw.get("source")) or source,
        )
    return None


def _info_from_memberships(code: str, items: List[Dict[str, Any]], source: str) -> Optional[SectorInfo]:
    sector = sector_code = industry = industry_code = ""
    for item in items:
        sector_type = _clean(item.get("sector_type")).lower()
        name = _clean(item.get("sector_name"))
        code_value = _clean(item.get("sector_code"))
        if not name:
            continue
        if sector_type == "industry" and not industry:
            industry = name
            industry_code = code_value
        elif not sector:
            sector = name
            sector_code = code_value
    if not (sector or industry):
        return None
    return SectorInfo(
        code=code,
        sector=sector or industry,
        sector_code=sector_code or industry_code,
        industry=industry,
        industry_code=industry_code,
        source=source,
    )


def _merge_sector_info(existing: Optional[SectorInfo], incoming: SectorInfo) -> SectorInfo:
    if existing is None:
        return incoming
    source = existing.source
    if incoming.source and incoming.source not in source.split("|"):
        source = f"{source}|{incoming.source}" if source else incoming.source
    return SectorInfo(
        code=existing.code or incoming.code,
        sector=existing.sector or incoming.sector,
        sector_code=existing.sector_code or incoming.sector_code,
        industry=existing.industry or incoming.industry,
        industry_code=existing.industry_code or incoming.industry_code,
        source=source,
    )


def _is_complete(info: Optional[SectorInfo]) -> bool:
    return bool(info and info.sector and info.industry)


def _clean(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if text in {"-", "--", "nan", "NaN", "None", "null"}:
        return ""
    return text


def _dedupe(items: Iterable[str]) -> List[str]:
    seen = set()
    result = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return max(1, int(raw))
    except ValueError:
        return default


def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    return raw not in {"0", "false", "no", "off"}


def _find_column(columns: Iterable[str], keywords: Iterable[str]) -> Optional[str]:
    for col in columns:
        text = str(col)
        lower = text.lower()
        for keyword in keywords:
            if str(keyword).lower() in lower or str(keyword) in text:
                return text
    return None


def _normalize_a_code(value: Any) -> str:
    code = _clean(value).upper()
    if not code:
        return ""
    prefix_match = re.match(r"^(?:SH|SZ|BJ)\.(\d{6})$", code)
    if prefix_match:
        return prefix_match.group(1)
    suffix_match = re.match(r"^(\d{6})\.(?:SH|SZ|SS)$", code)
    if suffix_match:
        return suffix_match.group(1)
    digits = re.sub(r"\D", "", code)
    if len(digits) != 6:
        return ""
    return digits


def _to_yahoo_symbol(market: str, code: str) -> str:
    market = normalize_market(market)
    code = _clean(code)
    if not code:
        return ""
    if market == "US":
        return code[3:] if code.upper().startswith("US.") else code
    if market == "HK":
        value = code[3:] if code.upper().startswith("HK.") else code
        if value.isdigit():
            return f"{str(int(value)).zfill(4)}.HK"
        return value
    if market == "A":
        if code.endswith((".SS", ".SZ")):
            return code
        digits = _normalize_a_code(code)
        if digits.startswith("6"):
            return f"{digits}.SS"
        if digits.startswith(("0", "3")):
            return f"{digits}.SZ"
    return code


def _guess_sector_from_text(text: str) -> str:
    text = text or ""
    patterns = [
        r"(?:sector|industry)\s*[:：]\s*([A-Za-z\u4e00-\u9fff &/.-]{2,40})",
        r"所属(?:板块|行业)\s*[:：]\s*([A-Za-z\u4e00-\u9fff &/.-]{2,40})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return _clean(match.group(1))
    return ""
