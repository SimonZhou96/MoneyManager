#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Factories for pluggable search and model providers."""

from __future__ import annotations

import os
from typing import List

from .llm_providers import (
    CodexResponsesLLMProvider,
    DeepSeekLLMProvider,
    FallbackLLMProvider,
    LLMProvider,
    NullLLMProvider,
    OpenAICompatibleLLMProvider,
)
from .models import AnalysisSettings
from .search_providers import NullSearchProvider, SearchProvider, TavilySearchProvider


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

    @staticmethod
    def from_env(settings: AnalysisSettings) -> SearchProvider:
        api_key = os.getenv("TAVILY_API_KEY", "").strip()
        if not api_key:
            return NullSearchProvider()
        endpoint = os.getenv("TAVILY_API_ENDPOINT", "https://api.tavily.com/search").strip()
        timeout = _env_int("SIGNAL_SEARCH_TIMEOUT_SEC", min(30, settings.timeout_sec))
        return TavilySearchProvider(api_key=api_key, endpoint=endpoint, timeout_sec=timeout)


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
        explicit_order = os.getenv("LLM_PROVIDER_ORDER", "").strip()
        if explicit_order:
            return LLMProviderFactory._dedupe_provider_names(explicit_order.split(","))

        preferred = os.getenv("LLM_PROVIDER", "").strip()
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
        api_key = os.getenv("LLM_API_KEY", "").strip()
        model = os.getenv("LLM_MODEL", "").strip()
        if not api_key or not model:
            return NullLLMProvider()
        api_base = os.getenv("LLM_API_BASE", "https://api.openai.com").strip()
        return OpenAICompatibleLLMProvider(
            api_key=api_key,
            model=model,
            api_base=api_base,
            timeout_sec=settings.timeout_sec,
        )

    @staticmethod
    def _codex_responses_from_env(settings: AnalysisSettings) -> LLMProvider:
        api_key = os.getenv("CODEX_API_KEY", "").strip() or os.getenv("LLM_API_KEY", "").strip()
        model = os.getenv("CODEX_LLM_MODEL", "gpt-5.2-codex").strip()
        if not api_key or not model:
            return NullLLMProvider()
        api_base = os.getenv("CODEX_API_BASE", "https://api.openai.com").strip()
        reasoning_effort = os.getenv("CODEX_REASONING_EFFORT", "medium").strip()
        return CodexResponsesLLMProvider(
            api_key=api_key,
            model=model,
            api_base=api_base,
            timeout_sec=settings.timeout_sec,
            reasoning_effort=reasoning_effort,
        )

    @staticmethod
    def _deepseek_from_env(settings: AnalysisSettings) -> LLMProvider:
        api_key = os.getenv("DEEPSEEK_API_KEY", "").strip() or os.getenv("LLM_API_KEY", "").strip()
        model = (
            os.getenv("DEEPSEEK_LLM_MODEL", "").strip()
            or os.getenv("DEEPSEEK_MODEL", "").strip()
            or "deepseek-v4-flash"
        )
        if not api_key or not model:
            return NullLLMProvider()
        api_base = os.getenv("DEEPSEEK_API_BASE", "https://api.deepseek.com").strip()
        return DeepSeekLLMProvider(
            api_key=api_key,
            model=model,
            api_base=api_base,
            timeout_sec=settings.timeout_sec,
        )
