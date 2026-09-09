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
import traceback

try:
    from .timeframe import (
        parse_timeframe, is_intraday,
        get_yf_period, get_akshare_min_period,
    )
except ImportError:
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


def _log_fetch_warning(source: str, action: str, error: Exception) -> None:
    """对预期网络失败输出简短日志，避免刷整屏 traceback。"""
    print(f"[{source}] {action} failed: {error}", file=sys.stderr)


# ---------------------------------------------------------------------------
# YFinance
# ---------------------------------------------------------------------------

class YFinanceKlineFetcher(KlineFetcherBase):
    """YFinance K 线获取器 - 支持全 timeframe、港股 / 美股 / A 股"""

    def __init__(self, session=None, owns_session: Optional[bool] = None):
        try:
            import yfinance as yf
            self.yf = yf
        except ImportError:
            raise ImportError("请安装 yfinance: pip install yfinance")
        self._session = session
        self._owns_session = session is None if owns_session is None else bool(owns_session)

    def _get_session(self):
        if self._session is None:
            # yfinance.download() otherwise creates a new curl_cffi session on
            # every single-symbol request and never closes the replaced session.
            from yfinance._http import new_session
            self._session = new_session()
            self._owns_session = True
        return self._session

    def _renew_owned_session(self) -> None:
        if not self._owns_session:
            return
        self.close()
        self._owns_session = True

    def close(self) -> None:
        """Release the HTTP session when this fetcher created it."""
        session = self._session
        self._session = None
        if self._owns_session and session is not None:
            close = getattr(session, "close", None)
            if callable(close):
                close()

    def get_name(self) -> str:
        return "YFinance"
    
    @staticmethod
    def _suppress_yfinance_warnings():
        """抑制 yfinance 的警告信息"""
        warnings.filterwarnings('ignore', category=FutureWarning)
        warnings.filterwarnings('ignore', message='.*possibly delisted.*')

    @staticmethod
    def _to_yf_code(stock_code: str, market: str) -> str:
        market = market.upper()
        code = str(stock_code).strip()
        if market == "HK":
            if code.startswith("HK."):
                code = code[3:]
            if not code.isdigit():
                return code  # 非数字代码（如 AAM.UT），原样返回避免崩溃
            return f"{str(int(code)).zfill(4)}.HK"
        if market == "A":
            # 已是 yahoo 格式 000001.SZ / 600000.SS，直接返回（必须在 prefix 剥离之前判断）
            if code.endswith((".SS", ".SZ")):
                return code
            # Futu 格式 SH.601398 / SZ.000001 -> 601398.SS / 000001.SZ
            if code.upper().startswith("SH."):
                return f"{code[3:]}.SS"
            if code.upper().startswith("SZ."):
                return f"{code[3:]}.SZ"
            if code.isdigit() and len(code) == 6:
                return f"{code}.SS" if code.startswith(("5", "6", "9")) else f"{code}.SZ"
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
                from yf_ratelimit import (
                    retry_on_rate_limit, _is_crumb_error, reset_yf_session,
                )

                @retry_on_rate_limit
                def _download():
                    return self.yf.download(
                        yf_code,
                        period=period,
                        interval=timeframe,
                        auto_adjust=True,
                        progress=False,
                        # Each fetch() handles one symbol; worker threads add
                        # no throughput but do allocate extra descriptors.
                        threads=False,
                        session=self._get_session(),
                    )

                self._suppress_yfinance_warnings()
                try:
                    data = _download()
                except Exception as dl_exc:
                    # crumb 过期 → 重置 YfData 单例后重试一次
                    if _is_crumb_error(dl_exc):
                        reset_yf_session()
                        self._renew_owned_session()
                        data = _download()
                    else:
                        raise

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
            df = df.sort_values("date").drop_duplicates(subset=["date"]).reset_index(drop=True)
            return df.tail(max_count).reset_index(drop=True)
        except Exception as e:
            print(f"[YFinance] fetch failed: code={stock_code} market={market} timeframe={timeframe} error={e}", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)
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
        """Futu SH.601398/SZ.000001 -> 601398；600000.SS -> 600000"""
        code = stock_code.strip()
        if code.upper().startswith(("SH.", "SZ.")):
            code = code[3:]  # SH.601398 -> 601398
        elif "." in code:
            code = code.split(".")[0]  # 600000.SS -> 600000
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
        except Exception as e:
            _log_fetch_warning("AKShare", f"_fetch_a_daily code={code}", e)
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
            except Exception as e:
                _log_fetch_warning("AKShare", f"_fetch_hk_daily {method_name} code={code}", e)
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
            except Exception as e:
                _log_fetch_warning("AKShare", f"_fetch_us_daily {method_name} code={code}", e)
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
        except Exception as e:
            _log_fetch_warning("AKShare", f"_fetch_a_min code={code} period={ak_period}", e)
            return None

    # -- 公共入口 --

    def fetch(
        self,
        stock_code: str,
        market: str = "HK",
        timeframe: str = "1d",
        max_count: int = 2000,
    ) -> Optional[pd.DataFrame]:
        try:
            market = market.upper()
            end_str = date.today().strftime("%Y%m%d")
            start_str = (date.today() - timedelta(days=365 * 5)).strftime("%Y%m%d")

            # A 股分钟线
            ak_min = get_akshare_min_period(timeframe)
            if market == "A" and ak_min is not None:
                code = self._a_code(stock_code)
                df = self._fetch_a_min(code, ak_period=ak_min)
                if df is not None:
                    return df.tail(max_count).reset_index(drop=True)
                return None

            # 非日线 + 非 A 股分钟 -> AKShare 不支持，返回 None 让 fallback 处理
            if timeframe != "1d":
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
        except Exception as e:
            _log_fetch_warning("AKShare", f"fetch code={stock_code} market={market} timeframe={timeframe}", e)
            return None


