#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""LLM provider abstractions for signal analysis."""

from __future__ import annotations

import json
import os
import re
from abc import ABC, abstractmethod
from typing import Dict, List

from .models import ScreeningSignalRow, SearchDocument, SignalAnalysisResult

try:
    import requests
except Exception:  # pragma: no cover
    requests = None


class LLMProvider(ABC):
    """Provider interface for batched stock-signal analysis."""

    name = "base"
    is_available = True

    @property
    @abstractmethod
    def model_name(self) -> str:
        """The model identifier used for analysis."""

    @abstractmethod
    def analyze_batch(
        self,
        market: str,
        signals: List[ScreeningSignalRow],
        market_documents: List[SearchDocument],
        sector_documents: List[SearchDocument],
        hot_sectors: List[str],
        company_documents: Dict[str, List[SearchDocument]],
    ) -> List[SignalAnalysisResult]:
        """Analyze a batch of screened stocks."""

    def drain_warnings(self) -> List[str]:
        """Return and clear provider-local warnings from the last call."""
        return []


class NullLLMProvider(LLMProvider):
    """Unavailable provider used when LLM credentials are not configured."""

    name = "null"
    is_available = False

    @property
    def model_name(self) -> str:
        return ""

    def analyze_batch(
        self,
        market: str,
        signals: List[ScreeningSignalRow],
        market_documents: List[SearchDocument],
        sector_documents: List[SearchDocument],
        hot_sectors: List[str],
        company_documents: Dict[str, List[SearchDocument]],
    ) -> List[SignalAnalysisResult]:
        raise RuntimeError("LLM provider is not configured")


class FallbackLLMProvider(LLMProvider):
    """Try multiple model providers in order for each analysis batch."""

    name = "fallback"

    def __init__(self, providers: List[LLMProvider]):
        self.providers = [provider for provider in providers if getattr(provider, "is_available", False)]
        self._warnings: List[str] = []
        self._last_success_provider = ""

    @property
    def is_available(self) -> bool:
        return bool(self.providers)

    @property
    def provider_names(self) -> List[str]:
        return [provider.name for provider in self.providers]

    @property
    def model_name(self) -> str:
        if self._last_success_provider:
            return self._last_success_provider
        return " -> ".join(_llm_provider_label(provider) for provider in self.providers)

    def analyze_batch(
        self,
        market: str,
        signals: List[ScreeningSignalRow],
        market_documents: List[SearchDocument],
        sector_documents: List[SearchDocument],
        hot_sectors: List[str],
        company_documents: Dict[str, List[SearchDocument]],
    ) -> List[SignalAnalysisResult]:
        self._warnings = []
        self._last_success_provider = ""
        failed_labels: List[str] = []

        for provider in self.providers:
            label = _llm_provider_label(provider)
            try:
                results = provider.analyze_batch(
                    market=market,
                    signals=signals,
                    market_documents=market_documents,
                    sector_documents=sector_documents,
                    hot_sectors=hot_sectors,
                    company_documents=company_documents,
                )
                if signals and not results:
                    raise RuntimeError("provider returned no analysis results")
            except Exception as exc:
                failed_labels.append(label)
                self._warnings.append(f"LLM provider {label} 失败: {type(exc).__name__}: {exc}")
                continue

            self._last_success_provider = label
            if self._warnings:
                self._warnings.append(f"LLM provider fallback 使用 {label} 成功返回")
            return results

        failed_text = ", ".join(failed_labels) if failed_labels else "无可用 provider"
        raise RuntimeError(f"所有 LLM provider 均失败: {failed_text}")

    def drain_warnings(self) -> List[str]:
        warnings = list(self._warnings)
        self._warnings = []
        return warnings


