#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Chain-of-responsibility implementation for signal analysis."""

from __future__ import annotations

import csv
import os
import tempfile
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Callable, Dict, List, Optional, Protocol, Tuple

from report_naming import analysis_report_path_for_csv, market_signal_report_stem

from market_intel.evidence import EvidencePackBuilder, flatten_bundle_items, intel_items_to_search_documents
from market_intel.reporting import render_multi_stock_report

from .evidence import apply_evidence_to_result, dedupe_documents, expand_company_documents
from .llm_providers import LLMProvider
from .hot_news import ManualHotNewsConfig
from .hot_sectors import AkshareHotSectorProvider, ManualHotSectorConfig, WebSearchHotSectorProvider
from .models import (
    AnalysisRunResult,
    AnalysisSettings,
    CONFIDENCE_SCORE_CRITERIA,
    HOT_SECTOR_MARK_CRITERIA,
    RELIABILITY_SCORE_CRITERIA,
    ScreeningSignalRow,
    SearchDocument,
    SIGNAL_BIAS_CRITERIA,
    SignalAnalysisResult,
    compute_unified_score,
)
from .search_providers import SearchProvider

from signal_analysis.renderers import RetailReportRenderer
from scoring.market_cache import MarketCache

AI_CSV_COLUMNS = [
    "最终统一评分",
    "最终评分公式",
    "技术规则分",
    "宏观五模块分",
    "事件热点分",
    "资金风险分",
    "LLM复核分",
    "评分缺失项",
    "宏观分",
    "行业分",
    "企业质量分",
    "估值分",
    "交易分",
    "AI分析状态",
    "信号可靠性评分",
    "信号可靠性评分口径",
    "模型置信度",
    "模型置信度口径",
    "辅助方向判断",
    "辅助方向判断口径",
    "关键利好因素",
    "关键风险因素",
    "宏观/政策因素",
    "公司事件",
    "市场热点新闻",
    "公司热点新闻",
    "新闻影响判断",
    "新闻来源",
    "AI识别热点板块",
    "热点板块标记",
    "匹配热点板块",
    "热点板块关联度",
    "热点板块匹配理由",
    "热点板块来源",
    "热点板块标记口径",
    "信息来源",
    "数据缺失原因",
    "引用来源",
    "因素引用",
]


MARKET_NAMES = {
    "HK": "港股",
    "US": "美股",
    "A": "A股",
}


class SignalAnalysisRepository(Protocol):
    """Persistence boundary used by PersistAnalysisStep."""

    def save_results(self, rows: List[dict]) -> None:
        """Persist normalized analysis rows."""


@dataclass
class SignalAnalysisContext:
    task_id: str
    market: str
    csv_path: str
    check_date: date
    settings: AnalysisSettings
    search_provider: SearchProvider
    llm_provider: LLMProvider
    timeframe: str = "1d"
    chain_key: str = ""
    chain_name: str = ""
    repository: Optional[SignalAnalysisRepository] = None
    manual_hot_news: ManualHotNewsConfig = field(default_factory=ManualHotNewsConfig)
    manual_hot_sectors: ManualHotSectorConfig = field(default_factory=ManualHotSectorConfig)
    rows: List[ScreeningSignalRow] = field(default_factory=list)
    all_rows: List[ScreeningSignalRow] = field(default_factory=list)
    csv_fieldnames: List[str] = field(default_factory=list)
    market_query: str = ""
    sector_query: str = ""
    company_queries: Dict[str, str] = field(default_factory=dict)
    market_documents: List[SearchDocument] = field(default_factory=list)
    sector_documents: List[SearchDocument] = field(default_factory=list)
    company_documents: Dict[str, List[SearchDocument]] = field(default_factory=dict)
    company_news_providers: Dict[str, str] = field(default_factory=dict)
    market_intel_service: Any = None
    market_intel_market_bundle: Dict[str, Any] = field(default_factory=dict)
    market_intel_stock_bundles: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    evidence_packs: Dict[str, dict] = field(default_factory=dict)
    hot_sectors: List[str] = field(default_factory=list)
    hot_sector_sources: List[str] = field(default_factory=list)
    results_by_code: Dict[str, SignalAnalysisResult] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)
    artifact_paths: List[str] = field(default_factory=list)
    analysis_profile: str = "default"
    force_refresh: bool = False
    aborted: bool = False
    skipped_reason: str = ""


class AnalysisStep(ABC):
    """One step in the post-screening analysis chain."""

    name = "AnalysisStep"

    @abstractmethod
    def run(self, context: SignalAnalysisContext) -> None:
        """Mutate the context or mark it aborted."""


