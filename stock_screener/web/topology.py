from __future__ import annotations

import re
import threading
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel

from industry_topology.resolver import format_market_cap
from industry_topology.symbols import bare_code, parse_topology_symbol, symbol_id
from industry_topology.service import TopologyService
from stock_terminal.providers.factory import build_stock_terminal_providers
from stock_terminal.repository import MySqlStockTerminalRepository
from stock_terminal.service import StockTerminalService
from stock_name_resolver import StockNameResolver

from .auth import get_db
from .config import mysql_config_from_env
from .topology_tasks import TopologyTaskRegistry, TopologyTaskRunner, task_to_payload
from db import MarketDatabase

router = APIRouter(prefix="/api/topology", tags=["topology"])
_TOPOLOGY_GENERATION_IN_FLIGHT: set[tuple[str, str]] = set()
_TOPOLOGY_GENERATION_LOCK = threading.Lock()
_TOPOLOGY_BATCH_SIZE = 5
_TOPOLOGY_TASKS = TopologyTaskRegistry()
_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_KNOWN_CHINESE_NAMES = {
    "US.TSM": "台积电",
    "US.GLW": "康宁",
    "US.SONY": "索尼",
    "US.QCOM": "高通",
    "US.MTK": "联发科",
    "SZ.300207": "欣旺达",
    "SZ.002600": "领益智造",
    "SH.688981": "中芯国际",
    "SZ.300567": "精测电子",
    "SZ.002384": "东山精密",
    "SZ.000050": "深天马A",
    "SZ.002273": "水晶光电",
    "SZ.002475": "立讯精密",
    "SZ.300115": "长盈精密",
    "SZ.002456": "欧菲光",
    "SZ.002008": "大族激光",
    "SH.600745": "闻泰科技",
    "SH.688018": "乐鑫科技",
    "SH.603501": "韦尔股份",
    "SH.603290": "斯达半导",
    "SH.603160": "汇顶科技",
    "SZ.002938": "鹏鼎控股",
    "SH.603296": "华勤技术",
    "SZ.002036": "联创电子",
    "SH.688005": "容百科技",
    "SH.688607": "康众医疗",
    "SZ.300124": "汇川技术",
    "SZ.002993": "奥海科技",
    "SZ.300136": "信维通信",
    "SH.688099": "晶晨股份",
    "SZ.300458": "全志科技",
    "SZ.002241": "歌尔股份",
    "SZ.000725": "京东方A",
    "SH.688012": "中微公司",
    "SZ.300408": "三环集团",
    "SH.600584": "长电科技",
    "SH.688001": "华兴源创",
    "SZ.002156": "通富微电",
    "SZ.000049": "德赛电池",
    "SZ.000100": "TCL科技",
    "HK.01415": "高伟电子",
    "HK.00285": "比亚迪电子",
    "HK.02382": "舜宇光学科技",
    "HK.00732": "信利国际",
    "HK.01882": "海天国际",
    "HK.01810": "小米集团-W",
    "HK.01478": "丘钛科技",
    "HK.02018": "瑞声科技",
    "JP.6762": "TDK",
    "JP.6758": "索尼集团",
    "JP.6981": "村田制作所",
    "KR.005930": "三星电子",
    "TW.3008": "大立光",
    "TW.2454": "联发科",
}


def get_topology_service(db=Depends(get_db)) -> TopologyService:
    return TopologyService(db)


class GraphRequest(BaseModel):
    code: str
    market: str
    depth: int = 3
    quote_mode: str = "llm_initial"
    center_name: str = ""


class ExistingNode(BaseModel):
    code: str
    market: str


class ExpandRequest(BaseModel):
    code: str
    market: str
    depth: int = 2
    existing_codes: List[str] = []
    existing_nodes: List[ExistingNode] = []
    quote_mode: str = "auto"


class RefreshRequest(BaseModel):
    code: str
    market: str


class QuoteRefreshRequest(BaseModel):
    symbols: List[str]
    force: bool = False


class SearchEnrichRequest(BaseModel):
    center_market: str
    center_code: str
    center_name: str = ""
    center_sector: str = ""
    center_industry: str = ""
    symbols: List[str]


