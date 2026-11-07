#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Factories for pluggable search and model providers."""

from __future__ import annotations

import os
from typing import List

from account_provider_config import current_account_user_id, provider_config_value

from .llm_providers import (
    CodexResponsesLLMProvider,
    DeepSeekLLMProvider,
    FallbackLLMProvider,
    LLMProvider,
    NullLLMProvider,
    OpenAICompatibleLLMProvider,
)
from .browser_search_providers import BingBaiduSearchProvider
from .models import AnalysisSettings
from .search_providers import (
    FallbackSearchProvider,
    NullSearchProvider,
    SearchProvider,
    TavilySearchProvider,
    ZhipuWebSearchProvider,
)


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


class SearchProviderFactory:
    """Create a search provider from environment configuration."""

    DEFAULT_PROVIDER_ORDER = ["bing_baidu", "tavily", "zhipuai"]

    @staticmethod
    def from_env(settings: AnalysisSettings) -> SearchProvider:
        providers = [
            provider
            for provider_name in SearchProviderFactory._provider_order_from_env()
            for provider in [SearchProviderFactory._provider_from_env(provider_name, settings)]
            if getattr(provider, "is_available", False)
        ]
        if not providers:
            return NullSearchProvider()
        if len(providers) == 1:
            return providers[0]
        return FallbackSearchProvider(providers)

    @staticmethod
    def _provider_from_env(provider: str, settings: AnalysisSettings) -> SearchProvider:
        if provider == "bing_baidu":
            return SearchProviderFactory._bing_baidu_from_env(settings)
        if provider == "tavily":
            return SearchProviderFactory._tavily_from_env(settings)
        if provider == "zhipuai":
            return SearchProviderFactory._zhipu_from_env(settings)
        return NullSearchProvider()

    @staticmethod
    def _bing_baidu_from_env(settings: AnalysisSettings) -> SearchProvider:
        headless = os.getenv("BING_BAIDU_BROWSER_HEADLESS", "true").strip().lower() != "false"
        timeout = _env_int("BING_BAIDU_SEARCH_TIMEOUT_SEC", min(30, settings.timeout_sec))
        provider = BingBaiduSearchProvider(headless=headless, timeout_sec=timeout)
        if not provider.is_available:
            return NullSearchProvider()
        return provider

    @staticmethod
    def _tavily_from_env(settings: AnalysisSettings) -> SearchProvider:
        api_key = (provider_config_value("TAVILY_API_KEY", "") or "").strip()
        if not api_key:
            return NullSearchProvider()
        endpoint = (provider_config_value("TAVILY_API_ENDPOINT", "https://api.tavily.com/search") or "").strip()
        timeout = _env_int("SIGNAL_SEARCH_TIMEOUT_SEC", min(30, settings.timeout_sec))
        return TavilySearchProvider(api_key=api_key, endpoint=endpoint, timeout_sec=timeout)

    @staticmethod
    def _zhipu_from_env(settings: AnalysisSettings) -> SearchProvider:
        api_key = (
            (provider_config_value("ZHIPUAI_API_KEY", "") or "").strip()
            or (provider_config_value("ZHIPU_API_KEY", "") or "").strip()
            or (provider_config_value("BIGMODEL_API_KEY", "") or "").strip()
        )
        if not api_key:
            return NullSearchProvider()
        endpoint = provider_config_value(
            "ZHIPUAI_WEB_SEARCH_ENDPOINT",
            "https://open.bigmodel.cn/api/paas/v4/web_search",
        ).strip()
        timeout = _env_int("SIGNAL_SEARCH_TIMEOUT_SEC", min(30, settings.timeout_sec))
        search_engine = (provider_config_value("ZHIPUAI_WEB_SEARCH_ENGINE", "search_std") or "").strip() or "search_std"
        content_size = (provider_config_value("ZHIPUAI_WEB_SEARCH_CONTENT_SIZE", "medium") or "").strip() or "medium"
        recency_filter = (provider_config_value("ZHIPUAI_WEB_SEARCH_RECENCY_FILTER", "noLimit") or "").strip() or "noLimit"
        return ZhipuWebSearchProvider(
            api_key=api_key,
            endpoint=endpoint,
            timeout_sec=timeout,
            search_engine=search_engine,
            content_size=content_size,
            recency_filter=recency_filter,
        )

    @staticmethod
    def _provider_order_from_env() -> List[str]:
        explicit_order = (provider_config_value("SIGNAL_SEARCH_PROVIDER_ORDER", "") or "").strip()
        if explicit_order:
            return SearchProviderFactory._dedupe_provider_names(explicit_order.split(","))
        return list(SearchProviderFactory.DEFAULT_PROVIDER_ORDER)

    @staticmethod
    def _dedupe_provider_names(raw_names) -> List[str]:
        aliases = {
            "bigmodel": "zhipuai",
            "zhipu": "zhipuai",
            "zhipu_ai": "zhipuai",
            "zhipu_web_search": "zhipuai",
            "bing": "bing_baidu",
            "baidu": "bing_baidu",
            "free_search": "bing_baidu",
            "browser": "bing_baidu",
        }
        valid = set(SearchProviderFactory.DEFAULT_PROVIDER_ORDER)
        names: List[str] = []
        seen = set()
        for raw_name in raw_names:
            name = aliases.get(str(raw_name).strip().lower(), str(raw_name).strip().lower())
            if not name or name in seen:
                continue
            if name not in valid:
                continue
            names.append(name)
            seen.add(name)
        return names


