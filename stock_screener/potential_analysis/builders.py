#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Snapshot Builder 层 — 每个 builder 负责一个模块的快照构建"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import pandas as pd

from .models import (
    CompanySnapshot,
    EnterprisePotentialEvidencePackage,
    IndustrySnapshot,
    MacroSnapshot,
    TradingSnapshot,
    ValuationSnapshot,
)

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════
# 抽象基类
# ═══════════════════════════════════════════════════════════════════


class SnapshotBuilder(ABC):
    """快照构建器基类"""

    @property
    @abstractmethod
    def module_name(self) -> str: ...

    @abstractmethod
    def build(self, market: str, code: str, name: str = "",
              **kwargs: Any) -> Any: ...


# ═══════════════════════════════════════════════════════════════════
# CompanySnapshotBuilder
# ═══════════════════════════════════════════════════════════════════


class CompanySnapshotBuilder(SnapshotBuilder):
    """
    企业质量快照构建器。

    数据来源（按优先级）：
    1. yfinance — 港股/美股三表 + info
    2. FutuOpenD — get_stock_filter（通过 context cache 注入）
    """

    @property
    def module_name(self) -> str:
        return "company"

    def build(self, market: str, code: str, name: str = "",
              yf_ticker: Optional[Any] = None,
              **kwargs: Any) -> CompanySnapshot:
        now = datetime.now(timezone.utc).isoformat()
        snap = CompanySnapshot(market=market, code=code, name=name, as_of=now)

        try:
            import yfinance as yf
        except ImportError:
            snap.provider_status["yfinance"] = "error: not installed"
            snap.data_gaps.append("yfinance 未安装，无法获取企业质量数据")
            return snap

        # 解析 ticker
        ticker_str = self._resolve_ticker(market, code, yf_ticker)
        try:
            tk = yf.Ticker(ticker_str)
            info = tk.info or {}
        except Exception as e:
            snap.provider_status["yfinance"] = f"error: {e}"
            snap.data_gaps.append(f"yfinance Ticker({ticker_str}) 失败")
            return snap

        snap.provider_status["yfinance"] = "ok"

        # ---- 盈利质量 ----
        snap.roic = self._pct(info.get("returnOnEquity"))  # yfinance 无直接 ROIC，用 ROE 代理
        snap.roe = self._pct(info.get("returnOnEquity"))
        snap.gross_margin = self._pct(info.get("grossMargins"))
        snap.net_margin = self._pct(info.get("profitMargins"))
        snap.operating_margin = self._pct(info.get("operatingMargins"))

        # ---- 成长性 ----
        snap.revenue_growth = self._pct(info.get("revenueGrowth"))
        snap.earnings_growth = self._pct(info.get("earningsGrowth"))

        # ---- 财务健康 ----
        snap.debt_to_equity = self._num(info.get("debtToEquity"))
        snap.current_ratio = self._num(info.get("currentRatio"))
        snap.free_cash_flow = self._fetch_fcf(tk)
        snap.market_cap = info.get("marketCap")
        snap.enterprise_value = info.get("enterpriseValue")
        snap.ebitda = info.get("ebitda")

        # ---- 估值代理字段 (会被 ValuationSnapshotBuilder 覆盖) ----
        snap.provider_status["yfinance"] = "ok"

        # 检查覆盖率
        fields = [snap.roic, snap.gross_margin, snap.net_margin,
                  snap.revenue_growth, snap.debt_to_equity, snap.free_cash_flow]
        if all(v is None for v in fields):
            snap.data_gaps.append("企业质量数据全部缺失")
            snap.provider_status["yfinance"] = "partial"

        return snap

    def _resolve_ticker(self, market: str, code: str,
                        yf_ticker: Optional[Any]) -> str:
        """解析 yfinance ticker 格式"""
        if yf_ticker is not None:
            return str(yf_ticker)
        m = market.upper()
        if m == "HK":
            # 01810 → 1810.HK
            value = code[3:] if code.upper().startswith("HK.") else code
            return f"{int(value):04d}.HK"
        elif m == "US":
            return code[3:] if code.upper().startswith("US.") else code
        elif m == "A":
            # 已是 yahoo 格式 000001.SZ / 600000.SS，直接返回
            if code.endswith((".SS", ".SZ")):
                return code
            # 000001 → 000001.SZ (Shenzhen) or 600000.SS (Shanghai)
            c = code[3:] if code.upper().startswith(("SZ.", "SH.")) else code
            c = str(c).zfill(6)
            return f"{c}.{'SS' if c.startswith('6') else 'SZ'}"
        return code

    def _pct(self, val) -> Optional[float]:
        """yfinance 的比率字段通常是 0~1 的小数，转为 %"""
        if val is None:
            return None
        try:
            v = float(val)
            return round(v * 100, 2)  # 0.22 → 22.0%
        except (TypeError, ValueError):
            return None

    def _num(self, val) -> Optional[float]:
        if val is None:
            return None
        try:
            return round(float(val), 4)
        except (TypeError, ValueError):
            return None

    def _fetch_fcf(self, tk) -> Optional[float]:
        """从现金流量表取自由现金流"""
        try:
            cf = tk.cashflow
            if cf is not None and not cf.empty:
                # 查找 Free Cash Flow 行
                for idx in cf.index:
                    if "free cash flow" in str(idx).lower():
                        val = cf.loc[idx].dropna()
                        if not val.empty:
                            return round(float(val.iloc[0]), 0)
                # 备选：Operating Cash Flow - Capital Expenditure
                ocf = None; capex = None
                for idx in cf.index:
                    s = str(idx).lower()
                    if "operating cash flow" in s and ocf is None:
                        v = cf.loc[idx].dropna()
                        if not v.empty: ocf = float(v.iloc[0])
                    if "capital expenditure" in s and "reported" not in s and capex is None:
                        v = cf.loc[idx].dropna()
                        if not v.empty: capex = float(v.iloc[0])
                if ocf is not None and capex is not None:
                    return round(ocf + capex, 0)  # capex is negative
        except Exception:
            pass
        return None


