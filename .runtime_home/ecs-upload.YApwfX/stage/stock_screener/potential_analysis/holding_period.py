#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""企业潜力分析 — 决策与因子驱动的建议持有周期

依据因子持有时长特性（交易/动量半衰期短、价值/质量回归慢）将主导贡献模块
映射为中文建议持有周期区间。仅对看涨（BUY/WATCH）输出，SKIP 返回空字符串。
"""

from __future__ import annotations

import re
from typing import Dict, Optional, Tuple

BUY_SCORE_THRESHOLD = 75.0
NEUTRAL_SCORE = 50.0

# 主导模块 -> 基准持有周期区间（单位：月）
# 交易/动量信号半衰期短，价值/质量类回归慢、适合中长期持有。
HORIZON_BANDS_BY_MODULE: Dict[str, Tuple[int, int]] = {
    "trading": (1, 3),
    "industry": (3, 9),
    "macro": (6, 12),
    "company": (12, 24),
    "valuation": (12, 36),
}

# 无主导模块时按决策的兜底区间
_FALLBACK_BANDS: Dict[str, Tuple[int, int]] = {
    "BUY": (6, 12),
    "WATCH": (3, 6),
}

_ENGLISH_PERIOD_PATTERN = re.compile(r"(\d+)\s*-\s*(\d+)\s*months?", re.IGNORECASE)
_SINGLE_ENGLISH_PATTERN = re.compile(r"(\d+)\s*months?", re.IGNORECASE)


def decision_from_total_score(total_score: float, threshold: float) -> str:
    """根据总分与阈值得出决策。"""
    if total_score >= BUY_SCORE_THRESHOLD:
        return "BUY"
    if total_score >= threshold:
        return "WATCH"
    return "SKIP"


def format_band(min_m: int, max_m: int) -> str:
    """格式化为中文区间文案。"""
    min_m = max(1, int(min_m))
    max_m = max(min_m, int(max_m))
    if min_m == max_m:
        return f"{min_m}个月"
    return f"{min_m}-{max_m}个月"


def dominant_module(
    module_scores: Dict[str, Optional[float]],
    weights: Dict[str, float],
) -> Optional[str]:
    """返回对超过中性分的加权贡献最大的模块；无正贡献返回 None。"""
    best_module: Optional[str] = None
    best_contrib = 0.0
    for mod, score in (module_scores or {}).items():
        if score is None or mod not in HORIZON_BANDS_BY_MODULE:
            continue
        weight = float(weights.get(mod, 0.0) or 0.0)
        contrib = weight * max(0.0, float(score) - NEUTRAL_SCORE)
        if contrib > best_contrib:
            best_contrib = contrib
            best_module = mod
    return best_module


def _shrink_for_watch(min_m: int, max_m: int) -> Tuple[int, int]:
    """WATCH 取偏短的一半区间，更保守，下限不低于 1 个月。"""
    new_min = max(1, min_m // 2)
    new_max = max(new_min, max(1, max_m // 2))
    return new_min, new_max


def suggest_holding_period(
    decision: str,
    module_scores: Dict[str, Optional[float]],
    weights: Dict[str, float],
    total_score: float,
    threshold: float,
) -> str:
    """因子驱动的建议持有周期；非看涨（SKIP）返回空字符串。"""
    resolved = (decision or "").strip().upper()
    if resolved not in ("BUY", "WATCH"):
        resolved = decision_from_total_score(total_score, threshold)
    if resolved not in ("BUY", "WATCH"):
        return ""

    dominant = dominant_module(module_scores, weights)
    if dominant is not None:
        min_m, max_m = HORIZON_BANDS_BY_MODULE[dominant]
        # WATCH 弱看涨：基于模块区间取更短、更保守的一半
        if resolved == "WATCH":
            min_m, max_m = _shrink_for_watch(min_m, max_m)
    else:
        # 无主导模块时按决策给兜底区间（已是决策定制，不再收窄）
        min_m, max_m = _FALLBACK_BANDS[resolved]

    return format_band(min_m, max_m)


def normalize_holding_period(period: str) -> str:
    """将 LLM/历史英文持有周期统一为中文展示格式。"""
    text = str(period or "").strip()
    if not text:
        return ""
    range_match = _ENGLISH_PERIOD_PATTERN.search(text)
    if range_match:
        return format_band(int(range_match.group(1)), int(range_match.group(2)))
    single_match = _SINGLE_ENGLISH_PATTERN.search(text)
    if single_match:
        return f"{int(single_match.group(1))}个月"
    return text
