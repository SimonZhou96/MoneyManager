#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
股票池缩减器 - 在筛选链执行前快速缩小待遍历股票数量（短期方案）

设计原则：
- 高内聚：每个缩减器只负责一个缩减维度，仅使用 StockInfo 已有字段（无需 K 线/网络）
- 低耦合：与 Filter 链解耦，在链执行前独立运行
- 工厂模式：UniverseFilterFactory 统一创建各类缩减器

使用示例：
    reducer = UniverseFilterFactory.create("market_cap", min_cap=1e9)
    reduced = reducer.reduce(stock_infos)
"""

from abc import ABC, abstractmethod
from typing import List, Optional, Any

from filters import StockInfo


class UniverseReducer(ABC):
    """股票池缩减器基类
    
    在筛选链执行前对股票列表进行预过滤，仅使用 StockInfo 已有字段，
    不触发 K 线获取或数据库查询，用于快速缩小待遍历规模。
    """

    @abstractmethod
    def reduce(self, stocks: List[StockInfo]) -> List[StockInfo]:
        """
        缩减股票列表

        Args:
            stocks: 原始股票列表

        Returns:
            缩减后的股票列表
        """
        pass

    @abstractmethod
    def get_name(self) -> str:
        """返回缩减器名称"""
        pass


class MarketCapUniverseReducer(UniverseReducer):
    """市值缩减器 - 按市值范围过滤"""

    def __init__(
        self,
        min_cap: Optional[float] = None,
        max_cap: Optional[float] = None,
    ):
        self.min_cap = min_cap
        self.max_cap = max_cap

    def reduce(self, stocks: List[StockInfo]) -> List[StockInfo]:
        result = []
        for s in stocks:
            if s.market_cap is None:
                continue
            if self.min_cap is not None and s.market_cap < self.min_cap:
                continue
            if self.max_cap is not None and s.market_cap > self.max_cap:
                continue
            result.append(s)
        return result

    def get_name(self) -> str:
        return "MarketCapUniverseReducer"


class PEUniverseReducer(UniverseReducer):
    """市盈率缩减器 - 按 PE 范围过滤"""

    def __init__(
        self,
        min_pe: Optional[float] = None,
        max_pe: Optional[float] = None,
        allow_negative: bool = False,
    ):
        self.min_pe = min_pe
        self.max_pe = max_pe
        self.allow_negative = allow_negative

    def reduce(self, stocks: List[StockInfo]) -> List[StockInfo]:
        result = []
        for s in stocks:
            if s.pe_ratio is None:
                continue
            if not self.allow_negative and s.pe_ratio < 0:
                continue
            if self.min_pe is not None and s.pe_ratio < self.min_pe:
                continue
            if self.max_pe is not None and s.pe_ratio > self.max_pe:
                continue
            result.append(s)
        return result

    def get_name(self) -> str:
        return "PEUniverseReducer"


class SectorUniverseReducer(UniverseReducer):
    """板块缩减器 - 按板块/行业过滤"""

    def __init__(
        self,
        include_sectors: Optional[List[str]] = None,
        exclude_sectors: Optional[List[str]] = None,
    ):
        self.include_sectors = set(s.strip() for s in (include_sectors or [])) or None
        self.exclude_sectors = set(s.strip() for s in (exclude_sectors or [])) or None

    def _get_sector(self, stock: StockInfo) -> Optional[str]:
        return stock.sector or stock.industry

    def reduce(self, stocks: List[StockInfo]) -> List[StockInfo]:
        result = []
        for s in stocks:
            sector = self._get_sector(s)
            if self.exclude_sectors and sector and sector in self.exclude_sectors:
                continue
            if self.include_sectors and (not sector or sector not in self.include_sectors):
                continue
            result.append(s)
        return result

    def get_name(self) -> str:
        return "SectorUniverseReducer"


class CompositeUniverseReducer(UniverseReducer):
    """组合缩减器 - 串联多个缩减器（AND 逻辑）"""

    def __init__(self, reducers: List[UniverseReducer]):
        self._reducers = reducers

    def reduce(self, stocks: List[StockInfo]) -> List[StockInfo]:
        current = stocks
        for r in self._reducers:
            current = r.reduce(current)
        return current

    def get_name(self) -> str:
        return "CompositeUniverseReducer"


class UniverseFilterFactory:
    """股票池缩减器工厂"""

    _REGISTRY = {
        "market_cap": MarketCapUniverseReducer,
        "pe": PEUniverseReducer,
        "sector": SectorUniverseReducer,
    }

    @classmethod
    def create(
        cls,
        strategy: str,
        **kwargs: Any,
    ) -> Optional[UniverseReducer]:
        """
        根据策略类型创建缩减器

        Args:
            strategy: 策略类型 ("market_cap" | "pe" | "sector" | "composite")
            **kwargs: 策略参数
                - market_cap: min_cap, max_cap
                - pe: min_pe, max_pe, allow_negative
                - sector: include_sectors, exclude_sectors (list 或逗号分隔字符串)
                - composite: reducers (List[UniverseReducer])

        Returns:
            UniverseReducer 或 None（策略无效时）
        """
        strategy = (strategy or "").strip().lower()
        if not strategy:
            return None

        if strategy == "composite":
            reducers = kwargs.get("reducers")
            if not reducers:
                return None
            return CompositeUniverseReducer(reducers)

        if strategy not in cls._REGISTRY:
            return None

        if strategy == "market_cap":
            return MarketCapUniverseReducer(
                min_cap=kwargs.get("min_cap"),
                max_cap=kwargs.get("max_cap"),
            )
        if strategy == "pe":
            return PEUniverseReducer(
                min_pe=kwargs.get("min_pe"),
                max_pe=kwargs.get("max_pe"),
                allow_negative=kwargs.get("allow_negative", False),
            )
        if strategy == "sector":
            include = kwargs.get("include_sectors")
            exclude = kwargs.get("exclude_sectors")
            if isinstance(include, str):
                include = [x.strip() for x in include.split(",") if x.strip()]
            if isinstance(exclude, str):
                exclude = [x.strip() for x in exclude.split(",") if x.strip()]
            return SectorUniverseReducer(
                include_sectors=include,
                exclude_sectors=exclude,
            )
        return None

    @classmethod
    def create_composite(
        cls,
        min_cap: Optional[float] = None,
        max_cap: Optional[float] = None,
        min_pe: Optional[float] = None,
        max_pe: Optional[float] = None,
        allow_negative_pe: bool = False,
        include_sectors: Optional[List[str]] = None,
        exclude_sectors: Optional[List[str]] = None,
    ) -> Optional[UniverseReducer]:
        """
        创建组合缩减器（便捷方法）

        按市值 -> PE -> 板块 顺序串联，任一维度有配置则加入组合。
        """
        reducers: List[UniverseReducer] = []
        if min_cap is not None or max_cap is not None:
            reducers.append(MarketCapUniverseReducer(min_cap=min_cap, max_cap=max_cap))
        if min_pe is not None or max_pe is not None:
            reducers.append(
                PEUniverseReducer(
                    min_pe=min_pe,
                    max_pe=max_pe,
                    allow_negative=allow_negative_pe,
                )
            )
        if include_sectors or exclude_sectors:
            reducers.append(
                SectorUniverseReducer(
                    include_sectors=include_sectors,
                    exclude_sectors=exclude_sectors,
                )
            )
        if not reducers:
            return None
        return CompositeUniverseReducer(reducers)


