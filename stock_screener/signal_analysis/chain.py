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
from typing import Dict, List, Optional, Protocol, Tuple

from .llm_providers import LLMProvider
from .hot_news import ManualHotNewsConfig
from .hot_sectors import AkshareHotSectorProvider, ManualHotSectorConfig
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
)
from .search_providers import SearchProvider


AI_CSV_COLUMNS = [
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
    repository: Optional[SignalAnalysisRepository] = None
    manual_hot_news: ManualHotNewsConfig = field(default_factory=ManualHotNewsConfig)
    manual_hot_sectors: ManualHotSectorConfig = field(default_factory=ManualHotSectorConfig)
    rows: List[ScreeningSignalRow] = field(default_factory=list)
    csv_fieldnames: List[str] = field(default_factory=list)
    market_query: str = ""
    sector_query: str = ""
    company_queries: Dict[str, str] = field(default_factory=dict)
    market_documents: List[SearchDocument] = field(default_factory=list)
    sector_documents: List[SearchDocument] = field(default_factory=list)
    company_documents: Dict[str, List[SearchDocument]] = field(default_factory=dict)
    hot_sectors: List[str] = field(default_factory=list)
    hot_sector_sources: List[str] = field(default_factory=list)
    results_by_code: Dict[str, SignalAnalysisResult] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)
    artifact_paths: List[str] = field(default_factory=list)
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
        if not context.rows:
            context.aborted = True
            context.skipped_reason = "CSV 没有可分析的股票行"
            context.warnings.append(context.skipped_reason)


class BuildSearchQueriesStep(AnalysisStep):
    name = "BuildSearchQueriesStep"

    def run(self, context: SignalAnalysisContext) -> None:
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
        if not context.search_provider.is_available:
            context.warnings.append("未配置搜索 provider，跳过联网检索，仅使用 CSV 信号交给模型分析")
            return

        try:
            context.market_documents = context.search_provider.search(
                context.market_query,
                context.settings.search_max_results,
            )
        except Exception as exc:
            context.warnings.append(f"市场上下文搜索失败: {type(exc).__name__}: {exc}")
            context.market_documents = []

        try:
            context.sector_documents = context.search_provider.search(
                context.sector_query,
                context.settings.search_max_results,
            )
        except Exception as exc:
            context.warnings.append(f"热点板块搜索失败: {type(exc).__name__}: {exc}")
            context.sector_documents = []

        batch_size = _company_search_batch_size()
        for batch in _chunk_rows(context.rows, batch_size):
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
                    context.company_documents[row.code] = []
                continue

            missing_codes = []
            for row in batch:
                documents = list((batch_documents or {}).get(row.code, []))
                context.company_documents[row.code] = documents
                if not documents:
                    missing_codes.append(row.code)
            if missing_codes:
                context.warnings.append(
                    f"公司事件批量搜索未匹配到 {len(missing_codes)} 只股票: {','.join(missing_codes)}"
                )


class ApplyManualHotNewsStep(AnalysisStep):
    name = "ApplyManualHotNewsStep"

    def run(self, context: SignalAnalysisContext) -> None:
        config = context.manual_hot_news
        if config.market_hot_news:
            context.market_documents = config.market_documents(context.market_query)
            context.warnings.append("已使用手动配置的市场热点信息覆盖搜索热点信息")

        for row in context.rows:
            if not config.company_hot_news(row.code):
                continue
            context.company_documents[row.code] = config.company_documents(
                row.code,
                context.company_queries.get(row.code, ""),
            )
            context.warnings.append(f"{row.code} 已使用手动配置的公司热点信息覆盖搜索热点信息")


class ResolveHotSectorsStep(AnalysisStep):
    name = "ResolveHotSectorsStep"

    def run(self, context: SignalAnalysisContext) -> None:
        manual = context.manual_hot_sectors
        if manual.hot_sectors:
            context.hot_sectors = list(manual.hot_sectors)
            context.hot_sector_sources = list(manual.sources) or ["manual_config"]
            context.sector_documents = manual.documents(context.sector_query)
            context.warnings.append("已使用手动配置的热点板块覆盖自动识别结果")
            return

        if os.getenv("SIGNAL_ENABLE_API_HOT_SECTORS", "1").strip().lower() in {"0", "false", "no", "off"}:
            return

        limit = max(1, int(os.getenv("SIGNAL_HOT_SECTOR_LIMIT", "10") or "10"))
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
        if not context.llm_provider.is_available:
            context.aborted = True
            context.skipped_reason = "未配置 LLM provider，跳过 AI 辅助分析"
            context.warnings.append(context.skipped_reason)
            return

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
                )
            except Exception as exc:
                context.warnings.extend(_drain_llm_provider_warnings(context.llm_provider))
                message = f"模型批量分析失败: {type(exc).__name__}: {exc}"
                context.warnings.append(message)
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


