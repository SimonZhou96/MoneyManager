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
    """Dynamic hot-sector discovery via DeepSeek LLM (with Bing/Baidu → Tavily → ZhipuAI fallback).

    Priority: DeepSeek (default) → Bing/Baidu (free) → Tavily → ZhipuAI.
    """

    name = "deepseek_hot_sector"

    def __init__(self, api_key: str = ""):
        self.api_key = api_key or os.getenv("DEEPSEEK_API_KEY", "").strip()
        self.api_base = os.getenv("DEEPSEEK_API_BASE", "https://api.deepseek.com").strip()
        self.model = os.getenv("DEEPSEEK_LLM_MODEL", "deepseek-chat").strip()
        self.tavily_key = os.getenv("TAVILY_API_KEY", "").strip()
        self.zhipu_key = (
            os.getenv("ZHIPUAI_API_KEY", "").strip()
            or os.getenv("ZHIPU_API_KEY", "").strip()
            or os.getenv("BIGMODEL_API_KEY", "").strip()
        )
        self.zhipu_endpoint = os.getenv(
            "ZHIPUAI_WEB_SEARCH_ENDPOINT",
            "https://open.bigmodel.cn/api/paas/v4/web_search",
        ).strip()
        self.zhipu_search_engine = os.getenv("ZHIPUAI_WEB_SEARCH_ENGINE", "search_std").strip() or "search_std"
        self.zhipu_content_size = os.getenv("ZHIPUAI_WEB_SEARCH_CONTENT_SIZE", "medium").strip() or "medium"
        self.zhipu_recency_filter = os.getenv("ZHIPUAI_WEB_SEARCH_RECENCY_FILTER", "noLimit").strip() or "noLimit"

    @property
    def is_available(self) -> bool:
        return bool(self.api_key) or bool(self.tavily_key) or bool(self.zhipu_key)

    def find_hot_sectors(self, market: str, limit: int = 6) -> List[HotSector]:
        # Priority 1: DeepSeek LLM (clean, reliable)
        if self.api_key:
            sectors = self._deepseek_discover(market, limit)
            if sectors:
                return sectors

        # Priority 2: Bing/Baidu free browser search
        sectors = self._bing_baidu_discover(market, limit)
        if sectors:
            return sectors

        # Priority 3: Tavily search + keyword extraction
        if self.tavily_key:
            sectors = self._tavily_discover(market, limit)
            if sectors:
                return sectors

        # Priority 4: ZhipuAI web search + keyword extraction
        if self.zhipu_key:
            sectors = self._zhipu_discover(market, limit)
            if sectors:
                return sectors

        return []

    def find_hot_sectors_or_defaults(self, market: str, limit: int = 6) -> List[HotSector]:
        """find_hot_sectors with built-in fallback when all search methods fail."""
        result = self.find_hot_sectors(market, limit)
        if not result:
            result = _get_default_sectors(market, limit)
        return result

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

    def _bing_baidu_discover(self, market: str, limit: int) -> List[HotSector]:
        """Discover hot sectors via free Bing/Baidu browser search."""
        market_name = {"HK": "港股", "US": "美股", "A": "A股"}.get(market, market)
        try:
            from .browser_search_providers import BingBaiduSearchProvider
            provider = BingBaiduSearchProvider(headless=True, timeout_sec=20)
            if not provider.is_available:
                return []
            query = f"{market_name} 今日热点板块 领涨行业 资金流入"
            documents = provider.search(query, max(limit, 8))
            provider.close()
        except Exception as exc:
            print(f"[hot_sectors] Bing/Baidu 搜索失败: {exc}")
            return []

        if not documents:
            return []

        raw_text = " ".join(
            (d.title or "") + " " + (d.content or "")
            for d in documents[:10]
        )
        sectors = _extract_sector_names(raw_text)
        if not sectors:
            sectors = _keyword_extract_sectors(raw_text)
        sectors = _clean_sector_names(sectors)
        sectors = _merge_synonyms(sectors)[:limit]
        if not sectors:
            return []
        print(f"[hot_sectors] Bing/Baidu 发现: {sectors}")
        return [HotSector(name=s, source="bing_baidu", score=0.6, reason="浏览器搜索关键词提取") for s in sectors]

    def _tavily_discover(self, market: str, limit: int) -> List[HotSector]:
        market_name = {"HK": "港股", "US": "美股", "A": "A股"}.get(market, market)
        try:
            import requests
            from .search_providers import _http_retry

            def _send():
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
                if resp.status_code >= 400 and resp.status_code not in (429, 432, 500, 502, 503, 504):
                    resp.raise_for_status()
                return resp

            resp = _http_retry(_send, label="hot_sectors.tavily")
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

    def _zhipu_discover(self, market: str, limit: int) -> List[HotSector]:
        market_name = {"HK": "港股", "US": "美股", "A": "A股"}.get(market, market)
        try:
            import requests
            from .search_providers import _http_retry

            def _send():
                resp = requests.post(
                    self.zhipu_endpoint,
                    json={
                        "search_query": f"{market_name} 今日热点板块 领涨行业 资金流入 龙头股",
                        "search_engine": self.zhipu_search_engine,
                        "search_intent": False,
                        "count": min(50, max(1, int(limit) * 2)),
                        "search_recency_filter": self.zhipu_recency_filter,
                        "content_size": self.zhipu_content_size,
                    },
                    headers={
                        "Authorization": f"Bearer {self.zhipu_key}",
                        "Content-Type": "application/json",
                    },
                    timeout=10,
                )
                if resp.status_code >= 400 and resp.status_code not in (429, 432, 500, 502, 503, 504):
                    resp.raise_for_status()
                return resp

            resp = _http_retry(_send, label="hot_sectors.zhipu")
            resp.raise_for_status()
            data = resp.json() or {}
        except Exception as exc:
            print(f"[hot_sectors] ZhipuAI 失败: {exc}")
            return []

        results = (data.get("search_result") or data.get("results") or []) if isinstance(data, dict) else []
        if not results:
            return []

        raw_text = " ".join(
            (r.get("title", "") or "") + " " + (r.get("content", "") or r.get("summary", "") or r.get("snippet", "") or "")
            for r in results[:10]
            if isinstance(r, dict)
        )
        sectors = _extract_sector_names(raw_text)
        if not sectors:
            sectors = _keyword_extract_sectors(raw_text, market)
        sectors = _clean_sector_names(sectors)[:limit]
        return [HotSector(name=s, source="zhipuai", score=0.7, reason="智谱AI搜索关键词提取") for s in sectors]