# ═══════════════════════════════════════════════════════════════════
# ValuationSnapshotBuilder
# ═══════════════════════════════════════════════════════════════════


class ValuationSnapshotBuilder(SnapshotBuilder):
    """估值快照构建器 — 主要用 yfinance info + 自算"""

    @property
    def module_name(self) -> str:
        return "valuation"

    def build(self, market: str, code: str, name: str = "",
              yf_info: Optional[Dict] = None,
              company: Optional[CompanySnapshot] = None,
              **kwargs: Any) -> ValuationSnapshot:
        now = datetime.now(timezone.utc).isoformat()
        snap = ValuationSnapshot(market=market, code=code, as_of=now)

        info = yf_info or {}
        if not info:
            # 自行拉取
            try:
                import yfinance as yf
                ticker_str = CompanySnapshotBuilder()._resolve_ticker(market, code, None)
                info = yf.Ticker(ticker_str).info or {}
            except Exception:
                pass

        snap.provider_status["yfinance"] = "ok" if info else "error"

        snap.pe_trailing = self._num(info.get("trailingPE"))
        snap.pe_forward = self._num(info.get("forwardPE"))
        snap.pb = self._num(info.get("priceToBook"))
        snap.ps = self._num(info.get("priceToSales"))
        snap.peg = self._num(info.get("pegRatio"))
        snap.market_cap = info.get("marketCap")
        snap.enterprise_value = info.get("enterpriseValue")

        # EV/EBITDA — 自算
        ev = info.get("enterpriseValue")
        ebitda = info.get("ebitda")
        if ev and ebitda and ebitda != 0:
            snap.ev_ebitda = round(ev / ebitda, 2)
        elif company and company.enterprise_value and company.ebitda and company.ebitda != 0:
            snap.ev_ebitda = round(company.enterprise_value / company.ebitda, 2)

        # 检查覆盖率
        missing = []
        for f in ["pe_trailing", "pe_forward", "pb", "peg", "ev_ebitda"]:
            if getattr(snap, f) is None:
                missing.append(f)
        if missing:
            snap.data_gaps = missing

        return snap

    def _num(self, val) -> Optional[float]:
        if val is None: return None
        try: return round(float(val), 4)
        except (TypeError, ValueError): return None


