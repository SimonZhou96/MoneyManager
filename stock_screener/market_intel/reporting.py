#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Markdown report templates for market-intel assisted signal analysis."""

from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional


DISCLAIMER = "本报告仅用于辅助观察和复盘，不构成投资建议。"


def render_single_stock_report(pack: dict, result: dict, report_date: date | None = None) -> str:
    """Render one Chinese plain-language stock report with chart-ready tables."""
    pack = dict(pack or {})
    result = dict(result or {})
    report_date = report_date or date.today()
    code = _text(result.get("code") or pack.get("code") or "-")
    name = _text(result.get("name") or pack.get("name") or code)
    score = _final_score(result)
    confidence = _score(result.get("confidence_score"))
    conclusion = _conclusion(result)
    score_rows = _single_score_rows(score, confidence, result, pack)
    flow_rows = _fund_flow_rows(pack)

    lines = [
        f"# {name}（{code}）市场情报与AI复核报告",
        "",
        f"**报告日期：** {report_date.strftime('%Y年%m月%d日')}",
        "**报告口径：** 市场情报、AI复核结果和可用资金/事件线索的汇总，不生成图片，只提供可直接制图的数据表。",
        "",
        f"> {DISCLAIMER}",
        "",
        "## 1. 一句话结论",
        "",
        conclusion,
        "",
        "## 2. 核心评分",
        "",
        "| 指标 | 数值 | 普通话解释 |",
        "|---|---:|---|",
        f"| 最终统一评分 | {_format_score(score)} | {_score_label(score)} |",
        f"| 最终评分公式 | {_text(result.get('最终评分公式') or result.get('final_score_formula') or '-')} | 五类因子加权后的唯一主评分 |",
        f"| 模型置信度 | {_format_score(confidence)} | {_confidence_label(confidence)} |",
        f"| 方向判断 | {_direction_label(result.get('signal_bias'))} | 只表示复核方向，不等同于买卖指令 |",
        "",
        "### 饼图：综合评分来源占比",
        "",
        "| 来源 | 分值 | 占比 |",
        "|---|---:|---:|",
    ]
    lines.extend(_table_rows(score_rows, ["来源", "分值", "占比"]))
    lines.extend([
        "",
        "### 柱状图：近 N 日资金流入/流出",
        "",
        "| 日期 | 资金流入 | 资金流出 | 净流入 |",
        "|---|---:|---:|---:|",
    ])
    lines.extend(_table_rows(flow_rows, ["日期", "资金流入", "资金流出", "净流入"]))
    lines.extend([
        "",
        "## 3. 支撑因素",
        "",
        _bullet_list(result.get("positive_factors"), fallback="暂未看到足够明确的利好因素。"),
        "",
        "## 4. 风险因素",
        "",
        _bullet_list(result.get("risk_factors"), fallback="暂未看到足够明确的风险因素，但仍需控制仓位和回撤。"),
        "",
        "## 5. 市场情报摘要",
        "",
        _items_table(_pack_items(pack), empty_text="暂无可用市场情报明细。"),
        "",
        "## 6. 数据缺失与来源说明",
        "",
        _data_gap_text([pack], [result]),
        "",
        f"> {DISCLAIMER}",
        "",
    ])
    return "\n".join(lines)


