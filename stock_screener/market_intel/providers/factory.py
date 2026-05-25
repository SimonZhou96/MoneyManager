from __future__ import annotations

import os
from typing import List

from market_intel.providers.base import MarketIntelProvider
from market_intel.providers.cailianpress import CailianpressIntelProvider
from market_intel.providers.eastmoney import EastmoneyMarketIntelProvider
from market_intel.providers.global_index import GlobalIndexProvider
from market_intel.providers.sina import SinaNewsIntelProvider
from market_intel.providers.source_registry import config_by_provider, enabled_provider_order
from market_intel.providers.tradingview import TradingViewNewsIntelProvider


def build_market_intel_providers(enable_live: bool | None = None) -> List[MarketIntelProvider]:
    if enable_live is None:
        enable_live = _env_flag("MARKET_INTEL_ENABLE_LIVE_PROVIDERS")
    if not enable_live:
        return []

    timeout_sec = _env_float("MARKET_INTEL_PROVIDER_TIMEOUT_SEC", 5.0)
    detail_limit = _env_int("MARKET_INTEL_TRADINGVIEW_DETAIL_LIMIT", 5)
    configs = config_by_provider()
    providers: List[MarketIntelProvider] = []
    for provider_name in enabled_provider_order():
        config = configs.get(provider_name)
        if config is None or not config.enabled():
            continue
        provider = _build_provider(provider_name, timeout_sec=timeout_sec, detail_limit=detail_limit)
        if provider is not None and provider.is_available:
            providers.append(provider)
    return providers


def _build_provider(provider_name: str, *, timeout_sec: float, detail_limit: int):
    if provider_name == "cailianpress":
        return CailianpressIntelProvider(timeout_sec=timeout_sec)
    if provider_name == "sina":
        return SinaNewsIntelProvider(timeout_sec=timeout_sec)
    if provider_name == "tradingview":
        return TradingViewNewsIntelProvider(timeout_sec=timeout_sec, detail_limit=detail_limit)
    if provider_name == "eastmoney":
        return EastmoneyMarketIntelProvider(timeout_sec=timeout_sec)
    if provider_name == "global_index":
        return GlobalIndexProvider(timeout_sec=timeout_sec)
    return None


def _env_flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default
