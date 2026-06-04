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
            if not value.isdigit():
                return code  # 非数字代码（如 AAM.UT），原样返回避免崩溃
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
    """批量估值拉取 — yfinance info（独立调用时使用）"""

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
    """批量交易行为拉取 — yfinance history（需要独立调用，需要 .history()）"""

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
        """批量预取五模块快照到 FilterContext._cache。

        company + valuation + industry 共用同一次 yf.Tickers()，
        trading 需要 .history() 独立调用。从 4 次请求减到 2 次。
        """
        import logging as _logging
        _logging.getLogger("urllib3").setLevel(_logging.ERROR)
        _logging.getLogger("yfinance").setLevel(_logging.WARNING)

        started = time.monotonic()
        report = PrefetchReport(market=market, codes=list(codes))

        # 1. 宏观（市场级共享）
        self._prefetch_macro(market, context)

        quote_ctx = context.get_cache("futu_quote_ctx")
        verbose = getattr(context, "verbose", False)
        _CHUNK = 50

        # ── 2. company + valuation + industry — 合并为一次 Tickers ──
        merged_mods = ["company", "valuation", "industry"]
        merged_missing: Dict[str, set] = {}
        for mod in merged_mods:
            ck = f"{_CACHE_PREFIX}:{mod}"
            merged_missing[mod] = {c for c in codes if context.get_cache(f"{ck}:{c}") is None}

        all_missing = sorted(set().union(*merged_missing.values()))

        if all_missing:
            company_fet = self._fetchers["company"]
            val_fet = self._fetchers["valuation"]
            ind_fet = self._fetchers["industry"]
            total = len(all_missing)
            ok_count = 0
            rescued = 0

            try:
                for chunk_idx in range(0, total, _CHUNK):
                    chunk = all_missing[chunk_idx:chunk_idx + _CHUNK]
                    t0 = time.monotonic()
                    ticker_map = {c: company_fet._to_yf(market, c) for c in chunk}

                    import yfinance as yf
                    tickers = yf.Tickers(" ".join(ticker_map.values()))

                    chunk_ok = 0
                    for code, yf_code in ticker_map.items():
                        tk = tickers.tickers.get(yf_code)
                        info = (tk.info or {}) if tk else {}
                        now = datetime.now(timezone.utc).isoformat()

                        # -- company --
                        if code in merged_missing["company"]:
                            try:
                                if tk is not None:
                                    snap_c = company_fet._build_from_ticker(tk, market, code)
                                else:
                                    snap_c = CompanySnapshot(market=market, code=code,
                                        provider_status={"yfinance": "error: no data"})
                            except Exception as e:
                                snap_c = CompanySnapshot(market=market, code=code,
                                    provider_status={"yfinance": f"error: {e}"})
                            context.set_cache(f"{_CACHE_PREFIX}:company:{code}", snap_c)
                            report.success.append(code)

                        # -- valuation --
                        if code in merged_missing["valuation"]:
                            try:
                                snap_v = ValuationSnapshot(market=market, code=code, as_of=now,
                                    provider_status={"yfinance": "ok" if info else "error"})
                                snap_v.pe_trailing = val_fet._num(info.get("trailingPE"))
                                snap_v.pe_forward = val_fet._num(info.get("forwardPE"))
                                snap_v.pb = val_fet._num(info.get("priceToBook"))
                                snap_v.ps = val_fet._num(info.get("priceToSales"))
                                snap_v.peg = val_fet._num(info.get("pegRatio"))
                                snap_v.market_cap = info.get("marketCap")
                                snap_v.enterprise_value = info.get("enterpriseValue")
                                ev = info.get("enterpriseValue"); ebitda = info.get("ebitda")
                                if ev and ebitda and ebitda != 0:
                                    snap_v.ev_ebitda = round(ev / ebitda, 2)
                            except Exception as e:
                                snap_v = ValuationSnapshot(market=market, code=code,
                                    provider_status={"yfinance": f"error: {e}"})
                            context.set_cache(f"{_CACHE_PREFIX}:valuation:{code}", snap_v)
                            report.success.append(code)

                        # -- industry --
                        if code in merged_missing["industry"]:
                            try:
                                snap_i = IndustrySnapshot(market=market, code=code, as_of=now,
                                    provider_status={"yfinance": "ok" if info else "error"})
                                snap_i.sector = str(info.get("sector") or "")
                                snap_i.industry = str(info.get("industry") or "")
                            except Exception as e:
                                snap_i = IndustrySnapshot(market=market, code=code,
                                    provider_status={"yfinance": f"error: {e}"})
                            context.set_cache(f"{_CACHE_PREFIX}:industry:{code}", snap_i)
                            report.success.append(code)

                        if info:
                            chunk_ok += 1
                    ok_count += chunk_ok

                    done = min(chunk_idx + _CHUNK, total)
                    chunk_ms = int((time.monotonic() - t0) * 1000)
                    print(f"  ⏳ [{market}] company/val/ind: {done}/{total} ({chunk_ok} ok, {chunk_ms}ms)")

                    if chunk_idx + _CHUNK < total:
                        time.sleep(min(1.5 + chunk_idx * 0.1, 5.0))

                # Futu 兜底
                if quote_ctx is not None:
                    from .builders import fill_snapshots_from_futu, _snapshot_is_empty
                    for mod in merged_mods:
                        ck = f"{_CACHE_PREFIX}:{mod}"
                        pairs = [(c, context.get_cache(f"{ck}:{c}"))
                                 for c in merged_missing[mod]
                                 if context.get_cache(f"{ck}:{c}") is not None
                                 and _snapshot_is_empty(context.get_cache(f"{ck}:{c}"))]
                        if pairs:
                            rescued += fill_snapshots_from_futu(market, pairs, quote_ctx)
                    if rescued > 0:
                        print(f"    ↳ [{market}] Futu 兜底: {rescued} 只 company/val/ind")

                _gaps = total - ok_count
                mod_ms = int((time.monotonic() - started) * 1000)
                parts = [f"{ok_count} ok"]
                if _gaps > 0:
                    parts.append(f"{_gaps} gaps")
                if rescued > 0:
                    parts.append(f"{rescued} futu兜底")
                print(f"  ✅ [{market}] company/val/ind: ({', '.join(parts)}) {mod_ms}ms")
                report.details["merged"] = f"company+valuation+industry: {ok_count} ok"

            except Exception as e:
                print(f"  ❌ [{market}] company/val/ind: {e}")
                logger.error(f"合并批量拉取失败: {e}")
                report.details["merged"] = f"error: {e}"
        else:
            if verbose:
                print(f"  ℹ [{market}] company/val/ind: 全部缓存命中")

        # ── 3. trading：独立调用（需要 .history() 取价格趋势/波动率）──
        trading_missing = [c for c in codes
                          if context.get_cache(f"{_CACHE_PREFIX}:trading:{c}") is None]
        if trading_missing:
            trading_fet = self._fetchers["trading"]
            total = len(trading_missing)
            ok_count = 0
            rescued = 0

            try:
                for chunk_idx in range(0, total, _CHUNK):
                    chunk = trading_missing[chunk_idx:chunk_idx + _CHUNK]
                    t0 = time.monotonic()

                    results = trading_fet.fetch(market, chunk)

                    for code, snap in results.items():
                        context.set_cache(f"{_CACHE_PREFIX}:trading:{code}", snap)
                        report.success.append(code)

                    chunk_ok = sum(1 for s in results.values()
                                  if getattr(s, "provider_status", {}).get("yfinance", "").startswith("ok"))
                    ok_count += chunk_ok
                    done = min(chunk_idx + _CHUNK, total)
                    chunk_ms = int((time.monotonic() - t0) * 1000)
                    print(f"  ⏳ [{market}] trading: {done}/{total} ({chunk_ok} ok, {chunk_ms}ms)")

                    if chunk_idx + _CHUNK < total:
                        time.sleep(min(1.5 + chunk_idx * 0.1, 5.0))

                if quote_ctx is not None:
                    from .builders import fill_snapshots_from_futu, _snapshot_is_empty
                    ck = f"{_CACHE_PREFIX}:trading"
                    pairs = [(c, context.get_cache(f"{ck}:{c}"))
                             for c in trading_missing
                             if context.get_cache(f"{ck}:{c}") is not None
                             and _snapshot_is_empty(context.get_cache(f"{ck}:{c}"))]
                    if pairs:
                        rescued = fill_snapshots_from_futu(market, pairs, quote_ctx)
                        if rescued > 0:
                            print(f"    ↳ [{market}] Futu 兜底: {rescued} 只 trading")

                mod_ms = int((time.monotonic() - started) * 1000)
                _gaps = total - ok_count
                parts = [f"{ok_count} ok"]
                if _gaps > 0:
                    parts.append(f"{_gaps} gaps")
                if rescued > 0:
                    parts.append(f"{rescued} futu兜底")
                print(f"  ✅ [{market}] trading: ({', '.join(parts)}) {mod_ms}ms")
                report.details["trading"] = f"{ok_count} ok"

            except Exception as e:
                print(f"  ❌ [{market}] trading: {e}")
                logger.error(f"trading 批量拉取失败: {e}")
                report.details["trading"] = f"error: {e}"
        else:
            if verbose:
                print(f"  ℹ [{market}] trading: 全部缓存命中")

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
