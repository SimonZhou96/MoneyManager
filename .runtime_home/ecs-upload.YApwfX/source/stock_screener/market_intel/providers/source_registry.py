from __future__ import annotations

from dataclasses import dataclass, field
import os
from typing import Dict, List


@dataclass(frozen=True)
class SourceConfig:
    provider: str
    source: str
    reliability_tier: str
    markets: List[str]
    item_types: List[str]
    freshness_minutes: int
    requires_auth: bool = False
    requires_browser: bool = False
    default_enabled: bool = True
    automated_safe: bool = True
    env_flag: str = ""
    disabled_reason: str = ""
    aliases: List[str] = field(default_factory=list)

    def enabled(self) -> bool:
        if self.requires_browser and not _env_flag("MARKET_INTEL_BROWSER_PROVIDERS_ALLOWED", False):
            return False
        return _env_flag(self.env_flag, self.default_enabled)

    def to_dict(self) -> Dict[str, object]:
        enabled = self.enabled()
        disabled_reason = ""
        if not enabled:
            disabled_reason = self.disabled_reason
            if self.requires_browser and not _env_flag("MARKET_INTEL_BROWSER_PROVIDERS_ALLOWED", False):
                disabled_reason = "requires browser/cookie support"
            elif not disabled_reason:
                disabled_reason = "disabled by configuration"
        return {
            "provider": self.provider,
            "source": self.source,
            "reliability_tier": self.reliability_tier,
            "markets": list(self.markets),
            "item_types": list(self.item_types),
            "freshness_minutes": self.freshness_minutes,
            "requires_auth": self.requires_auth,
            "requires_browser": self.requires_browser,
            "default_enabled": self.default_enabled,
            "automated_safe": self.automated_safe,
            "enabled": enabled,
            "status": "enabled" if enabled else "disabled",
            "disabled_reason": disabled_reason,
        }


SOURCE_CONFIGS: List[SourceConfig] = [
    SourceConfig(
        provider="cailianpress",
        source="财联社",
        reliability_tier="high",
        markets=["A", "HK", "US"],
        item_types=["market_news", "news"],
        freshness_minutes=15,
        env_flag="MARKET_INTEL_ENABLE_CAILIANPRESS",
        aliases=["news", "cls"],
    ),
    SourceConfig(
        provider="sina",
        source="新浪财经",
        reliability_tier="medium",
        markets=["A", "HK", "US"],
        item_types=["market_news"],
        freshness_minutes=15,
        env_flag="MARKET_INTEL_ENABLE_SINA",
    ),
    SourceConfig(
        provider="tradingview",
        source="TradingView",
        reliability_tier="medium",
        markets=["A", "HK", "US"],
        item_types=["market_news", "news"],
        freshness_minutes=30,
        env_flag="MARKET_INTEL_ENABLE_TRADINGVIEW",
    ),
    SourceConfig(
        provider="eastmoney",
        source="东方财富",
        reliability_tier="high",
        markets=["A", "HK", "US"],
        item_types=["financial", "announcement", "research_report"],
        freshness_minutes=720,
        env_flag="MARKET_INTEL_ENABLE_EASTMONEY",
    ),
    SourceConfig(
        provider="global_index",
        source="东方财富指数",
        reliability_tier="medium",
        markets=["A", "HK", "US", "global"],
        item_types=["index_snapshot"],
        freshness_minutes=15,
        env_flag="MARKET_INTEL_ENABLE_GLOBAL_INDEX",
    ),
    SourceConfig(
        provider="iwencai",
        source="问财",
        reliability_tier="medium",
        markets=["A"],
        item_types=["news", "research_report", "search_document"],
        freshness_minutes=60,
        requires_auth=True,
        default_enabled=False,
        env_flag="MARKET_INTEL_ENABLE_IWENCAI",
        disabled_reason="request contract not verified",
    ),
    SourceConfig(
        provider="eastmoney_search",
        source="东方财富妙想",
        reliability_tier="medium",
        markets=["A", "HK", "US"],
        item_types=["news", "announcement", "research_report", "search_document"],
        freshness_minutes=60,
        requires_auth=True,
        default_enabled=False,
        env_flag="MARKET_INTEL_ENABLE_EASTMONEY_SEARCH",
        disabled_reason="request contract not verified",
    ),
    SourceConfig(
        provider="xueqiu",
        source="雪球",
        reliability_tier="low",
        markets=["A", "HK", "US"],
        item_types=["hot_stock", "hot_event", "news"],
        freshness_minutes=30,
        requires_auth=True,
        requires_browser=True,
        default_enabled=False,
        automated_safe=False,
        env_flag="MARKET_INTEL_ENABLE_XUEQIU",
        disabled_reason="requires browser/cookie support",
    ),
]


def list_source_configs() -> List[SourceConfig]:
    return list(SOURCE_CONFIGS)


def source_status_payload() -> List[Dict[str, object]]:
    return [item.to_dict() for item in SOURCE_CONFIGS]


def enabled_provider_order() -> List[str]:
    configured = os.getenv("MARKET_INTEL_PROVIDER_ORDER", "").strip()
    names = configured.split(",") if configured else [
        "cailianpress",
        "sina",
        "tradingview",
        "eastmoney",
        "global_index",
    ]
    return _dedupe_provider_names(names)


def config_by_provider() -> Dict[str, SourceConfig]:
    rows: Dict[str, SourceConfig] = {}
    for config in SOURCE_CONFIGS:
        rows[config.provider] = config
        for alias in config.aliases:
            rows[alias] = config
    return rows


def _dedupe_provider_names(raw_names) -> List[str]:
    configs = config_by_provider()
    names: List[str] = []
    seen = set()
    for raw_name in raw_names:
        key = str(raw_name).strip().lower()
        config = configs.get(key)
        if config is None or config.provider in seen:
            continue
        names.append(config.provider)
        seen.add(config.provider)
    return names


def _env_flag(name: str, default: bool) -> bool:
    if not name:
        return default
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}