def get_stock_terminal_service(db=Depends(get_db)) -> StockTerminalService:
    repository = MySqlStockTerminalRepository(db)
    return StockTerminalService(repository, build_stock_terminal_providers(db=db))


def _graph_quote_mode(_: str) -> str:
    allowed = {"llm_initial", "auto", "sync"}
    mode = str(_ or "").strip().lower()
    return mode if mode in allowed else "llm_initial"


@router.get("/search")
def search(q: str = Query(..., min_length=1), limit: int = Query(10, le=50),
           svc: TopologyService = Depends(get_topology_service)):
    return {"ok": True, "data": svc.search(q, limit)}


@router.post("/graph")
def graph(req: GraphRequest, background_tasks: BackgroundTasks, svc: TopologyService = Depends(get_topology_service)):
    data = svc.build_graph(req.code, req.market, req.depth, quote_mode=_graph_quote_mode(req.quote_mode), center_name=req.center_name)
    if data.get("stats", {}).get("relation_status") == "generating":
        data["stats"]["background_started"] = _schedule_relation_generation(background_tasks, data["stats"])
    return {"ok": True, "data": data}


@router.post("/graph/tasks")
def create_graph_task(req: GraphRequest, background_tasks: BackgroundTasks):
    task = _TOPOLOGY_TASKS.create_task(
        code=req.code,
        market=req.market,
        depth=req.depth,
        center_name=req.center_name,
        quote_mode=_graph_quote_mode(req.quote_mode),
    )

    def service_factory() -> TopologyService:
        db = MarketDatabase(mysql_config_from_env())
        return TopologyService(db)

    background_tasks.add_task(TopologyTaskRunner(_TOPOLOGY_TASKS, service_factory).run, task.task_id)
    return {"ok": True, "data": task_to_payload(task)}


@router.get("/graph/tasks/{task_id}")
def get_graph_task(task_id: str):
    data = _TOPOLOGY_TASKS.get_snapshot(task_id)
    if data is None:
        raise HTTPException(status_code=404, detail={"ok": False, "error": "task_not_found"})
    return {"ok": True, "data": data}


@router.delete("/graph/tasks/{task_id}")
def cancel_graph_task(task_id: str):
    cancelled = _TOPOLOGY_TASKS.cancel(task_id)
    if not cancelled:
        raise HTTPException(status_code=404, detail={"ok": False, "error": "task_not_found"})
    data = _TOPOLOGY_TASKS.get_snapshot(task_id)
    return {"ok": True, "data": data}


@router.post("/graph/search-enrich")
def search_enrich(req: SearchEnrichRequest, svc: TopologyService = Depends(get_topology_service)):
    data = svc.search_enrich(
        center={
            "market": req.center_market,
            "code": req.center_code,
            "name": req.center_name,
            "sector": req.center_sector,
            "industry": req.center_industry,
        },
        symbols=req.symbols,
    )
    return {"ok": True, "data": data}


@router.post("/expand")
def expand(req: ExpandRequest, background_tasks: BackgroundTasks, svc: TopologyService = Depends(get_topology_service)):
    existing = list(req.existing_codes or [])
    existing.extend(symbol_id(item.market, item.code) for item in req.existing_nodes or [])
    data = svc.expand(req.code, req.market, req.depth, existing, quote_mode=_graph_quote_mode(req.quote_mode))
    if data.get("stats", {}).get("relation_status") == "generating":
        data["stats"]["background_started"] = _schedule_relation_generation(background_tasks, data["stats"])
    return {"ok": True, "data": data}


@router.post("/refresh")
def refresh(req: RefreshRequest, svc: TopologyService = Depends(get_topology_service)):
    return {"ok": True, "data": svc.refresh(req.code, req.market)}


