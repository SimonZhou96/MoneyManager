#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Shared evidence expansion and citation helpers for signal analysis."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from .models import ScreeningSignalRow, SearchDocument, SignalAnalysisResult


SOURCE_LABEL_FACTORS = {
    "HKEX公告",
    "SEC",
    "巨潮资讯",
    "上交所公告",
    "深交所公告",
    "Reuters",
    "CNBC",
    "Yahoo Finance",
    "arXiv",
    "小米IR",
    "小米年报",
    "小米公告",
    "公司IR",
}


def expand_company_documents(
    search_provider,
    market: str,
    row: ScreeningSignalRow,
    max_results: int,
) -> List[SearchDocument]:
    """Search additional market-specific authoritative sources for one stock."""
    if not getattr(search_provider, "is_available", False):
        return []

    documents: List[SearchDocument] = []
    per_query_limit = max(1, min(int(max_results or 2), 3))
    for query in build_company_source_queries(market, row):
        documents.extend(search_provider.search(query, max_results=per_query_limit) or [])
    return dedupe_documents(documents)


def build_company_source_queries(market: str, row: ScreeningSignalRow) -> List[str]:
    subject = " ".join(subject_terms(market, row))
    market_key = str(market or "").upper()
    queries = [
        f"{subject} official investor relations annual report earnings announcement company strategy",
        f"{subject} latest company news earnings guidance policy catalyst risk",
    ]
    subject_lower = subject.lower()
    if any(token in subject_lower for token in ("xiaomi", "小米", "01810", "1810.hk")):
        queries.append(f"{subject} annual results announcement AI robotics MiMo CyberDog CyberOne Xiaomi-Robotics-0 VLA")
    if market_key == "HK":
        digits = "".join(ch for ch in row.code if ch.isdigit()).lstrip("0") or row.code
        queries.append(f"site:hkexnews.hk/listedco/listconews/sehk {digits} {row.name} annual results announcement")
        queries.append(f"{subject} HKEX announcement annual report results")
    elif market_key == "US":
        ticker = normalized_ticker(row.code)
        queries.append(f"site:sec.gov {ticker} {row.name} 10-K 10-Q 8-K annual report")
        queries.append(f"{subject} investor relations annual report 10-K quarterly results")
    elif market_key == "A":
        ticker = normalized_ticker(row.code)
        queries.append(f"site:cninfo.com.cn {ticker} {row.name} 年报 业绩 公告")
        queries.append(f"site:sse.com.cn {ticker} {row.name} 年报 业绩 公告")
        queries.append(f"site:szse.cn {ticker} {row.name} 年报 业绩 公告")
    return dedupe(queries)


def subject_terms(market: str, row: ScreeningSignalRow) -> List[str]:
    terms: List[str] = []
    for value in (row.code, normalized_ticker(row.code), row.name):
        text = str(value or "").strip()
        if text and text not in terms:
            terms.append(text)

    market_key = str(market or "").upper()
    ticker = normalized_ticker(row.code)
    digits = "".join(ch for ch in ticker if ch.isdigit())
    if market_key == "HK" and digits:
        padded = digits.zfill(5)
        no_zero = padded.lstrip("0") or padded
        for value in (f"{padded}.HK", f"{no_zero}.HK", no_zero):
            if value not in terms:
                terms.append(value)
    elif market_key == "A" and digits:
        suffix = ""
        code = str(row.code or "").upper()
        if code.startswith("SH."):
            suffix = "SH"
        elif code.startswith("SZ."):
            suffix = "SZ"
        for value in ([f"{digits}.{suffix}"] if suffix else []):
            if value not in terms:
                terms.append(value)
    elif market_key == "US" and ticker:
        value = f"{ticker} stock"
        if value not in terms:
            terms.append(value)
    return terms or [row.code]


def build_evidence_links(source_documents: List[SearchDocument], source_urls: List[str]) -> List[dict]:
    links: List[dict] = []
    for doc in source_documents:
        if not (doc.url or "").strip():
            continue
        links.append(evidence_link_from_document(doc))
    for url in source_urls or []:
        links.append(evidence_link_from_url(str(url)))
    return dedupe_links(links)


def build_factor_citations(
    factors: List[str],
    source_documents: List[SearchDocument],
    evidence_links: List[dict],
) -> Dict[str, List[dict]]:
    documents = [doc for doc in source_documents if (doc.url or "").strip()]
    links_by_url = {item["url"]: item for item in evidence_links if item.get("url")}
    citations: Dict[str, List[dict]] = {}
    for factor in factors:
        factor_text = str(factor or "").strip()
        if not factor_text:
            continue
        terms = factor_terms(factor_text)
        matched: List[dict] = []
        for doc in documents:
            haystack = f"{doc.title} {doc.content} {doc.url}".lower()
            if terms and any(term in haystack for term in terms):
                link = links_by_url.get(doc.url) or evidence_link_from_document(doc)
                matched.append(link)
        if matched:
            citations[factor_text] = dedupe_links_by_label(dedupe_links(sort_evidence_links(matched)))[:3]
    return citations


