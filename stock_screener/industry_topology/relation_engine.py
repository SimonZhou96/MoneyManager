#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""LLM 产业链关系推理 + 解析校验。不含行情。"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Tuple

from .models import CachedRelation, Direction, RelationType

_VALID_RELATIONS = {r.value for r in RelationType if r != RelationType.OTHER}
_VALID_DIRECTIONS = {d.value for d in Direction}


class RelationEngineError(Exception):
    pass


_SYSTEM_PROMPT = (
    "你是产业分析专家。给定一只股票，推理其产业链上下游及同业公司。严格输出 JSON，不要解释。\n\n"
    "market 取值规则（必须严格使用以下大写缩写）：\n"
    "- A股: \"A\"\n"
    "- 港股: \"HK\"\n"
    "- 美股: \"US\"\n"
    "- 日股: \"JP\"\n"
    "- 台股: \"TW\"\n"
    "- 韩股: \"KR\"\n"
    "不要使用其他写法（如 JAPAN、SZ、SH、NASDAQ 等），只用以上 6 个缩写。\n\n"
    "code 格式规则（纯代码，去掉交易所后缀/前缀）：\n"
    "- A股: 6 位数字，如 \"600519\"（不要 .SH / .SZ / SZ. / SH.）\n"
    "- 港股: 1-5 位数字，如 \"00700\"（不要 .HK / HK.）\n"
    "- 美股: 大写字母，如 \"AAPL\"（不要 .US / US.）\n"
    "- 日股: 4 位数字，如 \"6146\"（不要 .T / JP.）\n"
    "- 台股: 4 位数字，如 \"2330\"（不要 .TW / TW.）\n"
    "- 韩股: 6 位数字，如 \"005930\"（不要 .KS / .KQ / KR.）\n"
    "不确定代码的公司坚决不列，宁可少列也不错列。"
)

_BATCH_SYSTEM_PROMPT = (
    "你是产业分析专家。给定多只股票，分别推理每只股票的产业链上下游及同业公司。严格输出 JSON，不要解释。\n\n"
    "market 取值规则（必须严格使用以下大写缩写）：\n"
    "- A股: \"A\"\n"
    "- 港股: \"HK\"\n"
    "- 美股: \"US\"\n"
    "- 日股: \"JP\"\n"
    "- 台股: \"TW\"\n"
    "- 韩股: \"KR\"\n"
    "不要使用其他写法（如 JAPAN、SZ、SH、NASDAQ 等），只用以上 6 个缩写。\n\n"
    "code 格式规则（纯代码，去掉交易所后缀/前缀），不确定代码的公司坚决不列。"
)

_ITEM_SCHEMA = {
    "type": "object",
    "properties": {
        "code": {"type": "string"},
        "name": {"type": "string"},
        "market": {"type": "string", "enum": ["A", "HK", "US", "JP", "TW", "KR"]},
        "direction": {"type": "string", "enum": ["upstream", "downstream", "peer"]},
        "relation": {"type": "string"},
        "evidence": {"type": "string"},
        "market_cap": {"type": "number"},
        "market_cap_str": {"type": "string"},
    },
    "required": ["code", "name", "market", "direction", "relation", "evidence"],
}

_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {"type": "array", "items": _ITEM_SCHEMA},
    },
    "required": ["items"],
}

_BATCH_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "groups": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "source_code": {"type": "string"},
                    "source_market": {"type": "string"},
                    "items": {"type": "array", "items": _ITEM_SCHEMA},
                },
                "required": ["source_code", "source_market", "items"],
            },
        }
    },
    "required": ["groups"],
}


_VALID_MARKETS = {"A", "HK", "US", "JP", "TW", "KR"}

# 市场别名 → 规范缩写（LLM 偶发输出旧格式的兜底映射）
_MARKET_ALIAS_MAP: Dict[str, str] = {
    "JAPAN": "JP", "TOKYO": "JP", "TSE": "JP", "TY": "JP",
    "KOREA": "KR", "KRX": "KR", "KS": "KR", "KQ": "KR",
    "TAIWAN": "TW", "TWO": "TW", "TAIEX": "TW",
    "SZ": "A", "SH": "A", "SHE": "A", "SHA": "A",
    "NASDAQ": "US", "NYSE": "US", "AMEX": "US",
    "HKG": "HK", "HKEX": "HK",
}

_CODE_SUFFIXES = (".SZ", ".SH", ".HK", ".US", ".T", ".TW", ".KS", ".KQ", ".JP")
_CODE_PREFIXES = ("SZ.", "SH.", "HK.", "US.", "JP.", "TW.", "KR.")