class PersistAnalysisStep(AnalysisStep):
    name = "PersistAnalysisStep"

    def run(self, context: SignalAnalysisContext) -> None:
        if context.repository is None:
            return
        rows = [
            context.results_by_code[row.code].to_db_row(
                task_id=context.task_id,
                market=context.market,
                check_date=context.check_date,
                csv_path=context.csv_path,
            )
            for row in context.rows
        ]
        try:
            context.repository.save_results(rows)
        except Exception as exc:
            context.warnings.append(f"AI 分析结果落库失败: {type(exc).__name__}: {exc}")


class WriteArtifactsStep(AnalysisStep):
    name = "WriteArtifactsStep"

    def run(self, context: SignalAnalysisContext) -> None:
        report_path = _derive_artifact_path(context.csv_path, "_ai_report.md")

        write_analysis_columns_to_csv(context.csv_path, context.results_by_code)

        with open(report_path, "w", encoding="utf-8") as f:
            f.write(_render_markdown_report(context))

        context.artifact_paths.append(report_path)


class SignalAnalysisChain:
    """Runs analysis steps and converts failures into warnings."""

    def __init__(self, steps: Optional[List[AnalysisStep]] = None):
        self.steps = steps or [
            LoadCsvSignalsStep(),
            BuildSearchQueriesStep(),
            SearchContextStep(),
            ApplyManualHotNewsStep(),
            ResolveHotSectorsStep(),
            LLMBatchAnalysisStep(),
            NormalizeAnalysisStep(),
            PersistAnalysisStep(),
            WriteArtifactsStep(),
        ]

    def run(self, context: SignalAnalysisContext) -> AnalysisRunResult:
        for step in self.steps:
            if context.aborted:
                break
            try:
                step.run(context)
            except Exception as exc:
                context.aborted = True
                context.skipped_reason = f"{step.name} 执行失败"
                context.warnings.append(f"{step.name} 执行失败: {type(exc).__name__}: {exc}")
                break

        return AnalysisRunResult(
            success=bool(context.artifact_paths),
            artifact_paths=list(context.artifact_paths),
            warnings=list(context.warnings),
            results_by_code=dict(context.results_by_code),
            analyzed_count=len(context.results_by_code),
            skipped_reason=context.skipped_reason,
        )


def _derive_artifact_path(csv_path: str, suffix: str) -> str:
    if csv_path.endswith(".csv"):
        return f"{csv_path[:-4]}{suffix}"
    return f"{csv_path}{suffix}"


def _drain_llm_provider_warnings(provider: LLMProvider) -> List[str]:
    drain = getattr(provider, "drain_warnings", None)
    if not callable(drain):
        return []
    return list(drain())


def _company_search_batch_size() -> int:
    raw = os.getenv("SIGNAL_COMPANY_SEARCH_BATCH_SIZE", "10").strip()
    try:
        return max(1, int(raw))
    except ValueError:
        return 10


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
        result.hot_sector_reason = f"股票所属板块/名称与热点板块直接匹配: {'；'.join(matched)}"
    elif result.hot_sectors and row.sector:
        result.hot_sector_mark = "观察"
        result.hot_sector_relevance = "30"
        result.hot_sector_reason = "当前所属板块未直接匹配热点板块，但保留观察市场轮动"
    elif result.hot_sectors:
        result.hot_sector_mark = "未知"
        result.hot_sector_relevance = ""
        result.hot_sector_reason = "股票缺少所属板块，无法稳定判断热点板块归属"
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
                    output.update(result.to_csv_columns())
                writer.writerow(output)
        os.replace(temp_path, csv_path)
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.unlink(temp_path)
            except OSError:
                pass


