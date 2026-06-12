from __future__ import annotations

import time
from collections import defaultdict
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, Query

from db import MarketDatabase
from market import normalize_market
from signal_analysis.hot_sectors import AkshareHotSectorProvider

from .auth import CurrentUser, get_db, require_user
from .business import BusinessError
from .config import mysql_config_from_env

router = APIRouter(prefix="/api/sectors", tags=["sectors"])

# ── Cache ──
_cache: Dict[str, tuple[List[Dict[str, Any]], float]] = {}
_CACHE_TTL = 300  # 5 分钟


# ── English → Chinese sector name mapping ──
_SECTOR_CN_MAP: Dict[str, str] = {
    # ── HK ──
    "ETF": "ETF",
    "Real Estate Developers": "房地产开发商",
    "Engineering & Construction": "工程建设",
    "Biotechnology": "生物科技",
    "Industrial Parts & Equipment": "工业零部件",
    "Digital Solution Services": "数字化解决方案",
    "Property Services & Management": "物业管理",
    "Medical Services": "医疗服务",
    "Pharmaceuticals": "制药",
    "Real Estate Investment": "房地产投资",
    "Apparel Manufacturing": "服装制造",
    "Credit Services": "信贷服务",
    "Heavy Infrastructure": "重工基建",
    "Medical Equipment & Supplies": "医疗器械",
    "Catering": "餐饮",
    "Advertising Agencies": "广告代理",
    "Securities & Brokerage": "证券经纪",
    "Application Software": "应用软件",
    "education": "教育",
    "Investment & Asset Management": "投资资管",
    "Other Support Services": "支援服务",
    "Banks": "银行",
    "packaged food": "包装食品",
    "Printing & Packaging": "印刷包装",
    "Semiconductors": "半导体",
    "Electronic Components": "电子元器件",
    "Construction Materials": "建材",
    "Shipping & Ports": "航运港口",
    "Environmental Services": "环保服务",
    "Speciality Chemicals": "特种化工",
    # ── US ──
    "Shell Companies": "壳公司",
    "Software - Application": "软件应用",
    "Software - Infrastructure": "软件基础设施",
    "Banks - Regional": "区域银行",
    "Medical Devices": "医疗器械",
    "Asset Management": "资产管理",
    "Capital Markets": "资本市场",
    "Aerospace & Defense": "航空航天与国防",
    "Drug Manufacturers - Specialty & Generic": "仿制药",
    "Internet Content & Information": "互联网内容",
    "Specialty Industrial Machinery": "工业机械",
    "Oil & Gas E&P": "油气勘探",
    "Information Technology Services": "IT服务",
    "Packaged Foods": "包装食品",
    "Financial Services": "金融服务",
    "Specialty Chemicals": "特种化工",
    "Oil & Gas Midstream": "油气中游",
    "Auto Parts": "汽车零部件",
    "Telecom Services": "电信服务",
    "Medical Instruments & Supplies": "医疗器械",
    "Other Industrial Metals & Mining": "工业金属与矿业",
    "Entertainment": "娱乐",
    "Medical Care Facilities": "医疗设施",
    "Real Estate Services": "房地产服务",
    "Restaurants": "餐饮",
    # ── A ──
    "General Equipment": "通用设备",
    "Special Equipment": "专用设备",
    "Chemicals": "化工",
    "Chemical Pharmaceuticals": "化学制药",
    "Power Grid Equipment": "电网设备",
    "Software Development": "软件开发",
    "IT Services": "IT服务",
    "Batteries": "电池",
    "Environmental Governance": "环保治理",
    "Electrical Utilities": "电力公用",
    "Consumer Electronics": "消费电子",
    "Real Estate Development": "房地产开发",
    "Optics & Optoelectronics": "光电",
    "Telecommunication Equipment": "通信设备",
    "Automation Equipment": "自动化设备",
    "Computer Equipment": "计算机设备",
    "Plastics": "塑料",
    "Home Furnishings": "家居",
    "Photovoltaic Equipment": "光伏设备",
    "Traditional Chinese Medicine": "中药",
    "Electronic Parts": "电子零部件",
    "Military Electronics": "军工电子",
    "Agrochemicals": "农药化工",
    "Chemical raw materials": "化工原料",
    "General Retail": "零售",
    "Industrial Metals": "工业金属",
    # ── 通用/兜底 ──
    "Computers & Equipment": "计算机设备",
    "Computer Equipment": "计算机设备",
    "Telecommunication network infrastructure": "电信网络基建",
    "Technology": "科技",
    "Technology Hardware & Equipment": "科技硬件",
    "Industrials": "工业",
    "Industrial Parts & Components": "工业零部件",
    "Interactive media and services": "互动媒体与服务",
    "Oil & Gas Services": "油气服务",
    "Oil & Gas Equipment & Services": "油气设备与服务",
    "Electrical Equipment & Parts": "电气设备",
    "Electrical Utilities & IPPs": "电力公用",
    "Healthcare": "医疗保健",
    "Healthcare Services": "医疗服务",
    "Consumer": "消费",
    "Consumer Services": "消费服务",
    "Financials": "金融",
    "Diversified Financials": "综合金融",
    "Materials": "原材料",
    "Utilities": "公用事业",
    "Real Estate": "房地产",
    "Telecom": "电信",
    "Energy Equipment & Services": "能源设备与服务",
    "Food & Beverage": "食品饮料",
    "Automobiles & Components": "汽车及零部件",
    "Retailing": "零售",
    "Media & Entertainment": "媒体娱乐",
    "Commercial Services & Supplies": "商业服务",
    "Transportation": "交通运输",
    "Building Products": "建材",
    "Household Durables": "家居耐用",
    "Textiles & Apparel": "纺织服装",
    "Food Products": "食品",
    "Tobacco": "烟草",
    "Personal Products": "个人护理",
    "Health Care Equipment": "医疗设备",
    "Life Sciences Tools & Services": "生命科学",
    "Software": "软件",
    "IT Services & Consulting": "IT咨询",
    "Communications Equipment": "通信设备",
    "Electronic Equipment & Instruments": "电子设备",
    "Office Electronics": "办公电子",
    "Semiconductor Equipment": "半导体设备",
}


