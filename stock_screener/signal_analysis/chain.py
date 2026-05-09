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
from typing import Dict, List, Optional, Protocol

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
                message = f"模型批量分析失败: {type(exc).__name__}: {exc}"
                context.warnings.append(message)
                for row in batch:
                    failed_rows[row.code] = message
                continue

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
    results = [context.results_by_code[row.code] for row in context.rows]
    ranked = sorted(
        results,
        key=lambda item: -1 if item.reliability_score is None else item.reliability_score,
        reverse=True,
    )

    lines = [
        f"# {market_name}选股信号 AI 辅助分析",
        "",
        f"- 任务 ID: `{context.task_id}`",
        f"- 日期: {context.check_date.isoformat()}",
        f"- 模型: `{context.llm_provider.model_name}`",
        f"- 股票数: {len(context.rows)}",
        "",
        "## 字段口径",
        "",
        f"- 信号可靠性评分: {RELIABILITY_SCORE_CRITERIA}",
        f"- 模型置信度: {CONFIDENCE_SCORE_CRITERIA}",
        f"- 辅助方向判断: {SIGNAL_BIAS_CRITERIA}",
        f"- 热点板块标记: {HOT_SECTOR_MARK_CRITERIA}",
        "",
        "## 评分较高的信号",
    ]
    for item in ranked[:10]:
        score = "" if item.reliability_score is None else f"{item.reliability_score:.2f}"
        lines.append(f"- `{item.code}` {item.name}: {score} | {item.signal_bias} | {item.summary}")

    lines.extend(["", "## 热点新闻影响"])
    for item in ranked[:10]:
        news = "；".join(item.market_hot_news + item.company_hot_news) or "无明确热点新闻摘要"
        impact = item.news_impact or "未明确判断"
        lines.append(f"- `{item.code}`: {impact} | {news}")

    lines.extend(["", "## 热点板块标注"])
    hot_sector_text = "；".join(context.hot_sectors) if context.hot_sectors else "未识别到明确热点板块"
    lines.append(f"- 识别热点板块: {hot_sector_text}")
    for item in ranked[:10]:
        matched = "；".join(item.matched_hot_sectors) or "无直接匹配"
        mark = item.hot_sector_mark or "未知"
        lines.append(f"- `{item.code}` {item.name}: {mark} | {matched} | {item.hot_sector_reason}")

    lines.extend(["", "## 风险提示"])
    for item in ranked[:10]:
        risks = "；".join(item.risk_factors) or item.error_message or "无明确风险摘要"
        lines.append(f"- `{item.code}`: {risks}")

    if context.warnings:
        lines.extend(["", "## 执行警告"])
        for warning in context.warnings:
            lines.append(f"- {warning}")

    lines.extend(["", "> 该分析仅用于辅助判断，不构成投资建议。", ""])
    return "\n".join(lines)