def build_data_gaps(
    row: Optional[ScreeningSignalRow],
    result: SignalAnalysisResult,
    source_documents: List[SearchDocument],
    factor_citations: Dict[str, List[dict]],
    main_force_context_label: str = "股票筛选",
) -> List[str]:
    gaps: List[str] = []
    if not source_documents:
        gaps.append("公司新闻及事件信息不足")
    elif generic_only_sources(source_documents, result.source_urls or result.news_sources):
        gaps.append("本次检索主要命中行情/公司概览页，缺少公告、年报或公司新闻级来源")
    if not row or not has_main_force_context(row):
        gaps.append("主力资金/盘口数据缺失或不足")
    if str(result.news_impact or "") in {"信息不足", "无明显新闻"} or any("公司新闻" in item for item in result.risk_factors):
        gaps.append("公司新闻及事件信息不足")
    uncited = [
        item for item in [*result.positive_factors, *result.risk_factors, *result.macro_factors]
        if str(item or "").strip() and str(item or "").strip() not in factor_citations
    ]
    if uncited:
        gaps.append("部分宏观因素缺少可追溯来源")
    return dedupe(gaps)


def apply_evidence_to_result(
    *,
    result: SignalAnalysisResult,
    row: Optional[ScreeningSignalRow],
    source_documents: List[SearchDocument],
    main_force_context_label: str = "股票筛选",
) -> None:
    clean_result_factors(result)
    evidence_links = build_evidence_links(source_documents, result.source_urls or result.news_sources)
    result.evidence_links = evidence_links
    if not result.source_urls:
        result.source_urls = [item["url"] for item in evidence_links if item.get("url")]
    result.factor_citations = build_factor_citations(
        [*result.positive_factors, *result.risk_factors, *result.macro_factors],
        source_documents,
        evidence_links,
    )
    result.data_gaps = build_data_gaps(
        row=row,
        result=result,
        source_documents=source_documents,
        factor_citations=result.factor_citations,
        main_force_context_label=main_force_context_label,
    )


def clean_result_factors(result: SignalAnalysisResult) -> None:
    result.positive_factors = clean_factor_list(result.positive_factors)
    result.risk_factors = clean_factor_list(result.risk_factors)
    result.macro_factors = clean_factor_list(result.macro_factors)


def clean_factor_list(factors: List[str]) -> List[str]:
    rows: List[str] = []
    seen = set()
    for item in factors or []:
        text = str(item or "").strip()
        if not text or is_source_label_factor(text) or text in seen:
            continue
        seen.add(text)
        rows.append(text)
    return rows


def is_source_label_factor(value: str) -> bool:
    text = str(value or "").strip()
    return text in SOURCE_LABEL_FACTORS


def evidence_link_from_document(doc: SearchDocument) -> dict:
    url = (doc.url or "").strip()
    title = (doc.title or "").strip()
    return {
        "label": source_label(url=url, title=title),
        "url": url,
        "title": title or url,
        "domain": domain(url),
        "source_type": source_type(url=url, title=title),
    }


def evidence_link_from_url(url: str) -> dict:
    clean_url = str(url or "").strip()
    return {
        "label": source_label(url=clean_url, title=""),
        "url": clean_url,
        "title": clean_url,
        "domain": domain(clean_url),
        "source_type": source_type(url=clean_url, title=""),
    }


def source_label(url: str, title: str) -> str:
    host = domain(url)
    text = f"{url} {title}".lower()
    if "ir.mi.com" in host or "xiaomi.gcs-web.com" in host:
        if "annual" in text or "report" in text or "业绩" in text:
            return "小米年报"
        return "小米IR"
    if "hkexnews.hk" in host:
        return "HKEX公告"
    if "sec.gov" in host:
        return "SEC"
    if "cninfo.com.cn" in host:
        return "巨潮资讯"
    if "sse.com.cn" in host:
        return "上交所公告"
    if "szse.cn" in host:
        return "深交所公告"
    if "arxiv.org" in host:
        return "arXiv"
    if "reuters.com" in host:
        return "Reuters"
    if "cnbc.com" in host:
        return "CNBC"
    if "finance.yahoo.com" in host:
        return "Yahoo Finance"
    if host:
        return host.replace("www.", "").split(".")[0][:12]
    return (title or "来源")[:12]


