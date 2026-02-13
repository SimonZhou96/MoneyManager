#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
股票筛选器架构 - 基于责任链模式的可扩展筛选器框架

设计原则：
- 高内聚：每个筛选器只负责一个筛选维度
- 低耦合：筛选器之间相互独立，可自由组合
- 可扩展：通过继承 Filter 基类轻松添加新筛选器
- 执行链：FilterChain 管理筛选器的执行顺序和结果聚合

使用示例：
    chain = FilterChain()
    chain.add_filter(MarketCapFilter(min_cap=100_000_000))  # 市值 >= 1亿
    chain.add_filter(PEFilter(min_pe=0, max_pe=50))  # PE 在 0-50 之间
    chain.add_filter(EMABreakoutFilter())  # EMA10 突破 EMA150
    
    results = chain.execute(stocks, context)
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, TypeVar, Generic
import pandas as pd


class FilterResult(Enum):
    """筛选结果枚举"""
    PASS = "pass"           # 通过筛选
    FAIL = "fail"           # 未通过筛选
    SKIP = "skip"           # 跳过（数据不足等）
    ERROR = "error"         # 执行错误


@dataclass
class StockInfo:
    """股票基本信息数据类"""
    market: str                      # 市场 (HK/US)
    code: str                        # 股票代码
    name: str = ""                   # 股票名称
    sector: Optional[str] = None     # 板块
    industry: Optional[str] = None   # 行业
    
    # 基本面数据（可选）
    market_cap: Optional[float] = None       # 市值
    pe_ratio: Optional[float] = None         # 市盈率
    pb_ratio: Optional[float] = None         # 市净率
    turnover_rate: Optional[float] = None    # 换手率
    volume: Optional[float] = None           # 成交量
    
    # K线数据（延迟加载）
    kline_df: Optional[pd.DataFrame] = None
    
    # 扩展字段
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class FilterContext:
    """筛选器上下文 - 在筛选器之间共享数据"""
    check_date: date                          # 检查日期
    market: str                               # 当前市场
    db: Optional[Any] = None                  # 数据库连接
    kline_fetcher: Optional[Any] = None       # K线数据获取器
    verbose: bool = False                     # 是否输出详细日志
    
    # 缓存数据
    _cache: Dict[str, Any] = field(default_factory=dict)
    
    def get_cache(self, key: str) -> Optional[Any]:
        """获取缓存数据"""
        return self._cache.get(key)
    
    def set_cache(self, key: str, value: Any):
        """设置缓存数据"""
        self._cache[key] = value


@dataclass
class FilterOutput:
    """单个筛选器的输出结果"""
    filter_name: str                          # 筛选器名称
    result: FilterResult                      # 筛选结果
    reason: str = ""                          # 结果原因/描述
    details: Dict[str, Any] = field(default_factory=dict)  # 详细数据


@dataclass
class StockFilterResult:
    """单只股票的完整筛选结果"""
    stock: StockInfo                          # 股票信息
    passed: bool = False                      # 是否通过所有筛选
    filter_outputs: List[FilterOutput] = field(default_factory=list)
    
    def add_output(self, output: FilterOutput):
        """添加筛选器输出"""
        self.filter_outputs.append(output)
    
    def get_failed_filters(self) -> List[FilterOutput]:
        """获取未通过的筛选器列表"""
        return [o for o in self.filter_outputs if o.result == FilterResult.FAIL]
    
    def get_summary(self) -> str:
        """获取筛选结果摘要"""
        passed_count = sum(1 for o in self.filter_outputs if o.result == FilterResult.PASS)
        failed_count = sum(1 for o in self.filter_outputs if o.result == FilterResult.FAIL)
        skipped_count = sum(1 for o in self.filter_outputs if o.result == FilterResult.SKIP)
        return f"passed={passed_count}, failed={failed_count}, skipped={skipped_count}"