class SignalAnalysisPromptBuilder:
    """Build shared prompts and schemas for signal-analysis LLM providers."""

    @staticmethod
    def system_prompt() -> str:
        return (
            "你是一个股票筛选信号的辅助评估器。你只能根据输入的选股信号、宏观/政策/新闻摘要、"
            "公司事件摘要评估信号可靠性，不能编造事实。输出必须是 JSON object（json 对象），且只包含 items。"
            "每个 item 必须包含 code、name、analysis_status、reliability_score、confidence_score、"
            "signal_bias、summary、positive_factors、risk_factors、macro_factors、company_events、"
            "market_hot_news、company_hot_news、news_impact、news_sources、hot_sectors、"
            "hot_sector_mark、matched_hot_sectors、hot_sector_relevance、hot_sector_reason、"
            "hot_sector_sources、source_urls。"
            "reliability_score 和 confidence_score 使用 0 到 100 的数字。signal_bias 使用 bullish、bearish、"
            "neutral、avoid 或 unknown。news_impact 使用 利好、利空、中性、混合、无明显新闻 或 信息不足。"
            "hot_sector_mark 使用 重点、相关、观察、无明确关联 或 未知。"
            "该结果仅为辅助判断，不构成投资建议。"
        )

    @staticmethod
    def user_prompt(
        market: str,
        signals: List[ScreeningSignalRow],
        market_documents: List[SearchDocument],
        sector_documents: List[SearchDocument],
        hot_sectors: List[str],
        company_documents: Dict[str, List[SearchDocument]],
    ) -> str:
        market_context_limit = _env_int("SIGNAL_MARKET_CONTEXT_LIMIT", 2)
        sector_context_limit = _env_int("SIGNAL_SECTOR_CONTEXT_LIMIT", 3)
        company_context_limit = _env_int("SIGNAL_COMPANY_CONTEXT_LIMIT", 2)
        content_chars = _env_int("SIGNAL_SEARCH_CONTENT_CHARS", 300)
        input_payload = {
            "market": market,
            "task": (
                "评估这些已经通过技术规则筛选的股票信号可靠性；不要改变筛选结果，只做辅助判断。"
                "每条 signal 都包含 instrument_type，取值为 股票 或 ETF。"
                "股票需要重点看公司新闻、公告、业绩、订单、监管、并购等公司事件；"
                "ETF 不做公司事件判断，请按跟踪指数、投资主题、板块暴露和宏观环境判断，"
                "ETF 的 company_events 和 company_hot_news 如无明确基金/主题新闻可以留空。"
                "请从 market_context 提炼市场热点新闻，从 company_context 提炼股票公司新闻或 ETF 主题上下文，"
                "并判断这些新闻对当前买入/卖出信号是利好、利空、中性、混合、无明显新闻还是信息不足。"
                "请从 hot_sector_candidates 和 sector_context 识别热点板块，并结合每只股票的 sector/name "
                "标注热点板块关系；所有股票都要保留，非热点股票也标注为观察、无明确关联或未知。"
            ),
            "signals": [row.to_prompt_dict() for row in signals],
            "hot_sector_candidates": hot_sectors,
            "market_context": [
                _document_to_prompt_dict(doc, content_chars)
                for doc in market_documents[:market_context_limit]
            ],
            "sector_context": [
                _document_to_prompt_dict(doc, content_chars)
                for doc in sector_documents[:sector_context_limit]
            ],
            "company_context": {
                row.code: [
                    _document_to_prompt_dict(doc, content_chars)
                    for doc in company_documents.get(row.code, [])[:company_context_limit]
                ]
                for row in signals
            },
            "output_schema": {
                "items": [
                    {
                        "code": "string",
                        "name": "string",
                        "analysis_status": "success|error",
                        "reliability_score": "number 0-100",
                        "confidence_score": "number 0-100",
                        "signal_bias": "bullish|bearish|neutral|avoid|unknown",
                        "summary": "string",
                        "positive_factors": ["string"],
                        "risk_factors": ["string"],
                        "macro_factors": ["string"],
                        "company_events": ["string"],
                        "market_hot_news": ["string"],
                        "company_hot_news": ["string"],
                        "news_impact": "利好|利空|中性|混合|无明显新闻|信息不足",
                        "news_sources": ["string"],
                        "hot_sectors": ["string"],
                        "hot_sector_mark": "重点|相关|观察|无明确关联|未知",
                        "matched_hot_sectors": ["string"],
                        "hot_sector_relevance": "string, 0-100 or label",
                        "hot_sector_reason": "string",
                        "hot_sector_sources": ["string"],
                        "source_urls": ["string"],
                    }
                ]
            },
        }
        return json.dumps(input_payload, ensure_ascii=False)

    @staticmethod
    def json_schema() -> Dict[str, object]:
        string_array = {"type": "array", "items": {"type": "string"}}
        item_properties = {
            "code": {"type": "string"},
            "name": {"type": "string"},
            "analysis_status": {"type": "string"},
            "reliability_score": {"type": "number"},
            "confidence_score": {"type": "number"},
            "signal_bias": {"type": "string"},
            "summary": {"type": "string"},
            "positive_factors": string_array,
            "risk_factors": string_array,
            "macro_factors": string_array,
            "company_events": string_array,
            "market_hot_news": string_array,
            "company_hot_news": string_array,
            "news_impact": {"type": "string"},
            "news_sources": string_array,
            "hot_sectors": string_array,
            "hot_sector_mark": {"type": "string"},
            "matched_hot_sectors": string_array,
            "hot_sector_relevance": {"type": "string"},
            "hot_sector_reason": {"type": "string"},
            "hot_sector_sources": string_array,
            "source_urls": string_array,
        }
        return {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": item_properties,
                        "required": list(item_properties.keys()),
                    },
                }
            },
            "required": ["items"],
        }