# ═══════════════════════════════════════════════════════════════════
# IndustrySnapshotBuilder
# ═══════════════════════════════════════════════════════════════════


class IndustrySnapshotBuilder(SnapshotBuilder):
    """行业景气快照 — 行业分类 + 板块数据"""

    @property
    def module_name(self) -> str:
        return "industry"

    def build(self, market: str, code: str, name: str = "",
              yf_info: Optional[Dict] = None,
              **kwargs: Any) -> IndustrySnapshot:
        now = datetime.now(timezone.utc).isoformat()
        snap = IndustrySnapshot(market=market, code=code, as_of=now)

        info = yf_info or {}
        if not info:
            try:
                import yfinance as yf
                ticker_str = CompanySnapshotBuilder()._resolve_ticker(market, code, None)
                info = yf.Ticker(ticker_str).info or {}
            except Exception:
                pass

        snap.provider_status["yfinance"] = "ok" if info else "error"
        snap.sector = str(info.get("sector") or "")
        snap.industry = str(info.get("industry") or "")

        if not snap.sector and not snap.industry:
            snap.data_gaps.append("行业分类缺失")

        return snap


# ═══════════════════════════════════════════════════════════════════
# TradingSnapshotBuilder
# ═══════════════════════════════════════════════════════════════════


class TradingSnapshotBuilder(SnapshotBuilder):
    """市场行为快照 — 价格/趋势/波动率"""

    @property
    def module_name(self) -> str:
        return "trading"

    def build(self, market: str, code: str, name: str = "",
              yf_ticker_obj: Optional[Any] = None,
              **kwargs: Any) -> TradingSnapshot:
        now = datetime.now(timezone.utc).isoformat()
        snap = TradingSnapshot(market=market, code=code, as_of=now)

        try:
            import yfinance as yf
        except ImportError:
            snap.provider_status["yfinance"] = "error: not installed"
            return snap

        if yf_ticker_obj is not None:
            tk = yf_ticker_obj
        else:
            ticker_str = CompanySnapshotBuilder()._resolve_ticker(market, code, None)
            tk = yf.Ticker(ticker_str)

        info = tk.info or {}
        snap.provider_status["yfinance"] = "ok"

        snap.current_price = self._num(info.get("currentPrice"))
        snap.beta = self._num(info.get("beta"))

        # 日涨跌幅
        try:
            hist = tk.history(period="5d")
            if hist is not None and len(hist) >= 2:
                c0, c1 = float(hist["Close"].iloc[-1]), float(hist["Close"].iloc[-2])
                if c1 != 0:
                    snap.pct_change = round((c0 - c1) / c1 * 100, 2)
        except Exception:
            pass

        # 均线偏离
        try:
            hist_50 = tk.history(period="3mo")
            if hist_50 is not None and len(hist_50) >= 50 and snap.current_price:
                ma50 = float(hist_50["Close"].tail(50).mean())
                snap.price_vs_50ma = round((snap.current_price - ma50) / ma50 * 100, 2)
            if hist_50 is not None and len(hist_50) >= 200 and snap.current_price:
                ma200 = float(hist_50["Close"].tail(min(200, len(hist_50))).mean())
                snap.price_vs_200ma = round((snap.current_price - ma200) / ma200 * 100, 2)
        except Exception:
            pass

        # 30 日波动率
        try:
            hist_30 = tk.history(period="1mo")
            if hist_30 is not None and len(hist_30) >= 5:
                returns = hist_30["Close"].pct_change().dropna()
                snap.volatility_30d = round(float(returns.std() * (252 ** 0.5) * 100), 2)
        except Exception:
            pass

        # 趋势强度 (简单评估: 价格相对 50MA 位置 + 近期涨跌)
        if snap.price_vs_50ma is not None:
            ts = 50 + (snap.price_vs_50ma * 2)  # base 50, +/-20 range
            snap.trend_strength = round(max(0, min(100, ts)), 1)

        if snap.current_price is None:
            snap.data_gaps.append("无法获取当前价格")

        return snap

    def _num(self, val) -> Optional[float]:
        if val is None: return None
        try: return round(float(val), 4)
        except (TypeError, ValueError): return None