class LoadCsvSignalsStep(AnalysisStep):
    name = "LoadCsvSignalsStep"

    def run(self, context: SignalAnalysisContext) -> None:
        if not os.path.exists(context.csv_path):
            context.aborted = True
            context.skipped_reason = f"CSV 不存在: {context.csv_path}"
            context.warnings.append(context.skipped_reason)
            return

        with open(context.csv_path, "r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            context.csv_fieldnames = list(reader.fieldnames or [])
            context.rows = [
                ScreeningSignalRow.from_csv_row(row, index=index, default_market=context.market)
                for index, row in enumerate(reader)
            ]
        context.all_rows = list(context.rows)
        if not context.rows:
            context.aborted = True
            context.skipped_reason = "CSV 没有可分析的股票行"
            context.warnings.append(context.skipped_reason)


class LoadCachedResultsStep(AnalysisStep):
    name = "LoadCachedResultsStep"

    def run(self, context: SignalAnalysisContext) -> None:
        if context.force_refresh or context.repository is None:
            return
        getter = getattr(context.repository, "get_signal_analysis_cache", None)
        if not callable(getter):
            return
        company_news_getter = getattr(context.repository, "get_company_news_cache", None)
        provider_candidates = _company_news_provider_candidates(context.search_provider)
        pending_rows: List[ScreeningSignalRow] = []
        for row in context.rows:
            cached = getter(
                context.market,
                row.code,
                context.timeframe,
                context.analysis_profile,
                context.check_date,
            )
            if cached:
                if callable(company_news_getter) and provider_candidates:
                    company_news_cache = _load_valid_company_news_cache(
                        getter=company_news_getter,
                        context=context,
                        row=row,
                        provider_candidates=provider_candidates,
                    )
                    if company_news_cache is None:
                        context.warnings.append(
                            "[AI分析] 公司时事缓存缺失，触发重算: "
                            f"code={row.code} providers={','.join(provider_candidates)}"
                        )
                        pending_rows.append(row)
                        continue
                    cached = _merge_company_news_cache(cached, company_news_cache)
                    provider = str(company_news_cache.get("company_news_provider") or "").strip()
                    if provider:
                        context.company_news_providers[row.code] = provider

                cached_events = cached.get("company_events") or []
                cached_hot_news = cached.get("company_hot_news") or []
                min_company_events = _cache_min_company_events()
                if len(cached_events) < min_company_events:
                    context.warnings.append(
                        "[AI分析] 缓存重算触发: "
                        f"code={row.code} "
                        f"company_events={len(cached_events)}<{min_company_events}"
                    )
                    pending_rows.append(row)
                    continue
                context.warnings.append(
                    "[AI分析] 缓存命中: "
                    f"code={row.code} "
                    f"company_events={len(cached_events)} "
                    f"company_hot_news={len(cached_hot_news)}"
                )
                context.results_by_code[row.code] = SignalAnalysisResult.from_llm_item(
                    cached,
                    model=str(cached.get("model") or "cache"),
                )
                continue
            pending_rows.append(row)
        context.rows = pending_rows


class BuildSearchQueriesStep(AnalysisStep):
    name = "BuildSearchQueriesStep"

    def run(self, context: SignalAnalysisContext) -> None:
        if not context.rows and context.results_by_code:
            return
        market_name = MARKET_NAMES.get(context.market, context.market)
        context.market_query = (
            f"{market_name} 股票市场 最新 政策 宏观经济 热点新闻 "
            f"{context.check_date.isoformat()} stock market policy macro news"
        )
        context.sector_query = (
            f"{market_name} 股票市场 今日 热点板块 领涨行业 资金流入 "
            f"{context.check_date.isoformat()} hot sectors leading industries"
        )
        for row in context.rows:
            context.company_queries[row.code] = (
                f"{row.code} {row.name} 股票 最新新闻 财报 公司事件 政策 "
                f"stock news earnings company event"
            )


class SearchContextStep(AnalysisStep):
    name = "SearchContextStep"

    def run(self, context: SignalAnalysisContext) -> None:
        if not context.rows and context.results_by_code:
            return
        if not context.search_provider.is_available:
            if _has_search_preflight_failure(context.warnings):
                context.warnings.append("搜索 provider 网络预检失败，跳过联网检索，仅使用 CSV 信号交给模型分析")
            else:
                context.warnings.append("未配置搜索 provider，跳过联网检索，仅使用 CSV 信号交给模型分析")
            return

        try:
            context.market_documents = dedupe_documents([
                *context.market_documents,
                *context.search_provider.search(
                    context.market_query,
                    context.settings.search_max_results,
                ),
            ])
            context.warnings.append(
                f"[AI分析] 联网检索结果: 市场上下文 {len(context.market_documents)} 条"
            )
        except Exception as exc:
            context.warnings.append(f"市场上下文搜索失败: {type(exc).__name__}: {exc}")

        try:
            context.sector_documents = dedupe_documents([
                *context.sector_documents,
                *context.search_provider.search(
                    context.sector_query,
                    context.settings.search_max_results,
                ),
            ])
            context.warnings.append(
                f"[AI分析] 联网检索结果: 热点板块 {len(context.sector_documents)} 条"
            )
        except Exception as exc:
            context.warnings.append(f"热点板块搜索失败: {type(exc).__name__}: {exc}")

        for row in context.rows:
            context.company_documents.setdefault(row.code, [])

        batch_size = _company_search_batch_size()
        stock_rows = [row for row in context.rows if not row.is_etf]
        for batch in _chunk_rows(stock_rows, batch_size):
            try:
                batch_documents = context.search_provider.search_companies_batch(
                    context.market,
                    batch,
                    context.settings.search_max_results,
                )
            except Exception as exc:
                codes = [row.code for row in batch]
                context.warnings.append(
                    f"公司事件批量搜索失败: {type(exc).__name__}: {exc}; codes={','.join(codes)}"
                )
                for row in batch:
                    context.company_documents.setdefault(row.code, [])
                continue

            missing_codes = []
            for row in batch:
                documents = list((batch_documents or {}).get(row.code, []))
                context.company_documents[row.code] = dedupe_documents([
                    *(context.company_documents.get(row.code) or []),
                    *documents,
                ])
                if not documents:
                    missing_codes.append(row.code)
                provider = _current_company_news_provider(context.search_provider)
                if provider:
                    context.company_news_providers[row.code] = provider
            matched_count = sum(
                1 for row in batch if context.company_documents.get(row.code)
            )
            total_docs = sum(len(context.company_documents.get(row.code) or []) for row in batch)
            context.warnings.append(
                f"[AI分析] 联网检索结果: 公司事件 batch={len(batch)} matched={matched_count} docs={total_docs}"
            )
            if missing_codes:
                context.warnings.append(
                    f"公司事件批量搜索未匹配到 {len(missing_codes)} 只股票: {','.join(missing_codes)}"
                )

        etf_rows = [row for row in context.rows if row.is_etf]
        for batch in _chunk_rows(etf_rows, batch_size):
            try:
                documents = context.search_provider.search(
                    _build_etf_theme_query(context.market, batch),
                    context.settings.search_max_results,
                )
            except Exception as exc:
                codes = [row.code for row in batch]
                context.warnings.append(
                    f"ETF主题批量搜索失败: {type(exc).__name__}: {exc}; codes={','.join(codes)}"
                )
                documents = []
            context.warnings.append(
                f"[AI分析] 联网检索结果: ETF主题 batch={len(batch)} docs={len(documents)}"
            )
            provider = _current_company_news_provider(context.search_provider)
            for row in batch:
                if provider:
                    context.company_news_providers[row.code] = provider
                context.company_documents[row.code] = dedupe_documents([
                    *(context.company_documents.get(row.code) or []),
                    *documents,
                ])

        # Apply Chinese financial sentiment pre-scoring to all company documents
        _apply_sentiment_to_company_documents(context)


class MarketIntelEvidenceStep(AnalysisStep):
    name = "MarketIntelEvidenceStep"

    def run(self, context: SignalAnalysisContext) -> None:
        if not context.rows and context.results_by_code:
            return
        service = context.market_intel_service
        if service is None:
            return

        market_items = []
        try:
            market_bundle = service.get_market_digest(context.market, force_refresh=context.force_refresh)
            context.market_intel_market_bundle = dict(market_bundle or {})
            market_items = flatten_bundle_items(market_bundle)
            context.market_documents = dedupe_documents([
                *context.market_documents,
                *intel_items_to_search_documents(market_items),
            ])
        except Exception as exc:
            context.warnings.append(f"市场情报摘要获取失败: {type(exc).__name__}: {exc}")

        for row in context.rows:
            try:
                stock_bundle = service.get_stock_intel(
                    context.market,
                    row.code,
                    force_refresh=context.force_refresh,
                )
                context.market_intel_stock_bundles[row.code] = dict(stock_bundle or {})
                stock_items = flatten_bundle_items(stock_bundle)
                stock_documents = intel_items_to_search_documents(stock_items)
                context.company_documents[row.code] = dedupe_documents([
                    *(context.company_documents.get(row.code) or []),
                    *stock_documents,
                ])
            except Exception as exc:
                context.warnings.append(f"{row.code} 市场情报证据包构建失败: {type(exc).__name__}: {exc}")


class ApplyManualHotNewsStep(AnalysisStep):
    name = "ApplyManualHotNewsStep"

    def run(self, context: SignalAnalysisContext) -> None:
        if not context.rows and context.results_by_code:
            return
        config = context.manual_hot_news
        if config.market_hot_news:
            context.market_documents = dedupe_documents([
                *context.market_documents,
                *config.market_documents(context.market_query),
            ])
            context.warnings.append("已使用手动配置的市场热点信息覆盖搜索热点信息")

        for row in context.rows:
            if not config.company_hot_news(row.code):
                continue
            context.company_documents[row.code] = dedupe_documents([
                *(context.company_documents.get(row.code) or []),
                *config.company_documents(row.code, context.company_queries.get(row.code, "")),
            ])
            context.warnings.append(f"{row.code} 已使用手动配置的公司热点信息覆盖搜索热点信息")


class MarketIntelFinalizeEvidencePacksStep(AnalysisStep):
    name = "MarketIntelFinalizeEvidencePacksStep"

    def run(self, context: SignalAnalysisContext) -> None:
        if not context.rows and context.results_by_code:
            return
        if context.market_intel_service is None and not context.market_intel_market_bundle and not context.market_intel_stock_bundles:
            return

        builder = EvidencePackBuilder()
        for row in context.rows:
            try:
                manual_items = [
                    *context.manual_hot_news.market_documents(context.market_query),
                    *context.manual_hot_news.company_documents(
                        row.code,
                        context.company_queries.get(row.code, ""),
                    ),
                ]
                pack = builder.build(
                    market=context.market,
                    code=row.code,
                    stock_bundle=context.market_intel_stock_bundles.get(row.code, {}),
                    market_bundle=context.market_intel_market_bundle,
                    search_documents=[
                        *context.market_documents,
                        *(context.company_documents.get(row.code) or []),
                    ],
                    manual_items=manual_items,
                    force_refresh=context.force_refresh,
                )
                context.evidence_packs[row.code] = pack.to_dict()
            except Exception as exc:
                context.warnings.append(f"{row.code} 市场情报证据包收尾失败: {type(exc).__name__}: {exc}")


class ExpandCompanyEvidenceStep(AnalysisStep):
    name = "ExpandCompanyEvidenceStep"

    def run(self, context: SignalAnalysisContext) -> None:
        if not context.rows and context.results_by_code:
            return
        if not context.search_provider.is_available:
            return
        if not _env_bool("SIGNAL_ENABLE_EVIDENCE_EXPANSION", False):
            return
        stock_rows = [row for row in context.rows if not row.is_etf]
        max_rows = _env_int("SIGNAL_EVIDENCE_EXPANSION_MAX_ROWS", 30)
        if max_rows <= 0:
            return
        selected_rows = stock_rows[:max_rows]
        if len(stock_rows) > len(selected_rows):
            context.warnings.append(
                f"证据来源拓展仅处理前 {len(selected_rows)} 只股票；"
                "可通过 SIGNAL_EVIDENCE_EXPANSION_MAX_ROWS 调整上限"
            )
        max_results = _env_int("SIGNAL_EVIDENCE_EXPANSION_MAX_RESULTS", min(context.settings.search_max_results, 2))
        for row in selected_rows:
            try:
                expanded = expand_company_documents(
                    search_provider=context.search_provider,
                    market=context.market,
                    row=row,
                    max_results=max_results,
                )
            except Exception as exc:
                context.warnings.append(f"{row.code} 证据来源拓展搜索失败: {type(exc).__name__}: {exc}")
                continue
            if not expanded:
                continue
            context.company_documents[row.code] = dedupe_documents([
                *(context.company_documents.get(row.code) or []),
                *expanded,
            ])


class ResolveHotSectorsStep(AnalysisStep):
    name = "ResolveHotSectorsStep"

    def run(self, context: SignalAnalysisContext) -> None:
        if not context.rows and context.results_by_code:
            return
        limit = max(1, int(os.getenv("SIGNAL_HOT_SECTOR_LIMIT", "10") or "10"))

        # Priority 1: Web search (Tavily) — dynamic discovery, default for all markets
        if os.getenv("SIGNAL_ENABLE_WEB_SEARCH_HOT_SECTORS", "1").strip().lower() not in {"0", "false", "no", "off"}:
            try:
                sectors = WebSearchHotSectorProvider().find_hot_sectors(context.market, limit=limit)
            except Exception as exc:
                context.warnings.append(f"WebSearch 热点板块识别失败: {type(exc).__name__}: {exc}")
                sectors = []
            if sectors:
                context.hot_sectors = [item.name for item in sectors]
                context.hot_sector_sources = [item.source for item in sectors if item.source]
                context.sector_documents = [
                    SearchDocument(
                        title=f"WebSearch 热点板块: {item.name}",
                        url=item.source or "web_search",
                        content=f"{item.name}: 热度分={item.score:.4f}; {item.reason}",
                        score=item.score,
                        query=context.sector_query,
                    )
                    for item in sectors
                ]
                context.warnings.append(f"[AI分析] 热点板块: WebSearch 动态发现 {len(sectors)} 个")
                return

        # Priority 2: Manual config (env var) — explicit override or fallback
        manual = context.manual_hot_sectors
        if manual.hot_sectors:
            context.hot_sectors = list(manual.hot_sectors)
            context.hot_sector_sources = list(manual.sources) or ["manual_config"]
            context.sector_documents = manual.documents(context.sector_query)
            context.warnings.append("已使用手动配置的热点板块覆盖自动识别结果")
            return

        # Priority 3: API hot sectors (akshare) — only for A shares
        if os.getenv("SIGNAL_ENABLE_API_HOT_SECTORS", "1").strip().lower() in {"0", "false", "no", "off"}:
            return

        try:
            sectors = AkshareHotSectorProvider().find_hot_sectors(context.market, limit=limit)
        except Exception as exc:
            context.warnings.append(f"行情 API 热点板块识别失败: {type(exc).__name__}: {exc}")
            sectors = []
        if not sectors:
            return

        context.hot_sectors = [item.name for item in sectors]
        context.hot_sector_sources = [item.source for item in sectors if item.source]
        context.sector_documents = [
            SearchDocument(
                title=f"行情 API 热点板块: {item.name}",
                url=item.source or "market_api",
                content=f"{item.name}: 热度分={item.score:.4f}; {item.reason}",
                score=item.score,
                query=context.sector_query,
            )
            for item in sectors
        ]


class LLMBatchAnalysisStep(AnalysisStep):
    name = "LLMBatchAnalysisStep"

    def run(self, context: SignalAnalysisContext) -> None:
        if not context.rows and context.results_by_code:
            return
        if not context.llm_provider.is_available:
            context.aborted = True
            if _has_llm_preflight_failure(context.warnings):
                context.skipped_reason = "LLM provider 网络预检失败，跳过 AI 辅助分析"
            else:
                context.skipped_reason = "未配置 LLM provider，跳过 AI 辅助分析"
            context.warnings.append(context.skipped_reason)
            return

        # Fetch real-time stock snapshots for context enrichment
        stock_snapshots = _fetch_stock_snapshots(context.rows)

        batch_size = max(1, int(context.settings.batch_size))
        failed_rows: Dict[str, str] = {}
        success_count = 0
        for start in range(0, len(context.rows), batch_size):
            batch = context.rows[start:start + batch_size]
            try:
                results = context.llm_provider.analyze_batch(
                    market=context.market,
                    signals=batch,
                    market_documents=context.market_documents,
                    sector_documents=context.sector_documents,
                    hot_sectors=context.hot_sectors,
                    company_documents=context.company_documents,
                    stock_snapshots=stock_snapshots,
                )
            except Exception as exc:
                context.warnings.extend(_drain_llm_provider_warnings(context.llm_provider))
                message = f"模型批量分析失败: {type(exc).__name__}: {exc}"
                context.warnings.append(message)
                if _is_llm_quota_error(exc):
                    context.aborted = True
                    context.skipped_reason = "LLM provider 额度不足，跳过 AI 辅助分析"
                    context.warnings.append(context.skipped_reason)
                    return
                for row in batch:
                    failed_rows[row.code] = message
                continue

            context.warnings.extend(_drain_llm_provider_warnings(context.llm_provider))
            for result in results:
                context.results_by_code[result.code] = result
                success_count += 1

        if success_count == 0:
            context.aborted = True
            context.skipped_reason = "模型分析没有成功返回任何股票结果，跳过 AI 附件生成"
            context.warnings.append(context.skipped_reason)
            return

        for row in context.rows:
            if row.code in failed_rows and row.code not in context.results_by_code:
                context.results_by_code[row.code] = SignalAnalysisResult.error(
                    row,
                    failed_rows[row.code],
                    model=context.llm_provider.model_name,
                )


class NormalizeAnalysisStep(AnalysisStep):
    name = "NormalizeAnalysisStep"

    def run(self, context: SignalAnalysisContext) -> None:
        for row in context.rows:
            result = context.results_by_code.get(row.code)
            if result is None:
                context.results_by_code[row.code] = SignalAnalysisResult.error(
                    row,
                    "模型未返回该股票结果",
                    model=context.llm_provider.model_name,
                )
            elif not result.name:
                result.name = row.name
            result = context.results_by_code.get(row.code)
            if result is not None:
                _fill_news_fallbacks(
                    result,
                    market_documents=context.market_documents,
                    company_documents=context.company_documents.get(row.code, []),
                    manual_hot_news=context.manual_hot_news,
                    code=row.code,
                )
                _fill_hot_sector_fallbacks(
                    row=row,
                    result=result,
                    hot_sectors=context.hot_sectors,
                    hot_sector_sources=context.hot_sector_sources,
                    sector_documents=context.sector_documents,
                )
                apply_evidence_to_result(
                    result=result,
                    row=row,
                    source_documents=[
                        *context.market_documents,
                        *context.sector_documents,
                        *(context.company_documents.get(row.code) or []),
                    ],
                    main_force_context_label="股票筛选",
                )


class PersistAnalysisStep(AnalysisStep):
    name = "PersistAnalysisStep"

    def run(self, context: SignalAnalysisContext) -> None:
        if context.repository is None:
            return
        rows = []
        for row in (context.all_rows or context.rows):
            item = context.results_by_code[row.code].to_db_row(
                task_id=context.task_id,
                market=context.market,
                check_date=context.check_date,
                csv_path=context.csv_path,
                timeframe=context.timeframe,
                analysis_profile=context.analysis_profile,
            )
            provider = context.company_news_providers.get(row.code) or _current_company_news_provider(context.search_provider)
            if provider:
                item["company_news_provider"] = provider
            rows.append(item)
        try:
            context.repository.save_results(rows)
        except Exception as exc:
            context.warnings.append(f"AI 分析结果落库失败: {type(exc).__name__}: {exc}")


class WriteArtifactsStep(AnalysisStep):
    name = "WriteArtifactsStep"

    def run(self, context: SignalAnalysisContext) -> None:
        report_path = analysis_report_path_for_csv(context.csv_path)

        write_analysis_columns_to_csv(context.csv_path, context.results_by_code)

        with open(report_path, "w", encoding="utf-8") as f:
            f.write(_render_artifact_report(context))

        context.artifact_paths.append(report_path)


class SignalAnalysisChain:
    """Runs analysis steps and converts failures into warnings."""

    def __init__(self, steps: Optional[List[AnalysisStep]] = None):
        self.steps = steps or [
            LoadCsvSignalsStep(),
            LoadCachedResultsStep(),
            BuildSearchQueriesStep(),
            MarketIntelEvidenceStep(),
            SearchContextStep(),
            ApplyManualHotNewsStep(),
            MarketIntelFinalizeEvidencePacksStep(),
            ExpandCompanyEvidenceStep(),
            ResolveHotSectorsStep(),
            LLMBatchAnalysisStep(),
            NormalizeAnalysisStep(),
            PersistAnalysisStep(),
            WriteArtifactsStep(),
        ]

    def run(self, context: SignalAnalysisContext) -> AnalysisRunResult:
        from concurrent.futures import ThreadPoolExecutor

        i = 0
        while i < len(self.steps):
            if context.aborted:
                break

            step = self.steps[i]

            # ── Parallel: MarketIntelEvidenceStep + SearchContextStep ──
            if (
                i + 1 < len(self.steps)
                and isinstance(step, MarketIntelEvidenceStep)
                and isinstance(self.steps[i + 1], SearchContextStep)
            ):
                step5 = self.steps[i + 1]
                errors: List[str] = []

                def _run_and_collect(s, ctx):
                    try:
                        s.run(ctx)
                        return None
                    except Exception as exc:
                        return f"{s.name} 执行失败: {type(exc).__name__}: {exc}"

                with ThreadPoolExecutor(max_workers=2) as executor:
                    f4 = executor.submit(_run_and_collect, step, context)
                    f5 = executor.submit(_run_and_collect, step5, context)
                    for err in (f4.result(), f5.result()):
                        if err:
                            errors.append(err)

                if errors:
                    context.warnings.extend(errors)
                    # Don't abort — both steps are best-effort, other steps can still proceed

                i += 2
                continue

            try:
                step.run(context)
            except Exception as exc:
                context.aborted = True
                context.skipped_reason = f"{step.name} 执行失败"
                context.warnings.append(f"{step.name} 执行失败: {type(exc).__name__}: {exc}")
                break

            i += 1

        return AnalysisRunResult(
            success=bool(context.artifact_paths),
            artifact_paths=list(context.artifact_paths),
            warnings=list(context.warnings),
            results_by_code=dict(context.results_by_code),
            evidence_packs=dict(context.evidence_packs),
            analyzed_count=len(context.results_by_code),
            skipped_reason=context.skipped_reason,
        )


def _drain_llm_provider_warnings(provider: LLMProvider) -> List[str]:
    drain = getattr(provider, "drain_warnings", None)
    if not callable(drain):
        return []
    return list(drain())


def _fetch_stock_snapshots(
    rows: List["ScreeningSignalRow"],
) -> Dict[str, Dict[str, object]]:
    """Fetch real-time stock snapshot data (price, change%, PE, etc.).

    Uses Sina API for A-shares; returns empty dict for unavailable markets.
    Each snapshot dict is safe to inject into the LLM prompt.
    """
    snapshots: Dict[str, Dict[str, object]] = {}
    if not rows:
        return snapshots

    a_share_rows = [r for r in rows if _row_market(r) == "A" and r.code and not getattr(r, "is_etf", False)]
    if not a_share_rows:
        return snapshots

    try:
        import requests
    except ImportError:
        return snapshots

    # Build Sina batch query: up to 50 codes per request
    sina_codes = []
    for row in a_share_rows[:50]:
        ticker = _normalize_sina_ticker(row.code)
        if not ticker:
            continue
        sina_codes.append((row.code, ticker))

    if not sina_codes:
        return snapshots

    query_str = ",".join(t for _, t in sina_codes)
    url = f"https://hq.sinajs.cn/list={query_str}"
    try:
        resp = requests.get(url, headers={"Referer": "https://finance.sina.com.cn"}, timeout=10)
        resp.raise_for_status()
        resp.encoding = "gbk"
        raw = resp.text
    except Exception:
        return snapshots

    # Parse Sina response: var hq_str_SH600519="name,open,prev_close,price,high,low,..."
    for code, ticker in sina_codes:
        try:
            prefix = f'var hq_str_{ticker}="'
            start = raw.find(prefix)
            if start == -1:
                continue
            start += len(prefix)
            end = raw.find('"', start)
            if end == -1:
                continue
            fields = raw[start:end].split(",")
            if len(fields) < 32:
                continue

            snapshots[code] = {
                "price": _safe_float(fields[3]),
                "change_pct": _safe_float(fields[9]) if len(fields) > 9 else None,
                "volume": _safe_int(fields[8]) if len(fields) > 8 else None,
                "turnover_rate": None,
                "total_mv_cny_billion": None,
                "pe_ttm": _safe_float(fields[31]) if len(fields) > 31 else None,
                "source": "sina",
            }
        except Exception:
            continue

    return snapshots


def _row_market(row) -> str:
    market = str(getattr(row, "market", "") or "").upper()
    if market in ("A", "HK", "US"):
        return market
    code = str(getattr(row, "code", "") or "").strip().upper()
    if code.startswith(("SH", "SZ", "BJ")) or code.endswith((".SH", ".SZ", ".BJ", ".SS")):
        return "A"
    if code.startswith("HK") or code.endswith(".HK"):
        return "HK"
    if code.startswith("US") or code.endswith((".US", ".O", ".N")):
        return "US"
    return market or "A"


def _normalize_sina_ticker(code: str) -> str:
    """Convert internal code to Sina ticker format (e.g. 'sh600519')."""
    value = str(code).strip()
    if "." in value:
        prefix, ticker = value.split(".", 1)
        prefix = prefix.upper()
        if prefix in ("SH", "SZ", "BJ", "SS"):
            return f"{prefix.lower()}{ticker}"
        if prefix == "HK":
            return ""  # Sina uses different format for HK
        if prefix == "US":
            return ""  # Sina US format differs
    if value.upper().startswith("SH"):
        return value.lower()
    if value.upper().startswith("SZ"):
        return value.lower()
    if value.upper().startswith("BJ"):
        return value.lower()
    if value.isdigit() and len(value) >= 6:
        val6 = value[-6:]
        if val6.startswith(("60", "68", "90")):
            return f"sh{val6}"
        if val6.startswith(("00", "30", "20")):
            return f"sz{val6}"
        if val6.startswith(("43", "83", "87", "88")):
            return f"bj{val6}"
    return ""


def _safe_float(val: str) -> Optional[float]:
    try:
        return float(str(val).strip())
    except (ValueError, TypeError):
        return None


def _safe_int(val: str) -> Optional[int]:
    try:
        return int(float(str(val).strip()))
    except (ValueError, TypeError):
        return None


def _apply_sentiment_to_company_documents(context: "SignalAnalysisContext") -> None:
    """Pre-score company documents with Chinese financial sentiment lexicon.

    Injects a ``[情感:正面|得分:+2.5]`` tag into each document's content field
    and sets ``doc.score`` to the sentiment score for downstream use.
    """
    try:
        from .sentiment import get_default_analyzer
    except ImportError:
        return

    analyzer = get_default_analyzer()
    for code, docs in context.company_documents.items():
        updated: List[SearchDocument] = []
        for doc in docs:
            text = f"{doc.title} {doc.content}"
            result = analyzer.analyze(text)
            new_content = f"{analyzer.analyze_to_tag(text)} {doc.content}"
            updated.append(
                SearchDocument(
                    title=doc.title,
                    url=doc.url,
                    content=new_content,
                    score=result.score,
                    query=doc.query,
                )
            )
        context.company_documents[code] = updated


_SEARCH_SOURCE_DISPLAY_MAP = {
    "bing_baidu": "Bing/Baidu（免费）",
    "tavily": "Tavily",
    "zhipuai": "ZhipuAI",
    "zhipu": "ZhipuAI",
}


def _format_search_source_label(context: SignalAnalysisContext) -> str:
    """Dynamically build the search source label for the report banner."""
    provider_name = _current_company_news_provider(context.search_provider)
    display = _SEARCH_SOURCE_DISPLAY_MAP.get(provider_name, provider_name)
    if display:
        return display

    # Fallback: enumerate all available provider names
    candidates = _company_news_provider_candidates(context.search_provider)
    labels = [
        _SEARCH_SOURCE_DISPLAY_MAP.get(c, c)
        for c in candidates
    ]
    return "/".join(labels) if labels else "Tavily/ZhipuAI"


def _company_news_provider_candidates(search_provider: SearchProvider) -> List[str]:
    if not getattr(search_provider, "is_available", False):
        return []
    names = getattr(search_provider, "provider_names", None)
    if names:
        return _dedupe([str(name).strip() for name in names if str(name).strip()])
    name = str(getattr(search_provider, "name", search_provider.__class__.__name__) or "").strip()
    if not name or name == "null":
        return []
    return [name]


def _current_company_news_provider(search_provider: SearchProvider) -> str:
    provider = str(getattr(search_provider, "last_success_provider", "") or "").strip()
    if provider:
        return provider
    candidates = _company_news_provider_candidates(search_provider)
    return candidates[0] if len(candidates) == 1 else ""


def _load_valid_company_news_cache(
    *,
    getter: Callable[..., Optional[dict]],
    context: SignalAnalysisContext,
    row: ScreeningSignalRow,
    provider_candidates: List[str],
) -> Optional[dict]:
    min_company_events = _cache_min_company_events()
    for provider in provider_candidates:
        cached = getter(
            context.market,
            row.code,
            context.timeframe,
            context.analysis_profile,
            context.check_date,
            provider,
        )
        if not cached:
            continue
        events = cached.get("company_events") or []
        if len(events) < min_company_events:
            context.warnings.append(
                "[AI分析] 公司时事缓存未达标: "
                f"code={row.code} provider={provider} "
                f"company_events={len(events)}<{min_company_events}"
            )
            continue
        return cached
    return None


def _merge_company_news_cache(base: dict, company_news: dict) -> dict:
    merged = dict(base)
    for key in (
        "company_events",
        "company_hot_news",
        "news_impact",
        "news_sources",
        "source_urls",
    ):
        merged[key] = company_news.get(key)
    return merged


def _has_search_preflight_failure(warnings: List[str]) -> bool:
    return any("联网检索预检失败" in warning for warning in warnings)


def _has_llm_preflight_failure(warnings: List[str]) -> bool:
    return any("LLM provider" in warning and "预检失败" in warning for warning in warnings)


def _is_llm_quota_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return (
        "http 429" in text
        and (
            "insufficient_quota" in text
            or "exceeded your current quota" in text
            or "billing" in text
        )
    )


def _company_search_batch_size() -> int:
    raw = os.getenv("SIGNAL_COMPANY_SEARCH_BATCH_SIZE", "10").strip()
    try:
        return max(1, int(raw))
    except ValueError:
        return 10


def _cache_min_company_events() -> int:
    raw = os.getenv("SIGNAL_CACHE_MIN_COMPANY_EVENTS", "0").strip()
    try:
        return max(0, int(raw))
    except ValueError:
        return 0


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        return int(raw)
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _build_etf_theme_query(market: str, rows: List[ScreeningSignalRow]) -> str:
    max_chars = _env_int("SIGNAL_COMPANY_SEARCH_QUERY_MAX_CHARS", 390)
    max_chars = min(400, max(120, max_chars))
    prefix = f"{MARKET_NAMES.get(market, market)} ETF 基金 跟踪指数 投资主题 板块 宏观影响: "
    items = []
    for row in rows:
        name = " ".join((row.name or "").split())
        item = f"{row.code} {name}".strip()
        if item:
            items.append(item)
    query = f"{prefix}{'；'.join(items)}".strip()
    if len(query) <= max_chars:
        return query
    return query[:max_chars].rstrip("；,; ")


def _chunk_rows(rows: List[ScreeningSignalRow], batch_size: int) -> List[List[ScreeningSignalRow]]:
    return [rows[start:start + batch_size] for start in range(0, len(rows), batch_size)]


def _fill_news_fallbacks(
    result: SignalAnalysisResult,
    market_documents: List[SearchDocument],
    company_documents: List[SearchDocument],
    manual_hot_news: ManualHotNewsConfig,
    code: str,
) -> None:
    manual_market_news = manual_hot_news.market_hot_news
    manual_company_news = manual_hot_news.company_hot_news(code)
    manual_sources = manual_hot_news.news_sources_for(code)

    if manual_market_news:
        result.market_hot_news = list(manual_market_news)
    elif not result.market_hot_news:
        result.market_hot_news = _documents_to_news(market_documents)

    if manual_company_news:
        result.company_hot_news = list(manual_company_news)
    elif not result.company_hot_news:
        result.company_hot_news = _documents_to_news(company_documents)

    if manual_sources:
        result.news_sources = list(manual_sources)
    elif not result.news_sources:
        result.news_sources = _documents_to_urls([*market_documents, *company_documents])

    if not result.source_urls:
        result.source_urls = list(result.news_sources)
    if not result.news_impact:
        result.news_impact = "未明确判断" if (market_documents or company_documents) else "信息不足"


def _fill_hot_sector_fallbacks(
    row: ScreeningSignalRow,
    result: SignalAnalysisResult,
    hot_sectors: List[str],
    hot_sector_sources: List[str],
    sector_documents: List[SearchDocument],
) -> None:
    if hot_sectors and not result.hot_sectors:
        result.hot_sectors = list(hot_sectors)
    elif not result.hot_sectors:
        result.hot_sectors = _documents_to_hot_sectors(sector_documents)

    if hot_sector_sources and not result.hot_sector_sources:
        result.hot_sector_sources = _dedupe(hot_sector_sources)
    elif not result.hot_sector_sources:
        result.hot_sector_sources = _documents_to_urls(sector_documents)

    if result.hot_sector_mark:
        return

    matched = _match_hot_sectors(row.sector, row.name, result.hot_sectors)
    if matched:
        result.hot_sector_mark = "重点"
        result.matched_hot_sectors = matched
        result.hot_sector_relevance = "100"
        subject = "ETF/基金主题" if row.is_etf else "股票所属板块/名称"
        result.hot_sector_reason = f"{subject}与热点板块直接匹配: {'；'.join(matched)}"
    elif result.hot_sectors and row.sector:
        result.hot_sector_mark = "观察"
        result.hot_sector_relevance = "30"
        result.hot_sector_reason = "当前所属板块/主题未直接匹配热点板块，但保留观察市场轮动"
    elif result.hot_sectors:
        result.hot_sector_mark = "未知"
        result.hot_sector_relevance = ""
        result.hot_sector_reason = "缺少所属板块/主题资料，无法稳定判断热点板块归属"
    else:
        result.hot_sector_mark = "未知"
        result.hot_sector_relevance = ""
        result.hot_sector_reason = "未识别到明确热点板块"


def _documents_to_news(documents: List[SearchDocument]) -> List[str]:
    news = []
    for doc in documents[:2]:
        title = (doc.title or "").strip()
        content = (doc.content or "").strip()
        text = title or content
        if not text:
            continue
        if len(text) > 120:
            text = f"{text[:120]}..."
        news.append(text)
    return news


def _documents_to_urls(documents: List[SearchDocument]) -> List[str]:
    urls = []
    seen = set()
    for doc in documents:
        url = (doc.url or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        urls.append(url)
    return urls


def _documents_to_hot_sectors(documents: List[SearchDocument]) -> List[str]:
    sectors = []
    for doc in documents[:3]:
        text = (doc.title or doc.content or "").strip()
        for sep in ("、", "，", ",", "；", ";", "|"):
            text = text.replace(sep, "\n")
        for item in text.split("\n"):
            item = item.strip()
            if not item:
                continue
            if len(item) > 40:
                continue
            if any(keyword in item for keyword in ("热点", "板块", "行业", "概念", "sector", "Sector")):
                sectors.append(item)
    return _dedupe(sectors)[:10]


def _match_hot_sectors(sector: str, name: str, hot_sectors: List[str]) -> List[str]:
    haystack = f"{sector or ''} {name or ''}".lower()
    matches = []
    for item in hot_sectors:
        text = (item or "").strip()
        if not text:
            continue
        text_lower = text.lower()
        if text_lower in haystack or (sector and sector.lower() in text_lower):
            matches.append(text)
    return _dedupe(matches)


def _dedupe(items: List[str]) -> List[str]:
    seen = set()
    result = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def write_analysis_columns_to_csv(
    csv_path: str,
    results_by_code: Dict[str, SignalAnalysisResult],
) -> None:
    """
    Append or refresh AI analysis columns in an existing CSV.

    The write uses a temporary file in the same directory and then atomically
    replaces the original path, so a failed write does not leave a partial CSV.
    """
    with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)

    output_fieldnames = list(fieldnames)
    for column in AI_CSV_COLUMNS:
        if column not in output_fieldnames:
            output_fieldnames.append(column)

    directory = os.path.dirname(os.path.abspath(csv_path)) or "."
    basename = os.path.basename(csv_path)
    temp_path = ""
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8-sig",
            newline="",
            dir=directory,
            prefix=f".{basename}.",
            suffix=".tmp",
            delete=False,
        ) as f:
            temp_path = f.name
            writer = csv.DictWriter(f, fieldnames=output_fieldnames)
            writer.writeheader()
            for row in rows:
                output = dict(row)
                for column in AI_CSV_COLUMNS:
                    output[column] = ""
                code = (row.get("股票代码") or row.get("code") or "").strip()
                result = results_by_code.get(code)
                if result is not None:
                    market = (row.get("market") or row.get("市场") or "").strip()
                    signal_row = ScreeningSignalRow.from_csv_row(row, index=0, default_market=market)
                    output.update(result.to_csv_columns(signal_row))
                writer.writerow(output)
        os.replace(temp_path, csv_path)
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.unlink(temp_path)
            except OSError:
                pass


