#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""宏观因子数据提供者 — 封装 akshare + yfinance 的数据获取

每个 fetch 方法使用 try/except 独立容错，单个因子失败不影响其他因子。
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import List, Optional

from .models import MacroFactorValue, MacroSnapshot

logger = logging.getLogger(__name__)

# akshare 宏观函数的列名统一为 "今值"（当前值），不是英文 "latest"
_AKSHARE_VAL_COL = "今值"


# ---------------------------------------------------------------------------
# 抽象基类
# ---------------------------------------------------------------------------


class MacroDataProvider(ABC):
    """宏观数据提供者基类"""

    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def markets(self) -> List[str]: ...

    @abstractmethod
    def fetch(self, market: str) -> MacroSnapshot:
        """获取指定市场的宏观因子快照"""


# ---------------------------------------------------------------------------
# AKShare 中国宏观数据提供者
# ---------------------------------------------------------------------------


class ChinaMacroProvider(MacroDataProvider):
    """使用 akshare 获取中国宏观数据（A/HK 市场）"""

    @property
    def name(self) -> str:
        return "akshare_china_macro"

    @property
    def markets(self) -> List[str]:
        return ["A", "HK"]

    def fetch(self, market: str) -> MacroSnapshot:
        snapshot = MacroSnapshot(market=market, as_of=datetime.now(timezone.utc).isoformat())
        snapshot.provider_status[self.name] = "ok"

        try:
            import akshare as ak  # noqa: F811
        except ImportError:
            snapshot.provider_status[self.name] = "error: akshare not installed"
            snapshot.data_gaps.append("akshare 库未安装")
            return snapshot

        # 逐个拉取，独立容错
        self._fetch_cpi(snapshot, ak)
        self._fetch_pmi(snapshot, ak)
        self._fetch_caixin_pmi(snapshot, ak)
        self._fetch_m2(snapshot, ak)
        self._fetch_lpr(snapshot, ak)
        self._fetch_gdp(snapshot, ak)
        self._fetch_industrial_production(snapshot, ak)
        self._fetch_social_financing(snapshot, ak)
        self._fetch_bond_yields(snapshot, ak)
        self._fetch_shanghai_composite(snapshot, ak)

        return snapshot

    # ---- helpers ----

    def _add(self, snapshot: MacroSnapshot, key: str, name: str,
             value: Optional[float] = None, unit: str = "", trend: str = "",
             status: str = "ok"):
        snapshot.factors.append(MacroFactorValue(
            factor_key=key, factor_name=name,
            value=value, unit=unit, trend=trend,
            source=self.name, status=status,
        ))

    def _last_val(self, df, col: str = _AKSHARE_VAL_COL) -> Optional[float]:
        """从 DataFrame 获取最后一行的数值"""
        if df is None or df.empty:
            return None
        try:
            raw = df[col].dropna()
            if raw.empty:
                return None
            return round(float(raw.iloc[-1]), 4)
        except Exception:
            return None

    def _compute_trend(self, df, col: str = _AKSHARE_VAL_COL) -> str:
        """根据最近两期数据判断趋势"""
        try:
            series = df[col].dropna()
            if len(series) < 2:
                return ""
            a, b = float(series.iloc[-2]), float(series.iloc[-1])
            return "rising" if b > a else "falling" if b < a else "stable"
        except Exception:
            return ""

    # ---- 因子抓取方法 ----

    def _fetch_cpi(self, snapshot: MacroSnapshot, ak):
        try:
            df = ak.macro_china_cpi_yearly()
            val = self._last_val(df)
            snapshot.cpi_yoy = val
            trend = self._compute_trend(df)
            snapshot.cpi_trend = trend
            self._add(snapshot, "cpi_yoy", "CPI 同比", value=val, unit="%",
                      trend=trend, status="ok" if val is not None else "missing")
        except Exception as e:
            logger.warning(f"CPI 获取失败: {e}")
            self._add(snapshot, "cpi_yoy", "CPI 同比", status="error")

    def _fetch_pmi(self, snapshot: MacroSnapshot, ak):
        try:
            df = ak.macro_china_pmi_yearly()
            val = self._last_val(df)
            snapshot.pmi_manufacturing = val
            self._add(snapshot, "pmi_manufacturing", "制造业 PMI",
                      value=val, status="ok" if val is not None else "missing")
        except Exception as e:
            logger.warning(f"PMI 获取失败: {e}")
            self._add(snapshot, "pmi_manufacturing", "制造业 PMI", status="error")

    def _fetch_caixin_pmi(self, snapshot: MacroSnapshot, ak):
        try:
            df = ak.macro_china_cx_pmi_yearly()
            val = self._last_val(df)
            snapshot.pmi_services = val
            self._add(snapshot, "pmi_services", "财新制造业 PMI",
                      value=val, status="ok" if val is not None else "missing")
        except Exception as e:
            logger.warning(f"财新 PMI 获取失败: {e}")
            self._add(snapshot, "pmi_services", "财新制造业 PMI", status="error")

    def _fetch_m2(self, snapshot: MacroSnapshot, ak):
        try:
            df = ak.macro_china_m2_yearly()
            val = self._last_val(df)
            snapshot.m2_yoy = val
            trend = self._compute_trend(df)
            self._add(snapshot, "m2_yoy", "M2 同比增速", value=val,
                      unit="%", trend=trend, status="ok" if val is not None else "missing")
        except Exception as e:
            logger.warning(f"M2 获取失败: {e}")
            self._add(snapshot, "m2_yoy", "M2 同比增速", status="error")

    def _fetch_lpr(self, snapshot: MacroSnapshot, ak):
        """LPR 利率 — 列名: TRADE_DATE, LPR1Y, LPR5Y"""
        try:
            df = ak.macro_china_lpr()
            if df is not None and not df.empty:
                lpr1y = self._last_val(df, "LPR1Y")
                lpr5y = self._last_val(df, "LPR5Y")
                snapshot.lpr_1y = lpr1y
                snapshot.lpr_5y = lpr5y
                self._add(snapshot, "lpr_1y", "LPR 1年期", value=lpr1y, unit="%",
                          status="ok" if lpr1y is not None else "missing")
                self._add(snapshot, "lpr_5y", "LPR 5年期", value=lpr5y, unit="%",
                          status="ok" if lpr5y is not None else "missing")
            else:
                self._add(snapshot, "lpr_1y", "LPR 1年期", status="missing")
                self._add(snapshot, "lpr_5y", "LPR 5年期", status="missing")
        except Exception as e:
            logger.warning(f"LPR 获取失败: {e}")
            self._add(snapshot, "lpr_1y", "LPR 1年期", status="error")
            self._add(snapshot, "lpr_5y", "LPR 5年期", status="error")

    def _fetch_gdp(self, snapshot: MacroSnapshot, ak):
        try:
            df = ak.macro_china_gdp_yearly()
            val = self._last_val(df)
            snapshot.gdp_yoy = val
            self._add(snapshot, "gdp_yoy", "GDP 同比增速", value=val, unit="%",
                      status="ok" if val is not None else "missing")
        except Exception as e:
            logger.warning(f"GDP 获取失败: {e}")
            self._add(snapshot, "gdp_yoy", "GDP 同比增速", status="error")

    def _fetch_industrial_production(self, snapshot: MacroSnapshot, ak):
        """工业增加值 — 函数名为 macro_china_industrial_production_yoy"""
        try:
            df = ak.macro_china_industrial_production_yoy()
            val = self._last_val(df)
            snapshot.industrial_production_yoy = val
            self._add(snapshot, "industrial_production_yoy", "工业增加值同比",
                      value=val, unit="%", status="ok" if val is not None else "missing")
        except Exception as e:
            logger.warning(f"工业增加值获取失败: {e}")
            self._add(snapshot, "industrial_production_yoy", "工业增加值同比", status="error")

    def _fetch_social_financing(self, snapshot: MacroSnapshot, ak):
        """社会融资规模 — 列名: 社会融资规模增量"""
        try:
            df = ak.macro_china_shrzgm()
            val = self._last_val(df, "社会融资规模增量")
            snapshot.social_financing_yoy = val
            self._add(snapshot, "social_financing", "社会融资规模增量",
                      value=val, unit="亿元", status="ok" if val is not None else "missing")
        except Exception as e:
            logger.warning(f"社融获取失败: {e}")
            self._add(snapshot, "social_financing", "社会融资规模增量", status="error")

    def _fetch_bond_yields(self, snapshot: MacroSnapshot, ak):
        """中美10年期国债收益率"""
        try:
            df = ak.bond_zh_us_rate()
            if df is not None and not df.empty:
                # 查找中国10年列
                cn_col = None
                us_col = None
                for col in df.columns:
                    col_str = str(col)
                    if "中国" in col_str and ("10" in col_str or "十年" in col_str):
                        cn_col = col
                    if "美国" in col_str and ("10" in col_str or "十年" in col_str):
                        us_col = col

                cn_val = self._last_val(df, cn_col) if cn_col else None
                us_val = self._last_val(df, us_col) if us_col else None

                if cn_val is not None:
                    snapshot.ten_year_yield = cn_val
                self._add(snapshot, "cn_10y_yield", "中国10年期国债收益率",
                          value=cn_val, unit="%",
                          status="ok" if cn_val is not None else "missing")

                if us_val is not None:
                    self._add(snapshot, "us_10y_yield_cn_src", "美国10年期国债收益率(中国视角)",
                              value=us_val, unit="%", status="ok")
            else:
                self._add(snapshot, "cn_10y_yield", "中国10年期国债收益率", status="missing")
        except Exception as e:
            logger.warning(f"债券收益率获取失败: {e}")
            self._add(snapshot, "cn_10y_yield", "中国10年期国债收益率", status="error")

    def _fetch_shanghai_composite(self, snapshot: MacroSnapshot, ak):
        """上证综指"""
        try:
            df = ak.stock_zh_index_daily(symbol="sh000001")
            if df is not None and not df.empty:
                val = self._last_val(df, "close")
                snapshot.shanghai_composite = val
                # 日涨跌幅
                if df.shape[0] >= 2 and val is not None:
                    prev = float(df["close"].dropna().iloc[-2])
                    if prev and prev != 0:
                        snapshot.shanghai_composite_change_pct = round(
                            (val - prev) / prev * 100, 2
                        )
                self._add(snapshot, "shanghai_composite", "上证综指",
                          value=val, unit="点",
                          status="ok" if val is not None else "missing")
        except Exception as e:
            logger.warning(f"上证综指获取失败: {e}")
            self._add(snapshot, "shanghai_composite", "上证综指", status="error")