def _translate_name(english: str) -> str:
    """英文板块名 → 中文; 未命中则保留原文"""
    return _SECTOR_CN_MAP.get(english, english)


def _compute_heat_scores(
    sectors: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """根据换手率/股票数/市值三个维度计算归一化热度分 (0–100)。

    不依赖 stock_kline_cache，完全基于 stock_pools 的实时成交数据。
    """
    if not sectors:
        return sectors

    raw_turnover = [s.get("avg_turnover_ratio", 0) or 0 for s in sectors]
    raw_count = [s.get("stock_count", 0) for s in sectors]
    raw_cap = [s.get("avg_market_cap", 0) or 0 for s in sectors]

    max_turnover = max(max(raw_turnover), 0.0001)
    max_count = max(max(raw_count), 1)
    max_cap = max(max(raw_cap), 1)

    for i, s in enumerate(sectors):
        # 归一化 (0–100)
        norm_turnover = min(raw_turnover[i] / max_turnover * 100, 100)
        norm_count = raw_count[i] / max_count * 100
        norm_cap = min(raw_cap[i] / max_cap * 100, 100)

        # 加权合成: 换手率 40% + 广度 30% + 市值 30%
        heat = norm_turnover * 0.4 + norm_count * 0.3 + norm_cap * 0.3
        s["heat_score"] = round(min(heat, 100), 1)
        s["change_pct"] = round(s.get("avg_change_pct", 0), 2)
        s["avg_turnover_ratio"] = round(raw_turnover[i], 4)
        s["up_ratio"] = round(norm_count, 1)

    return sectors


def _query_sector_heat(
    db: MarketDatabase, market: str, limit: int
) -> List[Dict[str, Any]]:
    """从 DB 查询板块热度（基于 stock_pools 成交额，不依赖 stock_kline_cache）。

    热度计算维度：
      1. 平均换手率 (avg turnover ratio) — 权重 40%
      2. 成分股数量 (stock count factor) — 权重 30%
      3. 平均市值 (avg market cap) — 权重 30%（大盘股主导的板块更受关注）
    """
    # Step 1: 从 stock_pools（best 池） JOIN 板块成员关系。用 pool_type='best' 命中唯一索引。
    sql = """
        SELECT m.sector_name, m.code,
               p.price, p.market_cap, p.turnover, p.pe_ratio
        FROM stock_sector_memberships m
        JOIN stock_pools p
            ON p.market = m.market AND p.code = m.code AND p.pool_type = 'best'
        WHERE m.market = %s
    """
    with db.conn.cursor() as cursor:
        cursor.execute(sql, [market])
        rows = cursor.fetchall() or []

    if not rows:
        return []

    # Step 2: Python 侧按板块聚合
    sector_agg: Dict[str, Dict[str, Any]] = defaultdict(
        lambda: {"codes": set(), "sum_turnover_ratio": 0.0, "sum_market_cap": 0.0, "count_with_data": 0}
    )

    for sector_name, code, price, market_cap, turnover, pe_ratio in rows:
        cap = float(market_cap or 0)
        t = float(turnover or 0)
        turnover_ratio = t / cap if cap > 0 else 0

        agg = sector_agg[sector_name]
        agg["codes"].add(code)
        agg["sum_turnover_ratio"] += turnover_ratio
        agg["sum_market_cap"] += cap
        agg["count_with_data"] += 1

    # Step 3: 构建结果
    result = []
    for name, agg in sector_agg.items():
        n = agg["count_with_data"]
        if n < 3:
            continue
        avg_turnover_ratio = agg["sum_turnover_ratio"] / n
        avg_market_cap = agg["sum_market_cap"] / n
        stock_count = len(agg["codes"])
        result.append({
            "name": name,
            "stock_count": stock_count,
            "avg_turnover_ratio": avg_turnover_ratio,
            "avg_market_cap": avg_market_cap,
            # 兼容旧字段（无涨跌幅数据时用成交活跃度替代）
            "avg_change_pct": 0,
            "up_count": 0,
        })

    return result

    # Sort by avg_change_pct desc
    result.sort(key=lambda s: s["avg_change_pct"], reverse=True)
    return result[: limit * 2]


def _aggregate_hot_sectors(market: str, limit: int = 15) -> List[Dict[str, Any]]:
    """聚合热点板块数据（stock_pools 驱动，零 stock_kline_cache 依赖）。

    数据源:
      HK/US: stock_pools (成交额/市值) + stock_sector_memberships (板块归属)
      A:    优先 Akshare 概念/行业板块 (实时涨跌幅+换手率+广度)
    """
    normalized = normalize_market(market)
    sectors: List[Dict[str, Any]] = []

    # 1. A股: 直接用 Akshare 板块数据（实时涨跌+换手率+广度，最全）
    if normalized == "A":
        try:
            akshare = AkshareHotSectorProvider()
            akshare_sectors = akshare.find_hot_sectors(normalized, limit=limit * 2)
            for item in akshare_sectors:
                sectors.append({
                    "name": item.name,
                    "stock_count": 0,
                    "avg_change_pct": item.score,
                    "avg_turnover_ratio": 0,
                    "up_count": 0,
                    "source": item.source or "akshare",
                    "reason": item.reason or "",
                })
            # 也补充 stock_pools 板块数据（合并）
            db = MarketDatabase(mysql_config_from_env())
            try:
                db_sectors = _query_sector_heat(db, normalized, limit)
                existing_names = {s["name"] for s in sectors}
                for s in db_sectors:
                    if s["name"] not in existing_names:
                        sectors.append(s)
            finally:
                db.close()
        except Exception:
            # Akshare 挂了 → fallback to stock_pools
            db = MarketDatabase(mysql_config_from_env())
            try:
                sectors = _query_sector_heat(db, normalized, limit * 2)
            finally:
                db.close()
    else:
        # 2. HK/US: stock_pools 成交额驱动
        db = MarketDatabase(mysql_config_from_env())
        try:
            sectors = _query_sector_heat(db, normalized, limit * 2)
        finally:
            db.close()

    # 3. 计算热度分（归一化 + 加权合成）
    sectors = _compute_heat_scores(sectors)

    # 4. 按热度排序 + 中文名映射
    sectors.sort(key=lambda s: s.get("heat_score", 0), reverse=True)

    result = []
    for s in sectors[:limit]:
        cn_name = _translate_name(s["name"])
        result.append({
            "name": cn_name,
            "source": s.get("source", "stock_pools"),
            "heat_score": s["heat_score"],
            "score": s["heat_score"] / 100.0,
            "stock_count": s["stock_count"],
            "change_pct": s.get("change_pct", 0),
            "reason": s.get("reason", f"换手率={s.get('avg_turnover_ratio', 0):.4f} 成分股={s.get('stock_count', 0)}只"),
        })

    return result


@router.get("/hot")
def get_hot_sectors(
    market: str = Query(default="HK"),
    limit: int = Query(default=15, ge=5, le=30),
    user: CurrentUser = Depends(require_user),
) -> Dict[str, Any]:
    """获取当前市场最热门的板块列表，按热度排序（5分钟缓存）。"""
    _ = user
    try:
        normalized = normalize_market(market)
    except ValueError as exc:
        raise BusinessError("SECTORS_INVALID_MARKET", str(exc)) from exc

    # Check cache
    cache_key = f"{normalized}:{limit}"
    now = time.time()
    if cache_key in _cache:
        cached_data, ts = _cache[cache_key]
        if now - ts < _CACHE_TTL:
            return cached_data

    # Compute
    try:
        sectors = _aggregate_hot_sectors(normalized, limit=limit)
    except Exception as exc:
        raise BusinessError("SECTORS_HOT_FAILED", f"获取热点板块失败: {exc}") from exc

    result = {
        "market": normalized,
        "sectors": sectors,
        "total": len(sectors),
    }

    # Store cache
    _cache[cache_key] = (result, now)
    return result


@router.get("/{sector_name}/stocks")
def get_sector_stocks(
    sector_name: str,
    market: str = Query(default="HK"),
    limit: int = Query(default=30, ge=5, le=100),
    user: CurrentUser = Depends(require_user),
    db: MarketDatabase = Depends(get_db),
) -> Dict[str, Any]:
    """获取指定板块下的成分股列表，含最新价格和涨跌幅。"""
    _ = user
    try:
        normalized = normalize_market(market)
    except ValueError as exc:
        raise BusinessError("SECTORS_INVALID_MARKET", str(exc)) from exc

    # 前端传中文名 → 反查英文名
    en_name = _reverse_lookup_cn(sector_name)

    try:
        members = _query_sector_stocks(db, normalized, en_name, limit)
    except Exception as exc:
        raise BusinessError("SECTORS_STOCKS_FAILED", f"查询板块成分股失败: {exc}") from exc

    stocks = []
    for item in members:
        code = item.get("code", "")
        stocks.append({
            "code": code,
            "name": item.get("name") or code,
            "price": item.get("close"),
            "change_pct": item.get("change_percent"),
            "volume": item.get("volume"),
            "market_cap": item.get("market_cap"),
            "date": item.get("date"),
        })

    return {
        "market": normalized,
        "sector": sector_name,
        "stocks": stocks,
        "total": len(members),
    }


def _reverse_lookup_cn(cn_name: str) -> str:
    """中文板块名 → 英文名"""
    for en, cn in _SECTOR_CN_MAP.items():
        if cn == cn_name:
            return en
    return cn_name


def _query_sector_stocks(
    db: MarketDatabase, market: str, sector_name: str, limit: int
) -> List[dict]:
    """从 stock_kline_cache + stocks 查询板块成分股"""
    # 找最新两个交易日
    with db.conn.cursor() as cursor:
        cursor.execute(
            "SELECT MAX(bar_time) FROM stock_kline_cache "
            "WHERE market = %s AND timeframe = '1d'",
            [market],
        )
        latest_date = (cursor.fetchone() or [None])[0]
        if not latest_date:
            return []
        cursor.execute(
            "SELECT MAX(bar_time) FROM stock_kline_cache "
            "WHERE market = %s AND timeframe = '1d' AND bar_time < %s",
            [market, latest_date],
        )
        prev_date = (cursor.fetchone() or [None])[0]

    # 一次查询拉取成分股代码 + 最新 K 线 + 前一日 K 线 + 基本资料
    sql = """
        SELECT m.code, s.name, s.market_cap,
               k1.close, k1.volume, k1.bar_time,
               k2.close AS prev_close
        FROM stock_sector_memberships m
        JOIN stock_kline_cache k1
            ON k1.market = m.market AND k1.code = m.code
            AND k1.timeframe = '1d' AND k1.bar_time = %s
        LEFT JOIN stock_kline_cache k2
            ON k2.market = m.market AND k2.code = m.code
            AND k2.timeframe = '1d' AND k2.bar_time = %s
        LEFT JOIN stocks s
            ON s.market = m.market AND s.code = m.code
        WHERE m.market = %s AND m.sector_name = %s
        ORDER BY s.market_cap DESC
        LIMIT %s
    """
    with db.conn.cursor() as cursor:
        cursor.execute(sql, [latest_date, prev_date, market, sector_name, limit])
        rows = cursor.fetchall() or []

    result = []
    for code, name, market_cap, close, volume, bar_time, prev_close in rows:
        change = None
        if prev_close and close and float(prev_close) > 0:
            change = round((float(close) - float(prev_close)) / float(prev_close) * 100, 2)
        result.append({
            "code": code,
            "name": name or code,
            "close": float(close) if close is not None else None,
            "change_percent": change,
            "volume": volume,
            "market_cap": market_cap,
            "date": str(bar_time) if bar_time else None,
        })
    return result
