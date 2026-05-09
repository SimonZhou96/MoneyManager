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
        company_documents: Dict[str, List[SearchDocument]],
    ) -> List[SignalAnalysisResult]:
        """Analyze a batch of screened stocks."""


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
        company_documents: Dict[str, List[SearchDocument]],
    ) -> List[SignalAnalysisResult]:
        raise RuntimeError("LLM provider is not configured")


class OpenAICompatibleLLMProvider(LLMProvider):
    """OpenAI Chat Completions compatible implementation."""

    name = "openai_compatible"

    def __init__(
        self,
        api_key: str,
        model: str,
        api_base: str = "https://api.openai.com",
        timeout_sec: int = 120,
    ):
        self.api_key = api_key
        self.model = model
        self.api_base = api_base.rstrip("/")
        self.timeout_sec = int(timeout_sec)

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
        company_documents: Dict[str, List[SearchDocument]],
    ) -> List[SignalAnalysisResult]:
        if requests is None:
            raise RuntimeError("requests is not installed")
        if not signals:
            return []

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": self._system_prompt()},
                {"role": "user", "content": self._user_prompt(market, signals, market_documents, company_documents)},
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
        items = parsed.get("items") if isinstance(parsed, dict) else None
        if not isinstance(items, list):
            raise RuntimeError("LLM response JSON must contain an items list")

        results: List[SignalAnalysisResult] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            result = SignalAnalysisResult.from_llm_item(item, model=self.model)
            if result.code:
                results.append(result)
        if not results:
            raise RuntimeError("LLM response did not contain any valid stock analysis items")
        return results

    def _system_prompt(self) -> str:
        return (
            "你是一个股票筛选信号的辅助评估器。你只能根据输入的选股信号、宏观/政策/新闻摘要、"
            "公司事件摘要评估信号可靠性，不能编造事实。输出必须是 JSON object，且只包含 items。"
            "每个 item 必须包含 code、name、analysis_status、reliability_score、confidence_score、"
            "signal_bias、summary、positive_factors、risk_factors、macro_factors、company_events、"
            "market_hot_news、company_hot_news、news_impact、news_sources、source_urls。"
            "reliability_score 和 confidence_score 使用 0 到 100 的数字。signal_bias 使用 bullish、bearish、"
            "neutral、avoid 或 unknown。news_impact 使用 利好、利空、中性、混合、无明显新闻 或 信息不足。"
            "该结果仅为辅助判断，不构成投资建议。"
        )

    def _user_prompt(
        self,
        market: str,
        signals: List[ScreeningSignalRow],
        market_documents: List[SearchDocument],
        company_documents: Dict[str, List[SearchDocument]],
    ) -> str:
        market_context_limit = _env_int("SIGNAL_MARKET_CONTEXT_LIMIT", 2)
        company_context_limit = _env_int("SIGNAL_COMPANY_CONTEXT_LIMIT", 2)
        content_chars = _env_int("SIGNAL_SEARCH_CONTENT_CHARS", 300)
        input_payload = {
            "market": market,
            "task": (
                "评估这些已经通过技术规则筛选的股票信号可靠性；不要改变筛选结果，只做辅助判断。"
                "请从 market_context 提炼市场热点新闻，从 company_context 提炼公司热点新闻，"
                "并判断这些新闻对当前买入/卖出信号是利好、利空、中性、混合、无明显新闻还是信息不足。"
            ),
            "signals": [row.to_prompt_dict() for row in signals],
            "market_context": [
                _document_to_prompt_dict(doc, content_chars)
                for doc in market_documents[:market_context_limit]
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
                        "source_urls": ["string"],
                    }
                ]
            },
        }
        return json.dumps(input_payload, ensure_ascii=False)


def _parse_json_content(content: str):
    """Parse raw JSON content, tolerating fenced code blocks from compatible providers."""
    text = content.strip()
    if not text:
        raise RuntimeError("LLM response content is empty")
    match = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    if match:
        text = match.group(1).strip()
    return json.loads(text)


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
