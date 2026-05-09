#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Factories for pluggable search and model providers."""

from __future__ import annotations

import os

from .llm_providers import (
    CodexResponsesLLMProvider,
    DeepSeekLLMProvider,
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

    @staticmethod
    def from_env(settings: AnalysisSettings) -> LLMProvider:
        provider = os.getenv("LLM_PROVIDER", "openai_compatible").strip().lower() or "openai_compatible"
        if provider == "codex_responses":
            return LLMProviderFactory._codex_responses_from_env(settings)
        if provider == "deepseek":
            return LLMProviderFactory._deepseek_from_env(settings)
        return LLMProviderFactory._openai_compatible_from_env(settings)

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