def _schedule_relation_generation(background_tasks: BackgroundTasks, stats: Dict[str, Any]) -> bool:
    """将本轮 stale sources 去重后分批（默认 5 个/批），每批启动一个后台批量任务。

    in-flight 仍按单个 (market, code) 管理，避免重复调度。
    """
    pending: List[tuple[str, str]] = []
    for item in stats.get("stale_sources") or []:
        code = str(item.get("code") or "").strip()
        market = str(item.get("market") or "").strip()
        if not code or not market:
            continue
        key = (market, code)
        with _TOPOLOGY_GENERATION_LOCK:
            if key in _TOPOLOGY_GENERATION_IN_FLIGHT:
                continue
            _TOPOLOGY_GENERATION_IN_FLIGHT.add(key)
        pending.append(key)
    if not pending:
        return False
    for i in range(0, len(pending), _TOPOLOGY_BATCH_SIZE):
        batch = pending[i:i + _TOPOLOGY_BATCH_SIZE]
        background_tasks.add_task(_generate_relation_cache_batch_task, batch, list(batch))
    return True


def _generate_relation_cache_batch_task(sources: List[tuple[str, str]], keys: List[tuple[str, str]]) -> None:
    db = MarketDatabase(mysql_config_from_env())
    try:
        payload = [{"code": code, "market": market} for (market, code) in sources]
        TopologyService(db).infer_and_cache_batch(payload)
    finally:
        try:
            db.close()
        finally:
            with _TOPOLOGY_GENERATION_LOCK:
                for key in keys:
                    _TOPOLOGY_GENERATION_IN_FLIGHT.discard(key)


@router.get("/quotes")
def quotes(
    symbols: str = Query(..., min_length=1),
    service: StockTerminalService = Depends(get_stock_terminal_service),
):
    items = [_quote_cache_item(raw, service) for raw in _split_symbols(symbols)]
    _enrich_quote_item_names(items, service)
    _enrich_quote_item_fundamentals(items, service)
    _enrich_quote_item_known_chinese_names(items)
    return {"ok": True, "data": _quote_batch_payload(items)}


@router.post("/quotes/refresh")
def refresh_quotes(
    req: QuoteRefreshRequest,
    background_tasks: BackgroundTasks,
    service: StockTerminalService = Depends(get_stock_terminal_service),
):
    seen = set()
    items = []
    for raw in req.symbols:
        parsed = parse_topology_symbol(raw)
        symbol = parsed["symbol"]
        if symbol in seen:
            continue
        seen.add(symbol)
        if parsed.get("skipped"):
            items.append(_quote_status_item(parsed, "skipped", error=parsed.get("error", "unsupported market")))
            continue
        quote, status = service.repository.get_quote(parsed["market"], parsed["code"], now=service.now())
        if quote is not None and status.status == "cached" and not req.force and _quote_has_complete_key_fields(quote):
            items.append(_quote_from_cache(parsed, quote, status))
            continue
        background_tasks.add_task(service.get_summary, parsed["market"], parsed["code"])
        items.append(_quote_status_item(parsed, "queued"))
    _enrich_quote_item_names(items, service)
    _enrich_quote_item_fundamentals(items, service)
    _enrich_quote_item_known_chinese_names(items)
    return {"ok": True, "data": _quote_batch_payload(items)}


def _split_symbols(symbols: str) -> List[str]:
    return [item.strip() for item in str(symbols or "").split(",") if item.strip()]


def _quote_cache_item(raw_symbol: str, service: StockTerminalService) -> Dict[str, Any]:
    parsed = parse_topology_symbol(raw_symbol)
    if parsed.get("skipped"):
        return _quote_status_item(parsed, "skipped", error=parsed.get("error", "unsupported market"))
    quote, status = service.repository.get_quote(parsed["market"], parsed["code"], now=service.now())
    if quote is None and getattr(status, "status", "") == "error":
        return _quote_status_item(parsed, "error", error=getattr(status, "error_message", ""), source=getattr(status, "source", ""), updated_at=status.to_dict().get("fetched_at") if hasattr(status, "to_dict") else None)
    if quote is None:
        return _quote_status_item(parsed, "pending")
    return _quote_from_cache(parsed, quote, status)


