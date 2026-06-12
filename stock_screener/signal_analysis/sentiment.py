#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Lightweight Chinese financial sentiment analyzer using lexicon-based scoring.

Inspired by go-stock's ``stock_sentiment_analysis.go`` (70+ word financial dictionary).
Provides a pre-scoring signal before LLM evaluation to reduce hallucination risk.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class SentimentResult:
    score: float = 0.0
    label: str = "中性"  # 正面 | 负面 | 中性
    positive_words: List[str] = field(default_factory=list)
    negative_words: List[str] = field(default_factory=list)
    confidence: float = 0.0


# ── Lexicon ────────────────────────────────────────────────────

POSITIVE_WORDS: dict[str, float] = {
    "涨": 1.0, "上涨": 2.0, "涨停": 3.0, "牛市": 3.0, "反弹": 2.0, "新高": 2.5,
    "利好": 2.5, "增持": 2.0, "买入": 2.0, "推荐": 1.5, "看多": 2.0,
    "盈利": 2.0, "增长": 2.0, "超预期": 2.5, "强劲": 1.5, "回升": 1.5,
    "复苏": 2.0, "突破": 2.0, "创新高": 3.0, "回暖": 1.5, "上扬": 1.5,
    "利好消息": 3.0, "收益增长": 2.5, "利润增长": 2.5, "业绩优异": 2.5,
    "潜力股": 2.0, "绩优股": 2.0, "强势": 1.5, "走高": 1.5, "攀升": 1.5,
    "大涨": 2.5, "飙升": 3.0, "井喷": 3.0, "暴涨": 3.0,
    "分红": 1.5, "回购": 2.0, "扭亏": 2.5, "预增": 2.0,
}

NEGATIVE_WORDS: dict[str, float] = {
    "跌": 2.0, "下跌": 2.0, "跌停": 3.0, "熊市": 3.0, "回调": 2.5, "新低": 2.5,
    "利空": 2.5, "减持": 2.0, "卖出": 2.0, "看空": 2.0, "亏损": 2.5,
    "下滑": 2.0, "萎缩": 2.0, "不及预期": 2.5, "疲软": 1.5, "恶化": 2.0,
    "衰退": 2.0, "跌破": 2.0, "创新低": 3.0, "走弱": 2.5, "下挫": 2.5,
    "利空消息": 3.0, "收益下降": 2.5, "利润下滑": 2.5, "业绩不佳": 2.5,
    "垃圾股": 2.0, "风险股": 2.0, "弱势": 2.5, "走低": 2.5, "缩量": 2.5,
    "大跌": 2.5, "暴跌": 3.0, "崩盘": 3.0, "跳水": 3.0, "重挫": 3.0,
    "跌超": 2.5, "跌逾": 2.5, "跌近": 3.0, "回吐": 3.0, "转跌": 3.0,
    "暴雷": 3.0, "退市": 3.0, "违约": 2.5,
}

NEGATION_WORDS: set[str] = {"不", "没", "无", "非", "未", "别", "勿"}

DEGREE_WORDS: dict[str, float] = {
    "非常": 1.8, "极其": 2.2, "太": 1.8, "很": 1.5,
    "比较": 0.8, "稍微": 0.6, "有点": 0.7, "显著": 1.5,
    "大幅": 1.8, "急剧": 2.0, "轻微": 0.6, "小幅": 0.7, "逾": 1.8, "超": 1.8,
}

# ── Windows for negation/degree lookback ──
_LOOKBACK_CHARS = 3


class ChineseFinanceSentimentAnalyzer:
    """Simple lexicon + jieba segmentation based financial sentiment scorer."""

    def __init__(self, use_jieba: bool = True):
        self._use_jieba = use_jieba
        self._jieba_loaded = False
        self._try_load_jieba()

    def _try_load_jieba(self):
        try:
            import jieba
            jieba.initialize()
            self._jieba_loaded = True
        except Exception:
            self._jieba_loaded = False

    # ── public API ──────────────────────────────────────────

    def analyze(self, text: str) -> SentimentResult:
        """Score a Chinese financial text's sentiment polarity."""
        if not text or not text.strip():
            return SentimentResult()

        words = self._tokenize(text)
        if not words:
            return SentimentResult()

        positive_hits: List[str] = []
        negative_hits: List[str] = []
        total_score = 0.0

        for i, word in enumerate(words):
            weight = POSITIVE_WORDS.get(word)
            if weight is not None:
                weight = self._apply_degree(i, words, weight)
                weight = self._apply_negation(i, words, weight)
                total_score += weight
                positive_hits.append(word)
                continue

            weight = NEGATIVE_WORDS.get(word)
            if weight is not None:
                weight = self._apply_degree(i, words, weight)
                weight = self._apply_negation(i, words, weight)
                total_score -= weight
                negative_hits.append(word)

        label = "中性"
        if total_score > 0.5:
            label = "正面"
        elif total_score < -0.5:
            label = "负面"

        hit_count = len(positive_hits) + len(negative_hits)
        confidence = min(1.0, hit_count / max(len(words), 1) * 5.0)

        return SentimentResult(
            score=round(total_score, 2),
            label=label,
            positive_words=positive_hits,
            negative_words=negative_hits,
            confidence=round(confidence, 2),
        )

    def analyze_to_tag(self, text: str) -> str:
        """Return a short inline tag like '[情感:正面|得分:+2.5]' for prompt injection."""
        result = self.analyze(text)
        return f"[情感:{result.label}|得分:{result.score:+.1f}]"

    # ── internal ────────────────────────────────────────────

    def _tokenize(self, text: str) -> List[str]:
        """Tokenize with jieba if available, otherwise use simple bigram fallback."""
        if self._use_jieba and self._jieba_loaded:
            try:
                import jieba
                return [w.strip() for w in jieba.cut(text) if w.strip()]
            except Exception:
                pass
        # Fallback: character bigram + word-match scanning
        cleaned = re.sub(r"\s+", "", text)
        tokens: List[str] = []
        # Scan all known words
        remaining = cleaned
        while remaining:
            matched = False
            for word in sorted(
                list(POSITIVE_WORDS.keys()) + list(NEGATIVE_WORDS.keys()),
                key=len,
                reverse=True,
            ):
                if remaining.startswith(word):
                    tokens.append(word)
                    remaining = remaining[len(word):]
                    matched = True
                    break
            if not matched:
                tokens.append(remaining[0])
                remaining = remaining[1:]
        return tokens

    @staticmethod
    def _apply_degree(idx: int, words: List[str], base_weight: float) -> float:
        """Amplify weight if a degree adverb appears within lookback window."""
        start = max(0, idx - 2)
        for j in range(start, idx):
            modifier = DEGREE_WORDS.get(words[j])
            if modifier is not None:
                return base_weight * modifier
        return base_weight

    @staticmethod
    def _apply_negation(idx: int, words: List[str], base_weight: float) -> float:
        """Flip polarity if a negation word appears within lookback window."""
        start = max(0, idx - 2)
        for j in range(start, idx):
            if words[j] in NEGATION_WORDS:
                return -base_weight
        return base_weight


# ── Convenience ────────────────────────────────────────────────

_default_analyzer: Optional[ChineseFinanceSentimentAnalyzer] = None


def get_default_analyzer() -> ChineseFinanceSentimentAnalyzer:
    global _default_analyzer
    if _default_analyzer is None:
        _default_analyzer = ChineseFinanceSentimentAnalyzer()
    return _default_analyzer
