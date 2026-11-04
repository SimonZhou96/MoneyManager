#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Snapshot Builder 层 — 每个 builder 负责一个模块的快照构建"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import pandas as pd

# 全局抑制 yfinance / urllib3 的 HTTP 404 噪音
# （yfinance 不覆盖的标的由 Futu 兜底，404 无需打屏）
logging.getLogger("urllib3").setLevel(logging.ERROR)

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
# Futu 兜底数据映射
# ═══════════════════════════════════════════════════════════════════

# Futu get_market_snapshot 返回的列名常量
_FUTU_COL_CODE = "code"
_FUTU_COL_NAME = "name"
_FUTU_COL_LAST_PRICE = "last_price"
_FUTU_COL_OPEN_PRICE = "open_price"
_FUTU_COL_HIGH_PRICE = "high_price"
_FUTU_COL_LOW_PRICE = "low_price"
_FUTU_COL_PREV_CLOSE = "prev_close_price"
_FUTU_COL_VOLUME = "volume"
_FUTU_COL_TURNOVER = "turnover"
_FUTU_COL_TURNOVER_RATE = "turnover_rate"
_FUTU_COL_AMPLITUDE = "amplitude"
_FUTU_COL_PE = "pe_ratio"
_FUTU_COL_PE_TTM = "pe_ttm_ratio"
_FUTU_COL_PB = "pb_ratio"
_FUTU_COL_MARKET_VAL = "total_market_val"
_FUTU_COL_CIRC_MARKET_VAL = "circular_market_val"
_FUTU_COL_ISSUED_SHARES = "issued_shares"
_FUTU_COL_NET_ASSET = "net_asset"
_FUTU_COL_NET_PROFIT = "net_profit"
_FUTU_COL_EPS = "earning_per_share"
_FUTU_COL_EY_RATIO = "ey_ratio"
_FUTU_COL_DIVIDEND_TTM = "dividend_ttm"
_FUTU_COL_DIVIDEND_RATIO_TTM = "dividend_ratio_ttm"
_FUTU_COL_CHANGE_RATE = "change_rate"
_FUTU_COL_SUSPENSION = "suspension"


def _futu_snapshot_map(codes: List[str], quote_ctx) -> Dict[str, Any]:
    """对一批代码调用 Futu get_market_snapshot，返回 {futu_code: row_dict}"""
    if not quote_ctx or not codes:
        return {}
    try:
        import futu as ft
        ret, data = quote_ctx.get_market_snapshot(list(codes))
        if ret != ft.RET_OK or data is None or data.empty:
            return {}
        result = {}
        for _, row in data.iterrows():
            result[str(row.get(_FUTU_COL_CODE, ""))] = row
        return result
    except Exception:
        return {}


def _float_or_none(val) -> Optional[float]:
    """安全转为 float，非数字返回 None"""
    import math
    try:
        v = float(val)
        if math.isnan(v) or math.isinf(v):
            return None
        return round(v, 4)
    except (TypeError, ValueError):
        return None


def _fill_company_snapshot_from_futu(snap: CompanySnapshot, row) -> CompanySnapshot:
    """用 Futu get_market_snapshot 数据填充 CompanySnapshot（跳过零值废股）"""
    mc = _float_or_none(row.get(_FUTU_COL_MARKET_VAL))
    if mc and mc > 0:
        snap.market_cap = mc
    if snap.market_cap is not None:
        prev_status = snap.provider_status.get("futu", "")
        snap.provider_status["futu"] = "partial" if not prev_status else prev_status
    return snap


def _fill_valuation_snapshot_from_futu(snap: ValuationSnapshot, row) -> ValuationSnapshot:
    """用 Futu get_market_snapshot 数据填充 ValuationSnapshot（跳过零值）"""
    mc = _float_or_none(row.get(_FUTU_COL_MARKET_VAL))
    if mc and mc > 0:
        snap.market_cap = mc
    pe = _float_or_none(row.get(_FUTU_COL_PE_TTM)) or _float_or_none(row.get(_FUTU_COL_PE))
    if pe and pe > 0:
        snap.pe_trailing = pe
    pb = _float_or_none(row.get(_FUTU_COL_PB))
    if pb and pb > 0:
        snap.pb = pb

    if snap.pe_trailing is not None or snap.pb is not None or snap.market_cap is not None:
        prev_status = snap.provider_status.get("futu", "")
        snap.provider_status["futu"] = "partial" if not prev_status else prev_status
    return snap