class OpenAICompatibleLLMProvider(LLMProvider):
    """OpenAI Chat Completions compatible implementation."""

    name = "openai_compatible"

    def __init__(
        self,
        api_key: str,
        model: str,
        api_base: str = "https://api.openai.com",
        timeout_sec: int = 120,
        prompt_builder: SignalAnalysisPromptBuilder | None = None,
    ):
        self.api_key = api_key
        self.model = model
        self.api_base = api_base.rstrip("/")
        self.timeout_sec = int(timeout_sec)
        self.prompt_builder = prompt_builder or SignalAnalysisPromptBuilder()

    @property
    def model_name(self) -> str:
        return self.model

    @property
    def chat_completions_url(self) -> str:
        if self.api_base.endswith("/v1"):
            return f"{self.api_base}/chat/completions"
        return f"{self.api_base}/v1/chat/completions"

    def analyze_batch(
        self,
        market: str,
        signals: List[ScreeningSignalRow],
        market_documents: List[SearchDocument],
        sector_documents: List[SearchDocument],
        hot_sectors: List[str],
        company_documents: Dict[str, List[SearchDocument]],
    ) -> List[SignalAnalysisResult]:
        if requests is None:
            raise RuntimeError("requests is not installed")
        if not signals:
            return []

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": self.prompt_builder.system_prompt()},
                {
                    "role": "user",
                    "content": self.prompt_builder.user_prompt(
                        market,
                        signals,
                        market_documents,
                        sector_documents,
                        hot_sectors,
                        company_documents,
                    ),
                },
            ],
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        response = requests.post(
            self.chat_completions_url,
            headers=headers,
            json=payload,
            timeout=self.timeout_sec,
        )
        if response.status_code >= 400 and "response_format" in payload:
            payload = dict(payload)
            payload.pop("response_format", None)
            response = requests.post(
                self.chat_completions_url,
                headers=headers,
                json=payload,
                timeout=self.timeout_sec,
            )
        if response.status_code >= 400:
            raise RuntimeError(f"LLM analysis failed: HTTP {response.status_code} {response.text[:300]}")

        data = response.json() or {}
        content = (((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
        parsed = _parse_json_content(content)
        return _results_from_parsed_json(parsed, self.model)


class DeepSeekLLMProvider(OpenAICompatibleLLMProvider):
    """DeepSeek Chat Completions implementation."""

    name = "deepseek"

    def __init__(
        self,
        api_key: str,
        model: str = "deepseek-v4-flash",
        api_base: str = "https://api.deepseek.com",
        timeout_sec: int = 120,
        prompt_builder: SignalAnalysisPromptBuilder | None = None,
    ):
        super().__init__(
            api_key=api_key,
            model=model,
            api_base=api_base,
            timeout_sec=timeout_sec,
            prompt_builder=prompt_builder,
        )

    @property
    def chat_completions_url(self) -> str:
        if self.api_base.endswith("/chat/completions"):
            return self.api_base
        return f"{self.api_base}/chat/completions"


class CodexResponsesLLMProvider(LLMProvider):
    """OpenAI Codex model implementation using the Responses API."""

    name = "codex_responses"

    def __init__(
        self,
        api_key: str,
        model: str = "gpt-5.2-codex",
        api_base: str = "https://api.openai.com",
        timeout_sec: int = 120,
        reasoning_effort: str = "medium",
        prompt_builder: SignalAnalysisPromptBuilder | None = None,
    ):
        self.api_key = api_key
        self.model = model
        self.api_base = api_base.rstrip("/")
        self.timeout_sec = int(timeout_sec)
        self.reasoning_effort = _normalize_reasoning_effort(reasoning_effort)
        self.prompt_builder = prompt_builder or SignalAnalysisPromptBuilder()

    @property
    def model_name(self) -> str:
        return self.model

    @property
    def responses_url(self) -> str:
        if self.api_base.endswith("/v1"):
            return f"{self.api_base}/responses"
        return f"{self.api_base}/v1/responses"

    def analyze_batch(
        self,
        market: str,
        signals: List[ScreeningSignalRow],
        market_documents: List[SearchDocument],
        sector_documents: List[SearchDocument],
        hot_sectors: List[str],
        company_documents: Dict[str, List[SearchDocument]],
    ) -> List[SignalAnalysisResult]:
        if requests is None:
            raise RuntimeError("requests is not installed")
        if not signals:
            return []

        payload = self._build_payload(
            market,
            signals,
            market_documents,
            sector_documents,
            hot_sectors,
            company_documents,
            text_format={
                "type": "json_schema",
                "name": "signal_analysis_result",
                "schema": self.prompt_builder.json_schema(),
                "strict": True,
            },
        )
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        response = requests.post(
            self.responses_url,
            headers=headers,
            json=payload,
            timeout=self.timeout_sec,
        )
        if response.status_code >= 400 and _should_retry_responses_json_object(response):
            payload = self._build_payload(
                market,
                signals,
                market_documents,
                sector_documents,
                hot_sectors,
                company_documents,
                text_format={"type": "json_object"},
            )
            response = requests.post(
                self.responses_url,
                headers=headers,
                json=payload,
                timeout=self.timeout_sec,
            )
        if response.status_code >= 400:
            raise RuntimeError(f"Codex Responses analysis failed: HTTP {response.status_code} {response.text[:300]}")

        data = response.json() or {}
        content = _extract_responses_output_text(data)
        parsed = _parse_json_content(content)
        return _results_from_parsed_json(parsed, self.model)

    def _build_payload(
        self,
        market: str,
        signals: List[ScreeningSignalRow],
        market_documents: List[SearchDocument],
        sector_documents: List[SearchDocument],
        hot_sectors: List[str],
        company_documents: Dict[str, List[SearchDocument]],
        text_format: Dict[str, object],
    ) -> Dict[str, object]:
        return {
            "model": self.model,
            "input": [
                {"role": "system", "content": self.prompt_builder.system_prompt()},
                {
                    "role": "user",
                    "content": self.prompt_builder.user_prompt(
                        market,
                        signals,
                        market_documents,
                        sector_documents,
                        hot_sectors,
                        company_documents,
                    ),
                },
            ],
            "reasoning": {"effort": self.reasoning_effort},
            "text": {"format": text_format},
        }


def _parse_json_content(content: str):
    """Parse raw JSON content, tolerating fenced code blocks from compatible providers."""
    text = content.strip()
    if not text:
        raise RuntimeError("LLM response content is empty")
    match = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    if match:
        text = match.group(1).strip()
    return json.loads(text)


def _llm_provider_label(provider: LLMProvider) -> str:
    model = (provider.model_name or "").strip()
    return f"{provider.name}:{model}" if model else provider.name


def _results_from_parsed_json(parsed, model: str) -> List[SignalAnalysisResult]:
    items = parsed.get("items") if isinstance(parsed, dict) else None
    if not isinstance(items, list):
        raise RuntimeError("LLM response JSON must contain an items list")

    results: List[SignalAnalysisResult] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        result = SignalAnalysisResult.from_llm_item(item, model=model)
        if result.code:
            results.append(result)
    if not results:
        raise RuntimeError("LLM response did not contain any valid stock analysis items")
    return results


def _extract_responses_output_text(data: Dict[str, object]) -> str:
    output_text = str(data.get("output_text") or "").strip()
    if output_text:
        return output_text

    text_parts: List[str] = []
    refusals: List[str] = []
    for item in data.get("output") or []:
        if not isinstance(item, dict):
            continue
        for content in item.get("content") or []:
            if not isinstance(content, dict):
                continue
            content_type = str(content.get("type") or "")
            if content_type in {"output_text", "text"}:
                text = str(content.get("text") or "").strip()
                if text:
                    text_parts.append(text)
            elif content_type == "refusal":
                refusal = str(content.get("refusal") or "").strip()
                if refusal:
                    refusals.append(refusal)
    if text_parts:
        return "\n".join(text_parts)
    if refusals:
        raise RuntimeError(f"Codex Responses refused the request: {'; '.join(refusals)}")
    raise RuntimeError("Codex Responses output text is empty")


def _should_retry_responses_json_object(response) -> bool:
    text = (getattr(response, "text", "") or "").lower()
    return response.status_code in {400, 422} and (
        "json_schema" in text
        or "schema" in text
        or "text.format" in text
        or "response_format" in text
        or "unsupported" in text
    )


def _normalize_reasoning_effort(value: str) -> str:
    effort = (value or "medium").strip().lower()
    return effort if effort in {"low", "medium", "high", "xhigh"} else "medium"


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return max(1, int(raw))
    except ValueError:
        return default


def _document_to_prompt_dict(doc: SearchDocument, max_content_chars: int) -> Dict[str, object]:
    content = doc.content or ""
    if len(content) > max_content_chars:
        content = f"{content[:max_content_chars]}..."
    return {
        "title": doc.title,
        "url": doc.url,
        "content": content,
        "score": doc.score,
    }
