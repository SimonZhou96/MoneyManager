#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""LLM 产业链关系推理 + 解析校验。不含行情。"""
from __future__ import annotations

from typing import Any, List

from .models import CachedRelation, Direction, RelationType

_VALID_RELATIONS = {r.value for r in RelationType if r != RelationType.OTHER}
_VALID_DIRECTIONS = {d.value for d in Direction}

_RELATION_CN = {
    "supplier": "供应商", "raw_material": "原材料", "equipment": "设备", "foundry_packaging": "代工封测",
    "customer": "客户", "odm": "代工", "distributor": "分销", "application": "应用场景",
    "competitor": "竞品", "substitute": "替代品",
}


class RelationEngineError(Exception):
    pass


_SYSTEM_PROMPT = "你是产业分析专家。给定一只股票，推理其产业链上下游及同业公司。严格输出 JSON，不要解释。"

_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "code": {"type": "string"},
                    "name": {"type": "string"},
                    "market": {"type": "string"},
                    "direction": {"type": "string", "enum": ["upstream", "downstream", "peer"]},
                    "relation": {"type": "string"},
                    "evidence": {"type": "string"},
                },
                "required": ["code", "name", "market", "direction", "relation", "evidence"],
            },
        }
    },
    "required": ["items"],
}


class RelationEngine:
    def __init__(self, resolver: Any, llm_provider: Any):
        self.resolver = resolver
        self.llm = llm_provider

    def infer(self, code: str, name: str, market: str, sector: str) -> List[CachedRelation]:
        user_prompt = self._user_prompt(name, market, code, sector)
        try:
            payload = self.llm.complete_json(
                system_prompt=_SYSTEM_PROMPT, user_prompt=user_prompt, json_schema=_JSON_SCHEMA,
            )
        except Exception as exc:
            raise RelationEngineError(f"LLM 调用失败: {exc}") from exc

        items = self._extract_items(payload)
        if items is None:
            # 重试一次
            try:
                payload = self.llm.complete_json(
                    system_prompt=_SYSTEM_PROMPT, user_prompt=user_prompt, json_schema=_JSON_SCHEMA,
                )
                items = self._extract_items(payload)
            except Exception as exc:
                raise RelationEngineError(f"LLM JSON 解析重试失败: {exc}") from exc
        if items is None:
            raise RelationEngineError("LLM 返回非预期 JSON 结构")

        relations: List[CachedRelation] = []
        seen = set()
        for it in items:
            peer_code = str(it.get("code", "")).strip()
            peer_name = str(it.get("name", "")).strip()
            peer_market = str(it.get("market", "")).strip().upper() or "A"
            direction_raw = str(it.get("direction", "")).strip()
            relation_raw = str(it.get("relation", "")).strip()
            evidence = str(it.get("evidence", "")).strip()
            if not peer_code:
                continue
            direction = Direction(direction_raw) if direction_raw in _VALID_DIRECTIONS else Direction.PEER
            relation = RelationType(relation_raw) if relation_raw in _VALID_RELATIONS else RelationType.OTHER
            # 去重：(peer_code, relation)
            key = (peer_code, relation.value)
            if key in seen:
                continue
            seen.add(key)
            relations.append(CachedRelation(
                source_code=code, source_market=market,
                peer_code=peer_code, peer_market=peer_market, peer_name=peer_name,
                relation=relation, direction=direction, evidence=evidence,
                expires_at=None, is_empty=False,
            ))
        return relations

    @staticmethod
    def _extract_items(payload: Any):
        if isinstance(payload, dict) and "items" in payload and isinstance(payload["items"], list):
            return payload["items"]
        return None

    @staticmethod
    def _user_prompt(name: str, market: str, code: str, sector: str) -> str:
        return (
            f"公司：{name}（{market}市场，代码{code}）\n"
            f"所属板块：{sector}\n\n"
            f"任务：列出最多 50 家与该公司有明确产业关系的上市公司，要求：\n"
            f"1. 关系必须真实、可溯源（凭产业链事实，不要编造公司）。\n"
            f"2. 优先覆盖上游(供应商/原材料/设备/代工封测)与下游(客户/ODM/分销/应用场景)，可含少量同业竞品。\n"
            f"3. 对方须为真实上市公司，给出股票代码与简称；代码不确定时宁可不列。\n"
            f"4. 每条给方向(upstream/downstream/peer)、归一化关系标签、一句话依据。\n"
            f"严格输出 JSON：{{\"items\":[{{\"code\":\"\",\"name\":\"\",\"market\":\"\",\"direction\":\"\",\"relation\":\"\",\"evidence\":\"\"}}]}}"
        )
