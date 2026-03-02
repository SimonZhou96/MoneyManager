#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
K 线数据获取器 - 工厂模式，支持多 timeframe、多数据源 fallback
数据在内存中使用，不做本地缓存和数据库存储。
"""

import pandas as pd
from datetime import date, timedelta
from abc import ABC, abstractmethod
from typing import Optional, List
import time
import random
import warnings
import sys
import os

from timeframe import (
    parse_timeframe, is_intraday,
    get_yf_period, get_akshare_min_period,
)


# ---------------------------------------------------------------------------
# 基类
# ---------------------------------------------------------------------------

class KlineFetcherBase(ABC):
    """K 线数据获取器基类"""

    @abstractmethod
    def fetch(
        self,
        stock_code: str,
        market: str = "HK",
        timeframe: str = "1d",
        max_count: int = 2000,
    ) -> Optional[pd.DataFrame]:
        """
        获取 K 线数据（内存 DataFrame）

        Args:
            stock_code: 股票代码（内部格式，如 HK.00700、000001.SZ）
            market: 市场 HK / US / A
            timeframe: 时间周期 1m~3mo
            max_count: 最大返回行数

        Returns:
            DataFrame(date, open, high, low, close, volume, ...) 或 None
        """
        pass

    @abstractmethod
    def get_name(self) -> str:
        """返回数据源名称"""
        pass


# ---------------------------------------------------------------------------
# 通用标准化
# ---------------------------------------------------------------------------

def _normalize_dataframe(df: pd.DataFrame) -> Optional[pd.DataFrame]:
    """将各数据源返回的 DataFrame 标准化为统一格式"""
    if df is None or df.empty:
        return None

    # 列名映射
    col_map = {
        "日期": "date", "时间": "date", "time_key": "date", "time": "date",
        "datetime": "date", "Datetime": "date",
        "开盘": "open", "收盘": "close", "最高": "high", "最低": "low",
        "开盘价": "open", "收盘价": "close", "最高价": "high", "最低价": "low",
        "成交量": "volume", "成交额": "turnover", "换手率": "turnover_rate",
        "涨跌幅": "change_rate",
    }
    df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})

    # 尝试从 index 获取 date
    if "date" not in df.columns:
        if isinstance(df.index, pd.DatetimeIndex):
            df = df.reset_index()
            df = df.rename(columns={df.columns[0]: "date"})
        elif "Date" in df.columns:
            df = df.rename(columns={"Date": "date"})

    if "date" not in df.columns:
        return None

    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df[df["date"].notna()]

    required = ["open", "high", "low", "close"]
    if any(c not in df.columns for c in required):
        return None

    return df.sort_values("date").reset_index(drop=True)


# ---------------------------------------------------------------------------
# YFinance
# ---------------------------------------------------------------------------

class YFinanceKlineFetcher(KlineFetcherBase):
    """YFinance K 线获取器 - 支持全 timeframe、港股 / 美股 / A 股"""

    def __init__(self):
        try:
            import yfinance as yf
            self.yf = yf
        except ImportError:
            raise ImportError("请安装 yfinance: pip install yfinance")

    def get_name(self) -> str:
        return "YFinance"
    
    @staticmethod
    def _suppress_yfinance_warnings():
        """抑制 yfinance 的警告信息"""
        warnings.filterwarnings('ignore', category=FutureWarning)
        warnings.filterwarnings('ignore', message='.*possibly delisted.*')
        # 重定向 stderr 到 devnull
        class DevNull:
            def write(self, msg):
                pass
            def flush(self):
                pass
        return DevNull()

    @staticmethod
    def _to_yf_code(stock_code: str, market: str) -> str:
        market = market.upper()
        code = str(stock_code).strip()
        if market == "HK":
            if code.startswith("HK."):
                code = code[3:]
            return f"{code.zfill(5)}.HK"
        if market == "A":
            if "." in code:
                return code  # 600000.SS / 000001.SZ
            if code.isdigit() and len(code) == 6:
                return f"{code}.SS" if code.startswith("6") else f"{code}.SZ"
            return code
        # US
        if code.upper().startswith("US."):
            return code[3:]
        return code

    def fetch(
        self,
        stock_code: str,
        market: str = "HK",
        timeframe: str = "1d",
        max_count: int = 2000,
    ) -> Optional[pd.DataFrame]:
        try:
            yf_code = self._to_yf_code(stock_code, market)
            period = get_yf_period(timeframe)
            
            # 抑制 yfinance 的警告信息
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                old_stderr = sys.stderr
                sys.stderr = self._suppress_yfinance_warnings()
                
                try:
                    data = self.yf.download(
                        yf_code,
                        period=period,
                        interval=timeframe,
                        auto_adjust=True,
                        progress=False,
                    )
                finally:
                    sys.stderr = old_stderr
            
            if data is None or data.empty:
                return None

            # yfinance >= 0.2.31 returns MultiIndex columns: (Price, Ticker)
            # Flatten to single level, keeping only the price name
            if isinstance(data.columns, pd.MultiIndex):
                data.columns = [c[0] if isinstance(c, tuple) else c for c in data.columns]

            data = data.reset_index()
            data.columns = [str(c).lower() for c in data.columns]

            df = _normalize_dataframe(data)
            if df is None:
                return None
            return df.tail(max_count).reset_index(drop=True)
        except Exception:
            return None


# ---------------------------------------------------------------------------
# AKShare
# ---------------------------------------------------------------------------

class AKShareKlineFetcher(KlineFetcherBase):
    """AKShare K 线获取器 - 港股 / 美股日线 + A 股日线 / 分钟线"""

    def __init__(self):
        try:
            import akshare as ak
            self.ak = ak
        except ImportError:
            raise ImportError("请安装 AKShare: pip install akshare")

    def get_name(self) -> str:
        return "AKShare"

    # -- 代码转换 --

    @staticmethod
    def _hk_code(stock_code: str) -> str:
        code = stock_code[3:] if stock_code.startswith("HK.") else stock_code
        return code.zfill(5) if code.isdigit() else code

    @staticmethod
    def _us_code(stock_code: str) -> str:
        code = stock_code.strip()
        return code[3:] if code.upper().startswith("US.") else code

    @staticmethod
    def _a_code(stock_code: str) -> str:
        code = stock_code.strip()
        if "." in code:
            code = code.split(".")[0]
        return code.zfill(6) if code.isdigit() else code

    # -- 日线 --

    def _fetch_a_daily(self, code: str, start: str, end: str) -> Optional[pd.DataFrame]:
        if not hasattr(self.ak, "stock_zh_a_hist"):
            return None
        try:
            df = self.ak.stock_zh_a_hist(
                symbol=code, period="daily",
                start_date=start, end_date=end, adjust="qfq",
            )
            return _normalize_dataframe(df)
        except Exception:
            return None

    def _fetch_hk_daily(self, code: str, start: str, end: str) -> Optional[pd.DataFrame]:
        for method_name in ("stock_hk_daily", "stock_hk_hist"):
            if not hasattr(self.ak, method_name):
                continue
            try:
                if method_name == "stock_hk_daily":
                    df = self.ak.stock_hk_daily(symbol=code, adjust="qfq")
                else:
                    df = self.ak.stock_hk_hist(
                        symbol=code, period="daily",
                        start_date=start, end_date=end, adjust="qfq",
                    )
                result = _normalize_dataframe(df)
                if result is not None and len(result) > 0:
                    return result
            except Exception:
                continue
            time.sleep(0.5 + random.uniform(0, 0.5))
        return None

    def _fetch_us_daily(self, code: str, start: str, end: str) -> Optional[pd.DataFrame]:
        for method_name in ("stock_us_daily", "stock_us_hist"):
            if not hasattr(self.ak, method_name):
                continue
            try:
                if method_name == "stock_us_daily":
                    df = self.ak.stock_us_daily(symbol=code, adjust="qfq")
                else:
                    df = self.ak.stock_us_hist(
                        symbol=code, period="daily",
                        start_date=start, end_date=end, adjust="qfq",
                    )
                result = _normalize_dataframe(df)
                if result is not None and len(result) > 0:
                    return result
            except Exception:
                continue
        return None

    # -- A 股分钟线 --

    def _fetch_a_min(self, code: str, ak_period: str) -> Optional[pd.DataFrame]:
        """使用 stock_zh_a_hist_min_em 获取 A 股分钟线"""
        if not hasattr(self.ak, "stock_zh_a_hist_min_em"):
            return None
        try:
            df = self.ak.stock_zh_a_hist_min_em(
                symbol=code, period=ak_period, adjust="qfq",
            )
            return _normalize_dataframe(df)
        except Exception:
            return None

    # -- 公共入口 --

    def fetch(
        self,
        stock_code: str,
        market: str = "HK",
        timeframe: str = "1d",
        max_count: int = 2000,
    ) -> Optional[pd.DataFrame]:
        market = market.upper()
        end_str = date.today().strftime("%Y%m%d")
        start_str = (date.today() - timedelta(days=365 * 5)).strftime("%Y%m%d")

        # A 股分钟线
        ak_min = get_akshare_min_period(timeframe)
        if market == "A" and ak_min is not None:
            code = self._a_code(stock_code)
            df = self._fetch_a_min(code, ak_min)
            if df is not None:
                return df.tail(max_count).reset_index(drop=True)
            return None

        # 非日线 + 非 A 股分钟 -> AKShare 不支持，返回 None 让 fallback 处理
        if timeframe != "1d":
            # TODO: AKShare 港股/美股分钟线支持有限，暂不实现
            return None

        # 日线
        if market == "A":
            code = self._a_code(stock_code)
            df = self._fetch_a_daily(code, start_str, end_str)
        elif market == "HK":
            code = self._hk_code(stock_code)
            df = self._fetch_hk_daily(code, start_str, end_str)
        else:
            code = self._us_code(stock_code)
            df = self._fetch_us_daily(code, start_str, end_str)

        if df is None:
            return None
        return df.tail(max_count).reset_index(drop=True)


# ---------------------------------------------------------------------------
# Futu
# ---------------------------------------------------------------------------

class FutuKlineFetcher(KlineFetcherBase):
    """富途 OpenAPI K 线获取器"""

    # timeframe -> futu KLType 枚举名
    _KL_MAP = {
        "1m": "K_1M", "3m": "K_3M", "5m": "K_5M",
        "15m": "K_15M", "30m": "K_30M", "60m": "K_60M",
        "1h": "K_60M",
        "1d": "K_DAY", "1wk": "K_WEEK", "1mo": "K_MON",
        "3mo": "K_QUARTER",
    }

    def __init__(self, quote_ctx, rate_limiter=None):
        self.quote_ctx = quote_ctx
        self.rate_limiter = rate_limiter

    def get_name(self) -> str:
        return "FutuOpenAPI"

    @staticmethod
    def _to_futu_code(stock_code: str, market: str) -> str:
        market = market.upper()
        code = stock_code.strip()
        if market == "HK":
            if code.startswith("HK."):
                return code
            if code.isdigit():
                return f"HK.{code.zfill(5)}"
        elif market == "US":
            if code.startswith("US."):
                return code
            return f"US.{code}"
        elif market == "A":
            if code.endswith(".SS"):
                return f"SH.{code[:-3]}"
            if code.endswith(".SZ"):
                return f"SZ.{code[:-3]}"
            if code.isdigit() and len(code) == 6:
                return f"SH.{code}" if code.startswith("6") else f"SZ.{code}"
        return code

    def _get_kl_type(self, timeframe: str):
        import futu as ft
        name = self._KL_MAP.get(timeframe)
        if name is None:
            return ft.KLType.K_DAY
        return getattr(ft.KLType, name, ft.KLType.K_DAY)

    @staticmethod
    def _estimate_days_back(timeframe: str, max_count: int) -> int:
        """估算 max_count 根 K 线需要的自然日（含节假日余量），用于限制 start 减少分页量"""
        if timeframe in ("1m", "3m", "5m"):
            bars_per_day = 240 if timeframe == "1m" else 80 if timeframe == "3m" else 48
        elif timeframe in ("15m", "30m"):
            bars_per_day = 16 if timeframe == "15m" else 8
        elif timeframe in ("60m", "1h"):
            bars_per_day = 4
        elif timeframe == "1d":
            bars_per_day = 1
        elif timeframe == "1wk":
            return int(max_count * 5 * 1.2) + 30  # 每周 1 根
        elif timeframe in ("1mo", "3mo"):
            return int(max_count * 22 * 1.2) + 60  # 每月约 22 交易日
        else:
            bars_per_day = 1
        days = int(max_count / bars_per_day * 1.25) + 10  # 25% 余量
        return max(60, min(days, 365 * 5))  # 至少 60 天，最多 5 年

    def fetch(
        self,
        stock_code: str,
        market: str = "HK",
        timeframe: str = "1d",
        max_count: int = 500,
    ) -> Optional[pd.DataFrame]:
        import futu as ft
        from datetime import datetime, timedelta

        if self.rate_limiter:
            self.rate_limiter.wait_if_needed()

        try:
            code = self._to_futu_code(stock_code, market)
            kl_type = self._get_kl_type(timeframe)
            end_date = datetime.now().strftime('%Y-%m-%d')
            days_back = self._estimate_days_back(timeframe, max_count)
            start_date = (datetime.now() - timedelta(days=days_back)).strftime('%Y-%m-%d')

            # 分页获取：显式 start 限制范围，减少分页次数；分页拉全量后取最新 max_count 条
            # 参考 https://github.com/FutunnOpen/py-futu-api/issues/145
            page_size = 1000
            all_data: List[pd.DataFrame] = []
            page_req_key = None

            while True:
                ret, data, page_req_key = self.quote_ctx.request_history_kline(
                    code=code,
                    start=start_date,
                    end=end_date,
                    ktype=kl_type,
                    max_count=page_size,
                    page_req_key=page_req_key,
                    autype=ft.AuType.QFQ,
                )
                if ret != ft.RET_OK or data is None:
                    break
                if data.empty:
                    break
                all_data.append(data)
                if page_req_key is None or len(data) < page_size:
                    break

            if not all_data:
                return None

            data = pd.concat(all_data, ignore_index=True)
            if "time_key" in data.columns:
                data = data.rename(columns={"time_key": "date"})
            data["date"] = pd.to_datetime(data["date"])
            data = data.sort_values("date").drop_duplicates(subset=["date"]).reset_index(drop=True)
            return data.tail(max_count).reset_index(drop=True)
        except Exception:
            return None


# ---------------------------------------------------------------------------
# 工厂
# ---------------------------------------------------------------------------

class KlineFetcherFactory:
    """K 线获取器工厂 - 按优先级创建 fetcher 链"""

    @staticmethod
    def create_fetcher_chain(
        quote_ctx=None,
        rate_limiter=None,
    ) -> List[KlineFetcherBase]:
        """
        创建获取器链，优先级：YFinance > AKShare > Futu
        （YFinance 对全 timeframe 支持最好，放首位）
        """
        fetchers: List[KlineFetcherBase] = []

        # 1. YFinance（全 timeframe）
        try:
            fetchers.append(YFinanceKlineFetcher())
        except ImportError:
            pass

        # 2. AKShare（日线 + A 股分钟线）
        try:
            fetchers.append(AKShareKlineFetcher())
        except ImportError:
            pass

        # 3. Futu（需要 OpenD）
        if quote_ctx is not None:
            try:
                fetchers.append(FutuKlineFetcher(quote_ctx, rate_limiter))
            except Exception:
                pass

        return fetchers
