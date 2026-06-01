#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""企业潜力分析 — LLM 提示词构建 + 解析"""

from __future__ import annotations

import json
from typing import Any, Dict, List

from .models import EnterprisePotentialEvidencePackage

SYSTEM_PROMPT = """你是资深企业基本面分析师。基于提供的五维度结构化数据，对每只股票进行潜力评分。

## 评分规则

五维度权重: 宏观30% / 行业25% / 企业质量25% / 估值10% / 交易10%
每维度 0-100 分，总分 = 加权求和。

### 宏观环境 (0-100)
- 低利率 + 降息趋势 → 加分 (>=70)
- 温和通胀 (1-3% CPI) → 加分
- PMI > 50 扩张 + GDP 高增速 → 加分
- VIX < 20 低恐慌 → 加分
- VIX > 30 高恐慌 → 减分

### 行业景气 (0-100)
- 行业增速 > 15% → 加分
- 政策支持强 → 加分
- 竞争激烈 → 谨慎

### 企业质量 (0-100)
- ROIC/ROE > 20% → 强 (>=75)
- 毛利率 > 40% → 有护城河
- 收入/盈利增速 > 15% → 成长型
- 低负债 (D/E < 50) → 加分

### 估值 (0-100)
- PE < 15 → 低估 (>=75)
- PEG < 1.5 → 合理
- EV/EBITDA < 12 → 合理

### 交易行为 (0-100)
- 趋势向上 (价格 > 50MA) → 加分
- 低波动 → 加分
- Beta 适中 0.5-1.5 → 加分

## 输出格式

对于每只股票，返回 JSON:

{
  "ticker": "HK.01810",
  "total_score": 78,
  "macro_score": 85,
  "industry_score": 72,
  "company_score": 80,
  "valuation_score": 65,
  "trading_score": 70,
  "holding_period": "6-12个月",
  "decision": "BUY",
  "confidence_score": 82,
  "main_drivers": ["低利率环境", "AI Capex 扩张", "产品竞争力强"],
  "main_risks": ["估值偏高", "监管风险"],
  "causal_chain": ["降息 → 流动性改善 → AI Capex 上升 → 需求提升 → 盈利预期改善"],
  "evidence_refs": [],
  "data_gaps": []
}

decision: BUY (total>=75) / WATCH (70-74) / SKIP (<70)

## 建议持有周期 (holding_period)

按"主导因子"决定持有时长，并用中文「个月」区间表示：
- 交易/动量主导 → 偏短（约 1-3个月，信号半衰期短、易反转）
- 行业景气主导 → 中期（约 3-9个月）
- 宏观环境主导 → 中长期（约 6-12个月）
- 企业质量主导 → 中长期（约 12-24个月，慢回归/复利）
- 估值修复主导 → 长期（约 12-36个月）

规则：
- BUY/WATCH 必须输出中文区间（如 "6-12个月"），WATCH 可适当取更短区间
- SKIP 输出空字符串 ""
- 单位统一用中文"个月"，不要用英文 months
"""


class EnterprisePotentialPromptBuilder:
    """构建批量 LLM 评分提示词"""

    def build_batch_prompt(self, packages: List[EnterprisePotentialEvidencePackage]) -> str:
        """为批量股票构建评分提示词"""
        parts = ["以下股票需要评分，共 {} 只：\n".format(len(packages))]

        for i, pkg in enumerate(packages, 1):
            parts.append(f"## 股票 {i}: {pkg.code} {pkg.name} ({pkg.market})")
            parts.append(pkg.evidence_digest())
            parts.append("---\n")

        parts.append("\n请对以上每只股票评分，返回 JSON 数组：")
        parts.append('[{"ticker": "...", "total_score": ..., ...}, ...]')
        return "\n".join(parts)

    def build_single_prompt(self, package: EnterprisePotentialEvidencePackage) -> str:
        """单只股票的评分提示词（批量 N=1 特殊情况）"""
        return self.build_batch_prompt([package])


class EnterprisePotentialParser:
    """解析 LLM 批量评分响应"""

    def parse(self, raw_text: str) -> List[Dict[str, Any]]:
        """从 LLM 响应中提取 JSON 数组"""
        text = raw_text.strip()

        # 尝试直接解析
        try:
            data = json.loads(text)
            if isinstance(data, list):
                return data
            if isinstance(data, dict):
                return [data]
        except json.JSONDecodeError:
            pass

        # 尝试提取 JSON 数组
        start = text.find("[")
        end = text.rfind("]")
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(text[start:end+1])
            except json.JSONDecodeError:
                pass

        # 尝试提取单个 JSON 对象
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                return [json.loads(text[start:end+1])]
            except json.JSONDecodeError:
                pass

        raise ValueError(f"无法解析 LLM 响应: {text[:200]}...")

    def validate(self, item: Dict[str, Any]) -> Dict[str, Any]:
        """规范化单条评分结果"""
        for key in ("total_score", "macro_score", "industry_score",
                     "company_score", "valuation_score", "trading_score",
                     "confidence_score"):
            if key in item:
                item[key] = max(0, min(100, float(item[key] or 50)))

        item.setdefault("ticker", "")
        item.setdefault("decision", "SKIP")
        item.setdefault("holding_period", "")
        from .holding_period import normalize_holding_period

        item["holding_period"] = normalize_holding_period(item.get("holding_period", ""))
        item.setdefault("main_drivers", [])
        item.setdefault("main_risks", [])
        item.setdefault("causal_chain", [])
        item.setdefault("data_gaps", [])
        return item
