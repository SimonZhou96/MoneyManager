from __future__ import annotations

import os
from typing import List

from market_intel.providers.base import MarketIntelProvider
from market_intel.providers.eastmoney import EastmoneyMarketIntelProvider
from market_intel.providers.global_index import GlobalIndexProvider
from market_intel.providers.news import NewsIntelProvider


def build_market_intel_providers(enable_live: bool | None = None) -> List[MarketIntelProvider]:
    if enable_live is None:
        enable_live = _env_flag("MARKET_INTEL_ENABLE_LIVE_PROVIDERS")
    if not enable_live:
        return []

    timeout_sec = _env_float("MARKET_INTEL_PROVIDER_TIMEOUT_SEC", 5.0)
    providers: List[MarketIntelProvider] = [
        EastmoneyMarketIntelProvider(timeout_sec=timeout_sec),
        NewsIntelProvider(timeout_sec=timeout_sec),
        GlobalIndexProvider(timeout_sec=timeout_sec),
    ]
    return [provider for provider in providers if provider.is_available]


def _env_flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default