class Filter(ABC):
    """筛选器基类
    
    所有具体筛选器都需要继承此基类并实现 apply 方法。
    """
    
    def __init__(self, name: Optional[str] = None, enabled: bool = True):
        """
        初始化筛选器
        
        Args:
            name: 筛选器名称（默认使用类名）
            enabled: 是否启用此筛选器
        """
        self._name = name or self.__class__.__name__
        self._enabled = enabled
    
    @property
    def name(self) -> str:
        return self._name
    
    @property
    def enabled(self) -> bool:
        return self._enabled
    
    def enable(self):
        """启用筛选器"""
        self._enabled = True
    
    def disable(self):
        """禁用筛选器"""
        self._enabled = False
    
    @abstractmethod
    def apply(self, stock: StockInfo, context: FilterContext) -> FilterOutput:
        """
        应用筛选逻辑
        
        Args:
            stock: 股票信息
            context: 筛选器上下文
            
        Returns:
            FilterOutput: 筛选结果
        """
        pass
    
    def _pass(self, reason: str = "", **details) -> FilterOutput:
        """创建通过结果"""
        return FilterOutput(
            filter_name=self.name,
            result=FilterResult.PASS,
            reason=reason,
            details=details
        )
    
    def _fail(self, reason: str = "", **details) -> FilterOutput:
        """创建失败结果"""
        return FilterOutput(
            filter_name=self.name,
            result=FilterResult.FAIL,
            reason=reason,
            details=details
        )
    
    def _skip(self, reason: str = "", **details) -> FilterOutput:
        """创建跳过结果"""
        return FilterOutput(
            filter_name=self.name,
            result=FilterResult.SKIP,
            reason=reason,
            details=details
        )
    
    def _error(self, reason: str = "", **details) -> FilterOutput:
        """创建错误结果"""
        return FilterOutput(
            filter_name=self.name,
            result=FilterResult.ERROR,
            reason=reason,
            details=details
        )