def _quote_from_cache(parsed: Dict[str, Any], quote: Any, status: Any) -> Dict[str, Any]:
    item = _quote_status_item(parsed, status.status or "cached")
    quote_payload = quote.to_dict() if hasattr(quote, "to_dict") else {}
    price = quote_payload.get("price")
    pct_chg = quote_payload.get("change_percent")
    market_cap = quote_payload.get("market_cap")
    fields = {"price": price, "pct_chg": pct_chg, "market_cap": market_cap}
    data_gaps = [field for field, value in fields.items() if value is None]
    field_errors = {field: "provider_missing_field" for field in data_gaps}
    item.update({
        "name": _quote_display_name(parsed, getattr(quote, "name", "")),
        "price": price,
        "pct_chg": pct_chg,
        "market_cap": market_cap,
        "market_cap_str": _quote_market_cap_text(market_cap),
        "field_sources": {
            "name": "resolver",
            **({"price": "resolver"} if price is not None else {}),
            **({"pct_chg": "resolver"} if pct_chg is not None else {}),
            **({"market_cap": "resolver"} if market_cap is not None else {}),
        },
        "data_gaps": data_gaps,
        "field_errors": field_errors,
        "data_stage": "source_partial" if data_gaps else "source_verified",
        "source": getattr(status, "source", "") or getattr(quote, "source", "") or "未知",
        "updated_at": status.to_dict().get("fetched_at") if hasattr(status, "to_dict") else "未知",
        "error": getattr(status, "error_message", "") or "无",
    })
    return item


def _quote_status_item(parsed: Dict[str, Any], status: str, error: str = "", source: str = "", updated_at: Optional[str] = None) -> Dict[str, Any]:
    field_reason = _quote_field_error_reason(status)
    return {
        "symbol": parsed["symbol"],
        "market": parsed["market"],
        "code": parsed["code"],
        "name": _quote_display_name(parsed),
        "sector": "",
        "industry": "",
        "status": status,
        "price": None,
        "pct_chg": None,
        "market_cap": None,
        "market_cap_str": "未知",
        "field_sources": {},
        "data_gaps": ["price", "pct_chg", "market_cap"],
        "field_errors": {
            "price": field_reason,
            "pct_chg": field_reason,
            "market_cap": field_reason,
        },
        "data_stage": "source_partial",
        "source": source or "未知",
        "updated_at": updated_at or "未知",
        "error": error or "无",
    }


def _quote_has_complete_key_fields(quote: Any) -> bool:
    quote_payload = quote.to_dict() if hasattr(quote, "to_dict") else {}
    return (
        quote_payload.get("price") is not None
        and quote_payload.get("change_percent") is not None
        and quote_payload.get("market_cap") is not None
    )


def _quote_field_error_reason(status: str) -> str:
    if status == "skipped":
        return "unsupported_market"
    if status in {"failed", "error"}:
        return "quote_error"
    return "quote_pending"


def _quote_display_name(parsed: Dict[str, Any], name: str = "") -> str:
    display = str(name or "").strip()
    return display or str(parsed.get("code") or parsed.get("symbol") or "").strip() or "未知"


def _contains_chinese(value: Any) -> bool:
    return bool(_CJK_RE.search(str(value or "")))


def _should_replace_display_name(current: Any, candidate: Any) -> bool:
    candidate_text = str(candidate or "").strip()
    if not candidate_text:
        return False
    current_text = str(current or "").strip()
    if _contains_chinese(candidate_text) and not _contains_chinese(current_text):
        return True
    return not current_text or current_text in {"未知"}


def _quote_market_cap_text(market_cap: Any) -> str:
    text = format_market_cap(market_cap)
    return text if text and text != "--" else "未知"


def _enrich_quote_item_names(items: List[Dict[str, Any]], service: StockTerminalService) -> None:
    db = getattr(getattr(service, "repository", None), "db", None)
    if db is None:
        return
    resolver = StockNameResolver.default(db=db, include_external=True)
    by_market: Dict[str, List[Dict[str, Any]]] = {}
    for item in items:
        market = str(item.get("market") or "").upper()
        if market in {"A", "HK"}:
            by_market.setdefault(market, []).append(item)
    for market, market_items in by_market.items():
        records = [{"code": item.get("code"), "name": item.get("name")} for item in market_items]
        try:
            resolver.enrich_records(market, records)
        except Exception:
            continue
        for item, record in zip(market_items, records):
            item["name"] = _quote_display_name(item, record.get("name") or item.get("name"))