def _safe_float(value: Any) -> Optional[float]:
    """安全转为 float，失败返回 None。"""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_peer_market(raw: str) -> str:
    """LLM 输出的 market 字符串 → 规范缩写 A/HK/US/JP/TW/KR"""
    m = raw.strip().upper()
    if not m:
        return "A"
    return _MARKET_ALIAS_MAP.get(m, m)


def _normalize_peer_code(code: str) -> str:
    """去交易所后缀/前缀，返回纯代码。例: '002185.SZ' → '002185', 'US.AAPL' → 'AAPL'"""
    c = code.strip()
    upper = c.upper()
    for sfx in _CODE_SUFFIXES:
        if upper.endswith(sfx):
            c = c[:-len(sfx)]
            break
    for pfx in _CODE_PREFIXES:
        if upper.startswith(pfx):
            c = c[len(pfx):]
            break
    return c.strip()


class RelationEngine:
    def __init__(self, resolver: Any, llm_provider: Any):
        self.resolver = resolver
        self.llm = llm_provider

    def infer(self, code: str, name: str, market: str, sector: str) -> List[CachedRelation]:
        user_prompt = self._user_prompt(name, market, code, sector)
        payload = self._call_llm(user_prompt, _SYSTEM_PROMPT, _JSON_SCHEMA)
        items = self._extract_items(payload)
        if items is None:
            # 重试一次
            payload = self._call_llm(user_prompt, _SYSTEM_PROMPT, _JSON_SCHEMA)
            items = self._extract_items(payload)
        if items is None:
            raise RelationEngineError("LLM 返回非预期 JSON 结构")
        return self._parse_items(items, code, market)

    def infer_batch(self, sources: List[Dict[str, str]]) -> Dict[Tuple[str, str], List[CachedRelation]]:
        """一次 LLM 调用推理多个 source 的产业关系。

        sources: [{code, market, name, sector}]
        返回: {(market, code): [CachedRelation]}。未返回 group 的 source 对应空列表，
        避免反复空推理。
        """
        result: Dict[Tuple[str, str], List[CachedRelation]] = {}
        for s in sources:
            code = str(s.get("code", "")).strip()
            market = str(s.get("market", "")).strip().upper()
            if not code:
                continue
            result[(market, code)] = []
        if not result:
            return result

        user_prompt = self._batch_user_prompt(sources)
        payload = self._call_llm(user_prompt, _BATCH_SYSTEM_PROMPT, _BATCH_JSON_SCHEMA)
        groups = self._extract_groups(payload)
        if groups is None:
            payload = self._call_llm(user_prompt, _BATCH_SYSTEM_PROMPT, _BATCH_JSON_SCHEMA)
            groups = self._extract_groups(payload)
        if groups is None:
            raise RelationEngineError("LLM 返回非预期 JSON 结构")

        for grp in groups:
            if not isinstance(grp, dict):
                continue
            src_code = str(grp.get("source_code", "")).strip()
            src_market = str(grp.get("source_market", "")).strip().upper()
            key = (src_market, src_code)
            if key not in result:
                continue
            items = grp.get("items") if isinstance(grp.get("items"), list) else []
            result[key] = self._parse_items(items, src_code, src_market)
        return result

    def _call_llm(self, user_prompt: str, system_prompt: str, json_schema: dict) -> Any:
        try:
            return self.llm.complete_json(
                system_prompt=system_prompt, user_prompt=user_prompt, json_schema=json_schema,
            )
        except Exception as exc:
            raise RelationEngineError(f"LLM 调用失败: {exc}") from exc

    def _parse_items(self, items: List[Any], source_code: str, source_market: str) -> List[CachedRelation]:
        relations: List[CachedRelation] = []
        seen = set()
        for it in items:
            if not isinstance(it, dict):
                continue
            raw_code = str(it.get("code", "")).strip()
            raw_market = str(it.get("market", "")).strip()
            peer_code = _normalize_peer_code(raw_code)
            peer_market = _normalize_peer_market(raw_market)
            # 如果 LLM 输出了不识别的 market（不在 6 个标准缩写中的陌生字符串），跳过
            if peer_market not in _VALID_MARKETS:
                continue
            peer_name = str(it.get("name", "")).strip()
            direction_raw = str(it.get("direction", "")).strip()
            relation_raw = str(it.get("relation", "")).strip()
            evidence = str(it.get("evidence", "")).strip()
            if not peer_code:
                continue
            direction = Direction(direction_raw) if direction_raw in _VALID_DIRECTIONS else Direction.PEER
            relation = RelationType(relation_raw) if relation_raw in _VALID_RELATIONS else RelationType.OTHER
            # 提取市值（可选字段，LLM 可能不返回）
            peer_market_cap = _safe_float(it.get("market_cap"))
            peer_market_cap_str = str(it.get("market_cap_str", "")).strip()
            # 去重：(peer_code, relation)
            key = (peer_code, relation.value)
            if key in seen:
                continue
            seen.add(key)
            relations.append(CachedRelation(
                source_code=source_code, source_market=source_market,
                peer_code=peer_code, peer_market=peer_market, peer_name=peer_name,
                relation=relation, direction=direction, evidence=evidence,
                expires_at=None, is_empty=False,
                peer_market_cap=peer_market_cap, peer_market_cap_str=peer_market_cap_str,
            ))
        return relations

    @staticmethod
    def _extract_items(payload: Any):
        if isinstance(payload, dict) and "items" in payload and isinstance(payload["items"], list):
            return payload["items"]
        return None

    @staticmethod
    def _extract_groups(payload: Any):
        if isinstance(payload, dict) and "groups" in payload and isinstance(payload["groups"], list):
            return payload["groups"]
        return None

    @staticmethod
    def _user_prompt(name: str, market: str, code: str, sector: str) -> str:
        today = datetime.now().strftime("%Y-%m-%d")
        return (
            f"当前日期：{today}\n"
            f"公司：{name}（{market}市场，代码{code}）\n"
            f"所属板块：{sector}\n\n"
            f"任务：列出最多 50 家与该公司有明确产业关系的上市公司，要求：\n"
            f"1. 关系必须真实、可溯源（凭产业链事实，不要编造公司）。\n"
            f"2. 优先覆盖上游(供应商/原材料/设备/代工封测)与下游(客户/ODM/分销/应用场景)，可含少量同业竞品。\n"
            f"3. 对方须为真实上市公司，给出股票代码与简称；代码不确定时宁可不列。\n"
            f"4. 每条给方向(upstream/downstream/peer)、归一化关系标签、一句话依据。\n"
            f"5. market 必须用标准缩写: A/HK/US/JP/TW/KR，不要用 JAPAN/SZ/NASDAQ 等。\n"
            f"6. code 必须是纯代码（去后缀），如台积电写 \"2330\" 不写 \"2330.TW\"，华天科技写 \"002185\" 不写 \"002185.SZ\"。\n"
            f"7. 请根据你的知识（截至{ today }）给出每家公司的市值（market_cap，单位：人民币元）和市值字符串（market_cap_str，如'2.8万亿'、'5000亿'、'80亿'），"
            f"不确定时可以不填。\n"
            f"严格输出 JSON：{{\"items\":[{{\"code\":\"\",\"name\":\"\",\"market\":\"\",\"direction\":\"\",\"relation\":\"\",\"evidence\":\"\",\"market_cap\":0,\"market_cap_str\":\"\"}}]}}"
        )

    @staticmethod
    def _batch_user_prompt(sources: List[Dict[str, str]]) -> str:
        today = datetime.now().strftime("%Y-%m-%d")
        lines = []
        for i, s in enumerate(sources, 1):
            name = s.get("name", "") or ""
            market = s.get("market", "") or ""
            code = s.get("code", "") or ""
            sector = s.get("sector", "") or ""
            lines.append(f"{i}. 公司：{name}（{market}市场，代码{code}） 所属板块：{sector}")
        return (
            f"当前日期：{today}\n"
            f"下面给出 {len(sources)} 只股票，请分别列出每只股票最多 50 家有明确产业关系的上市公司。\n"
            + "\n".join(lines)
            + "\n\n要求：\n"
            f"1. 关系必须真实、可溯源（凭产业链事实，不要编造公司）。\n"
            f"2. 优先覆盖上游(供应商/原材料/设备/代工封测)与下游(客户/ODM/分销/应用场景)，可含少量同业竞品。\n"
            f"3. 对方须为真实上市公司，给出股票代码与简称；代码不确定时宁可不列。\n"
            f"4. 每条给方向(upstream/downstream/peer)、归一化关系标签、一句话依据。\n"
            f"5. 每只股票的结果放进独立的 group，source_code/source_market 必须与输入一致。\n"
            f"6. market 必须用标准缩写: A/HK/US/JP/TW/KR；code 必须是纯代码，台积电写 \"2330\" 不写 \"2330.TW\"。\n"
            f"7. 请根据你的知识（截至{ today }）给出每家公司的市值（market_cap，单位：人民币元）和市值字符串（market_cap_str，如'2.8万亿'、'5000亿'、'80亿'），"
            f"不确定时可以不填。\n"
            f"严格输出 JSON：{{\"groups\":[{{\"source_code\":\"\",\"source_market\":\"\",\"items\":[{{\"code\":\"\",\"name\":\"\",\"market\":\"\",\"direction\":\"\",\"relation\":\"\",\"evidence\":\"\",\"market_cap\":0,\"market_cap_str\":\"\"}}]}}]}}"
        )