_NOISE_WORDS = {
    "今日", "主力", "资金", "市场", "大盘", "恒生", "综合", "按照",
    "板块", "行业", "概念", "涨幅", "跌幅", "领涨", "个股", "标的",
    "上周", "本周", "本月", "港股", "美股", "A股", "大市", "午评", "收评",
    "简称", "总市值", "流通市", "成交额", "市盈率", "和消费", "必需",
    "港股通", "科技股", "大型科", "投资主", "表现最", "表现较", "次追逐",
    # HTML / table fragments from browser scraping
    "按市场", "所属", "交易日", "每手入", "市场热",
    "深两市", "包括", "以及各", "数等详", "以下为", "序号",
    "名次", "最新价", "涨跌额", "涨跌幅", "昨收", "今开", "最高", "最低",
    "成交量", "成交额", "振幅", "换手率", "量比",
    "光纤", "目前", "本页面", "数据来", "请仔细", "阅读协",
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


# ── Known valid sector names (whitelist) ──
_VALID_SECTORS: set[str] = {
    # Tech
    "AI", "人工智能", "半导体", "芯片", "软件", "互联网", "5G", "算力", "云计算",
    "大数据", "区块链", "物联网", "机器人", "自动化",
    # Healthcare
    "创新药", "医药", "医疗", "生物", "制药", "中药", "医疗器械", "医美",
    # Consumer
    "消费", "餐饮", "零售", "白酒", "食品", "饮料", "家电", "汽车", "新能源车",
    "教育", "旅游", "博彩", "物业管理",
    # Finance
    "金融", "银行", "保险", "券商", "房地产",
    # Energy & Industry
    "新能源", "光伏", "锂电", "风电", "储能", "电力", "煤炭", "石油",
    "军工", "航天", "商业航天", "卫星", "高铁", "基建",
    # Materials
    "黄金", "贵金属", "稀土", "钢铁", "有色", "化工", "建材",
    # Others
    "传媒", "游戏", "电商", "物流", "环保", "农业", "电力设备",
    # English/abbreviated (from English sources)
    "SaaS", "EV", "ESG",
}

# ── Market-specific default sectors (ultimate fallback) ──
_DEFAULT_SECTORS_BY_MARKET: dict[str, list[str]] = {
    "HK": ["互联网", "金融", "消费", "创新药", "半导体", "博彩", "新能源", "物业管理", "白酒"],
    "US": ["AI", "半导体", "SaaS", "新能源车", "生物科技", "云计算", "电商", "航天", "金融"],
    "A":  ["机器人", "算力", "半导体", "新能源", "券商", "军工", "消费", "白酒", "电力"],
}


def _clean_sector_names(names: List[str]) -> List[str]:
    """Post-process: strip noise, filter garbage, validate against known sectors, merge synonyms."""
    # Normalize: trim, remove brackets/parens content
    cleaned: list[str] = []
    for name in names:
        name = name.strip()
        if not name:
            continue
        # Remove parenthetical notes like "半导体（领涨）" → "半导体"
        import re
        name = re.sub(r"[（(][^)）]*[)）]", "", name).strip()
        # Strip leading noise
        for prefix in sorted(_NOISE_PREFIXES, key=len, reverse=True):
            if name.startswith(prefix) and len(name) > len(prefix) + 1:
                name = name[len(prefix):]
                break
        # Strip trailing noise
        for suffix in sorted(_NOISE_SUFFIXES, key=len, reverse=True):
            if name.endswith(suffix) and len(name) > len(suffix) + 1:
                name = name[:-len(suffix)]
        if not name:
            continue
        cleaned.append(name)

    # First pass: keep names that match known valid sectors
    known: list[str] = []
    unknown: list[str] = []
    for name in cleaned:
        if name in _VALID_SECTORS:
            known.append(name)
        else:
            unknown.append(name)

    # Second pass: filter unknown names
    for name in unknown:
        # Must be 2-4 Chinese chars or 2-6 ASCII
        if not (2 <= len(name) <= 4 and all('一' <= c <= '鿿' or c in '·&' for c in name)):
            if not (2 <= len(name) <= 6 and all(c.isascii() and (c.isalpha() or c.isdigit()) for c in name)):
                continue
        # Must not contain garbage patterns
        if _is_garbage(name):
            continue
        if any(nw in name for nw in _NOISE_WORDS):
            continue
        # Extra: reject single-char repetitions, numbers-only, etc.
        if len(set(name)) == 1 and len(name) > 1:
            continue
        if name.isdigit():
            continue
        known.append(name)

    return _merge_synonyms(known)


def _is_garbage(name: str) -> bool:
    """Heuristic: detect if a name looks like text fragment, not a sector."""
    garbage_markers = [
        "下跌", "上涨", "流出", "流入", "净买", "净卖",
        "交易日", "每手", "简称", "总市值", "流通市",
        "最新价", "涨跌额", "涨跌幅", "昨收", "今开", "最高", "最低",
        "成交量", "成交额", "振幅", "换手率", "量比",
        "本页面", "数据来", "阅读", "条款", "声明",
        "以下为", "名次", "按市场", "所属",
        "追逐", "按照", "规范", "已形成", "业板指",
        "也暴", "仅是", "热门", "恒生", "综合",
        "技術", "防御", "性板", "类板", "等消", "分类",
        "数等", "资金", "主力", "大市",
        # English garbage
        "http", "www", ".com", ".cn",
    ]
    return any(m in name for m in garbage_markers)


def _get_default_sectors(market: str, limit: int) -> List[HotSector]:
    """Return curated default sectors when all search methods fail."""
    names = _DEFAULT_SECTORS_BY_MARKET.get(market, _DEFAULT_SECTORS_BY_MARKET["HK"])
    return [
        HotSector(name=n, source="builtin", score=0.7, reason="内置默认板块")
        for n in names[:limit]
    ]


def _dedupe_hot_sectors(items: List[HotSector]) -> List[HotSector]:
    seen = set()
    result = []
    for item in items:
        if item.name in seen:
            continue
        seen.add(item.name)
        result.append(item)
    return result


class HotSectorClassifier:
    """热点分类器：将原始热点从地域标签映射为行业/主题/地域三类。"""

    # ── 行业关键词（40+ Chinese sector names） ──
    INDUSTRY_NAMES = {
        # Tech — 科技
        "半导体", "芯片", "互联网", "软件", "云计算", "大数据", "物联网", "5G",
        "通信设备", "消费电子", "电子元器件", "计算机设备",
        "应用软件", "软件开发", "软件应用", "软件基础设施",
        "科技", "科技硬件", "电子设备", "光电", "数字化解决方案",
        # Healthcare — 医药
        "医药", "创新药", "医疗", "生物科技", "制药", "中药", "医疗器械",
        "医美", "医疗服务", "医疗设备", "生物", "仿制药", "化学制药",
        "医疗设施", "医疗保健", "生命科学",
        # Consumer — 消费
        "消费", "白酒", "食品饮料", "餐饮", "零售", "食品", "家电",
        "汽车", "新能源车", "汽车零部件", "博彩", "物业管理",
        "教育", "旅游", "服装制造", "包装食品", "饮料",
        "消费服务", "传媒", "游戏", "电商", "广告代理",
        "互动媒体与服务", "媒体娱乐", "个人护理", "烟草",
        "家居", "家居耐用", "纺织服装", "预制菜",
        # Finance — 金融
        "金融", "银行", "券商", "保险", "房地产", "地产",
        "证券经纪", "信贷服务", "资产管理", "资本市场",
        "金融服务", "综合金融", "投资资管", "区域银行",
        "房地产开发商", "房地产开发", "房地产投资", "房地产服务",
        # Energy — 能源
        "新能源", "光伏", "锂电", "风电", "储能", "电力", "煤炭", "石油",
        "电力设备", "电力公用", "电网设备", "光伏设备", "电池",
        "能源设备与服务", "油气勘探", "油气中游", "油气服务",
        "油气设备与服务", "核电", "核能", "氢能源",
        # Industry — 工业
        "工业", "工业金属", "工业零部件", "通用设备", "专用设备",
        "自动化设备", "电气设备", "工业机械", "电子零部件", "办公电子",
        # Materials — 材料
        "化工", "建材", "原材料", "特种化工", "化工原料",
        "黄金", "贵金属", "稀土", "钢铁", "有色",
        "工业金属与矿业", "玻璃纤维", "塑料", "农药化工",
        # Defense — 国防军工
        "军工", "航天", "商业航天", "卫星", "国防",
        "航空航天与国防", "军工电子",
        # Infrastructure & Transport — 基建交运
        "高铁", "交通运输", "航运港口", "物流",
        "电信服务", "电信网络基建", "电信", "基建", "工程建设",
        "重工基建", "建筑",
        # Services — 服务
        "环保", "环保服务", "环保治理", "农业", "商业服务",
        "支援服务", "IT服务", "IT咨询", "互联网内容",
        # Other — 其他
        "自动化", "壳公司",
    }

    # ── 主题关键词（概念性、事件驱动型主题） ──
    THEME_KEYWORDS = {
        "AI", "人工智能", "算力", "机器人", "低空经济", "功率半导体",
        "量子计算", "元宇宙", "数字人", "信创", "国产替代",
        "碳中和", "碳达峰", "华为概念", "特斯拉概念", "抖音概念",
        "固态电池", "钠离子电池", "钙钛矿",
        "机器视觉", "自动驾驶", "无人驾驶", "脑机接口",
        "合成生物", "减肥药",
        "中特估", "央企改革", "国企改革", "混改",
        "数据要素", "数据资产", "数字经济", "数字中国",
        "东数西算", "跨境支付", "数字货币", "区块链",
        "工业母机", "仪器仪表", "传感器",
        "液冷", "光通信", "CPO", "硅光",
        "稀土永磁", "超级电容",
        "冷链", "宠物经济",
        "直播带货", "网红经济", "新零售",
        "银发经济", "养老", "生育",
        "新能源",
    }

    # ── 地域催化剂关键词（只有匹配时才归为地域热点） ──
    REGION_CATALYSTS = {
        "海南自贸", "粤港澳", "长三角", "成渝",
        "雄安", "京津冀", "上海自贸", "深圳先行",
        "安徽国资", "重庆国资",
        "一带一路", "西部开发", "东北振兴",
        "杭州亚运", "横琴", "前海", "南沙",
    }

    # ── 省份/城市名（需要额外催化剂才算热点，否则跳过） ──
    PROVINCE_NAMES = {
        "安徽", "北京", "重庆", "福建", "甘肃", "广东", "广西", "贵州",
        "海南", "河北", "河南", "黑龙江", "湖北", "湖南",
        "江苏", "江西", "吉林", "辽宁", "内蒙古", "宁夏", "青海",
        "山东", "山西", "陕西", "上海", "四川",
        "台湾", "天津", "西藏", "香港", "新疆", "澳门", "云南", "浙江",
        "深圳", "广州", "成都", "武汉", "杭州", "南京",
        "苏州", "宁波", "厦门", "青岛", "大连", "雄安",
    }

    def classify(self, raw_hot_sectors: list[str]) -> dict:
        """将原始热点列表分类为行业/主题/地域三类。

        Args:
            raw_hot_sectors: 原始热点板块名称列表。

        Returns:
            {"industry": [...], "theme": [...], "region": [...]}
            无法分类的条目被静默跳过。
        """
        result: dict[str, list[str]] = {"industry": [], "theme": [], "region": []}

        for sector in raw_hot_sectors:
            if not sector or not sector.strip():
                continue
            sector = sector.strip()

            # 1. 行业关键词 → industry 桶
            if sector in self.INDUSTRY_NAMES:
                if sector not in result["industry"]:
                    result["industry"].append(sector)
                continue

            # 2. 主题关键词 → theme 桶
            if sector in self.THEME_KEYWORDS:
                if sector not in result["theme"]:
                    result["theme"].append(sector)
                continue

            # 3. 地域催化剂 → region 桶
            if sector in self.REGION_CATALYSTS:
                if sector not in result["region"]:
                    result["region"].append(sector)
                continue

            # 4. 省份/城市名，无催化剂 → 跳过
            if sector in self.PROVINCE_NAMES:
                continue

            # 5. 未知 → 尝试子串映射到行业，否则跳过
            mapped = self._map_to_industry(sector)
            if mapped and mapped not in result["industry"]:
                result["industry"].append(mapped)

        return result

    def _map_to_industry(self, sector: str) -> str | None:
        """尝试将未知板块名通过子串匹配映射到已知行业。"""
        for industry in sorted(self.INDUSTRY_NAMES, key=len, reverse=True):
            if len(industry) >= 2 and industry in sector:
                return industry
        return None
