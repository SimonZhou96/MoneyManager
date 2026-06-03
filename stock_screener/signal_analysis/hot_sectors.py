#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Hot-sector configuration and market-data providers for signal analysis."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List

from .models import SearchDocument


@dataclass(frozen=True)
class HotSector:
    name: str
    source: str = ""
    score: float = 0.0
    reason: str = ""


@dataclass(frozen=True)
class ManualHotSectorConfig:
    hot_sectors: List[str] = field(default_factory=list)
    sources: List[str] = field(default_factory=list)

    @classmethod
    def from_env(cls, market: str) -> "ManualHotSectorConfig":
        file_config = cls.from_file(os.getenv("SIGNAL_MANUAL_HOT_SECTORS_FILE", "").strip(), market)
        market_key = f"SIGNAL_MANUAL_MARKET_HOT_SECTORS_{market.upper()}"
        source_key = f"SIGNAL_MANUAL_HOT_SECTOR_SOURCES_{market.upper()}"
        hot_sectors = (
            _split_items(os.getenv(market_key))
            or _split_items(os.getenv("SIGNAL_MANUAL_MARKET_HOT_SECTORS"))
            or file_config.hot_sectors
        )
        sources = (
            _split_items(os.getenv(source_key))
            or _split_items(os.getenv("SIGNAL_MANUAL_HOT_SECTOR_SOURCES"))
            or file_config.sources
        )
        return cls(hot_sectors=hot_sectors, sources=sources)

    @classmethod
    def from_file(cls, path: str, market: str) -> "ManualHotSectorConfig":
        if not path:
            return cls()
        try:
            data = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
        except Exception:
            return cls()
        if not isinstance(data, dict):
            return cls()
        return cls(
            hot_sectors=_market_items(data.get("hot_sectors"), market),
            sources=_market_items(data.get("sources"), market),
        )

    def has_any(self) -> bool:
        return bool(self.hot_sectors)

    def documents(self, query: str) -> List[SearchDocument]:
        return [
            SearchDocument(
                title=f"手动热点板块 {index}",
                url=self.sources[index - 1] if index <= len(self.sources) else "manual_config",
                content=f"手动配置热点板块（优先级最高）: {item}",
                score=1.0,
                query=query,
            )
            for index, item in enumerate(self.hot_sectors, start=1)
        ]