def source_type(url: str, title: str) -> str:
    host = domain(url)
    text = f"{url} {title}".lower()
    if any(value in host for value in ("ir.", "investor.", "investors.", "gcs-web.com")):
        return "官方"
    if any(value in host for value in ("hkexnews.hk", "sec.gov", "cninfo.com.cn", "sse.com.cn", "szse.cn")):
        return "公告"
    if "arxiv.org" in host or "paper" in text or "technical report" in text:
        return "论文"
    if is_generic_market_source(url):
        return "行情"
    return "新闻"


def domain(url: str) -> str:
    try:
        return urlparse(str(url or "")).netloc.lower()
    except Exception:
        return ""


def factor_terms(factor: str) -> List[str]:
    text = str(factor or "").lower()
    terms: List[str] = []
    special_terms = (
        "ai", "人工智能", "机器人", "robot", "robotics", "mimo", "vla",
        "cyberdog", "cyberone", "xiaomi-robotics", "大模型", "policy",
        "政策", "sec", "10-k", "10-q", "8-k", "年报", "公告", "业绩",
    )
    for term in special_terms:
        if term in text or (term in {"robot", "robotics", "vla", "cyberdog", "cyberone", "xiaomi-robotics"} and "机器人" in text):
            terms.append(term)
    if "港股" in text or "ipo" in text:
        terms.extend(["港股", "ipo", "hkex", "listing", "上市"])
    if "公司新闻" in text or "事件" in text:
        terms.extend(["news", "event", "announcement", "公告"])
    words = [part for part in re.split(r"[^a-z0-9\u4e00-\u9fff-]+", text) if len(part) >= 3]
    terms.extend(words[:8])
    return dedupe(terms)


def has_main_force_context(row: ScreeningSignalRow) -> bool:
    values = [
        row.main_force_risk_level,
        row.main_force_risk_score,
        row.main_force_risk_signals,
        row.main_force_risk_summary,
        row.main_force_market_data_observation,
        row.main_force_fund_flow_data,
        row.main_force_order_book_data,
        row.main_force_lhb_data,
        row.main_force_chip_data,
    ]
    normalized = [str(value or "").strip() for value in values]
    return any(value and value not in {"数据不足", "暂无", "无"} for value in normalized)


def generic_only_sources(source_documents: List[SearchDocument], source_urls: List[str]) -> bool:
    urls = [(doc.url or "").strip() for doc in source_documents if (doc.url or "").strip()]
    urls.extend(str(url or "").strip() for url in source_urls or [] if str(url or "").strip())
    if not urls:
        return False
    return all(is_generic_market_source(url) for url in urls)


def is_generic_market_source(url: str) -> bool:
    text = str(url or "").lower()
    return any(
        pattern in text
        for pattern in (
            "reuters.com/markets/companies",
            "cnbc.com/quotes",
            "finance.yahoo.com/quote",
            "google.com/finance",
            "marketwatch.com/investing",
        )
    )


def dedupe_documents(documents: List[SearchDocument]) -> List[SearchDocument]:
    rows: List[SearchDocument] = []
    seen = set()
    for doc in documents:
        key = (doc.url or doc.title or doc.content or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        rows.append(doc)
    return rows


def sort_evidence_links(links: List[dict]) -> List[dict]:
    priority = {"官方": 0, "公告": 1, "论文": 2, "新闻": 3, "行情": 4}
    return sorted(links, key=lambda item: (priority.get(str(item.get("source_type") or ""), 9), str(item.get("label") or "")))


def dedupe_links(links: List[dict]) -> List[dict]:
    rows: List[dict] = []
    seen = set()
    for link in links:
        url = str(link.get("url") or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        rows.append(link)
    return sort_evidence_links(rows)


def dedupe_links_by_label(links: List[dict]) -> List[dict]:
    rows: List[dict] = []
    seen = set()
    for link in links:
        key = (
            str(link.get("label") or "").strip(),
            str(link.get("source_type") or "").strip(),
        )
        if key in seen:
            continue
        seen.add(key)
        rows.append(link)
    return rows


def list_of_dicts(value: Any) -> List[dict]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


def dict_of_link_lists(value: Any) -> Dict[str, List[dict]]:
    if not isinstance(value, dict):
        return {}
    result: Dict[str, List[dict]] = {}
    for key, links in value.items():
        result[str(key)] = list_of_dicts(links)
    return result


def normalized_ticker(code: str) -> str:
    value = (code or "").strip()
    if "." in value:
        prefix, suffix = value.split(".", 1)
        if prefix.upper() in {"HK", "US", "SH", "SZ", "BJ"}:
            return suffix
        if suffix.upper() in {"HK", "US", "SH", "SZ", "SS", "BJ"}:
            return prefix
    return value


def dedupe(items: List[str]) -> List[str]:
    result = []
    seen = set()
    for item in items:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result