def _enrich_quote_item_fundamentals(items: List[Dict[str, Any]], service: StockTerminalService) -> None:
    db = getattr(getattr(service, "repository", None), "db", None)
    if db is None or not hasattr(db, "get_stocks_by_codes"):
        return
    by_market: Dict[str, List[Dict[str, Any]]] = {}
    for item in items:
        by_market.setdefault(str(item.get("market") or "").upper(), []).append(item)
    for market, market_items in by_market.items():
        codes = [str(item.get("code") or "").strip() for item in market_items]
        # 补上 bare code 变体以兼容 AKShare（无前缀）和 Futu（有前缀）两种格式
        for code in list(codes):
            bare = bare_code(str(market).upper(), code)
            if bare.upper() != code.upper():
                codes.append(bare)
        try:
            rows = db.get_stocks_by_codes(market, codes, include_fundamentals=True)
        except Exception:
            continue
        by_code: Dict[str, dict] = {}
        for row in rows:
            key = bare_code(str(market).upper(), str(row.get("code") or "").strip().upper())
            if key not in by_code:
                by_code[key] = row
        for item in market_items:
            lookup = bare_code(str(market).upper(), str(item.get("code") or "").strip().upper())
            row = by_code.get(lookup)
            if not row:
                continue
            if _should_replace_display_name(item.get("name"), row.get("name")):
                item["name"] = _quote_display_name(item, row.get("name"))
                item.setdefault("field_sources", {})["name"] = "resolver_override"
            sector = row.get("sector") or row.get("industry")
            industry = row.get("industry") or row.get("sector")
            if sector:
                item["sector"] = sector
                item.setdefault("field_sources", {})["sector"] = "resolver"
            if industry:
                item["industry"] = industry
                item.setdefault("field_sources", {})["industry"] = "resolver"
            if item.get("market_cap") is None and row.get("market_cap") is not None:
                item["market_cap"] = row.get("market_cap")
                item["market_cap_str"] = _quote_market_cap_text(row.get("market_cap"))
                item.setdefault("field_sources", {})["market_cap"] = "resolver"
            if any(item.get(field) for field in ("sector", "industry")) and item.get("market_cap") is not None:
                item["data_stage"] = "source_verified"


def _enrich_quote_item_known_chinese_names(items: List[Dict[str, Any]]) -> None:
    for item in items:
        candidate = _KNOWN_CHINESE_NAMES.get(str(item.get("code") or "").strip().upper())
        if _should_replace_display_name(item.get("name"), candidate):
            item["name"] = candidate


def _quote_batch_payload(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    for item in items:
        _refresh_quote_field_gaps(item)
    ready_statuses = {"cached", "fresh", "stale"}
    failed_statuses = {"failed", "error", "skipped"}
    return {
        "items": items,
        "ready": sum(1 for item in items if item.get("status") in ready_statuses),
        "pending": sum(1 for item in items if item.get("status") in {"pending", "queued"}),
        "failed": sum(1 for item in items if item.get("status") in failed_statuses),
    }


def _refresh_quote_field_gaps(item: Dict[str, Any]) -> None:
    field_values = {
        "price": item.get("price"),
        "pct_chg": item.get("pct_chg"),
        "market_cap": item.get("market_cap"),
    }
    gaps = [field for field, value in field_values.items() if value is None]
    status = str(item.get("status") or "")
    if status == "skipped":
        reason = "unsupported_market"
    elif status in {"failed", "error"}:
        reason = "quote_error"
    elif status in {"pending", "queued"}:
        reason = "quote_pending"
    else:
        reason = "provider_missing_field"
    existing_errors = dict(item.get("field_errors") or {})
    item["data_gaps"] = gaps
    item["field_errors"] = {field: existing_errors.get(field) or reason for field in gaps}