def _render_markdown_report(context: SignalAnalysisContext) -> str:
    market_name = MARKET_NAMES.get(context.market, context.market)
    report_title = f"{market_name}观察池信号复核报告"
    results = [context.results_by_code[row.code] for row in context.rows]
    rows_by_code = {row.code: row for row in context.rows}
    ranked = sorted(
        results,
        key=lambda item: -1 if item.reliability_score is None else item.reliability_score,
        reverse=True,
    )
    attention = [item for item in ranked if _numeric(item.reliability_score) >= 60]
    cautious = [
        item for item in ranked
        if _numeric(item.reliability_score) < 40 or item.signal_bias in {"avoid", "unknown"}
    ]
    insufficient = [item for item in ranked if _has_information_gap(item, rows_by_code.get(item.code))]
    hot_matched = [item for item in ranked if item.hot_sector_mark in {"重点", "相关"}]
    event_supported = [item for item in ranked if _meaningful_company_items(item)]
    confirmed_timing = [
        item for item in ranked
        if _has_clear_entry_exit_timing(rows_by_code.get(item.code).conditions_met if rows_by_code.get(item.code) else "")
    ]
    overall_strength = _overall_strength(ranked)
    pool_category = _pool_category(ranked, attention, cautious, insufficient)
    hot_sector_text = "；".join(context.hot_sectors) if context.hot_sectors else "暂未识别到明确市场热点"

    lines = [
        f"# {report_title}",
        "",
        f"**报告日期：** {context.check_date.strftime('%Y年%m月%d日')}",
        f"**覆盖股票数量：** {len(context.rows)}只",
        "**报告用途：** 辅助判断 / 观察池复核 / 信号解释",
        "**适用读者：** 投研、业务负责人、非技术背景读者",
        "",
        "> 本报告基于市场信号、热点方向、公司事件和宏观环境进行综合复核，仅用于辅助判断，不构成投资建议。",
        "",
        "---",
        "",
        "## 一、核心结论",
        "",
        f"本次共复核 **{len(context.rows)}只股票**。整体来看，当前信号强度为：**{overall_strength}**。",
        "",
        "本批股票的主要特点是：",
        "",
        f"1. **买卖点确认：** 明确出现买入/卖出提示的股票为 **{len(confirmed_timing)}只**；其余股票暂未看到足够明确的买卖点。",
        f"2. **热点匹配：** 与当前热点方向直接或较强相关的股票为 **{len(hot_matched)}只**；当前识别热点为：**{hot_sector_text}**。",
        f"3. **公司催化：** 有明确公司新闻、公告或事件支撑的股票为 **{len(event_supported)}只**。",
        f"4. **信息充分度：** 存在信息缺口或判断依据偏弱的股票为 **{len(insufficient)}只**。",
        "",
        f"**综合判断：** 本批股票更适合归类为：**{pool_category}**。",
        "",
        "---",
        "",
        "## 二、本次复核结果总览",
        "",
        "| 股票代码 | 股票名称 | 综合评分 | 方向判断 | 热点匹配 | 简明结论 |",
        "|---|---|---:|---|---|---|",
    ]
    for item in ranked:
        lines.append(
            "| "
            f"`{item.code}` | "
            f"{item.name or '-'} | "
            f"{_format_score(item.reliability_score)} | "
            f"{_direction_label(item.signal_bias)} | "
            f"{_hot_mark_label(item.hot_sector_mark)} | "
            f"{_table_text(_friendly_text(item.summary or _brief_conclusion(item, rows_by_code.get(item.code))))} |"
        )

    lines.extend([
        "",
        "---",
        "",
        "## 三、整体信号解读",
        "",
        "### 1. 买卖点确认情况",
        "",
        f"- 已看到较明确买入/卖出提示的股票：{len(confirmed_timing)}只",
        f"- 暂未看到明确买入/卖出提示的股票：{max(0, len(context.rows) - len(confirmed_timing))}只",
        f"- 综合评分较高、可重点跟踪的股票：{len(attention)}只",
        f"- 评分偏低或信息不足的股票：{len(cautious)}只",
        "",
        "**解读：** 如果多数股票暂未出现明确买卖点，说明当前更适合观察，不宜只凭放量、上涨或短期异动做判断。",
        "",
        "### 2. 热点板块匹配情况",
        "",
        f"本次识别的市场热点包括：**{hot_sector_text}**",
        "",
        "| 股票名称 | 所属方向 | 热点匹配情况 | 解读 |",
        "|---|---|---|---|",
    ])
    for item in ranked:
        row = rows_by_code.get(item.code)
        lines.append(
            "| "
            f"{item.name or item.code} | "
            f"{_table_text((row.sector if row else '') or '未补齐')} | "
            f"{_hot_mark_label(item.hot_sector_mark)} | "
            f"{_table_text(_friendly_text(item.hot_sector_reason or '暂未看到与热点方向的明确关系'))} |"
        )

    lines.extend([
        "",
        "**解读：** 热点匹配度越高，越可能受到市场资金关注；如果不在当前主线上，即使出现短期异动，也需要降低预期。",
        "",
        "### 3. 公司事件支撑情况",
        "",
        "| 股票名称 | 是否有明确事件 | 事件类型 | 影响判断 |",
        "|---|---|---|---|",
    ])
    for item in ranked:
        lines.append(
            "| "
            f"{item.name or item.code} | "
            f"{_event_status(item)} | "
            f"{_table_text(_event_type(item))} | "
            f"{_news_impact_label(item.news_impact)} |"
        )

    lines.extend([
        "",
        "**解读：** 有明确公司事件支撑的信号，通常可信度更高；如果只有市场异动、缺少事件解释，后续持续性需要谨慎评估。",
        "",
        "---",
        "",
        "## 四、宏观与市场环境影响",
        "",
        "### 1. 当前宏观背景",
        "",
    ])
    macro_items = _top_macro_items(ranked)
    if macro_items:
        for item in macro_items:
            lines.append(f"- {_friendly_text(item)}")
    else:
        lines.append("- 暂未提炼出对本批股票有明确影响的宏观变量。")

    lines.extend([
        "",
        "### 2. 对本批股票的影响",
        "",
        "| 影响因素 | 可能影响方向 | 相关股票 | 判断 |",
        "|---|---|---|---|",
    ])
    if macro_items:
        for factor in macro_items[:5]:
            related = _related_stocks_for_factor(factor, ranked)
            lines.append(
                f"| {_table_text(_friendly_text(factor))} | 间接影响 | {_table_text(related)} | 需要结合热点方向和公司事件继续确认 |"
            )
    else:
        lines.append("| 暂无明确宏观变量 | 不明确 | - | 影响有限，暂不作为主要判断依据 |")

    lines.extend([
        "",
        "**解读：** 宏观因素如果只是间接影响，不应单独作为买入依据。只有当市场环境、热点方向、公司事件和买卖点提示共同出现时，信号可信度才会明显提高。",
        "",
        "---",
        "",
        "## 五、个股简评",
    ])

    for index, item in enumerate(ranked[:20], start=1):
        row = rows_by_code.get(item.code)
        score = _format_score(item.reliability_score)
        positives = [_friendly_text(value) for value in (item.positive_factors or ["暂无明确支持因素"])]
        risks = [_friendly_text(value) for value in (item.risk_factors or ["暂无明确风险摘要"])]
        events = _join_or_default(_meaningful_company_items(item), "无明确公司事件")
        gap_reasons = _information_gap_reasons(item, row)
        timing_text = _entry_exit_timing_text(row.conditions_met if row else "")
        lines.extend([
            "",
            f"### {index}. {item.name or item.code}：{_one_line_position(item, row)}",
            "",
            f"**股票代码：** `{item.code}`",
            f"**综合评分：** {score}",
            f"**方向判断：** {_direction_label(item.signal_bias)}",
            f"**热点匹配：** {_hot_mark_label(item.hot_sector_mark)}",
            "",
            "**核心结论：**",
            "",
            _friendly_text(item.summary or _brief_conclusion(item, row)),
            "",
            "**主要支持因素：**",
        ])
        for value in positives[:5]:
            lines.append(f"- {value}")
        lines.extend(["", "**主要风险因素：**"])
        for value in risks[:5]:
            lines.append(f"- {value}")
        if gap_reasons:
            lines.extend(["", "**信息缺口：**"])
            for value in gap_reasons[:5]:
                lines.append(f"- {value}")
        lines.extend([
            "",
            f"**买卖点判断：** {timing_text}",
            f"**公司事件：** {events}",
            f"**跟踪建议：** {_tracking_suggestion(item, row)}",
        ])

    lines.extend([
        "",
        "---",
        "",
        "## 六、风险提示",
        "",
        "本次报告需要重点关注以下风险：",
        "",
        "1. **买卖点未确认风险**  ",
        "   个股虽然可能出现异动，但如果暂未看到明确买入/卖出提示，信号可靠性需要打折。",
        "",
        "2. **热点不匹配风险**  ",
        "   如果个股不属于当前市场主线，短期资金关注度可能不足。",
        "",
        "3. **公司事件缺失风险**  ",
        "   缺少公告、订单、业绩或政策催化时，股价异动持续性较难判断。",
        "",
        "4. **宏观扰动风险**  ",
        "   海外利率、通胀、汇率、大宗商品价格波动，可能影响市场风险偏好。",
        "",
        "5. **信息不足风险**  ",
        "   对于缺少有效新闻、公告或数据支撑的个股，应降低判断权重。",
        "",
        "---",
        "",
        "## 七、后续跟踪计划",
        "",
        "后续建议重点跟踪以下三类变化：",
        "",
        "### 1. 买卖点是否重新确认",
        "",
        "观察个股是否重新出现明确买入/卖出提示。如果重新确认，可进入下一轮复核。",
        "",
        "### 2. 板块主线是否发生切换",
        "",
        "如果市场热点切换到样本股票所在行业，需要重新评估这些股票的关注优先级。",
        "",
        "### 3. 公司事件是否出现催化",
        "",
        "重点关注公告、业绩、订单、政策、并购重组等事件。如果出现明确催化，可提高个股跟踪优先级。",
        "",
        "---",
        "",
        "## 八、最终判断",
        "",
        "**操作建议分类：**",
        "",
        f"- {'[x]' if pool_category == '操作池' else '[ ]'} 可进入操作池",
        f"- {'[x]' if pool_category == '重点观察池' else '[ ]'} 重点观察",
        f"- {'[x]' if pool_category == '普通观察池' else '[ ]'} 普通观察",
        f"- {'[x]' if pool_category == '暂不跟踪池' else '[ ]'} 暂不跟踪",
        f"- {'[x]' if pool_category == '剔除观察池' else '[ ]'} 剔除观察池",
        "",
        "**一句话总结：**",
        "",
        f"本批股票当前信号为 **{overall_strength}**，主要原因是：买卖点确认数量为 {len(confirmed_timing)} 只，热点匹配数量为 {len(hot_matched)} 只，公司事件支撑数量为 {len(event_supported)} 只。",
        "",
        "**当前建议：**",
        "",
        "等待更明确的买卖点、板块共振或公司事件催化后，再做进一步判断。",
        "",
        "---",
        "",
        "## 附录：评分与字段说明",
        "",
        "### 1. 综合评分",
        "",
        "- **80-100分：** 信号较强，可重点关注",
        "- **60-79分：** 信号可关注，需要结合其他因素确认",
        "- **40-59分：** 信号偏弱，信息较混杂",
        "- **20-39分：** 可靠性较低，仅作观察",
        "- **0-19分：** 风险明显或信息不足，建议回避",
        "",
        "### 2. 方向判断",
        "",
        "- **看涨：** 综合因素偏正面",
        "- **中性：** 方向不明确，需继续观察",
        "- **看跌：** 综合因素偏负面",
        "- **回避：** 风险明显，不建议纳入跟踪重点",
    ])

    if context.warnings:
        lines.extend(["", "## 执行警告"])
        for warning in context.warnings:
            lines.append(f"- {warning}")

    lines.extend(["", "> 该分析仅用于辅助判断，不构成投资建议。", ""])
    return "\n".join(lines)


def _numeric(value: Optional[float]) -> float:
    return -1.0 if value is None else float(value)


def _format_score(value: Optional[float]) -> str:
    return "无评分" if value is None else f"{value:.2f}"


def _overall_strength(results: List[SignalAnalysisResult]) -> str:
    if not results:
        return "信息不足"
    scores = [_numeric(item.reliability_score) for item in results if item.reliability_score is not None]
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
    event = _join_or_default(item.company_events, "公司事件暂不明确")
    return f"{timing}；{_friendly_text(hot)}；{event}。"


def _tracking_suggestion(
    item: SignalAnalysisResult,
    row: Optional[ScreeningSignalRow],
) -> str:
    score = _numeric(item.reliability_score)
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
        reasons.append("热点板块未直接匹配")
    if row is not None and not row.sector:
        reasons.append("原始 CSV 缺少所属板块")
    if item.confidence_score is not None and item.confidence_score < 40:
        reasons.append("判断信心偏低")
    return _dedupe(reasons)