# ── Feature flag: switch to new RetailReportRenderer ───────────
USE_NEW_RENDERER = os.getenv("USE_NEW_RENDERER", "1") == "1"


def _build_stock_dict(
    row: ScreeningSignalRow,
    result: SignalAnalysisResult,
) -> dict:
    """Convert internal objects to stock dict expected by RetailReportRenderer."""
    unified = compute_unified_score(result, row)
    entry_score = unified.final_score if unified else 50.0
    holding_score = (
        result.reliability_score
        if result.reliability_score is not None
        else entry_score
    )

    # Build one-liner reason from conditions_met
    conds = (row.conditions_met or "").strip()
    if conds:
        parts = [
            p.split(":", 1)[1].strip() if ":" in p else p
            for p in conds.split("|")
            if p.strip()
        ]
        one_liner = "；".join(parts[:3]) if parts else "综合信号"
    elif result.hot_sector_mark in ("重点", "相关"):
        matched = "；".join(result.matched_hot_sectors or [])
        one_liner = f"热点板块匹配: {matched}" if matched else "热点方向匹配"
    elif result.summary:
        one_liner = result.summary
    else:
        one_liner = "综合技术信号与热点方向判断"

    # Build risk text
    risk_parts: list[str] = []
    if result.risk_factors:
        risk_parts.extend(result.risk_factors[:2])
    risk_level = (row.main_force_risk_level or "").strip()
    if risk_level:
        risk_parts.append(f"主力风险: {risk_level}")
    main_risk = "；".join(risk_parts) if risk_parts else "市场系统性风险或个股不确定性"

    # Build buy reason
    buy_reason_parts: list[str] = []
    if conds:
        buy_reason_parts.append("技术信号命中")
    if result.hot_sector_mark in ("重点", "相关"):
        buy_reason_parts.append("热点板块匹配")
    if result.company_events:
        buy_reason_parts.append("有公司事件催化")
    buy_reason = "；".join(buy_reason_parts) if buy_reason_parts else "综合技术信号与热点方向判断"

    # Build hold value
    hold_value_parts: list[str] = []
    if result.positive_factors:
        hold_value_parts.extend(result.positive_factors[:2])
    if getattr(row, "pe_ratio", "") or getattr(row, "market_cap", ""):
        hold_value_parts.append("有基本面数据支撑")
    hold_value = "；".join(hold_value_parts) if hold_value_parts else "企业基本面尚可"

    return {
        "code": result.code,
        "name": result.name or row.name or result.code,
        "sector": row.sector or "",
        "industry": row.sector or "",
        "entry_score": entry_score,
        "holding_score": holding_score,
        "one_liner": one_liner,
        "brief_reason": one_liner,
        "summary": result.summary or one_liner,
        "main_risk": main_risk,
        "risk": main_risk,
        "top_risk": main_risk,
        "risk_factor": main_risk,
        "buy_reason": buy_reason,
        "entry_reason": buy_reason,
        "positive_factor": buy_reason,
        "hold_value": hold_value,
        "holding_reason": hold_value,
        "enterprise_value": hold_value,
        "watch_point": "关注后续成交量变化和关键支撑位",
        "observation": "关注后续成交量变化和关键支撑位",
        "macro_risk": main_risk,
        "industry_risk": main_risk,
        "data_gap": "；".join(result.data_gaps) if result.data_gaps else "",
    }