def render_multi_stock_report(
    packs: list[dict],
    results: list[dict],
    report_date: date | None = None,
) -> str:
    """Render a Chinese multi-stock market-intel report with chart-ready tables."""
    packs = [dict(item or {}) for item in (packs or [])]
    results = [dict(item or {}) for item in (results or [])]
    report_date = report_date or date.today()
    pack_by_code = {_text(pack.get("code")): pack for pack in packs if _text(pack.get("code"))}
    ranked = sorted(results, key=lambda item: _final_score(item) or -1, reverse=True)
    rating_rows = _rating_distribution_rows(ranked)
    top_rows = _top_score_rows(ranked[:10])
    focus = [item for item in ranked if (_final_score(item) or 0) >= 70][:10]
    watch = [item for item in ranked if 50 <= (_final_score(item) or 0) < 70][:10]
    risk = [item for item in ranked if (_final_score(item) or 0) < 50][:10]

    lines = [
        "# 多股票市场情报与AI复核报告",
        "",
        f"**报告日期：** {report_date.strftime('%Y年%m月%d日')}",
        f"**覆盖股票数：** {len(results)}",
        "**报告口径：** 市场情报证据包与AI复核结果汇总，只输出可制图的Markdown数据表，不生成图片。",
        "",
        f"> {DISCLAIMER}",
        "",
        "## 1. 总体结论",
        "",
        _multi_conclusion(ranked),
        "",
        "## 2. 核心评分分布",
        "",
        "### 饼图：股票评级分布",
        "",
        "| 评级 | 股票数量 | 占比 |",
        "|---|---:|---:|",
    ]
    lines.extend(_table_rows(rating_rows, ["评级", "股票数量", "占比"]))
    lines.extend([
        "",
        "## 3. 最终统一评分排名",
        "",
        "### 柱状图：最终统一评分 Top 10",
        "",
        "| 排名 | 股票代码 | 股票名称 | 最终统一评分 | 命中规则 |",
        "|---:|---|---|---:|---|",
    ])
    lines.extend(_table_rows(top_rows, ["排名", "股票代码", "股票名称", "最终统一评分", "命中规则"]))
    lines.extend([
        "",
        "## 4. 方向判断分布",
        "",
        _direction_distribution_table(ranked),
        "",
        "## 5. 主要支撑因素",
        "",
        _factor_table(ranked, "positive_factors", "暂未看到足够明确的支撑因素。"),
        "",
        "## 6. 重点关注股票",
        "",
        _result_table(focus, fallback="暂无综合评分达到重点关注阈值的股票。"),
        "",
        "## 7. 观察池股票",
        "",
        _result_table(watch, fallback="暂无处于观察区间的股票。"),
        "",
        "## 8. 风险提示股票",
        "",
        _result_table(risk, fallback="暂无综合评分低于风险阈值的股票。"),
        "",
        "## 9. 证据来源概览",
        "",
        _source_overview_table(ranked, pack_by_code),
        "",
        "## 10. 数据缺失与来源说明",
        "",
        _data_gap_text(packs, results),
        "",
        f"> {DISCLAIMER}",
        "",
    ])
    return "\n".join(lines)


def _single_score_rows(score: Optional[float], confidence: Optional[float], result: dict, pack: dict) -> List[dict]:
    unified_parts = [
        ("技术规则分", "技术规则分", "30%"),
        ("宏观五模块分", "宏观五模块分", "30%"),
        ("事件热点分", "事件热点分", "20%"),
        ("资金风险分", "资金风险分", "10%"),
        ("LLM复核分", "LLM复核分", "10%"),
    ]
    if any(_score(result.get(key)) is not None for key, _, _ in unified_parts):
        return [
            {"来源": label, "分值": _format_score(_score(result.get(key))), "占比": weight}
            for key, label, weight in unified_parts
        ]
    evidence_count = len(_pack_items(pack))
    evidence_score = min(100.0, 50.0 + evidence_count * 10.0) if evidence_count else 30.0
    risk_penalty = min(30.0, len(_list(result.get("risk_factors"))) * 8.0 + len(_list(pack.get("data_gaps"))) * 5.0)
    rows = [
        {"来源": "最终统一评分", "分值": _format_score(score), "占比": "100%"},
        {"来源": "模型置信度", "分值": _format_score(confidence), "占比": "25%"},
        {"来源": "情报完整度", "分值": _format_score(evidence_score), "占比": "15%"},
        {"来源": "风险扣分", "分值": _format_score(max(0.0, 100.0 - risk_penalty)), "占比": "10%"},
    ]
    return rows


def _fund_flow_rows(pack: dict) -> List[dict]:
    rows = pack.get("fund_flow_rows") or pack.get("fund_flow") or []
    normalized = []
    if isinstance(rows, list):
        for item in rows[:20]:
            if not isinstance(item, dict):
                continue
            normalized.append({
                "日期": _text(item.get("date") or item.get("日期") or "未知日期"),
                "资金流入": _money(item.get("inflow") or item.get("资金流入")),
                "资金流出": _money(item.get("outflow") or item.get("资金流出")),
                "净流入": _money(item.get("net_inflow") or item.get("净流入")),
            })
    if normalized:
        return normalized
    return [{"日期": "近 N 日", "资金流入": "数据缺失", "资金流出": "数据缺失", "净流入": "数据缺失"}]


