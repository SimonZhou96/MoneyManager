#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
基本面补全器 - 单股循环中补全 market_cap、pe_ratio、sector、industry

支持数据源：
- yfinance: Ticker.info 获取 marketCap、trailingPE、sector、industry
"""

import time
from abc import ABC, abstractmethod
from typing import Any, List, Optional, Union


def _get_fundamental(obj: Any, key: str) -> Optional[Any]:
    """统一获取 dict 或 object 的属性"""
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def _set_fundamental(obj: Any, key: str, value: Any) -> None:
    """统一设置 dict 或 object 的属性"""
    if isinstance(obj, dict):
        obj[key] = value
    else:
        setattr(obj, key, value)


class FundamentalFetcherBase(ABC):
    """基本面补全器基类"""

    @abstractmethod
    def enrich(
        self,
        stock: Union[dict, Any],
        market: str,
        code: Optional[str] = None,
    ) -> bool:
        """
        补全单只股票的基本面信息（market_cap、pe_ratio、sector、industry）。
        仅对为 None 的字段补全，不覆盖已有值。

        Args:
            stock: 股票对象（dict 或 StockInfo），会被就地更新
            market: 市场 HK/US/A
            code: 股票代码（若 stock 为 dict 则从 stock["code"] 取，此处可覆盖）

        Returns:
            是否成功补全了至少一个字段
        """
        pass

    @abstractmethod
    def get_name(self) -> str:
        """返回数据源名称"""
        pass


class YFinanceFundamentalFetcher(FundamentalFetcherBase):
    """yfinance 基本面补全器 - 从 Ticker.info 获取 marketCap、trailingPE、sector、industry"""

    def __init__(self, sleep_seconds: float = 0.2):
        self.sleep_seconds = sleep_seconds
        self._yf = None

    def _get_yf(self):
        if self._yf is None:
            import yfinance as yf
            self._yf = yf
        return self._yf

    @staticmethod
    def _to_yf_symbol(code: str, market: str) -> Optional[str]:
        market = str(market).upper()
        code = str(code).strip()
        if not code:
            return None
        if market == "HK":
            if code.startswith("HK."):
                code = code[3:]
            if code.isdigit():
                return f"{code.zfill(5)}.HK"
            return None
        if market == "US":
            if code.upper().startswith("US."):
                code = code[3:]
            if "." in code:
                suffix = code.split(".")[-1]
                if suffix.isalpha():
                    code = suffix
            return code.upper() if code else None
        if market == "A":
            if "." in code:
                return code  # 600000.SS / 000001.SZ
            if code.isdigit() and len(code) == 6:
                return f"{code}.SS" if code.startswith("6") else f"{code}.SZ"
            return code
        return None

    def enrich(
        self,
        stock: Union[dict, Any],
        market: str,
        code: Optional[str] = None,
    ) -> bool:
        market = str(market).upper()
        if market not in ("HK", "US", "A"):
            return False

        need_any = (
            _get_fundamental(stock, "market_cap") is None
            or _get_fundamental(stock, "pe_ratio") is None
            or _get_fundamental(stock, "sector") is None
            or _get_fundamental(stock, "industry") is None
        )
        if not need_any:
            return False

        sym_code = code or _get_fundamental(stock, "code") or ""
        yf_symbol = self._to_yf_symbol(sym_code, market)
        if not yf_symbol:
            return False

        try:
            yf = self._get_yf()
            info = yf.Ticker(yf_symbol).info
            if not info or not isinstance(info, dict):
                return False

            updated = False

            if _get_fundamental(stock, "market_cap") is None:
                mc = info.get("marketCap")
                if mc is not None and isinstance(mc, (int, float)):
                    val = float(mc)
                    if val == val and abs(val) != float("inf"):
                        _set_fundamental(stock, "market_cap", val)
                        updated = True

            if _get_fundamental(stock, "pe_ratio") is None:
                pe = info.get("trailingPE") or info.get("forwardPE")
                if pe is not None and isinstance(pe, (int, float)):
                    val = float(pe)
                    if val == val and abs(val) != float("inf"):
                        _set_fundamental(stock, "pe_ratio", val)
                        updated = True

            if _get_fundamental(stock, "sector") is None:
                sector = info.get("sector") or info.get("sectorDisp")
                if sector and isinstance(sector, str) and sector.strip():
                    _set_fundamental(stock, "sector", sector.strip())
                    updated = True

            if _get_fundamental(stock, "industry") is None:
                industry = info.get("industry") or info.get("industryDisp")
                if industry and isinstance(industry, str) and industry.strip():
                    _set_fundamental(stock, "industry", industry.strip())
                    updated = True

            time.sleep(self.sleep_seconds)
            return updated
        except Exception:
            return False

    def get_name(self) -> str:
        return "YFinance"


class FundamentalFetcherFactory:
    """基本面补全器工厂 - 创建 fetcher 链"""

    @staticmethod
    def create_chain(
        sleep_seconds: float = 0.2,
    ) -> List[FundamentalFetcherBase]:
        """
        创建基本面补全器链，按优先级排列。
        当前仅 YFinance，可扩展 Futu get_market_snapshot 等。
        """
        fetchers: List[FundamentalFetcherBase] = []
        try:
            fetchers.append(YFinanceFundamentalFetcher(sleep_seconds=sleep_seconds))
        except ImportError:
            pass
        return fetchers