# ═══════════════════════════════════════════════════════════════════
# 工厂：构建统一证据包
# ═══════════════════════════════════════════════════════════════════


def build_enterprise_evidence(
    market: str, code: str, name: str = "",
    macro_snapshot: Optional[MacroSnapshot] = None,
    yf_ticker: Optional[str] = None,
) -> EnterprisePotentialEvidencePackage:
    """
    构建五模块统一证据包。

    参数:
        market: 市场 (HK/US/A)
        code: 股票代码
        name: 股票名称
        macro_snapshot: 可选的预构建宏观快照（避免重复拉取）
        yf_ticker: 可选的自定义 yfinance ticker

    Returns:
        EnterprisePotentialEvidencePackage
    """
    pkg = EnterprisePotentialEvidencePackage(
        market=market, code=code, name=name,
        as_of=datetime.now(timezone.utc).isoformat(),
    )

    # 1. 先拉取 yfinance 数据（用于多个模块）
    info = None; tk_obj = None
    try:
        import yfinance as yf
        ticker_str = yf_ticker or CompanySnapshotBuilder()._resolve_ticker(market, code, None)
        tk_obj = yf.Ticker(ticker_str)
        info = tk_obj.info or {}
    except Exception as e:
        logger.warning(f"yfinance 数据拉取失败: {e}")
        pkg.data_gaps.append(f"yfinance: {e}")

    # 2. 企业质量
    try:
        pkg.company = CompanySnapshotBuilder().build(
            market, code, name, yf_ticker=ticker_str if not yf_ticker else yf_ticker)
        pkg.modules_available.append("company")
    except Exception as e:
        logger.warning(f"企业质量快照失败: {e}")
        pkg.modules_missing.append("company")
        pkg.data_gaps.append(f"company: {e}")

    # 3. 估值
    try:
        pkg.valuation = ValuationSnapshotBuilder().build(
            market, code, name, yf_info=info, company=pkg.company)
        pkg.modules_available.append("valuation")
    except Exception as e:
        logger.warning(f"估值快照失败: {e}")
        pkg.modules_missing.append("valuation")
        pkg.data_gaps.append(f"valuation: {e}")

    # 4. 行业
    try:
        pkg.industry = IndustrySnapshotBuilder().build(market, code, name, yf_info=info)
        pkg.modules_available.append("industry")
    except Exception as e:
        logger.warning(f"行业快照失败: {e}")
        pkg.modules_missing.append("industry")

    # 5. 交易行为
    try:
        pkg.trading = TradingSnapshotBuilder().build(
            market, code, name, yf_ticker_obj=tk_obj)
        pkg.modules_available.append("trading")
    except Exception as e:
        logger.warning(f"交易快照失败: {e}")
        pkg.modules_missing.append("trading")

    # 6. 宏观（外部注入或默认空）
    pkg.macro = macro_snapshot
    if macro_snapshot:
        pkg.modules_available.append("macro")
    else:
        pkg.modules_missing.append("macro")

    # 汇总 data_gaps
    for mod in [pkg.macro, pkg.industry, pkg.company, pkg.valuation, pkg.trading]:
        if mod and hasattr(mod, "data_gaps"):
            for gap in mod.data_gaps:
                if gap not in pkg.data_gaps:
                    pkg.data_gaps.append(gap)

    return pkg
