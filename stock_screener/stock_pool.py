#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
股票池管理模块

支持港股、美股、A股的股票池获取和管理
包含5个子池：
1. 优选池（基于市值、价格、PE、成交量筛选）
2. 核心指数成分股
3. 主流行业前5
4. 最近两年上市新股
5. 全部ETF指数基金
"""

import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Iterable, List, Optional

import pandas as pd

from market import normalize_market


POOL_TYPE_BEST = "best"
POOL_TYPE_MAJOR_INDEX = "major_index"
POOL_TYPE_INDUSTRY_TOP5 = "industry_top5"
POOL_TYPE_RECENT_IPO_2Y = "recent_ipo_2y"
POOL_TYPE_ALL_ETF = "all_etf"

CANONICAL_POOL_TYPES = (
    POOL_TYPE_BEST,
    POOL_TYPE_MAJOR_INDEX,
    POOL_TYPE_INDUSTRY_TOP5,
    POOL_TYPE_RECENT_IPO_2Y,
    POOL_TYPE_ALL_ETF,
)

POOL_LABELS = {
    POOL_TYPE_BEST: "优选池",
    POOL_TYPE_MAJOR_INDEX: "核心指数成分股",
    POOL_TYPE_INDUSTRY_TOP5: "主流行业前5",
    POOL_TYPE_RECENT_IPO_2Y: "最近两年上市新股",
    POOL_TYPE_ALL_ETF: "全部ETF指数基金",
}

POOL_RESULT_KEY_BY_TYPE = {
    POOL_TYPE_BEST: "best_stocks",
    POOL_TYPE_MAJOR_INDEX: "major_index_constituents",
    POOL_TYPE_INDUSTRY_TOP5: "industry_top5",
    POOL_TYPE_RECENT_IPO_2Y: "recent_ipo_2y",
    POOL_TYPE_ALL_ETF: "all_etf",
}

DEFAULT_POOL_TYPES_TEXT = ",".join(CANONICAL_POOL_TYPES)

MAJOR_INDEX_CODES_BY_MARKET = {
    "HK": [
        "HK.HSI Constituent Stocks",
        "HK.HSCEI Stock",
        "HK.800700",  # 恒生科技指数，Futu 不同版本可能返回数字板块代码
    ],
    "A": [
        "000300",  # 沪深300
        "000905",  # 中证500
        "000016",  # 上证50
        "399006",  # 创业板指
    ],
    "US": [],
}

A_CORE_INDEX_DEFINITIONS = (
    ("000300", "沪深300"),
    ("000905", "中证500"),
    ("000016", "上证50"),
    ("399006", "创业板指"),
)

US_CORE_INDEX_DEFINITIONS = (
    ("sp500", "标普500", "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"),
    ("nasdaq100", "纳斯达克100", "https://en.wikipedia.org/wiki/Nasdaq-100"),
    ("dow30", "道琼斯工业平均", "https://en.wikipedia.org/wiki/Dow_Jones_Industrial_Average"),
)


def get_major_index_codes(market: str) -> List[str]:
    """Return configured core-index identifiers for a market."""
    market = normalize_market(market)
    env_name = f"STOCK_POOL_MAJOR_INDEX_CODES_{market}"
    raw = os.getenv(env_name, "").strip()
    if raw:
        return [item.strip() for item in raw.replace("，", ",").split(",") if item.strip()]
    return list(MAJOR_INDEX_CODES_BY_MARKET.get(market, []))


def normalize_pool_types(values: Iterable[str]) -> List[str]:
    """Validate pool types and remove duplicates while preserving order."""
    allowed = set(CANONICAL_POOL_TYPES)
    result: List[str] = []
    invalid: List[str] = []
    for raw in values:
        item = str(raw or "").strip().lower()
        if not item:
            continue
        if item not in allowed:
            invalid.append(item)
            continue
        if item not in result:
            result.append(item)
    if invalid:
        raise ValueError(f"无效股票池类型: {invalid}; 可选: {DEFAULT_POOL_TYPES_TEXT}")
    return result


def parse_pool_types(raw: str) -> List[str]:
    """Parse CLI/interactive pool type input."""
    value = str(raw or "").strip()
    if not value or value.lower() == "all":
        return list(CANONICAL_POOL_TYPES)
    return normalize_pool_types(value.replace("，", ",").split(","))


def pool_scope_from_types(values: Optional[Iterable[str]] = None) -> str:
    """Stable lock/cache scope string for a selected stock-pool set."""
    pool_types = normalize_pool_types(values or CANONICAL_POOL_TYPES)
    return ",".join(pool_types)


def _find_column(columns: Iterable[Any], candidates: Iterable[str]) -> Optional[Any]:
    column_map = {str(column).strip().lower(): column for column in columns}
    for candidate in candidates:
        matched = column_map.get(str(candidate).strip().lower())
        if matched is not None:
            return matched
    return None


def _normalize_a_stock_code(code: str) -> str:
    digits = "".join(ch for ch in str(code) if ch.isdigit()).zfill(6)[-6:]
    prefix = "SH" if digits.startswith(("5", "6", "9")) else "SZ"
    return f"{prefix}.{digits}"


def _dedupe_stock_rows(rows: Iterable[dict]) -> List[dict]:
    result: List[dict] = []
    seen = set()
    for row in rows:
        code = str(row.get("code") or "").strip()
        if not code or code in seen:
            continue
        seen.add(code)
        result.append(row)
    return result


@dataclass
class StockPoolCriteria:
    """股票池筛选条件"""
    market_cap_min: Optional[float] = None  # 最小市值
    price_min: Optional[float] = None  # 最小价格
    price_max: Optional[float] = None  # 最大价格
    pe_min: Optional[float] = None  # 最小市盈率
    pe_max: Optional[float] = None  # 最大市盈率
    avg_volume_min: Optional[float] = None  # 最小平均成交额
    listing_days_max: Optional[int] = None  # 上市天数上限（用于新股筛选）


class StockPoolFetcher:
    """股票池数据获取器"""

    def __init__(self, quote_ctx: Any = None, db: Any = None):
        """
        Args:
            quote_ctx: Futu OpenQuoteContext
            db: MarketDatabase 实例
        """
        self.quote_ctx = quote_ctx
        self.db = db

    def fetch_best_stocks(
        self, market: str, criteria: StockPoolCriteria
    ) -> List[dict]:
        """
        获取最好股票（第1部分）

        Args:
            market: HK/US/A
            criteria: 筛选条件

        Returns:
            [{"code": "HK.00700", "name": "腾讯", "market_cap": ..., ...}, ...]
        """
        market = normalize_market(market)
        if not self.quote_ctx:
            return []

        try:
            import futu as ft
        except Exception:
            return []

        # 获取所有股票基本信息
        # A股使用 SH（上交所）作为代表
        market_map = {"HK": ft.Market.HK, "US": ft.Market.US, "A": ft.Market.SH}
        market_enum = market_map.get(market, ft.Market.HK)

        ret, basic_info = self.quote_ctx.get_stock_basicinfo(
            market=market_enum, stock_type=ft.SecurityType.STOCK
        )
        if ret != ft.RET_OK:
            return []

        # 批量获取市场快照（分批处理，避免超限）
        codes = basic_info["code"].tolist()
        batch_size = 200
        all_snapshots = []

        for i in range(0, len(codes), batch_size):
            batch_codes = codes[i : i + batch_size]
            ret, snapshot = self.quote_ctx.get_market_snapshot(batch_codes)
            if ret == ft.RET_OK:
                all_snapshots.append(snapshot)

        if not all_snapshots:
            return []

        df = pd.concat(all_snapshots, ignore_index=True)

        # 应用筛选条件
        if criteria.market_cap_min:
            df = df[df["total_market_val"] >= criteria.market_cap_min]
        if criteria.price_min:
            df = df[df["last_price"] >= criteria.price_min]
        if criteria.price_max:
            df = df[df["last_price"] <= criteria.price_max]
        if criteria.pe_min:
            df = df[df["pe_ttm_ratio"] >= criteria.pe_min]
        if criteria.pe_max:
            df = df[df["pe_ttm_ratio"] <= criteria.pe_max]

        # 计算10天平均成交额（使用当前成交额作为近似）
        if criteria.avg_volume_min:
            df = df[df["turnover"] >= criteria.avg_volume_min]

        # 转换为字典列表
        result = []
        for _, row in df.iterrows():
            result.append({
                "code": row["code"],
                "name": row["name"],
                "market_cap": row["total_market_val"],
                "price": row["last_price"],
                "pe_ratio": row["pe_ttm_ratio"],
                "turnover": row["turnover"],
                "volume": row["volume"],
                "listing_date": row.get("listing_date"),
            })

        return result

    def fetch_index_constituents(
        self, market: str, index_codes: List[str]
    ) -> List[dict]:
        """
        获取指数成份股（第2部分）

        Args:
            market: HK/US/A
            index_codes: 指数代码列表，如 ["HK.800000", "HK.800700"]

        Returns:
            [{"code": "HK.00700", "name": "腾讯", "index": "恒生指数", ...}, ...]
        """
        market = normalize_market(market)
        if not self.quote_ctx:
            return []

        try:
            import futu as ft
        except Exception:
            return []

        result = []
        for index_code in index_codes:
            ret, data = self.quote_ctx.get_plate_stock(index_code)
            if ret == ft.RET_OK:
                for _, row in data.iterrows():
                    result.append({
                        "code": row["code"],
                        "name": row.get("stock_name", row["code"]),
                        "index_code": index_code,
                        "index_name": row.get("plate_name", ""),
                    })

        return result

    def fetch_major_index_constituents(
        self, market: str, index_codes: Optional[List[str]] = None
    ) -> List[dict]:
        """获取核心指数成分股。"""
        market = normalize_market(market)
        codes = index_codes if index_codes is not None else get_major_index_codes(market)
        result = self.fetch_index_constituents(market, codes)
        if market == "A":
            result.extend(self._fetch_a_core_index_constituents())
        elif market == "US":
            result.extend(self._fetch_us_core_index_constituents())
        return _dedupe_stock_rows(result)

    def _fetch_a_core_index_constituents(self) -> List[dict]:
        """Fetch A-share core index constituents from AkShare."""
        try:
            import akshare as ak
        except Exception:
            return []

        result: List[dict] = []
        for index_code, index_name in A_CORE_INDEX_DEFINITIONS:
            data = None
            for func_name in ("index_stock_cons_csindex", "index_stock_cons_sina", "index_stock_cons"):
                func = getattr(ak, func_name, None)
                if not func:
                    continue
                try:
                    candidate = func(symbol=index_code)
                except Exception:
                    continue
                if candidate is not None and not candidate.empty:
                    data = candidate
                    break
            if data is None or data.empty:
                continue
            code_col = _find_column(data.columns, ["成分券代码", "品种代码", "代码", "symbol", "code"])
            name_col = _find_column(data.columns, ["成分券名称", "品种名称", "名称", "name"])
            if not code_col:
                continue
            for _, row in data.iterrows():
                raw_code = str(row.get(code_col) or "").strip()
                if not raw_code:
                    continue
                code = _normalize_a_stock_code(raw_code)
                result.append({
                    "code": code,
                    "name": str(row.get(name_col) or code).strip() if name_col else code,
                    "index_code": index_code,
                    "index_name": index_name,
                })
        return result

    def _fetch_us_core_index_constituents(self) -> List[dict]:
        """Fetch US core index constituents from public index tables."""
        result: List[dict] = []
        for index_code, index_name, url in US_CORE_INDEX_DEFINITIONS:
            try:
                tables = pd.read_html(url)
            except Exception:
                continue
            for table in tables:
                code_col = _find_column(
                    table.columns,
                    ["Symbol", "Ticker", "Ticker symbol", "Ticker Symbol"],
                )
                name_col = _find_column(
                    table.columns,
                    ["Security", "Company", "Company name", "Company Name"],
                )
                if not code_col:
                    continue
                added_for_index = False
                for _, row in table.iterrows():
                    ticker = str(row.get(code_col) or "").strip()
                    if not ticker or ticker.lower() == "nan":
                        continue
                    ticker = ticker.replace(".", "-").upper()
                    result.append({
                        "code": f"US.{ticker}",
                        "name": str(row.get(name_col) or ticker).strip() if name_col else ticker,
                        "index_code": index_code,
                        "index_name": index_name,
                    })
                    added_for_index = True
                if added_for_index:
                    break
        return result

    def fetch_industry_leaders(
        self, market: str, top_n: int = 5
    ) -> List[dict]:
        """
        获取各行业前N名股票（第3部分）

        Args:
            market: HK/US/A
            top_n: 每个行业取前N名

        Returns:
            [{"code": "HK.00700", "name": "腾讯", "industry": "互联网", "rank": 1, ...}, ...]
        """
        market = normalize_market(market)
        if not self.quote_ctx:
            return []

        try:
            import futu as ft
        except Exception:
            return []

        # 获取行业板块列表
        # A股使用 SH（上交所）作为代表
        market_map = {"HK": ft.Market.HK, "US": ft.Market.US, "A": ft.Market.SH}
        market_enum = market_map.get(market, ft.Market.HK)

        ret, industries = self.quote_ctx.get_plate_list(market_enum, ft.Plate.INDUSTRY)
        if ret != ft.RET_OK:
            return []

        result = []
        for _, industry_row in industries.iterrows():
            industry_code = industry_row["code"]
            industry_name = industry_row["plate_name"]

            # 获取该行业的所有股票
            ret, stocks = self.quote_ctx.get_plate_stock(industry_code)
            if ret != ft.RET_OK:
                continue

            # 获取这些股票的市场快照
            codes = stocks["code"].tolist()[:100]  # 限制数量
            ret, snapshot = self.quote_ctx.get_market_snapshot(codes)
            if ret != ft.RET_OK:
                continue

            # 按市值排序，取前N名
            snapshot = snapshot.sort_values("total_market_val", ascending=False)
            top_stocks = snapshot.head(top_n)

            for rank, (_, row) in enumerate(top_stocks.iterrows(), 1):
                result.append({
                    "code": row["code"],
                    "name": row["name"],
                    "industry_code": industry_code,
                    "industry_name": industry_name,
                    "rank": rank,
                    "market_cap": row["total_market_val"],
                    "price": row["last_price"],
                })

        return result

    def fetch_industry_memberships(self, market: str) -> List[dict]:
        """
        获取完整行业板块成分关系。

        Returns:
            [{"code": "HK.00700", "name": "腾讯", "sector_type": "industry",
              "sector_code": "...", "sector_name": "互联网", "source": "futu_plate"}, ...]
        """
        market = normalize_market(market)
        if not self.quote_ctx:
            return []

        try:
            import futu as ft
        except Exception:
            return []

        market_map = {"HK": ft.Market.HK, "US": ft.Market.US, "A": ft.Market.SH}
        market_enum = market_map.get(market, ft.Market.HK)

        ret, industries = self.quote_ctx.get_plate_list(market_enum, ft.Plate.INDUSTRY)
        if ret != ft.RET_OK:
            return []

        result = []
        for _, industry_row in industries.iterrows():
            industry_code = industry_row["code"]
            industry_name = industry_row["plate_name"]
            if not industry_code or not industry_name:
                continue

            ret, stocks = self.quote_ctx.get_plate_stock(industry_code)
            if ret != ft.RET_OK:
                continue

            for _, stock_row in stocks.iterrows():
                code = stock_row.get("code")
                if not code:
                    continue
                result.append({
                    "code": code,
                    "name": stock_row.get("stock_name") or stock_row.get("name") or code,
                    "sector_type": "industry",
                    "sector_code": industry_code,
                    "sector_name": industry_name,
                    "industry_code": industry_code,
                    "industry_name": industry_name,
                    "source": "futu_plate",
                })

        return result

    def fetch_recent_ipos(
        self, market: str, days: int = 730
    ) -> List[dict]:
        """
        获取最近N天上市的新股（第4部分）

        Args:
            market: HK/US/A
            days: 上市天数，默认730天（约2年）

        Returns:
            [{"code": "HK.09988", "name": "阿里巴巴", "listing_date": "2019-11-26", ...}, ...]
        """
        market = normalize_market(market)
        if not self.quote_ctx:
            return []

        try:
            import futu as ft
        except Exception:
            return []

        # 获取所有股票基本信息
        # A股使用 SH（上交所）作为代表
        market_map = {"HK": ft.Market.HK, "US": ft.Market.US, "A": ft.Market.SH}
        market_enum = market_map.get(market, ft.Market.HK)

        ret, basic_info = self.quote_ctx.get_stock_basicinfo(
            market=market_enum, stock_type=ft.SecurityType.STOCK
        )
        if ret != ft.RET_OK:
            return []

        # 筛选最近上市的股票
        cutoff_date = datetime.now() - timedelta(days=days)
        result = []

        for _, row in basic_info.iterrows():
            listing_date_str = row.get("listing_date")
            if not listing_date_str or listing_date_str == "N/A":
                continue

            try:
                listing_date = datetime.strptime(listing_date_str, "%Y-%m-%d")
                if listing_date >= cutoff_date:
                    result.append({
                        "code": row["code"],
                        "name": row["name"],
                        "listing_date": listing_date_str,
                        "days_since_listing": (datetime.now() - listing_date).days,
                    })
            except Exception:
                continue

        return result

    def fetch_etf_list(self, market: str) -> List[dict]:
        """
        获取ETF列表（第5部分）

        Args:
            market: HK/US/A

        Returns:
            [{"code": "HK.02800", "name": "盈富基金", ...}, ...]
        """
        market = normalize_market(market)
        if not self.quote_ctx:
            return []

        try:
            import futu as ft
        except Exception:
            return []

        # 获取ETF列表
        # A股使用 SH（上交所）作为代表
        market_map = {"HK": ft.Market.HK, "US": ft.Market.US, "A": ft.Market.SH}
        market_enum = market_map.get(market, ft.Market.HK)

        ret, etf_info = self.quote_ctx.get_stock_basicinfo(
            market=market_enum, stock_type=ft.SecurityType.ETF
        )
        if ret != ft.RET_OK:
            return []

        result = []
        for _, row in etf_info.iterrows():
            result.append({
                "code": row["code"],
                "name": row["name"],
                "listing_date": row.get("listing_date"),
            })

        return result

    def fetch_all_pools(
        self,
        market: str,
        best_criteria: Optional[StockPoolCriteria] = None,
        index_codes: Optional[List[str]] = None,
        industry_top_n: int = 5,
        ipo_days: int = 730,
    ) -> dict:
        """
        一次性获取所有5个股票池

        Args:
            market: HK/US/A
            best_criteria: 最好股票筛选条件
            index_codes: 指数代码列表
            industry_top_n: 每个行业取前N名
            ipo_days: 新股上市天数

        Returns:
            {
                "best_stocks": [...],
            "major_index_constituents": [...],
            "industry_top5": [...],
            "recent_ipo_2y": [...],
            "all_etf": [...]
        }
        """
        return {
            "best_stocks": self.fetch_best_stocks(market, best_criteria or StockPoolCriteria()),
            "major_index_constituents": self.fetch_major_index_constituents(market, index_codes),
            "industry_top5": self.fetch_industry_leaders(market, industry_top_n),
            "recent_ipo_2y": self.fetch_recent_ipos(market, ipo_days),
            "all_etf": self.fetch_etf_list(market),
        }
