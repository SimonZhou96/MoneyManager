"""Build structured, database-ready reports from screening analysis results."""
from __future__ import annotations

from dataclasses import asdict
from typing import Any

from .models import ScreeningSignalRow, SignalAnalysisResult, compute_unified_score


class ReportContentBuilder:
    """Assemble report data without rendering files or markup."""

    schema_version = 1

    def build_market_content(self, context: Any) -> dict:
        rows = context.all_rows or context.rows
        stocks = []
        for row in rows:
            result = context.results_by_code.get(row.code)
            if result is None:
                continue
            stocks.append(self._build_stock(row, result, context))

        return {
            "schema_version": self.schema_version,
            "market": context.market,
            "market_label": rows[0].market_label if rows else context.market,
            "task_id": context.task_id,
            "task_date": context.check_date.isoformat(),
            "timeframe": context.timeframe,
            "chain_key": context.chain_key or "",
            "hot_sectors": list(context.hot_sectors or []),
            "market_sources": [self._source_dict(item) for item in context.market_documents],
            "sector_sources": [self._source_dict(item) for item in context.sector_documents],
            "passed_count": len(rows),
            "stocks": stocks,
        }

    def build_task_content(self, *, task_id: str, task_type: str, task_date: str,
                           markets: list[str], timeframe: str, chain_key: str,
                           market_content: dict[str, dict], passed_count: int) -> dict:
        return {
            "schema_version": self.schema_version,
            "overview": {
                "task_id": task_id,
                "task_type": task_type,
                "task_date": task_date,
                "markets": list(markets),
                "timeframe": timeframe,
                "chain_key": chain_key,
                "passed_count": passed_count,
            },
            "markets": [market_content[market] for market in markets if market in market_content],
            "disclaimer": "本报告由自动化筛选与 AI 分析生成，仅供辅助观察和复盘，不构成投资建议。",
        }

    def _build_stock(self, row: ScreeningSignalRow, result: SignalAnalysisResult, context: Any) -> dict:
        score = asdict(compute_unified_score(result, row))
        analysis = asdict(result)
        # Keep normalized analysis fields and citations, but omit the duplicate raw model payload.
        analysis.pop("raw_response", None)
        return {
            "code": row.code,
            "name": result.name or row.name or row.code,
            "instrument_type": row.instrument_type,
            "screening": {
                "market": row.market,
                "market_label": row.market_label,
                "sector": row.sector,
                "pe_ratio": row.pe_ratio,
                "market_cap": row.market_cap,
                "conditions_met": row.conditions_met,
                "main_force_risk_level": row.main_force_risk_level,
                "main_force_risk_score": row.main_force_risk_score,
                "main_force_risk_signals": row.main_force_risk_signals,
                "main_force_risk_summary": row.main_force_risk_summary,
                "raw": dict(row.raw or {}),
            },
            "score": score,
            "analysis": analysis,
            "evidence": dict(context.evidence_packs.get(row.code) or {}),
        }

    @staticmethod
    def _source_dict(source: Any) -> dict:
        if hasattr(source, "to_prompt_dict"):
            return source.to_prompt_dict()
        return dict(source) if isinstance(source, dict) else {"text": str(source)}