# ---------------------------------------------------------------------------
# yfinance 全球宏观数据提供者
# ---------------------------------------------------------------------------


class GlobalMacroProvider(MacroDataProvider):
    """使用 yfinance 获取全球宏观指标（VIX/DXY/美债/指数）"""

    TICKERS = [
        ("^VIX",      "vix",             "VIX 恐慌指数"),
        ("DX-Y.NYB",  "dxy",             "美元指数 DXY"),
        ("^GSPC",     "sp500",           "S&P 500"),
        ("^HSI",      "hsi",             "恒生指数"),
        ("^TNX",      "us_10y_yield",    "美国10年期国债收益率"),
    ]

    @property
    def name(self) -> str:
        return "yfinance_global_macro"

    @property
    def markets(self) -> List[str]:
        return ["US", "HK", "A"]

    def fetch(self, market: str) -> MacroSnapshot:
        snapshot = MacroSnapshot(market=market, as_of=datetime.now(timezone.utc).isoformat())
        snapshot.provider_status[self.name] = "ok"

        try:
            import yfinance as yf
        except ImportError:
            snapshot.provider_status[self.name] = "error: yfinance not installed"
            snapshot.data_gaps.append("yfinance 库未安装")
            return snapshot

        from yf_ratelimit import per_ticker_sleep

        for ticker, key, name in self.TICKERS:
            per_ticker_sleep()  # 5 只 macro ticker 之间 0.3s 延迟
            self._fetch_one(snapshot, yf, ticker, key, name)

        return snapshot

    # yfinance key → MacroSnapshot attribute mapping
    _KEY_ATTR_MAP = {
        "vix": "vix",
        "dxy": "dxy",
        "sp500": "sp500",
        "hsi": "hsi",
        "us_10y_yield": "ten_year_yield",
    }

    def _fetch_one(self, snapshot: MacroSnapshot, yf, ticker: str,
                   key: str, name: str):
        try:
            tk = yf.Ticker(ticker)
            hist = tk.history(period="5d")
            if hist is None or hist.empty:
                snapshot.factors.append(MacroFactorValue(
                    factor_key=key, factor_name=name,
                    source=self.name, status="missing",
                ))
                snapshot.data_gaps.append(name)
                return

            price = round(float(hist["Close"].iloc[-1]), 2)
            # 映射到正确的 dataclass 属性
            attr = self._KEY_ATTR_MAP.get(key, key)
            if hasattr(snapshot, attr):
                setattr(snapshot, attr, price)

            # 存到 factors 列表
            snapshot.factors.append(MacroFactorValue(
                factor_key=key, factor_name=name,
                value=price, unit="点" if ticker in ("^GSPC", "^HSI") else "",
                source=self.name, status="ok",
            ))
        except Exception as e:
            logger.warning(f"yfinance {ticker} 失败: {e}")
            snapshot.factors.append(MacroFactorValue(
                factor_key=key, factor_name=name,
                source=self.name, status="error",
            ))
            snapshot.data_gaps.append(f"{name} (error: {e})")


