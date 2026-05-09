#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Factories for pluggable search and model providers."""

from __future__ import annotations

import os

from .llm_providers import LLMProvider, NullLLMProvider, OpenAICompatibleLLMProvider
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
    """Create an OpenAI-compatible LLM provider from environment configuration."""

    @staticmethod
    def from_env(settings: AnalysisSettings) -> LLMProvider:
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