# ---------------------------------------------------------------------------
# Database cache
# ---------------------------------------------------------------------------

class DatabaseKlineFetcher(KlineFetcherBase):
    """K 线缓存获取器：优先读取本地 Agent 推送到 MySQL 的缓存。

    新增 freshness 检查：如果缓存中最新的 K 线距今超过
    max_staleness_days 天，拒绝返回，让链降级到实时数据源。
    """

    def __init__(self, db, min_rows: int = 20, max_staleness_days: int = 4):
        self.db = db
        self.min_rows = max(1, int(min_rows))
        self.max_staleness_days = max(1, int(max_staleness_days))

    def get_name(self) -> str:
        return "DatabaseKlineCache"

    def fetch(
        self,
        stock_code: str,
        market: str = "HK",
        timeframe: str = "1d",
        max_count: int = 2000,
    ) -> Optional[pd.DataFrame]:
        try:
            df = self.db.get_kline_cache(market=market, code=stock_code, timeframe=timeframe, max_count=max_count)
            if df is None or df.empty or len(df) < self.min_rows:
                return None
            normalized = _normalize_dataframe(df)
            if normalized is None or normalized.empty:
                return None

            # ── 数据新鲜度检查 ──
            # 如果缓存中最新 bar 的日期距今超过 max_staleness_days 天，
            # 说明 Agent 已停止推送或者这只股票的数据更新延迟了。
            # 此时返回 None，让链降级到 YFinance/AKShare 等实时数据源。
            from datetime import date, timedelta
            try:
                latest_date = normalized["date"].max()
                if hasattr(latest_date, "date"):
                    latest_date = latest_date.date()
                elif hasattr(latest_date, "to_pydatetime"):
                    latest_date = latest_date.to_pydatetime().date()
                cutoff = date.today() - timedelta(days=self.max_staleness_days)
                if latest_date < cutoff:
                    print(
                        f"[DatabaseKlineCache] stale cache for {stock_code}: "
                        f"latest={latest_date} > {self.max_staleness_days}d old, "
                        f"falling through to live source",
                        file=sys.stderr,
                    )
                    return None
            except Exception:
                pass  # 日期解析失败时保守放行

            return normalized.tail(max_count).reset_index(drop=True)
        except Exception as e:
            _log_fetch_warning("DatabaseKlineCache", f"fetch code={stock_code} market={market} timeframe={timeframe}", e)
            return None


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
            if code.upper().startswith(("SH.", "SZ.")):
                return code  # 已是 Futu 格式
            if code.endswith(".SS"):
                return f"SH.{code[:-3]}"
            if code.endswith(".SZ"):
                return f"SZ.{code[:-3]}"
            if code.isdigit() and len(code) == 6:
                return f"SH.{code}" if code.startswith(("5", "6", "9")) else f"SZ.{code}"
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
        except Exception as e:
            print(f"[FutuOpenAPI] fetch failed: code={stock_code} market={market} timeframe={timeframe} error={e}", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)
            return None


