#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stable topology symbol helpers."""
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple


SUPPORTED_QUOTE_MARKETS = {"A", "HK", "US", "JP", "TW", "KR"}
_A_EXCHANGES = {"SH", "SZ", "BJ"}
_US_EXCHANGES = {"US", "NYSE", "NASDAQ", "AMEX"}
_JP_EXCHANGES = {"JP", "TY", "TSE"}
_KR_EXCHANGES = {"KR", "KS", "KQ"}


def normalize_topology_market(market: str) -> str:
    return str(market or "").strip().upper() or "A"


def bare_code(market: str, code: str) -> str:
    normalized_market = normalize_topology_market(market)
    value = str(code or "").strip().upper()
    if normalized_market == "HK" and value.startswith("HK."):
        return value[3:]
    if normalized_market == "US" and value.startswith("US."):
        return value[3:]
    if normalized_market == "A" and value.startswith(("SH.", "SZ.", "BJ.")):
        return value[3:]
    return value


def symbol_id(market: str, code: str) -> str:
    normalized_market = normalize_topology_market(market)
    return f"{normalized_market}:{bare_code(normalized_market, code)}"


def parse_topology_symbol(raw_symbol: str) -> Dict[str, Any]:
    raw = str(raw_symbol or "").strip().upper()
    if ":" in raw:
        market, code = raw.split(":", 1)
    else:
        market, code = "", raw
    raw_market = normalize_topology_market(market)
    raw_code = str(code or "").strip().upper()
    symbol = f"{raw_market}:{raw_code}" if raw_market else raw_code

    normalized = _normalize_quote_symbol(raw_market, raw_code)
    if normalized is None:
        return {
            "symbol": symbol,
            "market": raw_market,
            "code": raw_code,
            "exchange": raw_market,
            "provider_symbols": {},
            "status": "skipped",
            "skipped": True,
            "error": f"unsupported market: {raw_market}",
        }
    market_key, normalized_code, exchange, provider_symbols = normalized
    return {
        "symbol": symbol,
        "market": market_key,
        "code": normalized_code,
        "exchange": exchange,
        "provider_symbols": provider_symbols,
        "status": "pending",
        "skipped": False,
    }


def _normalize_quote_symbol(market: str, code: str) -> Optional[Tuple[str, str, str, Dict[str, str]]]:
    exchange = _exchange_from(market, code)
    bare = _strip_known_suffixes(_strip_known_prefixes(code))
    if not bare:
        return None

    if market in _A_EXCHANGES or exchange in _A_EXCHANGES or market == "A":
        a_exchange = exchange if exchange in _A_EXCHANGES else _infer_a_exchange(bare)
        normalized_code = f"{a_exchange}.{bare.zfill(6) if bare.isdigit() else bare}"
        return "A", normalized_code, a_exchange, {
            "futu": normalized_code,
            "yfinance": _a_yfinance_symbol(a_exchange, bare),
            "eastmoney": normalized_code,
        }

    if market == "HK" or exchange == "HK":
        digits = bare.zfill(5) if bare.isdigit() else bare
        normalized_code = f"HK.{digits}"
        return "HK", normalized_code, "HK", {
            "futu": normalized_code,
            "yfinance": f"{digits}.HK",
            "eastmoney": normalized_code,
        }

    if market in _US_EXCHANGES or exchange in _US_EXCHANGES:
        ticker = bare.split(".", 1)[0]
        normalized_code = f"US.{ticker}"
        return "US", normalized_code, exchange if exchange in _US_EXCHANGES else "US", {
            "futu": normalized_code,
            "yfinance": ticker,
            "eastmoney": normalized_code,
        }

    if market in _JP_EXCHANGES or exchange in _JP_EXCHANGES:
        ticker = bare.split(".", 1)[0]
        return "JP", f"JP.{ticker}", exchange if exchange in _JP_EXCHANGES else "JP", {"yfinance": f"{ticker}.T"}

    if market == "TW" or exchange == "TW":
        ticker = bare.split(".", 1)[0]
        return "TW", f"TW.{ticker}", "TW", {"yfinance": f"{ticker}.TW"}

    if market in _KR_EXCHANGES or exchange in _KR_EXCHANGES:
        ticker = bare.split(".", 1)[0].zfill(6) if bare.split(".", 1)[0].isdigit() else bare.split(".", 1)[0]
        suffix = "KQ" if (market == "KQ" or exchange == "KQ") else "KS"
        return "KR", f"KR.{ticker}", exchange if exchange in _KR_EXCHANGES else "KR", {"yfinance": f"{ticker}.{suffix}"}

    return None


def _exchange_from(market: str, code: str) -> str:
    if "." in code:
        prefix, suffix = code.split(".", 1)[0], code.rsplit(".", 1)[-1]
        if prefix in _A_EXCHANGES or prefix in {"HK", "US"}:
            return prefix
        if suffix:
            suffix = suffix.upper()
            if suffix == "SS":
                return "SH"
            if suffix in {"SZ", "SH", "BJ", "HK", "US", "NYSE", "NASDAQ", "AMEX", "TY", "T", "TW", "KS", "KQ"}:
                return "JP" if suffix in {"TY", "T"} else suffix
    return market


def _strip_known_prefixes(code: str) -> str:
    for prefix in ("SH.", "SZ.", "BJ.", "HK.", "US.", "JP.", "TW.", "KR."):
        if code.startswith(prefix):
            return code[len(prefix):]
    return code


def _strip_known_suffixes(code: str) -> str:
    for suffix in (".SS", ".SZ", ".SH", ".BJ", ".HK", ".US", ".NYSE", ".NASDAQ", ".AMEX", ".TY", ".T", ".TW", ".KS", ".KQ"):
        if code.endswith(suffix):
            return code[:-len(suffix)]
    return code


def _infer_a_exchange(code: str) -> str:
    if code.startswith(("6", "9")):
        return "SH"
    if code.startswith(("8", "4")):
        return "BJ"
    return "SZ"


def _a_yfinance_symbol(exchange: str, code: str) -> str:
    suffix = "SS" if exchange == "SH" else exchange
    return f"{code.zfill(6) if code.isdigit() else code}.{suffix}"