class FilterChain:
    """筛选器执行链
    
    管理多个筛选器的执行顺序，支持：
    - 添加/删除筛选器
    - 按顺序执行筛选器
    - 支持「全部通过」或「任一通过」两种模式
    - 支持早停（第一个失败就停止）
    """
    
    def __init__(
        self,
        filters: Optional[List[Filter]] = None,
        mode: str = "all",  # "all" = 全部通过, "any" = 任一通过
        early_stop: bool = False,  # 是否在第一个失败后停止
    ):
        """
        初始化筛选器链
        
        Args:
            filters: 初始筛选器列表
            mode: 执行模式 ("all" 或 "any")
            early_stop: 是否在第一个失败后停止执行
        """
        self._filters: List[Filter] = filters or []
        self._mode = mode
        self._early_stop = early_stop
    
    def add_filter(self, filter_: Filter) -> "FilterChain":
        """添加筛选器，支持链式调用"""
        self._filters.append(filter_)
        return self
    
    def remove_filter(self, filter_name: str) -> "FilterChain":
        """按名称删除筛选器"""
        self._filters = [f for f in self._filters if f.name != filter_name]
        return self
    
    def clear(self) -> "FilterChain":
        """清空所有筛选器"""
        self._filters.clear()
        return self
    
    def get_filter(self, filter_name: str) -> Optional[Filter]:
        """按名称获取筛选器"""
        for f in self._filters:
            if f.name == filter_name:
                return f
        return None
    
    def list_filters(self) -> List[str]:
        """列出所有筛选器名称"""
        return [f.name for f in self._filters]
    
    def apply(self, stock: StockInfo, context: FilterContext) -> StockFilterResult:
        """
        对单只股票应用所有筛选器
        
        Args:
            stock: 股票信息
            context: 筛选器上下文
            
        Returns:
            StockFilterResult: 完整筛选结果
        """
        result = StockFilterResult(stock=stock)
        
        for filter_ in self._filters:
            if not filter_.enabled:
                continue
            
            try:
                output = filter_.apply(stock, context)
                result.add_output(output)
                
                # 早停检查
                if self._early_stop and output.result == FilterResult.FAIL:
                    break
                    
            except Exception as e:
                result.add_output(FilterOutput(
                    filter_name=filter_.name,
                    result=FilterResult.ERROR,
                    reason=str(e)
                ))
                if self._early_stop:
                    break
        
        # 计算最终结果
        if self._mode == "all":
            # 全部通过模式：所有筛选器都必须通过或跳过
            result.passed = all(
                o.result in (FilterResult.PASS, FilterResult.SKIP)
                for o in result.filter_outputs
            ) and any(
                o.result == FilterResult.PASS
                for o in result.filter_outputs
            )
        else:  # "any"
            # 任一通过模式：至少有一个筛选器通过
            result.passed = any(
                o.result == FilterResult.PASS
                for o in result.filter_outputs
            )
        
        return result
    
    def execute(
        self,
        stocks: List[StockInfo],
        context: FilterContext,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
    ) -> List[StockFilterResult]:
        """
        对股票列表执行筛选
        
        Args:
            stocks: 股票列表
            context: 筛选器上下文
            progress_callback: 进度回调函数 (current, total, stock_name)
            
        Returns:
            List[StockFilterResult]: 所有股票的筛选结果
        """
        results = []
        total = len(stocks)
        
        for idx, stock in enumerate(stocks):
            if progress_callback:
                progress_callback(idx + 1, total, stock.name or stock.code)
            
            result = self.apply(stock, context)
            results.append(result)
            
            if context.verbose:
                status = "✅" if result.passed else "❌"
                print(f"  [{idx+1}/{total}] {status} {stock.code} ({stock.name}): {result.get_summary()}")
        
        return results
    
    def get_passed(self, results: List[StockFilterResult]) -> List[StockFilterResult]:
        """获取通过筛选的股票"""
        return [r for r in results if r.passed]
    
    def get_failed(self, results: List[StockFilterResult]) -> List[StockFilterResult]:
        """获取未通过筛选的股票"""
        return [r for r in results if not r.passed]


# ============================================================================
# 具体筛选器实现
# ============================================================================

class EMABreakoutFilter(Filter):
    """EMA突破筛选器
    
    检查 EMA10 是否向上突破 EMA150（T-1 或 T-2 突破）
    """
    
    def __init__(
        self,
        ema_short: int = 10,
        ema_long: int = 150,
        lookback_days: int = 2,  # 回看天数（T-1 和 T-2）
        name: str = "EMABreakoutFilter",
        enabled: bool = True,
    ):
        super().__init__(name=name, enabled=enabled)
        self.ema_short = ema_short
        self.ema_long = ema_long
        self.lookback_days = lookback_days
    
    def apply(self, stock: StockInfo, context: FilterContext) -> FilterOutput:
        """应用 EMA 突破检查"""
        from strategy import check_ema_breakout, EMABreakoutResult
        
        # 获取 K 线数据
        df = stock.kline_df
        if df is None or df.empty:
            # 尝试从数据库获取
            if context.db is not None:
                from datetime import timedelta
                start_date = (context.check_date - timedelta(days=365)).strftime("%Y-%m-%d")
                end_date = context.check_date.strftime("%Y-%m-%d")
                df = context.db.get_klines(stock.market, stock.code, start_date, end_date)
                stock.kline_df = df  # 缓存到 stock 对象
        
        if df is None or df.empty:
            return self._skip("K线数据不足")
        
        # 执行 EMA 突破检查
        result, breakout_date, ema_short_val, ema_long_val = check_ema_breakout(
            df=df,
            check_date=context.check_date,
            ema_short=self.ema_short,
            ema_long=self.ema_long,
        )
        
        details = {
            "result_type": result.value,
            "breakout_date": str(breakout_date) if breakout_date else None,
            f"ema{self.ema_short}": ema_short_val,
            f"ema{self.ema_long}": ema_long_val,
        }
        
        if result.is_satisfied():
            return self._pass(
                reason=result.get_description(),
                **details
            )
        else:
            return self._fail(
                reason=result.get_description(),
                **details
            )