# ---------------------------------------------------------------------------
# Futu OpenD 自动连接（通过环境变量配置，每次 fetch 创建临时连接）
# ---------------------------------------------------------------------------

class OpenDQuotedKlineFetcher(KlineFetcherBase):
    """通过环境变量自动连接 Futu OpenD 的 K 线获取器。

    与 FutuKlineFetcher 不同，此类自行管理 OpenD 连接生命周期：
    每次 fetch() 时创建临时 quote_ctx，用完即释放。
    适用于 web 后端多请求并发场景（每个请求独立连接，避免连接池耗尽）。

    配置：
        FUTU_OPEN_HOST — OpenD 主机地址（默认不设置，不启用）
        FUTU_OPEN_PORT — OpenD 端口（默认 11111）

    安全提示：
        OpenD 默认仅监听 127.0.0.1。如需远程访问，应通过 SSH tunnel，
        不要将 OpenD 直接暴露在公网上。
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 11111, rate_limiter=None):
        self.host = host
        self.port = port
        self.rate_limiter = rate_limiter

    def get_name(self) -> str:
        return f"FutuOpenD({self.host}:{self.port})"

    def fetch(
        self,
        stock_code: str,
        market: str = "HK",
        timeframe: str = "1d",
        max_count: int = 2000,
    ) -> Optional[pd.DataFrame]:
        import futu as ft
        quote_ctx = None
        try:
            quote_ctx = ft.OpenQuoteContext(host=self.host, port=self.port)
            inner = FutuKlineFetcher(quote_ctx, self.rate_limiter)
            return inner.fetch(stock_code, market=market, timeframe=timeframe, max_count=max_count)
        except Exception as e:
            _log_fetch_warning(f"FutuOpenD({self.host}:{self.port})", f"fetch code={stock_code}", e)
            return None
        finally:
            if quote_ctx is not None:
                try:
                    quote_ctx.close()
                except Exception:
                    pass


# ---------------------------------------------------------------------------
# 工厂
# ---------------------------------------------------------------------------

class KlineFetcherFactory:
    """K 线获取器工厂 - 按优先级创建 fetcher 链"""

    @staticmethod
    def _env_enabled(name: str, default: bool = False) -> bool:
        value = os.getenv(name)
        if value is None:
            return default
        return value.strip().lower() in {"1", "true", "yes", "on"}

    @staticmethod
    def create_fetcher_chain(
        quote_ctx=None,
        db=None,
        rate_limiter=None,
        skip_db_cache: bool = False,
    ) -> List[KlineFetcherBase]:
        """
        创建获取器链。
        默认优先级：
        - DB 缓存 > OpenD(如有) > YFinance > AKShare

        Args:
            skip_db_cache: True 时跳过 DatabaseKlineFetcher（调用方已自行查过 DB 缓存时使用，
                           避免同一 (market, code, timeframe) 被查两次）
        """
        fetchers: List[KlineFetcherBase] = []
        disable_akshare = KlineFetcherFactory._env_enabled("KLINE_DISABLE_AKSHARE", default=False)

        # 1. Database cache（云端优先使用本地 Agent 推送的 OpenD 缓存）
        if db is not None and not skip_db_cache:
            try:
                fetchers.append(DatabaseKlineFetcher(db))
            except Exception:
                pass

        # 2. Futu OpenD：显式传入 quote_ctx 或通过 FUTU_OPEN_HOST 环境变量自动连接
        if quote_ctx is not None:
            try:
                fetchers.append(FutuKlineFetcher(quote_ctx, rate_limiter))
            except Exception:
                pass
        else:
            futu_host = os.getenv("FUTU_OPEN_HOST", "").strip()
            if futu_host:
                futu_port = int(os.getenv("FUTU_OPEN_PORT", "11111") or "11111")
                try:
                    fetchers.append(OpenDQuotedKlineFetcher(
                        host=futu_host, port=futu_port, rate_limiter=rate_limiter,
                    ))
                except Exception:
                    pass

        # 3. YFinance（全 timeframe）
        try:
            fetchers.append(YFinanceKlineFetcher())
        except ImportError:
            pass

        # 4. AKShare（日线 + A 股分钟线）。可通过 .env 禁用，避免 DNS/外站问题刷屏。
        if not disable_akshare:
            try:
                fetchers.append(AKShareKlineFetcher())
            except ImportError:
                pass

        return fetchers
