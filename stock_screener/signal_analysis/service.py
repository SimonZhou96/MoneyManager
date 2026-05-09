#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Public service entrypoint for best-effort signal analysis."""

from __future__ import annotations

import os
from datetime import date
from typing import List, Optional

from db import MarketDatabase, MySqlConfig

from .chain import SignalAnalysisChain, SignalAnalysisContext
from .factories import LLMProviderFactory, SearchProviderFactory
from .hot_news import ManualHotNewsConfig
from .models import AnalysisRunResult, AnalysisSettings


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
            warnings=["未配置 LLM_API_KEY 或 LLM_MODEL，跳过 AI 辅助分析"],
            skipped_reason="未配置 LLM provider",
        )

    search_provider = SearchProviderFactory.from_env(settings)
    context = SignalAnalysisContext(
        task_id=task_id,
        market=market,
        csv_path=csv_path,
        check_date=check_date or date.today(),
        settings=settings,
        search_provider=search_provider,
        llm_provider=llm_provider,
        repository=MySqlSignalAnalysisRepository(mysql_config),
        manual_hot_news=ManualHotNewsConfig.from_env(market),
    )
    return SignalAnalysisChain().run(context)
