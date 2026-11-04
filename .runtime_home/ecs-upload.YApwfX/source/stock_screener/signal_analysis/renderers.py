#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Retail-investor-friendly report renderer.

Replaces the old markdown template with a 10-section retail report
that uses plain Chinese, emoji indicators, and simple tables.

Usage::

    renderer = RetailReportRenderer()
    report = renderer.render({
        "market": "A",
        "market_temp": MarketTemperature(market="A"),
        "stocks": [...],
        "hot_sectors": {"industry": [...], "theme": [...], "region": [...]},
        "report_date": "2026-06-18",
        "chain_key": "unified_bullish_top20",
        "data_sources": [...],
    })
"""

from __future__ import annotations

from datetime import date
from typing import Dict, List, Optional

from scoring.models import (
    MarketTemperature,
)


# ═══════════════════════════════════════════════════════════════════
# 市场温度计颜色映射
# ═══════════════════════════════════════════════════════════════════

_TEMP_COLORS = {
    "偏强": ("🟢", "偏强 — 市场情绪积极，赚钱效应好"),
    "中性": ("🟡", "中性 — 市场方向不明确，观望为主"),
    "偏弱": ("🔴", "偏弱 — 市场情绪低迷，控制仓位"),
    "好": ("🟢", "好 — 多数股票处于上涨趋势"),
    "一般": ("🟡", "一般 — 涨跌互现，赚钱效应一般"),
    "差": ("🔴", "差 — 多数股票表现不佳"),
    "流入": ("🟢", "流入 — 资金面偏宽松，增量资金入场"),
    "流出": ("🔴", "流出 — 资金面偏紧，存量博弈"),
    "高": ("🟢", "高 — 投资者风险偏好较高"),
    "中": ("🟡", "中 — 风险偏好适中"),
    "低": ("🔴", "低 — 投资者趋于谨慎"),
    "清晰": ("🟢", "清晰 — 市场主线明确，热点聚焦"),
    "混乱": ("🔴", "混乱 — 板块轮动快，热点不持续"),
}



# ═══════════════════════════════════════════════════════════════════
# 入场/持有决策阈值
# ═══════════════════════════════════════════════════════════════════

_ENTRY_THRESHOLDS = [
    (75, "🟢 强入场信号", "技术形态良好，量价配合，可考虑入场"),
    (65, "🟡 中等入场信号", "部分指标共振，轻仓试探"),
    (50, "🟠 弱入场信号", "趋势待确认，等待更明确信号"),
    (0, "🔴 不建议入场", "入场条件不满足，观望为主"),
]

_HOLDING_THRESHOLDS = [
    (80, "🟢 强持有价值", "企业基本面优质，适合中线持有"),
    (65, "🟡 中等持有价值", "有一定基本面支撑，可继续观察"),
    (50, "🟠 弱持有价值", "企业基本面一般，注意风险"),
    (0, "🔴 不建议持有", "基本面或资金面存在明显问题"),
]

MARKET_NAMES = {
    "HK": "港股",
    "US": "美股",
    "A": "A股",
}


class RetailReportRenderer:
    """Retail-investor-friendly report renderer.

    Produces a 10-section markdown report designed for retail investors.
    Every section has an empty-stock fallback so the report is always
    structurally complete.
    """

    def render(self, context: dict) -> str:
        """Return complete markdown report string."""
        market = str(context.get("market", "A"))
        market_temp: Optional[MarketTemperature] = context.get("market_temp")
        stocks: List[dict] = context.get("stocks") or []
        hot_sectors: Optional[dict] = context.get("hot_sectors")
        report_date_str = str(context.get("report_date", ""))
        chain_key = str(context.get("chain_key", "unified_bullish_top20"))
        data_sources: List[dict] = context.get("data_sources") or []

        market_name = MARKET_NAMES.get(market, market)
        has_stocks = len(stocks) > 0

        # Parse date for formatted display
        if report_date_str:
            try:
                report_dt = date.fromisoformat(report_date_str)
                date_display = report_dt.strftime("%Y年%m月%d日")
            except (ValueError, TypeError):
                date_display = report_date_str
        else:
            date_display = "待定"

        lines: List[str] = []

        # ── Section 0: 重要提示（免责声明）────────────────────────
        self._section_disclaimer(lines, market_name, date_display, chain_key)

        # ── Section 1: 本期一句话结论 ─────────────────────────────
        self._section_one_liner(lines, market_name, market_temp, has_stocks)

        # ── Section 2: 本期市场温度计 ─────────────────────────────
        self._section_market_thermometer(lines, market_temp)

        # ── Section 3: 本期热点方向 ───────────────────────────────
        self._section_hot_directions(lines, hot_sectors)

        # ── Section 4: 本期评分怎么看 ─────────────────────────────
        self._section_scoring_guide(lines)

        # ── Section 5: 本期 Top20 简表 ────────────────────────────
        self._section_top20_table(lines, stocks, has_stocks)

        # ── Section 6: 精选个股卡片 ───────────────────────────────
        self._section_stock_cards(lines, stocks, has_stocks)

        # ── Section 7: 分类型建议 ─────────────────────────────────
        self._section_category_advice(lines, stocks, has_stocks)

        # ── Section 8: 风险提示 ───────────────────────────────────
        self._section_risk_disclosure(lines, stocks, has_stocks)

        # ── Section 9: 数据来源与新鲜度 ───────────────────────────
        self._section_data_sources(lines, data_sources, date_display)

        return "\n".join(lines)

    # ═══════════════════════════════════════════════════════════════
    # 节渲染方法
    # ═══════════════════════════════════════════════════════════════

    def _section_disclaimer(
        self,
        lines: List[str],
        market_name: str,
        date_display: str,
        chain_key: str,
    ) -> None:
        """Section 0: 免责声明 + 标题 + 报告元信息。"""
        lines.append(f"# {market_name} 散户友好版信号报告")
        lines.append("")
        lines.append(f"**报告日期**：{date_display}")
        if chain_key:
            lines.append(f"**规则链**：`{chain_key}`")
        lines.append("")
        lines.append(
            "> **重要提示**：本文档由自动化系统生成，基于技术信号、市场数据和大语言模型分析，"
            "仅供参考和学习交流。**不构成任何投资建议，不对据此操作产生的盈亏负责。**"
        )
        lines.append("> 股市有风险，投资需谨慎。请基于自身风险承受能力做出独立判断。")
        lines.append("")
        lines.append("---")
        lines.append("")

    def _section_one_liner(
        self,
        lines: List[str],
        market_name: str,
        market_temp: Optional[MarketTemperature],
        has_stocks: bool,
    ) -> None:
        """Section 1: 一句话结论。"""
        # Determine overall posture from market temperature
        if market_temp and market_temp.temperature_summary == "偏强":
            posture = "市场环境偏暖，可积极关注信号标的"
        elif market_temp and market_temp.temperature_summary == "偏弱":
            posture = "市场情绪偏弱，建议控制仓位、精选标的"
        else:
            posture = "市场方向尚不明确，保持中性仓位观察"

        if not has_stocks:
            posture = f"（{posture}）本期暂未筛选出符合条件标的，建议等待下一期"

        lines.extend([
            "## 一、本期一句话结论",
            "",
            f"> **{posture}**",
            "",
            "---",
            "",
        ])

    def _section_market_thermometer(
        self,
        lines: List[str],
        market_temp: Optional[MarketTemperature],
    ) -> None:
        """Section 2: 市场温度计 — 5项指标 + 颜色 + 白话解释。"""
        lines.extend([
            "## 二、本期市场温度计",
            "",
            "5个维度帮你看懂当前市场处在什么阶段：",
            "",
            "| 指标 | 状态 | 颜色 | 大白话解释 |",
            "|---|---|---|---|",
        ])

        indicators = [
            ("市场温度", "temperature_summary", "市场整体是强是弱？"),
            ("赚钱效应", "money_making_summary", "当前好不好赚钱？"),
            ("资金环境", "capital_env_summary", "资金在流入还是流出？"),
            ("风险偏好", "risk_appetite_summary", "大家敢不敢买股票？"),
            ("热点清晰度", "hot_clarity_summary", "市场有没有明确的主线？"),
        ]

        any_data = False
        for label, attr, explanation in indicators:
            if market_temp is not None:
                value = getattr(market_temp, attr, "") or "中性"
            else:
                value = "中性"

            color_emoji, color_explanation = _TEMP_COLORS.get(value, ("⚪", value))
            if value == "中性":
                color_explanation = f"{value} — 暂无明确倾向"

            any_data = True
            lines.append(
                f"| **{label}** | {color_emoji} {value} | "
                f"{color_emoji} | {explanation} → {color_explanation} |"
            )

        if not any_data:
            lines.append(
                "| 市场温度 | ⚪ 未知 | ⚪ | 数据暂未获取，请检查市场数据源连接 |"
            )

        lines.extend([
            "",
            "**说明**：🟢 = 偏正面 / 🟡 = 中性 / 🔴 = 偏负面。如果多个指标偏红，建议降低仓位。",
            "",
            "---",
            "",
        ])

    def _section_hot_directions(
        self,
        lines: List[str],
        hot_sectors: Optional[dict],
    ) -> None:
        """Section 3: 热点方向 — 行业/主题/地域 三类。"""
        lines.extend([
            "## 三、本期热点方向",
            "",
            "当前市场资金主要关注以下方向：",
            "",
        ])

        if hot_sectors:
            categories = [
                ("🏭 **热点行业**", "industry", "资金持续流入的行业板块"),
                ("🏷️ **热点主题**", "theme", "事件或政策驱动的主题概念"),
                ("🌍 **热点地域**", "region", "地域性投资机会"),
            ]

            for title, key, hint in categories:
                items = hot_sectors.get(key) or []
                if items:
                    items_text = "、".join(items[:6])
                    if len(items) > 6:
                        items_text += f" 等{len(items)}个"
                    lines.append(f"- **{title}**：{items_text}")
                    lines.append(f"  - *{hint}*")
                else:
                    lines.append(f"- **{title}**：{self._no_data_text()}")

            lines.append("")
        else:
            lines.append(f"- 🏭 热点行业：{self._no_data_text()}")
            lines.append(f"- 🏷️ 热点主题：{self._no_data_text()}")
            lines.append(f"- 🌍 热点地域：{self._no_data_text()}")
            lines.append("")

        lines.extend([
            "> 热点方向会随市场变化而轮动，以上仅为当前阶段识别结果。",
            "",
            "---",
            "",
        ])

    def _section_scoring_guide(self, lines: List[str]) -> None:
        """Section 4: 评分怎么看 — 双分解释 + 阈值表。"""
        lines.extend([
            "## 四、本期评分怎么看",
            "",
            "我们使用**两套评分**从不同角度看每只股票：",
            "",
            "### 入场信号分（Entry Score）—— 回答「现在能不能买」",
            "",
            "| 分数范围 | 判断 | 实际含义 |",
            "|---------|------|---------|",
        ])

        for score, label, meaning in _ENTRY_THRESHOLDS:
            lines.append(f"| {score}-100 | {label} | {meaning} |")

        lines.extend([
            "",
            "**入场分看什么**：技术趋势（权重25%）、动量强度（20%）、"
            "成交量配合（15%）、突破确认（25%）、波动风险（15%）。",
            "",
            "### 持有价值分（Holding Score）—— 回答「买完后值不值得拿」",
            "",
            "| 分数范围 | 判断 | 实际含义 |",
            "|---------|------|---------|",
        ])

        for score, label, meaning in _HOLDING_THRESHOLDS:
            lines.append(f"| {score}-100 | {label} | {meaning} |")

        lines.extend([
            "",
            "**持有分看什么**：企业质量（20%）、事件催化（20%）、"
            "流动性（15%）、宏观信用（15%）、盈利预期（15%）、LLM复核（15%）。",
            "",
            "> **组合使用**：入场分高+持有分高 = 比较理想的买入标的；"
            "入场分高+持有分低 = 可以做短线，但不适合重仓持有；"
            "入场分低+持有分高 = 好公司但还没到买点，先加入观察。",
            "",
            "---",
            "",
        ])

    def _section_top20_table(
        self,
        lines: List[str],
        stocks: List[dict],
        has_stocks: bool,
    ) -> None:
        """Section 5: Top20 简表 — 7列。"""
        lines.extend([
            "## 五、本期 Top20 简表",
            "",
            "按综合评分排序，从高到低：",
            "",
            "| 排名 | 股票 | 入场信号 | 持有价值 | 建议动作 | 一句话理由 | 主要风险 |",
            "|------|------|---------|---------|---------|-----------|---------|",
        ])

        if has_stocks:
            for i, stock in enumerate(stocks, 1):
                code = stock.get("code", "—")
                name = stock.get("name", "—")
                entry_score = stock.get("entry_score", 50.0)
                holding_score = stock.get("holding_score", 50.0)

                entry_label = self._entry_label(entry_score)
                holding_label = self._holding_label(holding_score)
                action = self._combined_action(entry_score, holding_score)
                reason = stock.get("one_liner", stock.get("brief_reason", stock.get("summary", "综合信号")))
                risk = stock.get("main_risk", stock.get("risk", stock.get("top_risk", "待确认")))

                lines.append(
                    f"| {i} | **{name}**<br>`{code}` | {entry_label} | "
                    f"{holding_label} | {action} | {reason} | {risk} |"
                )

            # Append legend
            lines.extend([
                "",
                "| 建议动作 | 含义 |",
                "|---------|------|",
                "| 🟢 建议入场 | 入场分 + 持有分均较高，可考虑建仓 |",
                "| 🟡 轻仓观察 | 有信号但不够强，小仓位试探 |",
                "| 🟠 观望等待 | 入场条件不成熟，等待更明确信号 |",
                "| 🔴 不建议 | 风险大于机会，回避为主 |",
                "| ⚪ 信息不足 | 数据不完整，无法给出明确建议 |",
            ])
        else:
            lines.append(
                "| — | — | ⚪ 无数据 | ⚪ 无数据 | ⚪ 无数据 | "
                "本期暂无符合条件的信号标的 | — |"
            )

        lines.extend([
            "",
            "---",
            "",
        ])

    def _section_stock_cards(
        self,
        lines: List[str],
        stocks: List[dict],
        has_stocks: bool,
    ) -> None:
        """Section 6: 精选个股卡片 — 每只独立区块。"""
        lines.extend([
            "## 六、精选个股卡片",
            "",
        ])

        if has_stocks:
            # Pick top stocks (up to 5, favoring those with richer data)
            card_candidates = [s for s in stocks if s.get("entry_score", 0) >= 50]
            card_stocks = card_candidates[:5] if card_candidates else stocks[:3]

            for stock in card_stocks:
                code = stock.get("code", "—")
                name = stock.get("name", "—")
                sector = stock.get("sector", stock.get("industry", "—"))
                entry_score = stock.get("entry_score", 50.0)
                holding_score = stock.get("holding_score", 50.0)

                buy_reason = stock.get("buy_reason") or stock.get(
                    "entry_reason", stock.get("positive_factor", "综合技术信号与热点方向判断")
                )
                hold_value = stock.get("hold_value") or stock.get(
                    "holding_reason", stock.get("enterprise_value", "企业基本面尚可")
                )
                main_risk = stock.get("main_risk") or stock.get(
                    "risk", stock.get("risk_factor", "市场系统性风险或个股不确定性")
                )
                watch_point = stock.get("watch_point") or stock.get(
                    "observation", "关注后续成交量变化和关键支撑位"
                )

                lines.extend([
                    f"### 📋 {name}（`{code}`）— {sector}",
                    "",
                    "| 维度 | 说明 |",
                    "|------|------|",
                    f"| **入场信号分** | {self._entry_label(entry_score)}（{entry_score:.1f}分） |",
                    f"| **持有价值分** | {self._holding_label(holding_score)}（{holding_score:.1f}分） |",
                    f"| **买点原因** | {buy_reason} |",
                    f"| **持有价值** | {hold_value} |",
                    f"| **主要风险** | 🔴 {main_risk} |",
                    f"| **观察点** | 👀 {watch_point} |",
                    "",
                ])
        else:
            lines.extend([
                "本期暂无符合条件的信号标的，无个股卡片可展示。",
                "",
            ])

        lines.extend([
            "---",
            "",
        ])

    def _section_category_advice(
        self,
        lines: List[str],
        stocks: List[dict],
        has_stocks: bool,
    ) -> None:
        """Section 7: 分类型建议 — 短线/中线/不追高三组。"""
        lines.extend([
            "## 七、分类型建议",
            "",
            "根据不同的操作风格，我们把本期标的分为三组：",
            "",
        ])

        short_term: List[dict] = []
        mid_term: List[dict] = []
        avoid: List[dict] = []

        if has_stocks:
            for stock in stocks:
                entry = stock.get("entry_score", 50.0)
                holding = stock.get("holding_score", 50.0)

                if entry >= 65:
                    short_term.append(stock)
                if holding >= 55:
                    mid_term.append(stock)
                if entry < 50 or holding < 50:
                    avoid.append(stock)

        # 短线组
        lines.append("### 短线机会（入场信号较强，适合短线交易）")
        lines.append("")
        if short_term:
            lines.append(
                "| 股票 | 入场分 | 理由 |"
            )
            lines.append(
                "|------|-------|------|"
            )
            for s in short_term[:5]:
                code = s.get("code", "—")
                name = s.get("name", "—")
                entry = s.get("entry_score", 0)
                reason = s.get("brief_reason") or s.get(
                    "one_liner", "技术面信号较强"
                )
                lines.append(
                    f"| **{name}**<br>`{code}` | "
                    f"{self._entry_label(entry)} {entry:.1f} | {reason} |"
                )
        else:
            lines.append("本期未识别到明确的短线机会标的。")
        lines.append("")

        # 中线组
        lines.append("### 中线关注（持有价值较高，适合波段/中线持有）")
        lines.append("")
        if mid_term:
            lines.append(
                "| 股票 | 持有分 | 理由 |"
            )
            lines.append(
                "|------|-------|------|"
            )
            for s in mid_term[:5]:
                code = s.get("code", "—")
                name = s.get("name", "—")
                holding = s.get("holding_score", 0)
                reason = s.get("hold_value") or s.get(
                    "enterprise_value", s.get("summary", "基本面有一定支撑")
                )
                lines.append(
                    f"| **{name}**<br>`{code}` | "
                    f"{self._holding_label(holding)} {holding:.1f} | {reason} |"
                )
        else:
            lines.append("本期未识别到明确的中线关注标的。")
        lines.append("")

        # 不追高组
        lines.append("### ⚠️ 不追高（入场风险较大，建议回避）")
        lines.append("")
        if avoid:
            lines.append(
                "| 股票 | 入场分 | 持有分 | 原因 |"
            )
            lines.append(
                "|------|-------|-------|------|"
            )
            for s in avoid[:5]:
                code = s.get("code", "—")
                name = s.get("name", "—")
                entry = s.get("entry_score", 0)
                holding = s.get("holding_score", 0)
                reason = s.get("main_risk") or s.get(
                    "risk_reason", "信号强度不足，建议等待"
                )
                lines.append(
                    f"| **{name}**<br>`{code}` | "
                    f"{entry:.1f} | {holding:.1f} | {reason} |"
                )
        else:
            lines.append("本期无不追高警示标的。")
        lines.append("")

        lines.extend([
            "---",
            "",
        ])

    def _section_risk_disclosure(
        self,
        lines: List[str],
        stocks: List[dict],
        has_stocks: bool,
    ) -> None:
        """Section 8: 5层风险提示。"""
        lines.extend([
            "## 八、风险提示",
            "",
            "投资前请充分了解以下5个层面的风险：",
            "",
        ])

        # Count risks from stocks if available
        macro_risk_count = sum(
            1 for s in stocks if self._has_data(s, "macro_risk")
        )
        industry_risk_count = sum(
            1 for s in stocks if self._has_data(s, "industry_risk")
        )
        stock_risk_texts = [
            s.get("main_risk", "").strip()
            for s in stocks
            if s.get("main_risk", "").strip()
        ]
        tech_risk_count = sum(
            1 for s in stocks
            if s.get("entry_score", 50) < 50
        )
        data_gap_count = sum(
            1 for s in stocks if self._has_data(s, "data_gap")
        )

        # Layer 1: 市场风险
        lines.extend([
            "### 1. 市场系统性风险",
            "",
            "- **市场波动**：股票市场受宏观经济、政策变化、地缘政治等影响，可能出现较大波动",
            "- **资金面**：市场资金流向变化可能影响个股流动性和价格",
            "- **情绪面**：市场情绪变化可能导致非理性波动",
        ])
        if macro_risk_count > 0:
            lines.append(
                f"- 本期有 {macro_risk_count} 只标的涉及宏观风险提示"
            )
        lines.append("")

        # Layer 2: 行业风险
        lines.extend([
            "### 2. 行业/板块风险",
            "",
            "- **行业周期**：不同行业处于不同景气周期，需关注行业拐点",
            "- **政策变化**：行业监管政策可能发生变化",
            "- **竞争格局**：行业竞争加剧可能影响企业盈利",
        ])
        if industry_risk_count > 0:
            lines.append(
                f"- 本期有 {industry_risk_count} 只标的涉及行业风险提示"
            )
        lines.append("")

        # Layer 3: 个股风险
        lines.extend([
            "### 3. 个股风险",
            "",
        ])
        if stock_risk_texts:
            unique_risks = list(dict.fromkeys(stock_risk_texts))
            for risk in unique_risks[:5]:
                lines.append(f"- 🔴 {risk}")
        else:
            lines.append("- 个股层面暂未识别到明确风险，但仍需关注基本面变化")
        lines.append("")

        # Layer 4: 技术风险
        lines.extend([
            "### 4. 技术面风险",
            "",
        ])
        if tech_risk_count > 0:
            lines.append(
                f"- 本期有 {tech_risk_count} 只标的技术入场信号偏弱 "
                f"（入场分 < 50），技术面未形成明确买入确认"
            )
        else:
            lines.append("- 技术面信号整体偏正面，但仍需注意假突破风险")
        lines.append(
            "- **假突破**：技术信号可能因市场环境变化而失效"
        )
        lines.append(
            "- **量价背离**：价格上涨但成交量萎缩，可能预示行情不可持续"
        )
        lines.append("")

        # Layer 5: 数据风险
        lines.extend([
            "### 5. 数据源与模型风险",
            "",
            "- **数据延迟**：行情数据存在一定延迟，实时性可能不足",
            "- **数据缺失**：部分股票可能缺少历史K线、财务数据或资金流向数据",
            "- **模型局限**：AI分析基于公开信息和历史数据，无法预测突发事件",
            "- **评分偏差**：自动化评分存在误判可能，请结合自身判断",
        ])
        if data_gap_count > 0:
            lines.append(
                f"- 本期有 {data_gap_count} 只标的存在重要数据缺失"
            )
        lines.append("")

        lines.extend([
            "---",
            "",
        ])

    def _section_data_sources(
        self,
        lines: List[str],
        data_sources: List[dict],
        date_display: str,
    ) -> None:
        """Section 9: 数据来源与新鲜度。"""
        lines.extend([
            "## 九、数据来源与新鲜度",
            "",
            "### 数据来源",
            "",
        ])

        if data_sources:
            lines.append(
                "| 数据维度 | 来源 | 状态 | 更新时间 |"
            )
            lines.append(
                "|---------|------|------|---------|"
            )
            for source in data_sources:
                dim = source.get("dimension", source.get("name", "—"))
                src = source.get("source", source.get("provider", "—"))
                status = source.get("status", "—")
                status_emoji = self._status_emoji(status)
                updated = source.get("updated_at", source.get("time", "—"))
                lines.append(
                    f"| {dim} | {src} | {status_emoji} {status} | {updated} |"
                )
        else:
            lines.extend([
                "- **技术信号**：Futu OpenD / YFinance / AKShare",
                "- **行情数据**：Futu OpenD > YFinance > AKShare（按优先级）",
                "- **公司新闻**：联网搜索（Tavily / ZhipuAI）",
                "- **AI 分析**：DeepSeek / OpenAI 兼容模型",
                "- **宏观数据**：YFinance / AKShare",
            ])

        lines.extend([
            "",
            "### 数据新鲜度",
            "",
            f"- **报告日期**：{date_display}",
            "- **K线数据**：最近一个交易日收盘数据（如有延迟会在备注中标注）",
            "- **公司新闻**：搜索当日最新公开信息",
            "- **AI分析**：基于上述数据实时生成",
            "- **评分时效**：评分结果仅在报告当日具备参考价值",
            "",
            "---",
            "",
            "*本报告由 MoneyManager 自动生成，仅供个人学习研究使用。*",
            "",
        ])

    # ═══════════════════════════════════════════════════════════════
    # 工具方法
    # ═══════════════════════════════════════════════════════════════

    @staticmethod
    def _entry_label(score: float) -> str:
        if score >= 80:
            return "🟢 强入场信号"
        if score >= 65:
            return "🟡 中等信号"
        if score >= 50:
            return "🟠 弱信号"
        return "🔴 不建议入场"

    @staticmethod
    def _holding_label(score: float) -> str:
        if score >= 80:
            return "🟢 强持有价值"
        if score >= 65:
            return "🟡 中等价值"
        if score >= 50:
            return "🟠 弱价值"
        return "🔴 不建议持有"

    @staticmethod
    def _combined_action(entry: float, holding: float) -> str:
        if entry <= 0 and holding <= 0:
            return "⚪ 信息不足"
        if entry >= 65 and holding >= 65:
            return "🟢 建议入场"
        if entry >= 65:
            return "🟡 轻仓观察"
        if entry >= 50:
            return "🟠 观望等待"
        if entry < 50:
            return "🔴 不建议"
        return "⚪ 信息不足"

    @staticmethod
    def _status_emoji(status: str) -> str:
        s = status.lower()
        if s in ("正常", "ok", "success", "可用", "up", "已连接"):
            return "🟢"
        if s in ("异常", "error", "fail", "不可用", "down", "断开"):
            return "🔴"
        if s in ("延迟", "stale", "过期", "部分可用"):
            return "🟡"
        return "⚪"

    @staticmethod
    def _no_data_text() -> str:
        return "暂未识别到明确方向（可能数据暂缺或当前市场无明显主线）"

    @staticmethod
    def _has_data(stock: dict, key: str) -> bool:
        value = stock.get(key)
        if value is None:
            return False
        text = str(value).strip()
        return bool(text) and text.lower() not in {"none", "null", "", "无", "暂无"}