def _build_hot_sectors(hot_sectors: list[str]) -> dict[str, list[str]]:
    """Map flat hot sector list to categorized dict for RetailReportRenderer."""
    if not hot_sectors:
        return {}
    return {"industry": list(hot_sectors), "theme": [], "region": []}


def _build_data_source_status(context: SignalAnalysisContext) -> list[dict]:
    """Build data source status list for the report."""
    check_date_str = (
        context.check_date.isoformat() if context.check_date else "—"
    )
    has_search = (
        context.search_provider.is_available
        if hasattr(context.search_provider, "is_available")
        else False
    )
    has_llm = (
        context.llm_provider.is_available
        if hasattr(context.llm_provider, "is_available")
        else False
    )
    llm_name = (
        context.llm_provider.model_name
        if hasattr(context.llm_provider, "model_name")
        else "LLM"
    )
    return [
        {
            "dimension": "技术信号",
            "source": "Futu OpenD / YFinance / AKShare",
            "status": "正常",
            "updated_at": check_date_str,
        },
        {
            "dimension": "公司新闻",
            "source": "联网搜索",
            "status": "正常" if has_search else "不可用",
            "updated_at": check_date_str,
        },
        {
            "dimension": "AI 分析",
            "source": llm_name,
            "status": "正常" if has_llm else "不可用",
            "updated_at": check_date_str,
        },
    ]


