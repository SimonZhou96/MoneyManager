from __future__ import annotations

import json
from typing import Any, Dict, Protocol

try:
    from market_intel.macro_scoring import (
        DEFAULT_MACRO_SUB_WEIGHTS,
        MacroEvidencePackage,
        MacroScoreParser,
        MacroScoreResult,
    )
except ModuleNotFoundError:
    from stock_screener.market_intel.macro_scoring import (
        DEFAULT_MACRO_SUB_WEIGHTS,
        MacroEvidencePackage,
        MacroScoreParser,
        MacroScoreResult,
    )


class JsonCompletionClient(Protocol):
    model_name: str

    def complete_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        json_schema: Dict[str, Any],
    ) -> Dict[str, Any]:
        ...


class MacroScorePromptBuilder:
    """Build prompts for AI macro evidence scoring."""

    @staticmethod
    def system_prompt() -> str:
        return (
            "你是 stock-screener macro evidence scorer，用于根据输入证据给出宏观共振评分。"
            "只能使用输入中的证据，不能编造事实。"
            "证据时间字段会影响可信度：event_time、published_at、fetched_at、expires_at、"
            "age_hours、is_stale 都必须纳入判断。"
            "Different-timed contradictory company events may reverse or weaken older signals；"
            "较新的相反公司事件可能反转或削弱较早信号。"
            "输出必须是 JSON object，macro_score 与所有子分数范围均为 -100 到 100。"
        )

    @staticmethod
    def user_prompt(package: MacroEvidencePackage, threshold: float) -> str:
        payload = {
            "task": "score_macro_evidence",
            "threshold": threshold,
            "score_range": {"min": -100, "max": 100},
            "sub_weights": dict(DEFAULT_MACRO_SUB_WEIGHTS),
            "evidence_package": package.to_prompt_dict(),
            "output_rules": {
                "macro_score": "number -100 to 100",
                "passed": "optional boolean; parser will recompute from threshold",
                "threshold": "optional number echoed from input",
                "sub_scores": "object containing the six weighted score dimensions",
                "weighted_contribution": "object with per-dimension weighted contribution",
                "summary": "concise Chinese summary grounded in evidence",
                "temporal_summary": "explain how evidence timing changes the conclusion",
                "risks": "array of concise risk strings",
                "evidence_refs": "array of referenced evidence objects, do not invent entries",
            },
        }
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)

    @staticmethod
    def json_schema() -> Dict[str, Any]:
        string_array = {"type": "array", "items": {"type": "string"}}
        number_map = {
            "type": "object",
            "additionalProperties": {"type": "number"},
        }
        return {
            "type": "object",
            "additionalProperties": True,
            "properties": {
                "macro_score": {"type": "number", "minimum": -100, "maximum": 100},
                "passed": {"type": "boolean"},
                "threshold": {"type": "number"},
                "sub_scores": number_map,
                "weighted_contribution": number_map,
                "summary": {"type": "string"},
                "temporal_summary": {"type": "string"},
                "risks": string_array,
                "evidence_refs": {
                    "type": "array",
                    "items": {"type": "object", "additionalProperties": True},
                },
            },
            "required": ["macro_score", "sub_scores", "summary"],
        }


class MacroScoreLLMScorer:
    def __init__(
        self,
        client: JsonCompletionClient,
        prompt_builder: MacroScorePromptBuilder | None = None,
    ):
        self.client = client
        self.prompt_builder = prompt_builder or MacroScorePromptBuilder()

    @property
    def model_name(self) -> str:
        return self.client.model_name

    def score(self, package: MacroEvidencePackage, threshold: float = 60) -> MacroScoreResult:
        payload = self.client.complete_json(
            system_prompt=self.prompt_builder.system_prompt(),
            user_prompt=self.prompt_builder.user_prompt(package, threshold),
            json_schema=self.prompt_builder.json_schema(),
        )
        return MacroScoreParser.parse(payload, threshold=threshold)


class MacroScoreJsonClient:
    def __init__(self, provider: Any):
        self.provider = provider

    @property
    def model_name(self) -> str:
        return str(getattr(self.provider, "model_name", "") or "")

    def complete_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        json_schema: Dict[str, Any],
    ) -> Dict[str, Any]:
        complete_json = getattr(self.provider, "complete_json", None)
        if callable(complete_json):
            return complete_json(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                json_schema=json_schema,
            )
        raise RuntimeError("configured LLM provider does not support macro JSON completion")