class WebSearchHotSectorProvider:
    """Dynamic hot-sector discovery via DeepSeek LLM (with Tavily fallback).

    DeepSeek can reason about current market hot sectors. Much cleaner than
    keyword extraction from raw search results.

    Priority: DeepSeek (default) → Tavily (fallback) → keyword heuristics.
    """

    name = "deepseek_hot_sector"

    def __init__(self, api_key: str = ""):
        self.api_key = api_key or os.getenv("DEEPSEEK_API_KEY", "").strip()
        self.api_base = os.getenv("DEEPSEEK_API_BASE", "https://api.deepseek.com").strip()
        self.model = os.getenv("DEEPSEEK_LLM_MODEL", "deepseek-chat").strip()
        self.tavily_key = os.getenv("TAVILY_API_KEY", "").strip()

    @property
    def is_available(self) -> bool:
        return bool(self.api_key) or bool(self.tavily_key)

    def find_hot_sectors(self, market: str, limit: int = 6) -> List[HotSector]:
        # Priority 1: DeepSeek LLM (clean, reliable)
        if self.api_key:
            sectors = self._deepseek_discover(market, limit)
            if sectors:
                return sectors

        # Priority 2: Tavily search + keyword extraction
        if self.tavily_key:
            sectors = self._tavily_discover(market, limit)
            if sectors:
                return sectors

        return []

    def _deepseek_discover(self, market: str, limit: int) -> List[HotSector]:
        market_name = {"HK": "港股", "US": "美股", "A": "A股"}.get(market, market)
        market_hint = {
            "HK": "关注港股特色：互联网科技、创新药、消费、金融、博彩、物业管理等",
            "US": "关注美股特色：AI/大模型、半导体、SaaS、生物科技、新能源车、航天等",
            "A": "关注A股特色：机器人、算力、半导体、新能源、券商、白酒、军工等",
        }.get(market, "")
        prompt = (
            f"今天是2026年6月。请根据你的知识，列出当前{market_name}市场最热门的5-8个板块/行业。\n"
            f"{market_hint}\n"
            "只输出板块简称（2-4个字），每行一个，不要编号、不要解释。\n"
            "简称示例：AI、创新药、消费、半导体、新能源、金融、机器人。"
        )
        try:
            import requests
            resp = requests.post(
                f"{self.api_base.rstrip('/')}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.1,
                    "max_tokens": 200,
                },
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            content = data["choices"][0]["message"]["content"].strip()
        except Exception as exc:
            print(f"[hot_sectors] DeepSeek 失败: {exc}")
            return []

        # Parse: one sector per line, strip noise
        sectors = []
        for line in content.split("\n"):
            name = line.strip().lstrip("-*•·1234567890.（）() ").strip()
            if name and len(name) >= 1 and len(name) <= 6:
                sectors.append(name)
        sectors = _clean_sector_names(sectors)[:limit]
        return [HotSector(name=s, source="deepseek", score=1.0, reason="LLM动态发现") for s in sectors]

    def _tavily_discover(self, market: str, limit: int) -> List[HotSector]:
        market_name = {"HK": "港股", "US": "美股", "A": "A股"}.get(market, market)
        try:
            import requests
            resp = requests.post(
                "https://api.tavily.com/search",
                json={
                    "api_key": self.tavily_key,
                    "query": f"{market_name} 今日热点板块 领涨行业 资金流入",
                    "search_depth": "basic",
                    "max_results": max(limit, 8),
                },
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            print(f"[hot_sectors] Tavily 失败: {exc}")
            return []

        results = data.get("results", []) if isinstance(data, dict) else []
        if not results:
            return []

        raw_text = " ".join(
            (r.get("title", "") or "") + " " + (r.get("content", "") or "")
            for r in results[:10]
        )
        sectors = _extract_sector_names(raw_text)
        if not sectors:
            sectors = _keyword_extract_sectors(raw_text, market)
        sectors = _clean_sector_names(sectors)[:limit]
        return [HotSector(name=s, source="tavily", score=0.8, reason="搜索关键词提取") for s in sectors]


_NOISE_WORDS = {
    "今日", "主力", "资金", "市场", "大盘", "恒生", "综合", "按照",
    "板块", "行业", "概念", "涨幅", "跌幅", "领涨", "个股", "标的",
    "上周", "本周", "本月", "港股", "美股", "A股", "大市", "午评", "收评",
    "简称", "总市值", "流通市", "成交额", "市盈率", "和消费", "必需",
    "港股通", "科技股", "大型科", "投资主", "表现最", "表现较", "次追逐",
}
_NOISE_PREFIXES = {"和", "及", "与", "按照", "根据", "其中", "包括"}
_NOISE_SUFFIXES = {"板", "块", "的", "等", "和", "及", "与", "涨", "股", "指", "收"}

def _extract_sector_names(text: str) -> List[str]:
    """Extract Chinese sector names from search result text."""
    candidates = []
    import re
    # Pattern: keyword + 板块/行业/概念
    for m in re.finditer(r"([一-鿿]{2,4})(?:板块|行业|概念)", text):
        name = m.group(1).strip()
        if name not in _NOISE_WORDS:
            candidates.append(name)
    # Pattern: 领涨/强势/活跃/热门 + sector name
    for m in re.finditer(r"(?:领涨|强势|活跃|热门)(?:的)?(?:板块|行业|概念)[：:]\s*([一-鿿]{2,6})", text):
        for part in re.split(r"[、，,;\s]+", m.group(1)):
            part = part.strip()
            if part and len(part) >= 2 and part not in _NOISE_WORDS:
                candidates.append(part)
    return list(dict.fromkeys(candidates))


def _keyword_extract_sectors(text: str, market: str) -> List[str]:
    """Fallback: keyword matching for common sector names."""
    keywords = [
        "AI", "人工智能", "半导体", "芯片", "机器人",
        "创新药", "医药", "医疗", "生物",
        "消费", "汽车", "新能源", "金融", "券商", "银行", "保险",
        "黄金", "电力", "军工", "5G", "算力", "软件",
        "商业航天", "卫星", "互联网", "电商",
    ]
    found = []
    text_lower = text.lower()
    for kw in keywords:
        if kw.lower() in text_lower:
            if kw not in found:
                found.append(kw)
    # Normalize: merge synonyms
    merged = _merge_synonyms(found)
    return merged[:8]


def _merge_synonyms(names: List[str]) -> List[str]:
    groups = [
        (["AI", "人工智能"], "AI"),
        (["半导体", "芯片"], "半导体"),
        (["创新药", "医药", "医疗", "生物", "制药"], "创新药"),
        (["金融", "券商", "银行", "保险", "大金融"], "金融"),
        (["消费", "消费品", "必需消费", "可选消费", "汽车", "零售"], "消费"),
        (["新能源", "光伏", "锂电", "风电"], "新能源"),
        (["机器人"], "机器人"),
        (["5G", "算力", "软件", "互联网", "电信"], "科技"),
        (["商业航天", "卫星"], "商业航天"),
        (["黄金", "贵金属"], "黄金"),
        (["电力", "公用"], "电力"),
        (["军工"], "军工"),
    ]
    result = list(names)
    merged_set = set()
    for aliases, canonical in groups:
        if any(a in result for a in aliases):
            merged_set.add(canonical)
            result = [n for n in result if n not in aliases]
    return list(merged_set) + result


class AkshareHotSectorProvider:
    """Market-data hot-sector provider, currently strongest for A shares."""

    name = "akshare_hot_sector"

    def find_hot_sectors(self, market: str, limit: int = 10) -> List[HotSector]:
        if market != "A":
            return []
        try:
            import akshare as ak
        except Exception:
            return []

        sectors: List[HotSector] = []
        for func_name, source_name in (
            ("stock_board_industry_name_em", "akshare_industry"),
            ("stock_board_concept_name_em", "akshare_concept"),
        ):
            func = getattr(ak, func_name, None)
            if func is None:
                continue
            try:
                df = func()
            except Exception:
                continue
            name_col = _find_column(getattr(df, "columns", []), ["板块名称", "名称"])
            pct_col = _find_column(getattr(df, "columns", []), ["涨跌幅"])
            turnover_col = _find_column(getattr(df, "columns", []), ["换手率"])
            up_col = _find_column(getattr(df, "columns", []), ["上涨家数"])
            down_col = _find_column(getattr(df, "columns", []), ["下跌家数"])
            leader_col = _find_column(getattr(df, "columns", []), ["领涨股票-涨跌幅"])
            if not name_col:
                continue
            for _, row in df.iterrows():
                name = _clean(row.get(name_col))
                if not name:
                    continue
                pct = _safe_float(row.get(pct_col)) if pct_col else 0.0
                turnover = _safe_float(row.get(turnover_col)) if turnover_col else 0.0
                up = _safe_float(row.get(up_col)) if up_col else 0.0
                down = _safe_float(row.get(down_col)) if down_col else 0.0
                leader_pct = _safe_float(row.get(leader_col)) if leader_col else 0.0
                rising_ratio = up / (up + down) * 100 if (up + down) > 0 else 0.0
                score = pct * 0.4 + turnover * 0.2 + rising_ratio * 0.3 + leader_pct * 0.1
                sectors.append(
                    HotSector(
                        name=name,
                        source=source_name,
                        score=round(score, 4),
                        reason=(
                            f"涨跌幅={pct:.2f}, 换手率={turnover:.2f}, "
                            f"上涨占比={rising_ratio:.2f}, 领涨={leader_pct:.2f}"
                        ),
                    )
                )
        sectors.sort(key=lambda item: item.score, reverse=True)
        return _dedupe_hot_sectors(sectors)[: max(1, int(limit))]


def _split_items(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    if not text:
        return []
    for sep in ("\n", "；", ";", "|", ","):
        text = text.replace(sep, "\n")
    return [item.strip() for item in text.split("\n") if item.strip()]


def _market_items(value: Any, market: str) -> List[str]:
    if isinstance(value, dict):
        return _split_items(value.get(market) or value.get("default") or value.get("*"))
    return _split_items(value)


def _find_column(columns, keywords) -> str:
    for col in columns:
        text = str(col)
        for keyword in keywords:
            if str(keyword) in text:
                return text
    return ""


def _safe_float(value: Any) -> float:
    try:
        text = str(value).strip().replace("%", "").replace(",", "")
        if not text or text in {"-", "--", "nan", "None"}:
            return 0.0
        return float(text)
    except Exception:
        return 0.0


def _clean(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text in {"-", "--", "nan", "None", ""} else text


def _clean_sector_names(names: List[str]) -> List[str]:
    """Post-process: strip noise prefix/suffix, filter garbage, merge synonyms."""
    result = []
    for name in names:
        # Strip leading noise
        for prefix in sorted(_NOISE_PREFIXES, key=len, reverse=True):
            if name.startswith(prefix) and len(name) > len(prefix) + 1:
                name = name[len(prefix):]
                break
        # Strip trailing noise
        for suffix in sorted(_NOISE_SUFFIXES, key=len, reverse=True):
            if name.endswith(suffix) and len(name) > len(suffix) + 1:
                name = name[:-len(suffix)]
        # Filter: reasonable sector name length (2-4 chars for Chinese)
        # and must not contain noise words
        if not name or len(name) < 2 or len(name) > 4:
            continue
        if any(nw in name for nw in ["恒生", "指数", "综合", "追逐", "按照", "简称", "总市值",
                                      "热门", "当日", "主要", "领涨", "也暴", "仅是", "规范", "已形成"]):
            continue
        result.append(name)
    return _merge_synonyms(result)


def _dedupe_hot_sectors(items: List[HotSector]) -> List[HotSector]:
    seen = set()
    result = []
    for item in items:
        if item.name in seen:
            continue
        seen.add(item.name)
        result.append(item)
    return result