class MarketCapFilter(Filter):
    """市值筛选器
    
    根据市值范围筛选股票
    """
    
    def __init__(
        self,
        min_cap: Optional[float] = None,  # 最小市值（单位取决于数据源）
        max_cap: Optional[float] = None,  # 最大市值
        name: str = "MarketCapFilter",
        enabled: bool = True,
    ):
        super().__init__(name=name, enabled=enabled)
        self.min_cap = min_cap
        self.max_cap = max_cap
    
    def apply(self, stock: StockInfo, context: FilterContext) -> FilterOutput:
        """应用市值筛选"""
        market_cap = stock.market_cap
        
        if market_cap is None:
            return self._skip("市值数据缺失")
        
        if self.min_cap is not None and market_cap < self.min_cap:
            return self._fail(
                reason=f"市值 {market_cap:.2f} < 最小值 {self.min_cap:.2f}",
                market_cap=market_cap,
                min_cap=self.min_cap
            )
        
        if self.max_cap is not None and market_cap > self.max_cap:
            return self._fail(
                reason=f"市值 {market_cap:.2f} > 最大值 {self.max_cap:.2f}",
                market_cap=market_cap,
                max_cap=self.max_cap
            )
        
        return self._pass(
            reason=f"市值 {market_cap:.2f} 在范围内",
            market_cap=market_cap
        )


class PEFilter(Filter):
    """市盈率(PE)筛选器
    
    根据 PE 范围筛选股票
    """
    
    def __init__(
        self,
        min_pe: Optional[float] = None,
        max_pe: Optional[float] = None,
        allow_negative: bool = False,  # 是否允许负 PE
        name: str = "PEFilter",
        enabled: bool = True,
    ):
        super().__init__(name=name, enabled=enabled)
        self.min_pe = min_pe
        self.max_pe = max_pe
        self.allow_negative = allow_negative
    
    def apply(self, stock: StockInfo, context: FilterContext) -> FilterOutput:
        """应用 PE 筛选"""
        pe = stock.pe_ratio
        
        if pe is None:
            return self._skip("PE数据缺失")
        
        if not self.allow_negative and pe < 0:
            return self._fail(
                reason=f"PE {pe:.2f} 为负值",
                pe=pe
            )
        
        if self.min_pe is not None and pe < self.min_pe:
            return self._fail(
                reason=f"PE {pe:.2f} < 最小值 {self.min_pe:.2f}",
                pe=pe,
                min_pe=self.min_pe
            )
        
        if self.max_pe is not None and pe > self.max_pe:
            return self._fail(
                reason=f"PE {pe:.2f} > 最大值 {self.max_pe:.2f}",
                pe=pe,
                max_pe=self.max_pe
            )
        
        return self._pass(
            reason=f"PE {pe:.2f} 在范围内",
            pe=pe
        )


class PBFilter(Filter):
    """市净率(PB)筛选器
    
    根据 PB 范围筛选股票
    """
    
    def __init__(
        self,
        min_pb: Optional[float] = None,
        max_pb: Optional[float] = None,
        name: str = "PBFilter",
        enabled: bool = True,
    ):
        super().__init__(name=name, enabled=enabled)
        self.min_pb = min_pb
        self.max_pb = max_pb
    
    def apply(self, stock: StockInfo, context: FilterContext) -> FilterOutput:
        """应用 PB 筛选"""
        pb = stock.pb_ratio
        
        if pb is None:
            return self._skip("PB数据缺失")
        
        if self.min_pb is not None and pb < self.min_pb:
            return self._fail(
                reason=f"PB {pb:.2f} < 最小值 {self.min_pb:.2f}",
                pb=pb,
                min_pb=self.min_pb
            )
        
        if self.max_pb is not None and pb > self.max_pb:
            return self._fail(
                reason=f"PB {pb:.2f} > 最大值 {self.max_pb:.2f}",
                pb=pb,
                max_pb=self.max_pb
            )
        
        return self._pass(
            reason=f"PB {pb:.2f} 在范围内",
            pb=pb
        )