def _fill_trading_snapshot_from_futu(snap: TradingSnapshot, row) -> TradingSnapshot:
    """用 Futu get_market_snapshot 数据填充 TradingSnapshot（跳过零价格废股）"""
    price = _float_or_none(row.get(_FUTU_COL_LAST_PRICE))
    if price and price > 0:
        snap.current_price = price
    if snap.current_price is not None and snap.pct_change is None:
        prev_close = _float_or_none(row.get(_FUTU_COL_PREV_CLOSE))
        if prev_close and prev_close > 0:
            snap.pct_change = round((snap.current_price - prev_close) / prev_close * 100, 2)

    if snap.current_price is not None:
        prev_status = snap.provider_status.get("futu", "")
        snap.provider_status["futu"] = "partial" if not prev_status else prev_status
    return snap


def _snapshot_is_empty(snap: Any) -> bool:
    """判断 snapshot 是否因为 yfinance 失败或关键字段缺失而为空（需要 Futu 兜底）。

    检查三个层面：
    1. yfinance provider_status 是否为 error（原有逻辑）
    2. 所有关键字段全部缺失（yfinance 完全没返回数据）
    3. PE 缺失但其他数据可用 —— yfinance 认识这只股票但缺少财务数据
       （常见于新上市股票/小盘股，Futu 可能有补充）
    """
    status = getattr(snap, "provider_status", {})
    yf_status = status.get("yfinance", "")

    # 原逻辑：yfinance 明确失败
    if yf_status and yf_status != "ok":
        if "error" in str(yf_status).lower() or (isinstance(yf_status, str) and yf_status.startswith("error")):
            return True

    pe = getattr(snap, "pe_trailing", None)
    mc = getattr(snap, "market_cap", None)
    price = getattr(snap, "current_price", None)

    # yfinance 返回了 ok 但所有关键字段全空
    if pe is None and mc is None and price is None:
        return True

    # yfinance 知道这只股票（有价格/市值）但缺少 PE —— 尝试 Futu 补充
    if pe is None and (mc is not None or price is not None):
        return True

    return False


def _to_futu_code(code: str, market: str) -> str:
    """将各种格式的股票代码统一转为 Futu API 格式。

    yfinance 格式 → Futu 格式:
      HK: 0700.HK → HK.00700
      US: AAPL    → US.AAPL
      A:  000001.SZ → SZ.000001; 600000.SS → SH.600000

    已为 Futu 格式则原样返回: HK.00700 / US.AAPL / SZ.000001 / SH.600000
    """
    mkt = market.upper()
    code = str(code).strip()

    # 已是 Futu 格式（带 HK./US./SH./SZ. 前缀）
    if code.upper().startswith(("HK.", "US.", "SH.", "SZ.")):
        return code

    if mkt == "HK":
        if code.endswith(".HK"):
            digits = code[:-3]
            return f"HK.{digits.zfill(5)}"
        if code.isdigit():
            return f"HK.{code.zfill(5)}"
        return f"HK.{code}"

    if mkt == "US":
        return f"US.{code}"

    if mkt == "A":
        if code.endswith(".SS"):
            return f"SH.{code[:-3]}"
        if code.endswith(".SZ"):
            return f"SZ.{code[:-3]}"
        if code.isdigit() and len(code) == 6:
            return f"SH.{code}" if code.startswith("6") else f"SZ.{code}"
        return code

    return code


def fill_snapshots_from_futu(
    market: str,
    code_snap_pairs: List[tuple],
    quote_ctx,
) -> int:
    """
    对 yfinance 失败的空 snapshot 尝试 Futu 兜底。
    返回成功兜底的股票数量。
    """
    if not quote_ctx:
        return 0

    # 找出需要兜底的
    empty_pairs = [(c, s) for c, s in code_snap_pairs if _snapshot_is_empty(s)]
    if not empty_pairs:
        return 0

    # 用 _to_futu_code 统一转换，确保格式正确（yfinance 格式 → Futu 格式）
    futu_codes = [_to_futu_code(code, market) for code, _ in empty_pairs]
    futu_codes = list(set(futu_codes))  # 去重

    # 批量拉取 Futu 数据
    futu_map = _futu_snapshot_map(futu_codes, quote_ctx)
    if not futu_map:
        return 0

    succeeded = 0
    for code, snap in empty_pairs:
        futu_code = _to_futu_code(code, market)
        row = futu_map.get(futu_code)
        if row is None:
            continue

        try:
            if isinstance(snap, CompanySnapshot):
                _fill_company_snapshot_from_futu(snap, row)
            elif isinstance(snap, ValuationSnapshot):
                _fill_valuation_snapshot_from_futu(snap, row)
            elif isinstance(snap, TradingSnapshot):
                _fill_trading_snapshot_from_futu(snap, row)
            succeeded += 1
        except Exception:
            continue

    return succeeded


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
            if not value.isdigit():
                return code  # 非数字代码（如 AAM.UT），原样返回避免崩溃
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