class LLMProviderFactory:
    """Create an LLM provider from environment configuration."""

    DEFAULT_PROVIDER_ORDER = ["openai_compatible", "codex_responses", "deepseek"]

    @staticmethod
    def from_env(settings: AnalysisSettings) -> LLMProvider:
        providers = [
            provider
            for provider_name in LLMProviderFactory._provider_order_from_env()
            for provider in [LLMProviderFactory._provider_from_env(provider_name, settings)]
            if getattr(provider, "is_available", False)
        ]
        if not providers:
            return NullLLMProvider()
        if len(providers) == 1:
            return providers[0]
        return FallbackLLMProvider(providers)

    @staticmethod
    def _provider_from_env(provider: str, settings: AnalysisSettings) -> LLMProvider:
        if provider == "codex_responses":
            return LLMProviderFactory._codex_responses_from_env(settings)
        if provider == "deepseek":
            return LLMProviderFactory._deepseek_from_env(settings)
        if provider == "openai_compatible":
            return LLMProviderFactory._openai_compatible_from_env(settings)
        return NullLLMProvider()

    @staticmethod
    def _provider_order_from_env() -> List[str]:
        explicit_order = (provider_config_value("LLM_PROVIDER_ORDER", "") or "").strip()
        if explicit_order:
            return LLMProviderFactory._dedupe_provider_names(explicit_order.split(","))

        preferred = (provider_config_value("LLM_PROVIDER", "") or "").strip()
        if preferred:
            return LLMProviderFactory._dedupe_provider_names([
                preferred,
                *LLMProviderFactory.DEFAULT_PROVIDER_ORDER,
            ])
        return list(LLMProviderFactory.DEFAULT_PROVIDER_ORDER)

    @staticmethod
    def _dedupe_provider_names(raw_names) -> List[str]:
        aliases = {
            "openai": "openai_compatible",
            "chat_completions": "openai_compatible",
            "codex": "codex_responses",
        }
        names: List[str] = []
        seen = set()
        for raw_name in raw_names:
            name = aliases.get(str(raw_name).strip().lower(), str(raw_name).strip().lower())
            if not name or name in seen:
                continue
            if name not in set(LLMProviderFactory.DEFAULT_PROVIDER_ORDER):
                continue
            names.append(name)
            seen.add(name)
        return names

    @staticmethod
    def _openai_compatible_from_env(settings: AnalysisSettings) -> LLMProvider:
        api_key = (provider_config_value("LLM_API_KEY", "") or "").strip()
        model = (provider_config_value("LLM_MODEL", "") or "").strip()
        if not api_key or not model:
            return NullLLMProvider()
        api_base = (provider_config_value("LLM_API_BASE", "https://api.openai.com") or "").strip()
        return OpenAICompatibleLLMProvider(
            api_key=api_key,
            model=model,
            api_base=api_base,
            timeout_sec=settings.timeout_sec,
        )

    @staticmethod
    def _codex_responses_from_env(settings: AnalysisSettings) -> LLMProvider:
        api_key = (provider_config_value("CODEX_API_KEY", "") or "").strip()
        if not api_key and current_account_user_id() is None:
            api_key = (provider_config_value("LLM_API_KEY", "") or "").strip()
        model = (provider_config_value("CODEX_LLM_MODEL", "gpt-5.2-codex") or "").strip()
        if not api_key or not model:
            return NullLLMProvider()
        api_base = (provider_config_value("CODEX_API_BASE", "https://api.openai.com") or "").strip()
        reasoning_effort = (provider_config_value("CODEX_REASONING_EFFORT", "medium") or "").strip()
        return CodexResponsesLLMProvider(
            api_key=api_key,
            model=model,
            api_base=api_base,
            timeout_sec=settings.timeout_sec,
            reasoning_effort=reasoning_effort,
        )

    @staticmethod
    def _deepseek_from_env(settings: AnalysisSettings) -> LLMProvider:
        api_key = (provider_config_value("DEEPSEEK_API_KEY", "") or "").strip()
        if not api_key and current_account_user_id() is None:
            api_key = (provider_config_value("LLM_API_KEY", "") or "").strip()
        model = (
            (provider_config_value("DEEPSEEK_LLM_MODEL", "") or "").strip()
            or (provider_config_value("DEEPSEEK_MODEL", "") or "").strip()
            or "deepseek-v4-flash"
        )
        if not api_key or not model:
            return NullLLMProvider()
        api_base = (provider_config_value("DEEPSEEK_API_BASE", "https://api.deepseek.com") or "").strip()
        return DeepSeekLLMProvider(
            api_key=api_key,
            model=model,
            api_base=api_base,
            timeout_sec=settings.timeout_sec,
        )
