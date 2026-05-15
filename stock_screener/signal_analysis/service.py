#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Public service entrypoint for best-effort signal analysis."""

from __future__ import annotations

import os
from datetime import date
from typing import List, Optional

from db import MarketDatabase, MySqlConfig
from network_preflight import check_host_resolution

from .chain import SignalAnalysisChain, SignalAnalysisContext
from .factories import LLMProviderFactory, SearchProviderFactory
from .llm_providers import FallbackLLMProvider, LLMProvider, NullLLMProvider
from .hot_news import ManualHotNewsConfig
from .hot_sectors import ManualHotSectorConfig
from .models import AnalysisRunResult, AnalysisSettings
from .search_providers import NullSearchProvider, SearchProvider


def env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    return raw not in {"0", "false", "no", "off"}


def env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


class MySqlSignalAnalysisRepository:
    """Adapter between the analysis chain and MarketDatabase."""

    def __init__(self, mysql_config: MySqlConfig):
        self.mysql_config = mysql_config

    def save_results(self, rows: List[dict]) -> None:
        db = MarketDatabase(self.mysql_config)
        try:
            db.init_signal_analysis_schema()
            db.upsert_signal_analysis_results(rows)
        finally:
            db.close()


def settings_from_env() -> AnalysisSettings:
    return AnalysisSettings(
        batch_size=max(1, env_int("LLM_ANALYSIS_BATCH_SIZE", 20)),
        timeout_sec=max(1, env_int("LLM_ANALYSIS_TIMEOUT_SEC", 120)),
        search_max_results=max(1, env_int("SIGNAL_SEARCH_MAX_RESULTS", 5)),
    )


def run_signal_analysis_for_market(
    mysql_config: MySqlConfig,
    task_id: str,
    market: str,
    csv_path: str,
    check_date: Optional[date] = None,
    timeframe: str = "1d",
    enabled: Optional[bool] = None,
) -> AnalysisRunResult:
    """Run optional post-screening analysis without affecting the main screening flow."""
    should_run = env_flag("ENABLE_LLM_ANALYSIS", True) if enabled is None else bool(enabled)
    if not should_run:
        return AnalysisRunResult(success=False, skipped_reason="AI 辅助分析已关闭")

    settings = settings_from_env()
    llm_provider = LLMProviderFactory.from_env(settings)
    if not llm_provider.is_available:
        return AnalysisRunResult(
            success=False,
            warnings=["未配置可用 LLM provider，跳过 AI 辅助分析"],
            skipped_reason="未配置 LLM provider",
        )

    search_provider = SearchProviderFactory.from_env(settings)
    search_provider, llm_provider, preflight_warnings = _prepare_analysis_providers(search_provider, llm_provider)
    if not llm_provider.is_available:
        return AnalysisRunResult(
            success=False,
            warnings=preflight_warnings,
            skipped_reason="LLM provider 网络预检失败，跳过 AI 辅助分析",
        )

    context = SignalAnalysisContext(
        task_id=task_id,
        market=market,
        csv_path=csv_path,
        check_date=check_date or date.today(),
        settings=settings,
        search_provider=search_provider,
        llm_provider=llm_provider,
        timeframe=timeframe,
        repository=MySqlSignalAnalysisRepository(mysql_config),
        manual_hot_news=ManualHotNewsConfig.from_env(market),
        manual_hot_sectors=ManualHotSectorConfig.from_env(market),
        warnings=list(preflight_warnings),
    )
    return SignalAnalysisChain().run(context)


def _prepare_analysis_providers(
    search_provider: SearchProvider,
    llm_provider: LLMProvider,
) -> tuple[SearchProvider, LLMProvider, List[str]]:
    warnings: List[str] = []
    search_provider, search_warnings = _filter_search_provider_by_preflight(search_provider)
    llm_provider, llm_warnings = _filter_llm_provider_by_preflight(llm_provider)
    warnings.extend(search_warnings)
    warnings.extend(llm_warnings)
    return search_provider, llm_provider, warnings


def _filter_search_provider_by_preflight(search_provider: SearchProvider) -> tuple[SearchProvider, List[str]]:
    warnings: List[str] = []
    endpoint = getattr(search_provider, "endpoint", "")
    if not getattr(search_provider, "is_available", False) or not endpoint:
        return search_provider, warnings

    failures = check_host_resolution([endpoint])
    if not failures:
        return search_provider, warnings

    for host, reason in failures:
        warnings.append(
            f"[AI分析] 联网检索预检失败: 域名解析失败 `{host}` | {reason}；"
            "已降级为不联网分析"
        )
    return NullSearchProvider(), warnings


def _filter_llm_provider_by_preflight(llm_provider: LLMProvider) -> tuple[LLMProvider, List[str]]:
    warnings: List[str] = []
    providers = [
        provider
        for provider in (getattr(llm_provider, "providers", None) or [llm_provider])
        if getattr(provider, "is_available", False)
    ]
    if not providers:
        return NullLLMProvider(), warnings

    available: List[LLMProvider] = []
    for provider in providers:
        api_base = getattr(provider, "api_base", "")
        if not api_base:
            available.append(provider)
            continue
        failures = check_host_resolution([api_base])
        if not failures:
            available.append(provider)
            continue
        label = getattr(provider, "name", provider.__class__.__name__)
        for host, reason in failures:
            warnings.append(
                f"[AI分析] LLM provider {label} 预检失败: 域名解析失败 `{host}` | {reason}；"
                "已跳过该 provider"
            )

    if not available:
        return NullLLMProvider(), warnings
    if len(available) == 1:
        return available[0], warnings
    return FallbackLLMProvider(available), warnings


def _analysis_network_preflight(search_provider, llm_provider) -> List[str]:
    """Backward-compatible warning-only preflight helper for tests and scripts."""
    _, _, warnings = _prepare_analysis_providers(search_provider, llm_provider)
    return warnings