def _rating_distribution_rows(results: List[dict]) -> List[dict]:
    buckets = [
        ("重点关注", lambda score: score >= 70),
        ("观察", lambda score: 50 <= score < 70),
        ("谨慎", lambda score: 0 <= score < 50),
        ("数据不足", lambda score: score < 0),
    ]
    total = max(1, len(results))
    rows = []
    for label, matcher in buckets:
        count = sum(1 for item in results if matcher(_final_score(item) if _final_score(item) is not None else -1))
        rows.append({"评级": label, "股票数量": str(count), "占比": _percent(count, total)})
    return rows


def _top_score_rows(results: List[dict]) -> List[dict]:
    if not results:
        return [{"排名": "-", "股票代码": "-", "股票名称": "-", "综合评分": "数据缺失"}]
    return [
        {
            "排名": str(index),
            "股票代码": _text(item.get("code") or "-"),
            "股票名称": _text(item.get("name") or item.get("code") or "-"),
            "最终统一评分": _format_score(_final_score(item)),
            "命中规则": _conditions_text(item),
        }
        for index, item in enumerate(results, start=1)
    ]


def _direction_distribution_table(results: List[dict]) -> str:
    labels = {}
    for item in results:
        label = _direction_label(item.get("signal_bias"))
        labels[label] = labels.get(label, 0) + 1
    if not labels:
        labels = {"数据不足": 0}
    rows = [{"方向": key, "股票数量": str(value)} for key, value in labels.items()]
    return "\n".join(["| 方向 | 股票数量 |", "|---|---:|", *_table_rows(rows, ["方向", "股票数量"])])


def _factor_table(results: List[dict], field: str, fallback: str) -> str:
    rows = []
    for item in results[:10]:
        factors = "；".join(_list(item.get(field))[:3]) or fallback
        rows.append({
            "股票代码": _text(item.get("code") or "-"),
            "股票名称": _text(item.get("name") or item.get("code") or "-"),
            "主要因素": factors,
        })
    if not rows:
        rows = [{"股票代码": "-", "股票名称": "-", "主要因素": fallback}]
    return "\n".join(["| 股票代码 | 股票名称 | 主要因素 |", "|---|---|---|", *_table_rows(rows, ["股票代码", "股票名称", "主要因素"])])


def _result_table(results: List[dict], fallback: str) -> str:
    if not results:
        return fallback
    rows = []
    for item in results:
        rows.append({
            "股票代码": _text(item.get("code") or "-"),
            "股票名称": _text(item.get("name") or item.get("code") or "-"),
            "最终统一评分": _format_score(_final_score(item)),
            "方向": _direction_label(item.get("signal_bias")),
            "命中规则": _conditions_text(item),
            "简明结论": _conclusion(item),
        })
    return "\n".join([
        "| 股票代码 | 股票名称 | 最终统一评分 | 方向 | 命中规则 | 简明结论 |",
        "|---|---|---:|---|---|---|",
        *_table_rows(rows, ["股票代码", "股票名称", "最终统一评分", "方向", "命中规则", "简明结论"]),
    ])


def _source_overview_table(results: List[dict], pack_by_code: Dict[str, dict]) -> str:
    rows = []
    for item in results[:20]:
        code = _text(item.get("code"))
        pack = pack_by_code.get(code, {})
        citations = pack.get("citations") if isinstance(pack.get("citations"), list) else []
        source_urls = _list(item.get("source_urls"))
        rows.append({
            "股票代码": code or "-",
            "股票名称": _text(item.get("name") or code or "-"),
            "情报条数": str(len(_pack_items(pack))),
            "引用来源数": str(len(citations) + len(source_urls)),
        })
    if not rows:
        rows = [{"股票代码": "-", "股票名称": "-", "情报条数": "0", "引用来源数": "0"}]
    return "\n".join(["| 股票代码 | 股票名称 | 情报条数 | 引用来源数 |", "|---|---|---:|---:|", *_table_rows(rows, ["股票代码", "股票名称", "情报条数", "引用来源数"])])


def _items_table(items: List[dict], empty_text: str) -> str:
    if not items:
        return empty_text
    rows = []
    for item in items[:10]:
        rows.append({
            "标题": _text(item.get("title") or item.get("label") or "-"),
            "摘要": _text(item.get("summary") or item.get("content") or "-"),
            "来源": _text(item.get("url") or item.get("source") or "-"),
        })
    return "\n".join(["| 标题 | 摘要 | 来源 |", "|---|---|---|", *_table_rows(rows, ["标题", "摘要", "来源"])])