class SectorFilter(Filter):
    """板块筛选器
    
    根据板块/行业筛选股票
    """
    
    def __init__(
        self,
        include_sectors: Optional[List[str]] = None,  # 包含的板块
        exclude_sectors: Optional[List[str]] = None,  # 排除的板块
        include_industries: Optional[List[str]] = None,  # 包含的行业
        exclude_industries: Optional[List[str]] = None,  # 排除的行业
        name: str = "SectorFilter",
        enabled: bool = True,
    ):
        super().__init__(name=name, enabled=enabled)
        self.include_sectors = include_sectors or []
        self.exclude_sectors = exclude_sectors or []
        self.include_industries = include_industries or []
        self.exclude_industries = exclude_industries or []
    
    def apply(self, stock: StockInfo, context: FilterContext) -> FilterOutput:
        """应用板块筛选"""
        sector = stock.sector or ""
        industry = stock.industry or ""
        
        # 检查板块排除
        if self.exclude_sectors and sector in self.exclude_sectors:
            return self._fail(
                reason=f"板块 '{sector}' 在排除列表中",
                sector=sector
            )
        
        # 检查行业排除
        if self.exclude_industries and industry in self.exclude_industries:
            return self._fail(
                reason=f"行业 '{industry}' 在排除列表中",
                industry=industry
            )
        
        # 检查板块包含
        if self.include_sectors and sector not in self.include_sectors:
            return self._fail(
                reason=f"板块 '{sector}' 不在包含列表中",
                sector=sector
            )
        
        # 检查行业包含
        if self.include_industries and industry not in self.include_industries:
            return self._fail(
                reason=f"行业 '{industry}' 不在包含列表中",
                industry=industry
            )
        
        return self._pass(
            reason=f"板块/行业符合条件",
            sector=sector,
            industry=industry
        )


class TurnoverRateFilter(Filter):
    """换手率筛选器
    
    根据换手率范围筛选股票
    """
    
    def __init__(
        self,
        min_rate: Optional[float] = None,  # 最小换手率（百分比）
        max_rate: Optional[float] = None,  # 最大换手率
        name: str = "TurnoverRateFilter",
        enabled: bool = True,
    ):
        super().__init__(name=name, enabled=enabled)
        self.min_rate = min_rate
        self.max_rate = max_rate
    
    def apply(self, stock: StockInfo, context: FilterContext) -> FilterOutput:
        """应用换手率筛选"""
        rate = stock.turnover_rate
        
        if rate is None:
            return self._skip("换手率数据缺失")
        
        if self.min_rate is not None and rate < self.min_rate:
            return self._fail(
                reason=f"换手率 {rate:.2f}% < 最小值 {self.min_rate:.2f}%",
                turnover_rate=rate,
                min_rate=self.min_rate
            )
        
        if self.max_rate is not None and rate > self.max_rate:
            return self._fail(
                reason=f"换手率 {rate:.2f}% > 最大值 {self.max_rate:.2f}%",
                turnover_rate=rate,
                max_rate=self.max_rate
            )
        
        return self._pass(
            reason=f"换手率 {rate:.2f}% 在范围内",
            turnover_rate=rate
        )