# ---------------------------------------------------------------------------
# 工厂：合并多个 provider 的快照
# ---------------------------------------------------------------------------


def build_default_macro_provider(market: str) -> MacroSnapshot:
    """构建默认宏观快照 = ChinaMacroProvider + GlobalMacroProvider + FredMacroProvider(US)"""
    china = ChinaMacroProvider()
    global_ = GlobalMacroProvider()

    snapshot = china.fetch(market)
    global_snap = global_.fetch(market)

    # 合并 global 因子
    existing = {f.factor_key for f in snapshot.factors}
    for f in global_snap.factors:
        if f.factor_key not in existing:
            snapshot.factors.append(f)
            existing.add(f.factor_key)

    # 合并已知标量字段
    for key in ("vix", "dxy", "sp500", "hsi", "ten_year_yield"):
        if getattr(snapshot, key) is None:
            setattr(snapshot, key, getattr(global_snap, key))

    snapshot.provider_status.update(global_snap.provider_status)
    for gap in global_snap.data_gaps:
        if gap not in snapshot.data_gaps:
            snapshot.data_gaps.append(gap)

    # 尝试 FRED（仅限 US 市场，没有 API Key 则静默跳过）
    if market in ("US",):
        try:
            from .fred_provider import build_fred_provider
            fred = build_fred_provider()
            if fred.available:
                fred_snap = fred.fetch(market)
                for f in fred_snap.factors:
                    if f.factor_key not in existing:
                        snapshot.factors.append(f)
                        existing.add(f.factor_key)
                for key in ("policy_rate", "ten_year_yield", "two_year_yield",
                            "unemployment_rate", "industrial_production_yoy"):
                    if getattr(snapshot, key) is None:
                        setattr(snapshot, key, getattr(fred_snap, key))
                snapshot.provider_status.update(fred_snap.provider_status)
                for gap in fred_snap.data_gaps:
                    if gap not in snapshot.data_gaps:
                        snapshot.data_gaps.append(gap)
        except Exception:
            pass  # FRED 不可用时静默跳过

    return snapshot
