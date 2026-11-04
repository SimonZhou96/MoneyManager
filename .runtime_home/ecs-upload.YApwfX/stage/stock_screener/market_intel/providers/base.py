from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from typing import Dict, Iterable, List, Tuple

from market_intel.models import IntelItem


class MarketIntelProvider(ABC):
    provider_name: str = ""

    @property
    @abstractmethod
    def is_available(self) -> bool:
        raise NotImplementedError

    @abstractmethod
    def fetch_stock(self, market: str, code: str) -> List[IntelItem]:
        raise NotImplementedError

    @abstractmethod
    def fetch_market(self, market: str) -> List[IntelItem]:
        raise NotImplementedError


class NullMarketIntelProvider(MarketIntelProvider):
    provider_name = "null"

    @property
    def is_available(self) -> bool:
        return False

    def fetch_stock(self, market: str, code: str) -> List[IntelItem]:
        return []

    def fetch_market(self, market: str) -> List[IntelItem]:
        return []


def ttl_for_item_type(item_type: str) -> timedelta:
    ttl_map = {
        "financial": timedelta(days=1),
        "announcement": timedelta(hours=12),
        "research_report": timedelta(hours=12),
        "money_flow": timedelta(minutes=30),
        "long_tiger": timedelta(minutes=30),
        "hot_sector": timedelta(minutes=30),
        "market_news": timedelta(minutes=15),
        "index_snapshot": timedelta(minutes=15),
        "search_document": timedelta(hours=2),
    }
    return ttl_map.get(item_type, timedelta(hours=6))


def dedupe_items(items: Iterable[IntelItem]) -> List[IntelItem]:
    seen: set[Tuple[str, str, str, str, str]] = set()
    deduped: List[IntelItem] = []
    for item in items:
        key = (
            item.scope_type,
            item.market,
            item.code,
            item.provider,
            item.dedupe_key,
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _datetime_sort_timestamp(value: datetime | None) -> float:
    if value is None:
        return 0.0
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value.timestamp()


def _item_sort_timestamp(item: IntelItem) -> float:
    return _datetime_sort_timestamp(item.published_at or item.fetched_at)


def group_items(items: Iterable[IntelItem]) -> Dict[str, List[IntelItem]]:
    grouped: Dict[str, List[IntelItem]] = {}
    for item in items:
        grouped.setdefault(item.item_type, []).append(item)
    for values in grouped.values():
        values.sort(key=_item_sort_timestamp, reverse=True)
    return grouped


# =============================================================================
# P2 数据源抽象接口 —— 预留 FRED / Tushare Pro / Wind 等专业数据源接入点。
# P0/P1 阶段不使用这些接口，仅作为设计契约存在。
# =============================================================================


from dataclasses import dataclass
from typing import Optional


@dataclass
class CreditSpreadData:
    """信用利差数据"""
    hy_oas: Optional[float] = None       # 高收益债 OAS
    ig_oas: Optional[float] = None       # 投资级债 OAS
    source: str = ""
    date: str = ""


@dataclass
class FinancialConditionsData:
    """金融条件数据"""
    nfci: Optional[float] = None         # 芝加哥联储 NFCI
    fed_funds_rate: Optional[float] = None
    source: str = ""


@dataclass
class AnalystEstimateData:
    """分析师预期数据"""
    eps_fy1: Optional[float] = None      # 一致预期 EPS (FY1)
    eps_fy2: Optional[float] = None
    revenue_fy1: Optional[float] = None
    num_estimates: int = 0
    revision_direction: str = "stable"   # "up" | "stable" | "down"
    source: str = ""


class MacroDataProvider(ABC):
    """P2 宏观数据源抽象接口。

    实现类：
      - FREDProvider: 美国信用利差(OAS)、金融条件(NFCI)、联邦基金利率
      - TushareProProvider: A 股业绩预告、财务指标、公告原文
    """

    @abstractmethod
    def get_credit_spread(self, market: str) -> Optional[CreditSpreadData]:
        """获取信用利差数据。"""
        ...

    @abstractmethod
    def get_financial_conditions(self) -> Optional[FinancialConditionsData]:
        """获取金融条件数据。"""
        ...

    @abstractmethod
    def get_analyst_estimates(self, code: str) -> Optional[AnalystEstimateData]:
        """获取分析师一致预期数据。"""
        ...
