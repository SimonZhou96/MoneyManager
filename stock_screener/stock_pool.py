#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
股票池管理模块

支持港股、美股、A股的股票池获取和管理
包含5个子池：
1. 最好股票（基于市值、价格、PE、成交量筛选）
2. 指数成份股
3. 行业龙头股（各行业前5名）
4. 新股（最近两年上市）
5. ETF列表
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, List, Optional

import pandas as pd

from market import normalize_market


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
                "index_constituents": [...],
                "industry_leaders": [...],
                "recent_ipos": [...],
                "etf_list": [...]
            }
        """
        return {
            "best_stocks": self.fetch_best_stocks(market, best_criteria or StockPoolCriteria()),
            "index_constituents": self.fetch_index_constituents(market, index_codes or []),
            "industry_leaders": self.fetch_industry_leaders(market, industry_top_n),
            "recent_ipos": self.fetch_recent_ipos(market, ipo_days),
            "etf_list": self.fetch_etf_list(market),
        }
