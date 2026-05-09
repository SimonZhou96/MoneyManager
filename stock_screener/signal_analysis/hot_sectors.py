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


def _dedupe_hot_sectors(items: List[HotSector]) -> List[HotSector]:
    seen = set()
    result = []
    for item in items:
        if item.name in seen:
            continue
        seen.add(item.name)
        result.append(item)
    return result