def _data_gap_text(packs: List[dict], results: List[dict]) -> str:
    gaps = []
    for pack in packs:
        gaps.extend(_list(pack.get("data_gaps")))
    for result in results:
        gaps.extend(_list(result.get("data_gaps")))
    if not gaps:
        return "当前证据包没有记录明确的数据缺失项。仍需注意，报告依赖已接入的数据源和模型输出，不能替代人工复核。"
    return _bullet_list(gaps, fallback="")


def _multi_conclusion(results: List[dict]) -> str:
    if not results:
        return "本次没有可复核的股票结果。"
    strong = sum(1 for item in results if (_final_score(item) or 0) >= 70)
    weak = sum(1 for item in results if (_final_score(item) or 0) < 50)
    return f"本次共复核 {len(results)} 只股票，其中 {strong} 只进入重点关注区，{weak} 只需要谨慎处理。建议先看评分和数据缺失，再结合自身交易计划复盘。"


def _pack_items(pack: dict) -> List[dict]:
    items = []
    for key in ("structured_items", "search_documents", "manual_items"):
        value = pack.get(key)
        if isinstance(value, list):
            items.extend([dict(item) for item in value if isinstance(item, dict)])
    for container_key in ("stock_context", "market_context"):
        container = pack.get(container_key)
        if isinstance(container, dict) and isinstance(container.get("items"), list):
            items.extend([dict(item) for item in container["items"] if isinstance(item, dict)])
    return items


def _table_rows(rows: List[dict], columns: List[str]) -> List[str]:
    return ["| " + " | ".join(_table_text(row.get(column)) for column in columns) + " |" for row in rows]


def _conditions_text(item: dict) -> str:
    text = _text(item.get("conditions_met") or item.get("满足的条件"))
    if not text:
        return "-"
    parts = [part.strip() for part in text.replace("；", "|").split("|") if part.strip()]
    return "\n".join(parts) if parts else text


def _bullet_list(value: Any, fallback: str) -> str:
    items = _list(value)
    if not items:
        return fallback
    return "\n".join(f"- {_text(item)}" for item in items)


def _conclusion(result: dict) -> str:
    summary = _text(result.get("summary"))
    if summary:
        return summary
    score = _final_score(result)
    if score is None:
        return "当前信息不足，适合先补齐数据后再复核。"
    if score >= 70:
        return "综合评分较高，可以放入重点观察名单，但仍需结合风险因素复盘。"
    if score >= 50:
        return "综合评分处于中间区间，建议继续观察，不宜只凭单一信号判断。"
    return "综合评分偏弱，当前更适合谨慎处理。"


def _score_label(score: Optional[float]) -> str:
    if score is None:
        return "缺少评分，不能稳定判断。"
    if score >= 80:
        return "信号较强，但仍需看风险和资金连续性。"
    if score >= 60:
        return "具备观察价值，需要等待更多确认。"
    if score >= 40:
        return "信号偏弱或信息混杂。"
    return "风险较高或证据不足。"


def _confidence_label(score: Optional[float]) -> str:
    if score is None:
        return "模型没有给出置信度。"
    if score >= 70:
        return "模型对本次判断相对有把握。"
    if score >= 50:
        return "模型有一定把握，但信息仍不充分。"
    return "模型把握较低，需要人工复核。"


def _direction_label(value: Any) -> str:
    mapping = {
        "bullish": "偏看涨",
        "bearish": "偏看跌",
        "neutral": "中性",
        "avoid": "回避",
        "unknown": "信息不足",
    }
    text = _text(value).lower()
    return mapping.get(text, _text(value) or "信息不足")


def _score(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _final_score(item: dict) -> Optional[float]:
    return (
        _score(item.get("最终统一评分"))
        or _score(item.get("final_unified_score"))
        or _score(item.get("final_score"))
        or _score(item.get("reliability_score"))
    )


def _format_score(value: Optional[float]) -> str:
    if value is None:
        return "数据缺失"
    return f"{value:.2f}"


def _percent(count: int, total: int) -> str:
    if total <= 0:
        return "0.0%"
    return f"{count / total * 100:.1f}%"


def _money(value: Any) -> str:
    text = _text(value)
    return text or "数据缺失"


def _list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [_text(item) for item in value if _text(item)]
    text = _text(value)
    if not text:
        return []
    return [text]


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _table_text(value: Any) -> str:
    text = _text(value) or "-"
    return text.replace("|", "\\|").replace("\n", "<br>").replace("\r", "")