class VolumeFilter(Filter):
    """成交量筛选器
    
    根据成交量范围筛选股票
    """
    
    def __init__(
        self,
        min_volume: Optional[float] = None,  # 最小成交量
        max_volume: Optional[float] = None,  # 最大成交量
        name: str = "VolumeFilter",
        enabled: bool = True,
    ):
        super().__init__(name=name, enabled=enabled)
        self.min_volume = min_volume
        self.max_volume = max_volume
    
    def apply(self, stock: StockInfo, context: FilterContext) -> FilterOutput:
        """应用成交量筛选"""
        volume = stock.volume
        
        if volume is None:
            return self._skip("成交量数据缺失")
        
        if self.min_volume is not None and volume < self.min_volume:
            return self._fail(
                reason=f"成交量 {volume:.0f} < 最小值 {self.min_volume:.0f}",
                volume=volume,
                min_volume=self.min_volume
            )
        
        if self.max_volume is not None and volume > self.max_volume:
            return self._fail(
                reason=f"成交量 {volume:.0f} > 最大值 {self.max_volume:.0f}",
                volume=volume,
                max_volume=self.max_volume
            )
        
        return self._pass(
            reason=f"成交量 {volume:.0f} 在范围内",
            volume=volume
        )


class CustomFilter(Filter):
    """自定义筛选器
    
    允许用户传入自定义筛选函数
    """
    
    def __init__(
        self,
        filter_func: Callable[[StockInfo, FilterContext], FilterOutput],
        name: str = "CustomFilter",
        enabled: bool = True,
    ):
        """
        初始化自定义筛选器
        
        Args:
            filter_func: 筛选函数，接收 (StockInfo, FilterContext) 返回 FilterOutput
            name: 筛选器名称
            enabled: 是否启用
        """
        super().__init__(name=name, enabled=enabled)
        self._filter_func = filter_func
    
    def apply(self, stock: StockInfo, context: FilterContext) -> FilterOutput:
        """应用自定义筛选函数"""
        return self._filter_func(stock, context)


# ============================================================================
# 辅助函数
# ============================================================================

def create_default_filter_chain(
    use_ema_breakout: bool = True,
    use_market_cap: bool = False,
    use_pe: bool = False,
    min_market_cap: Optional[float] = None,
    max_market_cap: Optional[float] = None,
    min_pe: Optional[float] = None,
    max_pe: Optional[float] = None,
) -> FilterChain:
    """
    创建默认筛选器链
    
    Args:
        use_ema_breakout: 是否使用 EMA 突破筛选器
        use_market_cap: 是否使用市值筛选器
        use_pe: 是否使用 PE 筛选器
        min_market_cap: 最小市值
        max_market_cap: 最大市值
        min_pe: 最小 PE
        max_pe: 最大 PE
        
    Returns:
        FilterChain: 配置好的筛选器链
    """
    chain = FilterChain(mode="all", early_stop=False)
    
    if use_ema_breakout:
        chain.add_filter(EMABreakoutFilter())
    
    if use_market_cap:
        chain.add_filter(MarketCapFilter(min_cap=min_market_cap, max_cap=max_market_cap))
    
    if use_pe:
        chain.add_filter(PEFilter(min_pe=min_pe, max_pe=max_pe))
    
    return chain


def stocks_to_stock_infos(
    stocks: List[Dict],
    market: str,
    db: Optional[Any] = None,
) -> List[StockInfo]:
    """
    将股票字典列表转换为 StockInfo 列表
    
    Args:
        stocks: 股票字典列表 [{"code": "...", "name": "...", ...}, ...]
        market: 市场
        db: 数据库连接（用于获取板块信息）
        
    Returns:
        List[StockInfo]: StockInfo 列表
    """
    result = []
    for stock in stocks:
        info = StockInfo(
            market=market,
            code=stock.get("code", ""),
            name=stock.get("name", ""),
            sector=stock.get("sector"),
            industry=stock.get("industry"),
            market_cap=stock.get("market_cap"),
            pe_ratio=stock.get("pe_ratio"),
            pb_ratio=stock.get("pb_ratio"),
            turnover_rate=stock.get("turnover_rate"),
            volume=stock.get("volume"),
        )
        result.append(info)
    return result
