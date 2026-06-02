#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""企业潜力分析批量编排服务

职责:
  1. prefetch_batch() — 筛选循环前一次拉取所有候选股票的五模块快照
  2. score_batch()    — 批量 LLM 评分（可选，复用 signal_analysis LLM client）

批量 = 单股 N=1 的超集，统一接口。
"""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .models import (
    CompanySnapshot,
    IndustrySnapshot,
    MacroSnapshot,
    TradingSnapshot,
    ValuationSnapshot,
)
from .providers import build_default_macro_provider

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
# PrefetchReport
# ═══════════════════════════════════════════════════════════════════


@dataclass
class PrefetchReport:
    market: str
    codes: List[str]
    success: List[str] = field(default_factory=list)
    failures: List[str] = field(default_factory=list)
    duration_ms: int = 0
    details: Dict[str, Any] = field(default_factory=dict)

    @property
    def ok_count(self) -> int:
        return len(self.success)

    @property
    def fail_count(self) -> int:
        return len(self.failures)


# ═══════════════════════════════════════════════════════════════════
# BatchFetcher 抽象 + 具体实现
# ═══════════════════════════════════════════════════════════════════


class BatchFetcher(ABC):
    """批量数据拉取器抽象基类"""

    @property
    @abstractmethod
    def module_name(self) -> str: ...

    @abstractmethod
    def fetch(self, market: str, codes: List[str]) -> Dict[str, Any]:
        """返回 {code: snapshot_dict}"""


class BatchCompanyFetcher(BatchFetcher):
    """批量企业质量数据拉取 — yfinance Tickers"""

    @property
    def module_name(self) -> str:
        return "company"

    def fetch(self, market: str, codes: List[str]) -> Dict[str, Any]:
        import yfinance as yf

        ticker_map = {c: self._to_yf(market, c) for c in codes}
        ticker_strs = " ".join(ticker_map.values())
        results: Dict[str, Any] = {}

        try:
            tickers = yf.Tickers(ticker_strs)
        except Exception as e:
            logger.warning(f"yf.Tickers 批量失败: {e}, fallback 逐股")
            for c, t in ticker_map.items():
                results[c] = self._fetch_one(yf, t, c)
            return results

        for code, yf_code in ticker_map.items():
            try:
                tk = tickers.tickers.get(yf_code)
                if tk is None:
                    results[code] = self._fetch_one(yf, yf_code, code)
                    continue
                snap = self._build_from_ticker(tk, market, code)
                results[code] = snap
            except Exception as e:
                logger.warning(f"{code} 企业数据拉取失败: {e}")
                results[code] = CompanySnapshot(market=market, code=code,
                                                provider_status={"yfinance": f"error: {e}"},
                                                data_gaps=[str(e)])
        return results

    def _to_yf(self, market: str, code: str) -> str:
        m = market.upper()
        if m == "HK":
            # 保持前导零: "00700" → "0700.HK"
            value = code[3:] if code.upper().startswith("HK.") else code
            return f"{int(value):04d}.HK"
        elif m == "US":
            return code[3:] if code.upper().startswith("US.") else code
        elif m == "A":
            # 已是 yahoo 格式 000001.SZ / 600000.SS，直接返回
            if code.endswith((".SS", ".SZ")):
                return code
            # 去掉可能已带的前缀 "SZ." / "SH."，避免双后缀
            c = code[3:] if code.upper().startswith(("SZ.", "SH.")) else code
            c = str(c).zfill(6)
            return f"{c}.{'SS' if c.startswith('6') else 'SZ'}"
        return code

    def _fetch_one(self, yf, yf_code: str, code: str) -> CompanySnapshot:
        """fallback 单股拉取"""
        from .builders import CompanySnapshotBuilder
        return CompanySnapshotBuilder().build(market="", code=code, yf_ticker=yf_code)

    def _build_from_ticker(self, tk, market: str, code: str) -> CompanySnapshot:
        info = tk.info or {}
        now = datetime.now(timezone.utc).isoformat()
        snap = CompanySnapshot(market=market, code=code, as_of=now,
                               provider_status={"yfinance": "ok"})
        snap.roic = self._pct(info.get("returnOnEquity"))
        snap.roe = self._pct(info.get("returnOnEquity"))
        snap.gross_margin = self._pct(info.get("grossMargins"))
        snap.net_margin = self._pct(info.get("profitMargins"))
        snap.operating_margin = self._pct(info.get("operatingMargins"))
        snap.revenue_growth = self._pct(info.get("revenueGrowth"))
        snap.earnings_growth = self._pct(info.get("earningsGrowth"))
        snap.debt_to_equity = self._num(info.get("debtToEquity"))
        snap.current_ratio = self._num(info.get("currentRatio"))
        snap.market_cap = info.get("marketCap")
        snap.enterprise_value = info.get("enterpriseValue")
        snap.ebitda = info.get("ebitda")
        snap.free_cash_flow = self._fcf(tk)
        return snap

    def _pct(self, v) -> Optional[float]:
        try: return round(float(v) * 100, 2) if v is not None else None
        except: return None

    def _num(self, v) -> Optional[float]:
        try: return round(float(v), 4) if v is not None else None
        except: return None

    def _fcf(self, tk) -> Optional[float]:
        try:
            cf = tk.cashflow
            if cf is None or cf.empty: return None
            for idx in cf.index:
                if "free cash flow" in str(idx).lower():
                    v = cf.loc[idx].dropna()
                    if not v.empty: return round(float(v.iloc[0]), 0)
        except: pass
        return None


class BatchValuationFetcher(BatchFetcher):
    """批量估值拉取 — yfinance info"""

    @property
    def module_name(self) -> str:
        return "valuation"

    def fetch(self, market: str, codes: List[str]) -> Dict[str, Any]:
        import yfinance as yf
        fetcher = BatchCompanyFetcher()
        ticker_map = {c: fetcher._to_yf(market, c) for c in codes}
        results: Dict[str, Any] = {}

        try:
            tickers = yf.Tickers(" ".join(ticker_map.values()))
        except Exception:
            for c, t in ticker_map.items():
                results[c] = self._fetch_one(yf, t, c)
            return results

        for code, yf_code in ticker_map.items():
            try:
                tk = tickers.tickers.get(yf_code)
                info = (tk.info or {}) if tk else {}
                now = datetime.now(timezone.utc).isoformat()
                snap = ValuationSnapshot(market=market, code=code, as_of=now,
                                         provider_status={"yfinance": "ok"})
                snap.pe_trailing = self._num(info.get("trailingPE"))
                snap.pe_forward = self._num(info.get("forwardPE"))
                snap.pb = self._num(info.get("priceToBook"))
                snap.ps = self._num(info.get("priceToSales"))
                snap.peg = self._num(info.get("pegRatio"))
                snap.market_cap = info.get("marketCap")
                snap.enterprise_value = info.get("enterpriseValue")
                ev = info.get("enterpriseValue"); ebitda = info.get("ebitda")
                if ev and ebitda and ebitda != 0:
                    snap.ev_ebitda = round(ev / ebitda, 2)
                results[code] = snap
            except Exception as e:
                results[code] = ValuationSnapshot(market=market, code=code,
                                                  provider_status={"yfinance": f"error: {e}"})
        return results

    def _fetch_one(self, yf, yf_code: str, code: str) -> ValuationSnapshot:
        from .builders import ValuationSnapshotBuilder
        return ValuationSnapshotBuilder().build(market="", code=code, yf_info=None)

    def _num(self, v) -> Optional[float]:
        try: return round(float(v), 4) if v is not None else None
        except: return None


class BatchTradingFetcher(BatchFetcher):
    """批量交易行为拉取 — yfinance history"""

    @property
    def module_name(self) -> str:
        return "trading"

    def fetch(self, market: str, codes: List[str]) -> Dict[str, Any]:
        import yfinance as yf
        fetcher = BatchCompanyFetcher()
        ticker_map = {c: fetcher._to_yf(market, c) for c in codes}
        results: Dict[str, Any] = {}

        try:
            tickers = yf.Tickers(" ".join(ticker_map.values()))
        except Exception:
            for c, t in ticker_map.items():
                results[c] = self._fetch_one(yf, t, market, c)
            return results

        for code, yf_code in ticker_map.items():
            try:
                tk = tickers.tickers.get(yf_code)
                snap = self._build_from_ticker(tk, market, code) if tk else TradingSnapshot(market=market, code=code)
                results[code] = snap
            except Exception as e:
                results[code] = TradingSnapshot(market=market, code=code,
                                                provider_status={"yfinance": f"error: {e}"})
        return results

    def _fetch_one(self, yf, yf_code: str, market, code) -> TradingSnapshot:
        from .builders import TradingSnapshotBuilder
        return TradingSnapshotBuilder().build(market=market, code=code, yf_ticker_obj=yf.Ticker(yf_code))

    def _build_from_ticker(self, tk, market: str, code: str) -> TradingSnapshot:
        now = datetime.now(timezone.utc).isoformat()
        snap = TradingSnapshot(market=market, code=code, as_of=now,
                               provider_status={"yfinance": "ok"})
        if tk is None: return snap
        info = tk.info or {}
        snap.current_price = self._n(info.get("currentPrice"))
        snap.beta = self._n(info.get("beta"))
        try:
            hist = tk.history(period="5d")
            if hist is not None and len(hist) >= 2:
                c0, c1 = float(hist["Close"].iloc[-1]), float(hist["Close"].iloc[-2])
                if c1 != 0: snap.pct_change = round((c0-c1)/c1*100, 2)
        except: pass
        try:
            hist = tk.history(period="3mo")
            if snap.current_price and hist is not None and len(hist) >= 50:
                ma50 = float(hist["Close"].tail(50).mean())
                snap.price_vs_50ma = round((snap.current_price-ma50)/ma50*100, 2)
            if hist is not None and snap.current_price:
                n = min(200, len(hist))
                ma200 = float(hist["Close"].tail(n).mean())
                snap.price_vs_200ma = round((snap.current_price-ma200)/ma200*100, 2)
        except: pass
        try:
            hist = tk.history(period="1mo")
            if hist is not None and len(hist) >= 5:
                r = hist["Close"].pct_change().dropna()
                snap.volatility_30d = round(float(r.std()*(252**0.5)*100), 2)
        except: pass
        if snap.price_vs_50ma is not None:
            snap.trend_strength = round(max(0, min(100, 50+snap.price_vs_50ma*2)), 1)
        return snap

    def _n(self, v) -> Optional[float]:
        try: return round(float(v), 4) if v is not None else None
        except: return None


class BatchIndustryFetcher(BatchFetcher):
    """批量行业拉取"""

    @property
    def module_name(self) -> str:
        return "industry"

    def fetch(self, market: str, codes: List[str]) -> Dict[str, Any]:
        import yfinance as yf
        fetcher = BatchCompanyFetcher()
        ticker_map = {c: fetcher._to_yf(market, c) for c in codes}
        results: Dict[str, Any] = {}

        try:
            tickers = yf.Tickers(" ".join(ticker_map.values()))
        except Exception:
            for c, t in ticker_map.items():
                results[c] = self._fetch_one(yf, t, market, c)
            return results

        for code, yf_code in ticker_map.items():
            try:
                tk = tickers.tickers.get(yf_code)
                info = (tk.info or {}) if tk else {}
                now = datetime.now(timezone.utc).isoformat()
                snap = IndustrySnapshot(market=market, code=code, as_of=now,
                                       provider_status={"yfinance": "ok"})
                snap.sector = str(info.get("sector") or "")
                snap.industry = str(info.get("industry") or "")
                results[code] = snap
            except Exception as e:
                results[code] = IndustrySnapshot(market=market, code=code)
        return results

    def _fetch_one(self, yf, yf_code, market, code) -> IndustrySnapshot:
        from .builders import IndustrySnapshotBuilder
        return IndustrySnapshotBuilder().build(market=market, code=code)


# ═══════════════════════════════════════════════════════════════════
# EnterprisePotentialService — 门面
# ═══════════════════════════════════════════════════════════════════

_CACHE_PREFIX = "enterprise"


class EnterprisePotentialService:
    """企业潜力分析批量编排服务"""

    def __init__(self, llm_client=None):
        self._llm_client = llm_client
        self._fetchers: Dict[str, BatchFetcher] = {
            "company":   BatchCompanyFetcher(),
            "valuation": BatchValuationFetcher(),
            "trading":   BatchTradingFetcher(),
            "industry":  BatchIndustryFetcher(),
        }

    # ── 批量预取 ──────────────────────────────────────────────

    def prefetch_batch(self, market: str, codes: List[str],
                       context: Any) -> PrefetchReport:
        """批量预取五模块快照到 FilterContext._cache"""
        started = time.monotonic()
        report = PrefetchReport(market=market, codes=list(codes))

        # 1. 宏观（市场级共享）
        self._prefetch_macro(market, context)

        # 2. 企业/估值/交易/行业（批量 + cache）
        for mod, fetcher in self._fetchers.items():
            cache_key_prefix = f"{_CACHE_PREFIX}:{mod}"
            missing = [c for c in codes if context.get_cache(f"{cache_key_prefix}:{c}") is None]

            if not missing:
                continue

            try:
                results = fetcher.fetch(market, missing)
                for code, snap in results.items():
                    context.set_cache(f"{cache_key_prefix}:{code}", snap)
                report.success.extend(list(results.keys()))
                report.details[mod] = f"{len(results)} ok"
            except Exception as e:
                logger.error(f"批量 {mod} 拉取失败: {e}")
                report.failures.extend(missing)
                report.details[mod] = f"error: {e}"

        report.duration_ms = int((time.monotonic() - started) * 1000)
        return report

    def _prefetch_macro(self, market: str, context: Any):
        """宏观快照（市场级，同市场所有股票共享）"""
        cache_key = f"{_CACHE_PREFIX}:macro:{market}"
        if context.get_cache(cache_key) is not None:
            return
        snap = build_default_macro_provider(market)
        context.set_cache(cache_key, snap)

    # ── 从缓存读取单股证据包 ──────────────────────────────────

    def load_evidence(self, market: str, code: str, name: str = "",
                      context: Any = None) -> Any:
        """从 FilterContext._cache 读取预拉取的快照，封装为 EvidencePackage"""
        from .models import EnterprisePotentialEvidencePackage
        from .providers import MacroSnapshot

        def _cached(mod: str):
            if context is None:
                return None
            return context.get_cache(f"{_CACHE_PREFIX}:{mod}:{code}")

        pkg = EnterprisePotentialEvidencePackage(
            market=market, code=code, name=name,
            as_of=datetime.now(timezone.utc).isoformat(),
        )

        if context:
            pkg.macro = context.get_cache(f"{_CACHE_PREFIX}:macro:{market}")
        else:
            pkg.macro = build_default_macro_provider(market)

        pkg.company = _cached("company")
        pkg.valuation = _cached("valuation")
        pkg.trading = _cached("trading")
        pkg.industry = _cached("industry")

        for mod, snap in [("company", pkg.company), ("valuation", pkg.valuation),
                          ("trading", pkg.trading), ("industry", pkg.industry)]:
            if snap is not None:
                pkg.modules_available.append(mod)
            else:
                pkg.modules_missing.append(mod)

        return pkg

    # ── 批量评分 ──────────────────────────────────────────────

    def build_scorer(self, threshold: float = 70.0):
        """创建评分器（优先 LLM，回退规则型）"""
        from .scoring import EnterprisePotentialScorer
        return EnterprisePotentialScorer(threshold=threshold, llm_client=self._llm_client)
