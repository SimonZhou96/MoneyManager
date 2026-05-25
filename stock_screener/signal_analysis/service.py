#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Public service entrypoint for best-effort signal analysis."""

from __future__ import annotations

import os
import tempfile
from datetime import date
from pathlib import Path
from typing import List, Optional

from db import MarketDatabase, MySqlConfig
from network_preflight import check_host_resolution

from .chain import SignalAnalysisChain, SignalAnalysisContext
from .factories import LLMProviderFactory, SearchProviderFactory
from .llm_providers import FallbackLLMProvider, LLMProvider, NullLLMProvider
from .hot_news import ManualHotNewsConfig
from .hot_sectors import ManualHotSectorConfig
from .models import AnalysisRunResult, AnalysisSettings, ScreeningSignalRow, SignalAnalysisResult
from .search_providers import FallbackSearchProvider, NullSearchProvider, SearchProvider


DEFAULT_ANALYSIS_PROFILE = "default"


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
            db.init_signal_analysis_cache_schema()
            db.upsert_signal_analysis_cache(rows)
        finally:
            db.close()

    def get_signal_analysis_cache(
        self,
        market: str,
        code: str,
        timeframe: str,
        analysis_profile: str,
        trade_date: date,
    ) -> Optional[dict]:
        db = MarketDatabase(self.mysql_config)
        try:
            db.init_signal_analysis_cache_schema()
            return db.get_signal_analysis_cache(
                market=market,
                code=code,
                timeframe=timeframe,
                analysis_profile=analysis_profile,
                trade_date=trade_date,
            )
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
    analysis_profile: str = DEFAULT_ANALYSIS_PROFILE,
    force_refresh: bool = False,
    repository_override: Optional[MySqlSignalAnalysisRepository] = None,
    search_provider_override: Optional[SearchProvider] = None,
    llm_provider_override: Optional[LLMProvider] = None,
) -> AnalysisRunResult:
    """Run optional post-screening analysis without affecting the main screening flow."""
    should_run = env_flag("ENABLE_LLM_ANALYSIS", True) if enabled is None else bool(enabled)
    if not should_run:
        return AnalysisRunResult(success=False, skipped_reason="AI 辅助分析已关闭")

    settings = settings_from_env()
    llm_provider = llm_provider_override or LLMProviderFactory.from_env(settings)
    search_provider = search_provider_override or SearchProviderFactory.from_env(settings)
    search_provider, llm_provider, preflight_warnings = _prepare_analysis_providers(search_provider, llm_provider)
    if not llm_provider.is_available:
        if _has_llm_preflight_failure(preflight_warnings):
            preflight_warnings = [*preflight_warnings, "LLM provider 网络预检失败，仅返回命中的缓存结果"]
        else:
            preflight_warnings = [*preflight_warnings, "未配置可用 LLM provider，仅返回命中的缓存结果"]

    context = SignalAnalysisContext(
        task_id=task_id,
        market=market,
        csv_path=csv_path,
        check_date=check_date or date.today(),
        settings=settings,
        search_provider=search_provider,
        llm_provider=llm_provider,
        timeframe=timeframe,
        repository=repository_override or MySqlSignalAnalysisRepository(mysql_config),
        manual_hot_news=ManualHotNewsConfig.from_env(market),
        manual_hot_sectors=ManualHotSectorConfig.from_env(market),
        warnings=list(preflight_warnings),
        analysis_profile=analysis_profile,
        force_refresh=force_refresh,
    )
    return SignalAnalysisChain().run(context)


def run_signal_analysis_for_row(
    mysql_config: MySqlConfig,
    *,
    task_id: str,
    row: ScreeningSignalRow,
    market: str,
    check_date: Optional[date] = None,
    timeframe: str = "1d",
    enabled: Optional[bool] = None,
    analysis_profile: str = DEFAULT_ANALYSIS_PROFILE,
    force_refresh: bool = False,
) -> tuple[Optional[SignalAnalysisResult], List[str]]:
    run_date = check_date or date.today()
    with tempfile.TemporaryDirectory(prefix="signal_analysis_row_") as tmp_dir:
        csv_path = Path(tmp_dir) / f"{row.code}.csv"
        fieldnames = [
            "股票代码", "市场", "名称", "标的类型", "pe", "市值", "所属板块", "满足的条件",
            "主力流出风险", "主力风险分", "主力风险信号", "主力风险说明",
            "资金与盘面观察", "资金流向数据", "盘口数据", "龙虎榜数据", "成交量分布数据",
        ]
        csv_row = {
            "股票代码": row.code,
            "市场": row.market_label or row.market,
            "名称": row.name,
            "标的类型": row.instrument_type,
            "pe": row.pe_ratio,
            "市值": row.market_cap,
            "所属板块": row.sector,
            "满足的条件": row.conditions_met,
            "主力流出风险": row.main_force_risk_level,
            "主力风险分": row.main_force_risk_score,
            "主力风险信号": row.main_force_risk_signals,
            "主力风险说明": row.main_force_risk_summary,
            "资金与盘面观察": row.main_force_market_data_observation,
            "资金流向数据": row.main_force_fund_flow_data,
            "盘口数据": row.main_force_order_book_data,
            "龙虎榜数据": row.main_force_lhb_data,
            "成交量分布数据": row.main_force_chip_data,
        }
        import csv

        with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerow(csv_row)
        result = run_signal_analysis_for_market(
            mysql_config=mysql_config,
            task_id=task_id,
            market=market,
            csv_path=str(csv_path),
            check_date=run_date,
            timeframe=timeframe,
            enabled=enabled,
            analysis_profile=analysis_profile,
            force_refresh=force_refresh,
        )
        return (result.results_by_code or {}).get(row.code), list(result.warnings or [])


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
    providers = getattr(search_provider, "providers", None)
    if providers is not None:
        available: List[SearchProvider] = []
        for provider in providers:
            if not getattr(provider, "is_available", False):
                continue
            endpoint = getattr(provider, "endpoint", "")
            if not endpoint:
                available.append(provider)
                continue
            failures = check_host_resolution([endpoint])
            label = getattr(provider, "name", provider.__class__.__name__)
            if not failures:
                available.append(provider)
                continue
            for host, reason in failures:
                warnings.append(
                    f"[AI分析] 联网检索 provider {label} 预检失败: 域名解析失败 `{host}` | {reason}；"
                    "已跳过该 provider"
                )

        if not available:
            return NullSearchProvider(), warnings
        if len(available) == 1:
            return available[0], warnings
        return FallbackSearchProvider(available), warnings

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


def _has_llm_preflight_failure(warnings: List[str]) -> bool:
    return any("LLM provider" in warning and "预检失败" in warning for warning in warnings)