def _render_artifact_report(context: SignalAnalysisContext) -> str:
    if context.evidence_packs:
        report_rows = context.all_rows or context.rows
        packs = [
            context.evidence_packs[row.code]
            for row in report_rows
            if row.code in context.evidence_packs
        ]
        results = []
        for row in report_rows:
            if row.code not in context.results_by_code:
                continue
            result_row = context.results_by_code[row.code].to_db_row(
                task_id=context.task_id,
                market=context.market,
                check_date=context.check_date,
                csv_path=context.csv_path,
                timeframe=context.timeframe,
                analysis_profile=context.analysis_profile,
            )
            result_row["conditions_met"] = row.conditions_met
            result_row.update(compute_unified_score(context.results_by_code[row.code], row).to_csv_columns())
            results.append(result_row)
        return render_multi_stock_report(packs, results, report_date=context.check_date)

    if USE_NEW_RENDERER:
        # Pre-compute market temperature for the report
        market_cache = MarketCache()
        market_temp = market_cache.get_or_compute(context.market)

        # Build stock list from results
        report_rows = context.all_rows or context.rows
        stocks: list[dict] = []
        for row in report_rows:
            result = context.results_by_code.get(row.code)
            if result is None:
                continue
            stocks.append(_build_stock_dict(row, result))

        # Sort by entry_score descending
        stocks.sort(key=lambda s: s.get("entry_score", 0), reverse=True)

        renderer = RetailReportRenderer()
        renderer_context = {
            "market": context.market,
            "market_temp": market_temp,
            "stocks": stocks,
            "hot_sectors": _build_hot_sectors(context.hot_sectors),
            "report_date": context.check_date.isoformat(),
            "chain_key": context.chain_key or "",
            "data_sources": _build_data_source_status(context),
        }
        return renderer.render(renderer_context)

    return _render_markdown_report(context)  # deprecated, use RetailReportRenderer


