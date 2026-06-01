#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FRED 宏观数据提供者 — 美股/全球宏观经济指标

使用 fredapi 库获取美联储经济数据库的结构化宏观因子。
若无 API Key 或 fredapi 未安装，自动降级为空。

FRED API Key 获取: https://fred.stlouisfed.org/docs/api/api_key.html
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import List, Optional

from .models import MacroFactorValue, MacroSnapshot
from .providers import MacroDataProvider

logger = logging.getLogger(__name__)

# FRED Series ID → factor 映射
FRED_SERIES = {
    # 利率
    "FEDFUNDS":  ("policy_rate",    "联邦基金利率",       "%"),
    "DGS10":     ("ten_year_yield",  "美国10年期国债收益率",  "%"),
    "DGS2":      ("two_year_yield",  "美国2年期国债收益率",   "%"),
    # 通胀
    "CPIAUCSL":  ("cpi_index",      "CPI (All Urban)",    "指数"),
    "CPILFESL":  ("core_cpi_index", "核心 CPI",            "指数"),
    # 就业
    "UNRATE":    ("unemployment_rate", "失业率",           "%"),
    # 货币
    "M2SL":      ("m2_stock",       "M2 货币存量",          "十亿$"),
    # 经济增长
    "INDPRO":    ("industrial_production", "工业生产指数",  "指数"),
    "TCU":       ("capacity_utilization", "产能利用率",     "%"),
    "GDPC1":     ("real_gdp",       "实际 GDP",            "十亿$"),
    # 制造业
    "BUSINV":    ("business_inventories", "商业库存",      "百万$"),
    "DGORDER":   ("durable_goods_orders", "耐用品订单",     "百万$"),
}


class FredMacroProvider(MacroDataProvider):
    """
    FRED 宏观经济数据提供者。

    环境变量:
        FRED_API_KEY — 必需 (免费注册获取)

    用法:
        provider = FredMacroProvider(api_key="YOUR_KEY")
        snapshot = provider.fetch("US")
    """

    def __init__(self, api_key: Optional[str] = None):
        self._api_key = api_key
        self._fred = None
        self._init_error: Optional[str] = None
        self._try_init()

    def _try_init(self):
        try:
            import os
            key = self._api_key or os.getenv("FRED_API_KEY", "")
            if not key:
                self._init_error = "FRED_API_KEY 未设置"
                return
            from fredapi import Fred
            self._fred = Fred(api_key=key)
        except ImportError:
            self._init_error = "fredapi 未安装 (pip install fredapi)"
        except Exception as e:
            self._init_error = f"FRED 初始化失败: {e}"

    @property
    def name(self) -> str:
        return "fred_macro"

    @property
    def markets(self) -> List[str]:
        return ["US"]

    @property
    def available(self) -> bool:
        return self._fred is not None

    def fetch(self, market: str) -> MacroSnapshot:
        snap = MacroSnapshot(market=market, as_of=datetime.now(timezone.utc).isoformat())

        if not self.available:
            snap.provider_status[self.name] = f"unavailable: {self._init_error}"
            snap.data_gaps.append(f"FRED: {self._init_error}")
            return snap

        snap.provider_status[self.name] = "ok"
        for series_id, (factor_key, factor_name, unit) in FRED_SERIES.items():
            self._fetch_series(snap, series_id, factor_key, factor_name, unit)

        return snap

    def _fetch_series(self, snap: MacroSnapshot, series_id: str,
                      factor_key: str, factor_name: str, unit: str):
        try:
            series = self._fred.get_series(series_id)
            if series is None or series.empty:
                snap.factors.append(MacroFactorValue(
                    factor_key=factor_key, factor_name=factor_name,
                    source=self.name, status="missing",
                ))
                snap.data_gaps.append(factor_name)
                return

            latest = series.dropna().iloc[-1]
            val = round(float(latest), 4)

            # 写入标量字段
            attr_map = {
                "policy_rate": "policy_rate",
                "ten_year_yield": "ten_year_yield",
                "two_year_yield": "two_year_yield",
                "unemployment_rate": "unemployment_rate",
                "industrial_production": "industrial_production_yoy",
            }
            attr = attr_map.get(factor_key, factor_key)
            if hasattr(snap, attr):
                setattr(snap, attr, val)

            # 趋势
            trend = ""
            clean = series.dropna()
            if len(clean) >= 2:
                a, b = float(clean.iloc[-2]), float(clean.iloc[-1])
                trend = "rising" if b > a else "falling" if b < a else "stable"

            snap.factors.append(MacroFactorValue(
                factor_key=factor_key, factor_name=factor_name,
                value=val, unit=unit, trend=trend,
                source=self.name, status="ok",
            ))
        except Exception as e:
            logger.warning(f"FRED {series_id} 获取失败: {e}")
            snap.factors.append(MacroFactorValue(
                factor_key=factor_key, factor_name=factor_name,
                source=self.name, status="error",
            ))


# 工厂
def build_fred_provider(api_key: Optional[str] = None) -> FredMacroProvider:
    """创建 FRED provider，自动从环境变量读取 API Key"""
    return FredMacroProvider(api_key=api_key)