# deprecated, use RetailReportRenderer
def _render_markdown_report(context: SignalAnalysisContext) -> str:
    """Render v2 simplified report: market bg → scoring → overview → macro → top5 → risks."""
    from collections import Counter

    report_rows = context.all_rows or context.rows
    results = [context.results_by_code[row.code] for row in report_rows if row.code in context.results_by_code]
    rows_by_code = {row.code: row for row in report_rows}
    unified_by_code = {
        result.code: compute_unified_score(result, rows_by_code.get(result.code))
        for result in results
    }

    ranked = sorted(
        results,
        key=lambda item: unified_by_code[item.code].final_score,
        reverse=True,
    )

    # ── Dynamic rule extraction from CSV ────────────────────
    cond_counts = Counter()
    for row in report_rows:
        conds = (row.conditions_met or "").strip()
        if conds:
            for c in conds.split("|"):
                c = c.strip()
                if c:
                    cond_counts[c] += 1
    condition_values = [str(row.conditions_met or "") for row in report_rows]
    uses_unified_bullish_chain = any(
        marker in value
        for value in condition_values
        for marker in ("看涨:", "准备反弹:", "左一看涨:")
    )
    report_chain_key = (
        (context.chain_key or "").strip()
        or ("unified_bullish_top20" if uses_unified_bullish_chain else "CSV信号规则链")
    )

    # ── Stats ───────────────────────────────────────────────
    stock_ranked = [item for item in ranked if not _is_etf_result(item, rows_by_code)]
    hot_matched = [item for item in ranked if item.hot_sector_mark in {"重点", "相关"}]
    event_supported = [item for item in stock_ranked if _meaningful_company_items(item)]
    main_force_rows = [row for row in report_rows if _has_main_force_risk(row)]
    mf_high = [row for row in main_force_rows if row.main_force_risk_level == "高"]
    mf_medium = [row for row in main_force_rows if row.main_force_risk_level == "中"]
    mf_signal_text = _main_force_top_signal_text(main_force_rows)
    overall = _overall_strength_from_scores([unified_by_code[item.code].final_score for item in ranked])
    hot_sector_text = "；".join(context.hot_sectors) if context.hot_sectors else "暂未识别到明确市场热点"
    market_name = MARKET_NAMES.get(context.market, context.market)

    def _short_items(items, *, limit=3, max_len=72):
        values = []
        for item in items or []:
            text = _table_text(str(item or "")).strip()
            if not text or text in {"-", "—"}:
                continue
            if len(text) > max_len:
                text = text[:max_len].rstrip() + "..."
            if text not in values:
                values.append(text)
            if len(values) >= limit:
                break
        return values

    def _market_bundle_items(bundle):
        if not isinstance(bundle, dict):
            return []
        values = []

        def walk(value):
            if isinstance(value, dict):
                title = value.get("title") or value.get("name") or value.get("指标") or value.get("source")
                content = (
                    value.get("summary")
                    or value.get("content")
                    or value.get("description")
                    or value.get("value")
                    or value.get("text")
                )
                if title and content:
                    values.append(f"{title}: {content}")
                elif content:
                    values.append(str(content))
                for child in value.values():
                    walk(child)
            elif isinstance(value, list):
                for child in value:
                    walk(child)

        walk(bundle)
        return values

    def _market_background_items():
        items = []
        items.extend(_market_bundle_items(context.market_intel_market_bundle))
        items.extend(doc.content or doc.title for doc in context.market_documents)
        for result in ranked:
            items.extend(result.market_hot_news)
            items.extend(result.macro_factors)
        return _short_items(items, limit=5, max_len=96)

    market_background_items = _market_background_items()

    def _event_news_cell(item: SignalAnalysisResult) -> str:
        company_events = _short_items(item.company_events, limit=2)
        company_hot_news = _short_items(item.company_hot_news, limit=2)
        related_hot = _short_items(
            [
                *(item.matched_hot_sectors or []),
                item.hot_sector_reason,
                *(item.market_hot_news or []),
            ],
            limit=2,
        )
        parts = []
        if company_events:
            parts.append("公司事件：" + "；".join(company_events))
        if company_hot_news:
            parts.append("公司热点：" + "；".join(company_hot_news))
        if related_hot:
            parts.append("关联热点：" + "；".join(related_hot))
        return "<br>".join(parts) if parts else "暂无明确事件/热点新闻"

    def _hot_cell(item: SignalAnalysisResult) -> str:
        mark = item.hot_sector_mark or "—"
        lines = [f"{_hot_emoji(mark)} {mark}".strip()]
        matched = _short_items(item.matched_hot_sectors, limit=3, max_len=48)
        if matched:
            lines.append("匹配：" + "；".join(matched))
        reason = _short_items([item.hot_sector_reason], limit=1, max_len=72)
        if reason:
            lines.append(f"<small>{reason[0]}</small>")
        elif not matched and mark not in {"—", "无明确关联", "未知"}:
            lines.append("<small>暂无明确匹配说明</small>")
        return "<br>".join(lines)

    # ── Top 5 picks (bullish, different sectors) ────────────
    def _pick_top5(ranked_results, by_code):
        bullish = [r for r in ranked_results if r.signal_bias == "bullish"]
        others = [r for r in ranked_results if r.signal_bias != "bullish"]
        ordered = bullish + others
        picked, seen = [], set()
        for r in ordered:
            if len(picked) >= 5:
                break
            row = by_code.get(r.code)
            sec = (row.sector or "").strip() if row else ""
            if sec and sec not in seen:
                picked.append(r)
                seen.add(sec)
        for r in ordered:
            if len(picked) >= 5:
                break
            if r not in picked:
                row = by_code.get(r.code)
                pseudo = _infer_sector_label(r, row)
                if pseudo not in seen:
                    picked.append(r)
                    seen.add(pseudo)
        return picked[:5]

    top5 = _pick_top5(ranked, rows_by_code)

    # ── Scoring factor helpers ──────────────────────────────
    def _bias_emoji(bias):
        return {"bullish": "🟢", "bearish": "🔴", "neutral": "🟡"}.get(bias or "", "⚪")

    def _hot_emoji(mark):
        return {"重点": "⭐", "相关": "🔗", "观察": "👀"}.get(mark or "", "")

    def _build_score_with_formula(item, row):
        """Display unified score formula (delegates to compute_unified_score).

        技术规则决定 Top20 入围（权重 0%），最终评分口径：
          五模块×40% + 事件热点×30% + 资金风险×20% + LLM复核×10%
        """
        unified = unified_by_code.get(item.code)
        if unified is not None:
            return unified.final_score, unified.formula
        return 50.0, "评分缺失，按50中性补齐"

    def _rule_text(row, *, limit=6):
        conds = (row.conditions_met or "") if row else ""
        items = [part.strip() for part in conds.split("|") if part.strip()]
        if not items:
            return "—"
        visible = items[:limit]
        suffix = f"<br>+{len(items) - limit}条" if len(items) > limit else ""
        return "<br>".join(_table_text(item) for item in visible) + suffix

    def _rule_key_guess(condition: str) -> str:
        label = condition.split(":", 1)[1].strip() if ":" in condition else condition.strip()
        mapping = {
            "左一战法-看涨": "zuoyi_bullish_signal",
            "左一战法-看跌": "zuoyi_signal",
            "EMA突破": "ema_breakout",
            "EMA金叉": "ema_golden_cross",
            "均线金叉": "sma_golden_cross",
            "MACD金叉": "macd_bullish_cross",
            "KDJ金叉": "kdj_bullish_cross",
            "低位KDJ金叉": "kdj_low_bullish_cross",
            "RSI超卖": "rsi_oversold",
            "RSI超卖回升": "rsi_bullish_rebound",
            "布林下轨反弹": "bollinger_lower_rebound",
            "放量超前三日": "volume_spike_prior3",
            "放量突破": "volume_price_breakout",
            "当日涨4%~4.5%": "daily_rise_4_45",
            "当日跌6%~6.5%": "daily_drop_6_65",
        }
        return mapping.get(label, "dynamic_rule")

    def _rule_logic(condition: str) -> str:
        if condition.startswith("准备反弹:"):
            return "准备反弹类信号，纳入技术规则命中"
        if condition.startswith("左一看涨:"):
            return "左一看涨信号，纳入技术规则命中"
        if condition.startswith("看涨:"):
            return "看涨类信号，纳入技术规则命中"
        if "放量" in condition:
            return "资金关注度信号"
        if "RSI超卖" in condition:
            return "超跌反弹潜力"
        if "看跌" in condition or "超买" in condition:
            return "风险或回落信号"
        return "动态技术规则命中"

    def _pick_reason(r, row):
        reasons = []
        conds = (row.conditions_met or "") if row else ""
        if conds:
            fallback_rules = []
            for group in ("左一看涨", "准备反弹", "看涨"):
                matches = [
                    part.split(":", 1)[1].strip()
                    for part in conds.split("|")
                    if part.strip().startswith(f"{group}:") and ":" in part
                ]
                if matches:
                    reasons.append(f"{group}: {'、'.join(matches[:2])}")
            if not reasons:
                fallback_rules = [part.strip() for part in conds.split("|") if part.strip()]
                reasons.extend(fallback_rules[:3])
        elif r.signal_bias == "bullish":
            reasons.append("左一看涨")
        elif r.signal_bias == "bearish":
            reasons.append("左一看跌（注意方向）")
        if r.hot_sector_mark in ("重点", "相关"):
            m = "; ".join(r.matched_hot_sectors) if r.matched_hot_sectors else r.hot_sector_mark
            reasons.append(f"热点: {m}")
        conds = (row.conditions_met or "") if row else ""
        if "放量" in conds:
            reasons.append("放量确认")
        raw2 = getattr(row, "raw", {}) if row else {}
        bd2 = (raw2.get("左一突破用时", "") or raw2.get("breakthrough_days", "")).strip()
        if bd2 and bd2.isdigit() and int(bd2) <= 2:
            reasons.append(f"仅{bd2}日突破，动能强")
        sr = (raw2.get("左一支撑区间", "") or raw2.get("support_range", "")).strip()
        if sr:
            reasons.append(f"支撑: {sr}")
        return "；".join(reasons) if reasons else "综合信号"

    def _recommendation(r, row):
        s = unified_by_code.get(r.code).final_score if r.code in unified_by_code else 0
        if r.signal_bias == "bullish":
            if s >= 70: return "🟢 买入"
            if s >= 55: return "🟡 持有/观察"
            return "🟠 轻仓观察"
        if r.signal_bias == "neutral":
            return "🟡 持有/观察"
        return "⚠️ 回避"

    # ── Macro factor analysis status ────────────────────────
    macro_status_icons = []
    # Quick check: are any macro factors available?
    has_any_macro = any(
        bool(getattr(r, "macro_factors", None) or getattr(r, "market_hot_news", None))
        for r in results
    )
    macro_note = "⚠️ 本期宏观因子因网络或数据源问题未能完整采集" if not has_any_macro else "✅ 宏观因子已采集"

    # ── Render ──────────────────────────────────────────────
    lines = [
        f"# {market_name}观察池信号复核报告",
        "",
        f"**报告日期**：{context.check_date.strftime('%Y年%m月%d日')}　｜　**标的数量**：{len(report_rows)}只　｜　**周期**：{context.timeframe}",
        "",
        "> ⚠️ 本报告基于市场信号、热点方向和宏观环境进行综合复核，仅用于辅助判断，**不构成投资建议**。",
        "",
        "---",
        "",
        f"## 一、市场背景",
        "",
        f"- **市场**：{market_name}",
        f"- **热点板块**：**{hot_sector_text}**",
        f"- **标的数量**：{len(report_rows)}只　｜　**周期**：{context.timeframe}",
        "",
        "### 当前市场信息",
        "",
        *([f"- {item}" for item in market_background_items] or ["- 市场实时摘要暂未获取，可检查 SIGNAL_ENABLE_MARKET_INTEL 或搜索数据源。"]),
        "",
        "---",
        "",
        "## 二、评分体系",
        "",
        f"本报告使用规则链 **`{report_chain_key}`**（{market_name}）的评分框架：",
        "",
        "```",
        "最终统一评分 = 技术规则分(0%) + 宏观五模块分(40%) + 事件热点分(30%) + 资金风险分(20%) + LLM复核分(10%)",
        "```",
        "",
        "### 1.1 本期技术规则（用于入围，权重0%不纳入最终评分）",
        "",
    ]

    # Dynamic rules table
    if cond_counts:
        lines.append("| 技术规则 | 规则Key | 触发次数 | 触发率 | 评分逻辑 |")
        lines.append("|---|---|---|---|---|")
        for name, count in cond_counts.most_common():
            pct = count / max(1, len(report_rows)) * 100
            lines.append(
                f"| {_table_text(name)} | {_rule_key_guess(name)} | {count} | {pct:.0f}% | {_rule_logic(name)} |"
            )
    else:
        lines.append("本期无技术规则触发数据。")

    lines.extend([
        "",
        "### 1.2 最终统一评分因子",
        "",
        "| 分项 | 权重 | 说明 |",
        "|---|---|---|",
        "| 技术规则分 | 0% | 看涨、准备反弹、左一看涨命中数用于入围Top20，不纳入最终评分 |",
        "| 宏观五模块分 | 40% | 宏观、行业、企业质量、估值、交易；缺失模块按50中性补齐 |",
        "| 事件热点分 | 30% | 公司事件、公司热点新闻、市场热点新闻、热点板块匹配 |",
        "| 资金风险分 | 20% | 主力风险分或主力风险等级；缺失按50中性补齐 |",
        "| LLM复核分 | 10% | 信号可靠性、模型置信度、利好/风险因素和方向判断 |",
        "",
        "### 1.3 宏观五模块（`enterprise_potential_analysis`，纳入最终统一评分40%）",
        "",
        "| 模块 | 权重 | 核心指标 |",
        "|---|---|---|",
        "| ① 宏观 | 20% | CPI/PMI/M2/LPR/VIX/DXY/美债 |",
        "| ② 行业 | 20% | 行业景气度、板块资金流、热点匹配 |",
        "| ③ 企业质量 | 25% | 盈利、成长性、财务健康 |",
        "| ④ 估值 | 15% | PE分位、PB、PS估值水位 |",
        "| ⑤ 交易 | 20% | 量价信号、技术形态确认 |",
        "",
        f"> {macro_note}。评分区间：80+ 重点关注 / 60-79 可关注 / 40-59 偏弱观察 / <40 谨慎。",
        "",
        "---",
        "",
        "## 三、信号复核总览",
        "",
        f"**热点板块**（{'动态发现' if context.hot_sectors else '待识别'}）：**{hot_sector_text}**",
        "",
        "| # | 代码 | 名称 | 板块 | 方向 | 最终评分 | 命中规则 | 最终评分依据 | 热点 | 事件 |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ])
    for i, item in enumerate(ranked, 1):
        row = rows_by_code.get(item.code)
        sector = (row.sector or "—") if row else "—"
        unified = unified_by_code[item.code]
        missing_note = (
            "<br><small>缺失：" + _table_text("；".join(unified.missing_items[:3])) + "</small>"
            if unified.missing_items else ""
        )
        lines.append(
            f"| {i} | `{item.code}` | {item.name or '-'} | {_table_text(sector)} | "
            f"{_bias_emoji(item.signal_bias)} {_direction_label(item.signal_bias)} | "
            f"**{unified.final_score:.1f}** | "
            f"{_rule_text(row)} | "
            f"{_table_text(unified.formula)}{missing_note} | "
            f"{_hot_cell(item)} | "
            f"{_event_news_cell(item)} |"
        )
    lines.extend([
        "",
        f"**综合信号强度**：{overall}　｜　热点匹配：{len(hot_matched)}/{len(ranked)}　｜　主力风险可评估：{len(main_force_rows)}/{len(ranked)}",
        "",
        "---",
        "",
        "## 四、宏观与市场环境影响",
        "",
    ])

    # Simple macro impact section
    macro_items = list(context.hot_sectors or [])
    if not macro_items:
        macro_items = ["市场主线待识别"]
    macro_detail = "；".join(macro_items[:6])

    mf_risk_note = (
        f"⚠️ 主力数据：{len(main_force_rows)}/{len(report_rows)} 可评估"
        if len(main_force_rows) < len(report_rows)
        else "✅ 主力数据：全部可评估"
    )

    lines.extend([
        f"- **热点方向**：{macro_detail}",
        f"- **信号强度**：{overall}（最终均分 {sum(unified_by_code[r.code].final_score for r in ranked) / max(1, len(ranked)):.1f}）",
        f"- **主力风险**：高 {len(mf_high)} / 中 {len(mf_medium)} / 数据不足 {len(report_rows) - len(main_force_rows)}",
        f"- **公司催化**：{len(event_supported)}/{len(stock_ranked)} 只个股有明确事件支撑",
        "",
        "> 宏观因素仅作背景参考，不单独构成买入依据。",
        "",
        "---",
        "",
        "## 五、精选推荐",
        "",
        f"从{len(report_rows)}只信号标的中，按不同板块各选1只最具潜力的看涨股票：",
        "",
        "| 股票 | 板块 | 信号 | 最终评分 | 建议 | 核心理由 |",
        "|------|------|------|------|------|---------|",
    ])

    for r in top5:
        row = rows_by_code.get(r.code)
        sector = (row.sector or _infer_sector_label(r, row)) if row else "—"
        lines.append(
            f"| **{r.name or r.code}**<br>`{r.code}` | {sector} | "
            f"{_bias_emoji(r.signal_bias)} {_direction_label(r.signal_bias)} | "
            f"**{unified_by_code[r.code].final_score:.1f}** | "
            f"{_recommendation(r, row)} | "
            f"{_pick_reason(r, row)} |"
        )

    lines.extend([
        "",
        "| 建议 | 含义 |",
        "|------|------|",
        "| 🟢 买入 | 信号较强，热点匹配，可考虑建仓 |",
        "| 🟡 持有/观察 | 信号可关注，需等待更多确认 |",
        "| 🟠 轻仓观察 | 有信号但风险因素多，小仓位试探 |",
        "| ⚠️ 回避 | 看跌信号，不建议此时介入 |",
        "",
        "---",
        "",
        "## 六、风险提示",
        "",
    ])

    risks = []
    if len(main_force_rows) == 0:
        risks.append("1. **主力数据缺失** ⚠️ 全部标的主力流出风险数据不足，缺资金面验证")
    elif len(main_force_rows) < len(report_rows):
        risks.append(f"1. **主力数据部分缺失** ⚠️ {len(report_rows) - len(main_force_rows)}只主力流出风险数据不足")
    no_sector_count = sum(1 for row in report_rows if not (row.sector or "").strip())
    if no_sector_count > 0:
        risks.append(f"2. **板块数据缺失** {no_sector_count}只（{no_sector_count / max(1, len(report_rows)) * 100:.0f}%）CSV板块字段为空")
    if not event_supported:
        risks.append("3. **公司事件缺失** 联网检索未获取到公司事件，事件维度为「信息不足」")
    pe_missing = sum(1 for row in report_rows if not (getattr(row, 'pe_ratio', '') or "").strip() and not (getattr(row, 'market_cap', '') or "").strip())
    if pe_missing > len(report_rows) // 2:
        risks.append(f"4. **基本面稀疏** {pe_missing}只PE/市值缺失，估值判断依据不足")
    risks.append(f"5. **热点覆盖** {len(hot_matched)}/{len(ranked)} 只匹配当前热点，关注板块轮动风险")

    lines.extend(risks)
    lines.extend([
        "",
        "---",
        "",
        "## 附录",
        "",
        f"- 规则链：{report_chain_key}（{market_name}）",
        f"- 热点板块：{hot_sector_text}",
        f"- 数据来源：Futu OpenD + YFinance + {_format_search_source_label(context)} + DeepSeek",
        f"- 分析引擎：{context.llm_provider.model_name if hasattr(context.llm_provider, 'model_name') else 'LLM'}",
        "",
        "> 该分析仅用于辅助判断，不构成投资建议。",
        "",
    ])

    return "\n".join(lines)


def _infer_sector_label(item, row) -> str:
    """Infer sector category from name when CSV sector is empty."""
    if row and row.sector:
        return row.sector
    name = (item.name if hasattr(item, 'name') else str(row.name if row else '')).lower()
    kw_map = [
        (["药", "医", "health", "pharma", "bio", "康"], "Healthcare"),
        (["科技", "tech", "智能", "软件", "数据", "网"], "Technology"),
        (["电力", "能源", "电", "power", "energy", "utility"], "Utilities"),
        (["消费", "饮料", "食品", "茶", "零售", "蜜雪", "周六福", "纽曼思"], "Consumer"),
        (["汽车", "车", "auto", "motor", "交通", "运输"], "Auto/Transport"),
        (["金融", "银行", "保险", "券商", "证券"], "Financial"),
        (["地产", "物业", "房产"], "Real Estate"),
    ]
    for kws, label in kw_map:
        if any(kw in name for kw in kws):
            return label
    return "综合"


def _numeric(value: Optional[float]) -> float:
    return -1.0 if value is None else float(value)


def _format_score(value: Optional[float]) -> str:
    return "无评分" if value is None else f"{value:.2f}"


def _overall_strength(results: List[SignalAnalysisResult]) -> str:
    if not results:
        return "信息不足"
    scores = [_numeric(item.reliability_score) for item in results if item.reliability_score is not None]
    return _overall_strength_from_scores(scores)


def _overall_strength_from_scores(scores: List[float]) -> str:
    if not scores:
        return "信息不足"
    average = sum(scores) / len(scores)
    if average >= 70:
        return "偏强"
    if average >= 50:
        return "中性"
    if average >= 25:
        return "偏弱"
    return "风险较高"


def _pool_category(
    results: List[SignalAnalysisResult],
    attention: List[SignalAnalysisResult],
    cautious: List[SignalAnalysisResult],
    insufficient: List[SignalAnalysisResult],
) -> str:
    if not results:
        return "暂不跟踪池"
    if len(attention) >= max(1, len(results) // 2):
        return "重点观察池"
    if attention:
        return "普通观察池"
    if len(cautious) == len(results) or len(insufficient) == len(results):
        return "暂不跟踪池"
    return "普通观察池"


def _direction_label(value: str) -> str:
    mapping = {
        "bullish": "看涨",
        "bearish": "看跌",
        "neutral": "中性",
        "avoid": "回避",
        "unknown": "信息不足",
    }
    return mapping.get((value or "").strip(), value or "信息不足")


def _hot_mark_label(value: str) -> str:
    if not value:
        return "未知"
    return value


def _is_etf_result(
    item: SignalAnalysisResult,
    rows_by_code: Dict[str, ScreeningSignalRow],
) -> bool:
    row = rows_by_code.get(item.code)
    return bool(row and row.is_etf)


def _display_sector_theme(row: Optional[ScreeningSignalRow]) -> str:
    if row is None:
        return "资料暂缺"
    value = (row.sector or "").strip()
    if value:
        return value
    return "主题资料暂缺" if row.is_etf else "资料暂缺"


def _has_main_force_risk(row: Optional[ScreeningSignalRow]) -> bool:
    if row is None:
        return False
    return any([
        row.main_force_risk_level,
        row.main_force_risk_score,
        row.main_force_risk_signals,
        row.main_force_risk_summary,
        row.main_force_missing_data,
    ])


def _main_force_risk_for_table(row: Optional[ScreeningSignalRow]) -> str:
    if not _has_main_force_risk(row):
        return "数据不足"
    score = f"（{row.main_force_risk_score}分）" if row and row.main_force_risk_score else ""
    return f"{row.main_force_risk_level or '数据不足'}{score}"


def _main_force_top_signal_text(rows: List[ScreeningSignalRow]) -> str:
    signals: List[str] = []
    for row in rows:
        if row.main_force_risk_signals:
            signals.extend([item.strip() for item in row.main_force_risk_signals.split("；") if item.strip()])
    deduped = _dedupe(signals)
    return "；".join(deduped[:5]) if deduped else "暂无明确主力流出信号"


def _main_force_market_observation(row: Optional[ScreeningSignalRow]) -> str:
    if row is None:
        return "暂无资金与盘面明细"
    if row.main_force_market_data_observation:
        return row.main_force_market_data_observation
    items = [
        _main_force_readable_fund_flow(row.main_force_fund_flow_data),
        _main_force_readable_order_book(row.main_force_order_book_data),
        _main_force_readable_chip(row.main_force_chip_data),
    ]
    values = [item for item in items if item]
    return "；".join(values) if values else "暂无资金与盘面明细"


def _main_force_readable_fund_flow(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if text.startswith("可用"):
        return "资金流向: 已取得明细"
    return "资金流向: 暂无明细"


def _main_force_readable_order_book(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if text.startswith("可用"):
        return "盘口: 已取得买卖盘明细"
    return "盘口: 暂无明细"


def _main_force_readable_chip(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if "成交量分布" in text:
        return "成交量分布: 已取得K线成交量分布"
    return ""


def _append_main_force_detail(lines: List[str], row: Optional[ScreeningSignalRow]) -> None:
    if row is None or not _has_main_force_risk(row):
        lines.extend(["", "**主力流出风险：** 数据不足"])
        return
    score = f"（{row.main_force_risk_score}分）" if row.main_force_risk_score else ""
    lines.extend([
        "",
        f"**主力流出风险：** {row.main_force_risk_level or '数据不足'}{score}",
    ])
    if row.main_force_risk_signals:
        lines.extend(["", "**触发信号：**"])
        for value in row.main_force_risk_signals.split("；")[:5]:
            value = value.strip()
            if value:
                lines.append(f"- {value}")
    if row.main_force_risk_summary:
        lines.extend(["", f"**风险说明：** {_friendly_text(row.main_force_risk_summary)}"])
    gap_items = [item.strip() for item in row.main_force_missing_data.split("；") if item.strip()]
    if gap_items:
        lines.extend(["", "**数据缺口：**"])
        for value in gap_items[:5]:
            lines.append(f"- {value}")


def _hot_mark_label_for_row(value: str, row: Optional[ScreeningSignalRow]) -> str:
    label = _hot_mark_label(value)
    if label != "未知":
        return label
    if row is not None and row.is_etf:
        return "主题资料暂缺"
    return "行业资料不足"


def _event_status(item: SignalAnalysisResult) -> str:
    if _meaningful_company_items(item):
        return "是"
    return "否"


def _event_type(item: SignalAnalysisResult) -> str:
    text = "；".join(_meaningful_company_items(item))
    if not text:
        return "无"
    labels = []
    checks = [
        ("业绩", ("业绩", "财报", "盈利", "亏损", "增长")),
        ("订单", ("订单", "合同", "中标")),
        ("政策", ("政策", "补贴", "监管", "许可")),
        ("并购", ("并购", "收购", "重组")),
        ("产品/技术", ("产品", "技术", "研发", "突破")),
        ("增持/回购", ("增持", "回购")),
        ("风险事件", ("处罚", "诉讼", "减持", "违约", "亏损")),
    ]
    for label, keywords in checks:
        if any(keyword in text for keyword in keywords):
            labels.append(label)
    return "；".join(labels) if labels else "其他事件"


def _meaningful_company_items(item: SignalAnalysisResult) -> List[str]:
    invalid = {"无重大事件", "无明确公司事件", "无相关新闻", "无明确事件", "暂无", "无"}
    values = []
    for value in [*item.company_events, *item.company_hot_news]:
        text = (value or "").strip()
        if not text:
            continue
        if text in invalid:
            continue
        if text.startswith("无") and len(text) <= 8:
            continue
        values.append(text)
    return values


def _news_impact_label(value: str) -> str:
    if not value:
        return "未明确判断"
    if value == "信息不足":
        return "信息不足"
    return value


def _join_or_default(items: List[str], default: str) -> str:
    return "；".join(item for item in items if item) or default


def _signal_brief(item: SignalAnalysisResult) -> str:
    return (
        f"`{item.code}`{item.name}"
        f"({ _format_score(item.reliability_score) }/{item.signal_bias or 'unknown'})"
    )


def _entry_exit_timing_text(conditions_met: str) -> str:
    text = _friendly_text(conditions_met or "")
    if not text:
        return "暂未提供买卖点信息"
    text = text.replace("|", "；")
    if "暂未找到明确买入点" in text or "暂未找到明确卖出点" in text:
        return text
    return text


def _has_clear_entry_exit_timing(conditions_met: str) -> bool:
    text = _friendly_text(conditions_met or "")
    return "出现看涨买点" in text or "出现看跌卖点" in text


def _one_line_position(
    item: SignalAnalysisResult,
    row: Optional[ScreeningSignalRow],
) -> str:
    if item.reliability_score is not None and item.reliability_score >= 60:
        return "信号值得继续跟踪"
    if item.hot_sector_mark in {"重点", "相关"}:
        return "热点方向可关注"
    if _has_clear_entry_exit_timing(row.conditions_met if row else ""):
        return "出现买卖点提示但仍需确认"
    if _has_information_gap(item, row):
        return "信息不足，先观察"
    return "普通观察"


def _brief_conclusion(
    item: SignalAnalysisResult,
    row: Optional[ScreeningSignalRow],
) -> str:
    timing = _entry_exit_timing_text(row.conditions_met if row else "")
    hot = item.hot_sector_reason or "热点方向暂未形成明确支撑"
    if row is not None and row.is_etf:
        theme = _display_sector_theme(row)
        return f"{timing}；{_friendly_text(hot)}；按ETF/基金主题观察，当前主题为{theme}。"
    event = _join_or_default(item.company_events, "公司事件暂不明确")
    return f"{timing}；{_friendly_text(hot)}；{event}。"


def _tracking_suggestion(
    item: SignalAnalysisResult,
    row: Optional[ScreeningSignalRow],
) -> str:
    score = _numeric(item.reliability_score)
    if row is not None and row.is_etf:
        if score >= 80 and item.hot_sector_mark in {"重点", "相关"}:
            return "重点跟踪，观察主题热度、指数走势和宏观环境是否继续共振"
        if score >= 60:
            return "普通观察，关注跟踪主题是否持续受到资金关注"
        return "暂不提高优先级，等待主题热度或买卖点进一步确认"
    if score >= 80 and item.hot_sector_mark in {"重点", "相关"}:
        return "重点跟踪，等待买卖点和公司事件继续确认"
    if score >= 60:
        return "普通观察，关注后续是否有热点或公司事件支撑"
    if _has_clear_entry_exit_timing(row.conditions_met if row else ""):
        return "普通观察，暂不提高优先级"
    if score < 20 or item.signal_bias in {"avoid", "unknown"}:
        return "暂不跟踪，等待更多信息"
    return "普通观察，等待更明确的触发因素"


def _top_macro_items(results: List[SignalAnalysisResult]) -> List[str]:
    items: List[str] = []
    for result in results:
        items.extend(result.macro_factors)
        items.extend(result.market_hot_news)
    return _dedupe([_friendly_text(item) for item in items if item])[:8]


def _related_stocks_for_factor(factor: str, results: List[SignalAnalysisResult]) -> str:
    related = []
    for item in results:
        joined = "；".join([*item.macro_factors, *item.market_hot_news])
        if factor and factor in joined:
            related.append(item.name or item.code)
    return "；".join(related[:8]) if related else "本批股票"


def _table_text(text: str) -> str:
    return (text or "-").replace("|", "/").replace("\n", " ").strip()


def _friendly_text(text: str) -> str:
    """Translate internal strategy wording into report-friendly language."""
    if not text:
        return ""
    replacements: List[Tuple[str, str]] = [
        ("默认规则链未通过：左一及其他策略未命中", "暂未找到明确买入点，其他辅助信号也不够强"),
        ("默认规则链未通过：左一战法未命中", "暂未找到明确买入点"),
        ("默认规则链未通过", "暂未找到明确买入点"),
        ("规则链未通过左一战法", "暂未找到明确买入点"),
        ("规则链未形成强确认", "买卖点尚未形成较强确认"),
        ("规则链未通过", "暂未找到明确买入点"),
        ("左一战法未命中", "暂未找到明确买入点"),
        ("左一及其他策略未命中", "暂未找到明确买入点，其他辅助信号也不够强"),
        ("左一战法-看涨", "出现看涨买点"),
        ("左一战法-看跌", "出现看跌卖点"),
        ("EMA突破", "趋势信号改善"),
        ("其他策略", "其他辅助信号"),
        ("策略", "辅助信号"),
        ("量化信号", "市场信号"),
        ("核心规则", "核心条件"),
    ]
    result = text
    for old, new in replacements:
        result = result.replace(old, new)
    return result


def _has_information_gap(
    item: SignalAnalysisResult,
    row: Optional[ScreeningSignalRow],
) -> bool:
    return bool(_information_gap_reasons(item, row))


def _information_gap_reasons(
    item: SignalAnalysisResult,
    row: Optional[ScreeningSignalRow],
) -> List[str]:
    reasons: List[str] = []
    is_etf = bool(row is not None and row.is_etf)
    if is_etf:
        if not item.company_hot_news and not item.market_hot_news:
            reasons.append("缺少明确ETF主题或基金新闻")
    else:
        if not item.company_hot_news:
            reasons.append("缺少明确公司新闻")
        if not item.company_events:
            reasons.append("缺少明确公司事件")
    if item.news_impact == "信息不足":
        reasons.append("目前信息不足，无法判断新闻方向")
    if not item.news_sources and not item.source_urls:
        reasons.append("缺少可追溯新闻来源")
    if item.hot_sector_mark in {"未知", "无明确关联"} or (
        not item.matched_hot_sectors and item.hot_sector_mark not in {"重点", "相关"}
    ):
        reasons.append("热点主题未直接匹配" if is_etf else "热点板块未直接匹配")
    if row is not None and not row.sector:
        reasons.append("原始 CSV 缺少主题资料" if is_etf else "原始 CSV 缺少所属板块")
    if item.confidence_score is not None and item.confidence_score < 40:
        reasons.append("判断信心偏低")
    return _dedupe(reasons)
