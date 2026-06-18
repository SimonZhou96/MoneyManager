#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
市场缓存：在筛选开始时预计算 5 个市场级规则，结果缓存 60 分钟。
同一市场所有 Top20 股票共用一份 MarketTemperature，避免重复 API 调用。

Phase 1 实现：
- CreditRiskRegime (来自 YFinance ETF 代理指标)
- 其余 4 个维度为桩 (Phase 2-3 实现)
"""

from datetime import datetime, timezone
from typing import Dict, Optional

from .models import (
    MarketTemperature,
    CreditRiskResult,
    MarketBreadthResult,
    LiquidityNowcastResult,
    PolicyEventResult,
    CommodityShockResult,
)

try:
    from db import MarketDatabase, MySqlConfig

    _MARKET_DB_AVAILABLE = True
except Exception:
    MarketDatabase = None  # type: ignore[assignment]
    MySqlConfig = None  # type: ignore[assignment]
    _MARKET_DB_AVAILABLE = False


class MarketCache:
    """市场级规则缓存。"""

    def __init__(self):
        self._cache: Dict[str, MarketTemperature] = {}

    def get_or_compute(self, market: str) -> MarketTemperature:
        """获取市场温度计。如缓存未过期则直接返回；否则重新计算全部 5 个维度。"""
        cached = self._cache.get(market)
        if cached is not None and not cached.is_stale():
            return cached
        result = self._compute_all(market)
        result.computed_at = datetime.now(timezone.utc).isoformat()
        self._cache[market] = result
        return result

    def invalidate(self, market: str) -> None:
        """强制使指定市场的缓存失效。"""
        self._cache.pop(market, None)

    # ── 内部计算 ───────────────────────────────────────────────

    def _compute_all(self, market: str) -> MarketTemperature:
        """顺序计算 5 个维度。每个维度失败不阻塞其他维度。"""
        temp = MarketTemperature(market=market)

        temp.credit_risk = self._compute_credit_risk(market)
        temp.market_breadth = self._compute_market_breadth(market)
        temp.liquidity = self._compute_liquidity(market)
        temp.policy_event = self._compute_policy_event(market)
        temp.commodity_shock = self._compute_commodity_shock(market)

        self._build_summaries(temp)
        return temp

    def _compute_credit_risk(self, market: str) -> Optional[CreditRiskResult]:
        """用 YFinance ETF 代理指标计算信用风险。

        HK/US: HYG / LQD / JNK / IEF / VIX / DXY
        A: AKShare 信用利差（Phase 2 接入，Phase 1 用中性值兜底）
        """
        try:
            import yfinance as yf
            from yf_ratelimit import yf_sleep

            if market in ("HK", "US"):
                tickers = yf.Tickers("HYG LQD JNK IEF ^VIX DX-Y.NYB")
                yf_sleep()

                def _pct_change(hist, days=20):
                    if hist is None or hist.empty or len(hist) < days:
                        return 0.0
                    closes = hist["Close"]
                    return float((closes.iloc[-1] - closes.iloc[-days]) / closes.iloc[-days])

                def _max_drawdown(hist, days=20):
                    if hist is None or hist.empty or len(hist) < days:
                        return 0.0
                    closes = hist["Close"].tail(days)
                    peak = closes.cummax()
                    return float(((closes - peak) / peak).min())

                # 安全获取 ticker history，返回 None 而非抛异常
                def _get_hist(symbol, period="1mo"):
                    t = tickers.tickers.get(symbol)
                    if t is None:
                        return None
                    return t.history(period=period)

                hyg_hist = _get_hist("HYG", "1mo")
                lqd_hist = _get_hist("LQD", "1mo")

                hyg_ret = _pct_change(hyg_hist)
                lqd_ret = _pct_change(lqd_hist)
                hyg_dd = _max_drawdown(hyg_hist)

                score = 50.0
                if hyg_ret < -0.02:
                    score -= 20
                if hyg_ret - lqd_ret < -0.01:
                    score -= 15
                if hyg_dd > 0.03:
                    score -= 10
                if hyg_ret > 0.02:
                    score += 15

                vix_hist = _get_hist("^VIX", "5d")
                if vix_hist is not None and not vix_hist.empty:
                    vix = float(vix_hist["Close"].iloc[-1])
                else:
                    vix = 20.0
                if vix > 25:
                    score -= 10
                if vix < 15:
                    score += 10

                score = max(0.0, min(100.0, score))
                level = "low" if score >= 70 else ("elevated" if score >= 50 else "high")
                return CreditRiskResult(
                    score=score, level=level,
                    indicators={"hyg_ret": round(hyg_ret, 4), "lqd_ret": round(lqd_ret, 4), "vix": round(vix, 2)},
                    explanation=(
                        f"HYG近20日{'上涨' if hyg_ret > 0 else '下跌'}{abs(hyg_ret) * 100:.1f}%，"
                        f"VIX={vix:.1f}，信用环境{'稳定' if score >= 50 else '承压'}"
                    ),
                )

            elif market == "A":
                # Phase 1: A 股信用风险用 Shibor + 信用债收益率代理
                # 简化实现：返回中性
                return CreditRiskResult(
                    score=50.0, level="low",
                    explanation="A股信用风险暂用中性（Phase 2 接入 AKShare 信用利差）",
                )

            return None

        except Exception as e:
            return CreditRiskResult(
                score=50.0, level="low",
                explanation=f"信用风险评估异常: {e}",
            )

    def _compute_market_breadth(self, market: str) -> Optional[MarketBreadthResult]:
        """从 stock_kline_cache 表查询全市场 K 线数据，计算市场宽度指标。

        指标：
        - advance_decline_ratio: 最新一根 K 线上涨/下跌股票数比值
        - above_ma50_pct:    收盘价站上 MA50 的股票占比
        - above_ma200_pct:   收盘价站上 MA200 的股票占比
        - new_high_52w:      收盘价创 52 周（~250 交易日）新高的股票数
        - new_low_52w:       收盘价创 52 周新低的股票数

        评分模型：基线 50 分，按阈值加减，最终 clamp 0-100。
        """
        if not _MARKET_DB_AVAILABLE:
            return MarketBreadthResult(
                score=50.0,
                explanation="MySQL 驱动不可用，市场宽度暂未计算",
            )

        try:
            import os

            import pandas as pd

            config = MySqlConfig(
                host=os.getenv("MYSQL_HOST", "127.0.0.1"),
                port=int(os.getenv("MYSQL_PORT", "3306")),
                user=os.getenv("MYSQL_USER", "root"),
                password=os.getenv("MYSQL_PASSWORD", "123456"),
                database=os.getenv("MYSQL_DATABASE", "market_data"),
            )
            db = MarketDatabase(config)
            try:
                # 获取每个股票最近 250 根日 K 线收盘价（约 52 个交易周）
                # 用 ROW_NUMBER() 窗口函数取 TOP N
                sql = """
                    SELECT code, bar_time, close
                    FROM (
                        SELECT code, bar_time, close,
                               ROW_NUMBER() OVER (
                                   PARTITION BY code ORDER BY bar_time DESC
                               ) AS rn
                        FROM stock_kline_cache
                        WHERE market = %s AND timeframe = '1d'
                    ) ranked
                    WHERE rn <= 250
                    ORDER BY code, bar_time ASC
                """
                with db.conn.cursor() as cursor:
                    cursor.execute(sql, (market,))
                    rows = cursor.fetchall()
            finally:
                db.close()

            if not rows:
                return MarketBreadthResult(
                    score=50.0,
                    explanation=f"市场 {market} 暂无缓存 K 线数据",
                )

            df = pd.DataFrame(rows, columns=["code", "bar_time", "close"])
            df["close"] = df["close"].astype(float)

            total_stocks = df["code"].nunique()

            advance = 0
            decline = 0
            above_ma50 = 0
            above_ma200 = 0
            new_high = 0
            new_low = 0

            for _code, grp in df.groupby("code"):
                closes = grp["close"].values
                n = len(closes)

                if n < 2:
                    continue

                last_close = closes[-1]
                prev_close = closes[-2]

                # 涨跌比
                if last_close > prev_close:
                    advance += 1
                elif last_close < prev_close:
                    decline += 1

                # MA50
                if n >= 50:
                    ma50 = closes[-50:].mean()
                    if last_close > ma50:
                        above_ma50 += 1

                # MA200
                if n >= 200:
                    ma200 = closes[-200:].mean()
                    if last_close > ma200:
                        above_ma200 += 1

                # 52 周新高 / 新低
                window = closes[-min(250, n):]
                if last_close >= window.max():
                    new_high += 1
                if last_close <= window.min():
                    new_low += 1

            # 标准化为比例
            advance_decline_ratio = advance / max(decline, 1)
            above_ma50_pct = above_ma50 / max(total_stocks, 1)
            above_ma200_pct = above_ma200 / max(total_stocks, 1)

            # 评分：基线 50，按阈值加减
            score = 50.0
            if above_ma50_pct > 0.60:
                score += 25
            elif above_ma50_pct < 0.40:
                score -= 20

            if above_ma200_pct > 0.55:
                score += 15

            if advance_decline_ratio > 1.5:
                score += 10

            if new_high > new_low * 2:
                score += 10
            elif new_low > new_high * 2:
                score -= 15

            score = max(0.0, min(100.0, score))

            return MarketBreadthResult(
                score=score,
                above_ma50_pct=above_ma50_pct,
                above_ma200_pct=above_ma200_pct,
                advance_decline_ratio=advance_decline_ratio,
                new_high_52w=new_high,
                new_low_52w=new_low,
                explanation=(
                    f"市场宽度={above_ma50_pct:.0%}站上MA50、"
                    f"涨跌比={advance_decline_ratio:.2f}、"
                    f"新高={new_high}只/新低={new_low}只，"
                    f"综合评分={score:.1f}"
                ),
            )
        except Exception as e:
            return MarketBreadthResult(
                score=50.0,
                explanation=f"市场宽度计算异常: {e}",
            )

    def _compute_liquidity(self, market: str) -> Optional[LiquidityNowcastResult]:
        """流动性即时报：根据市场类型，从可用数据源计算资金流动性指标。

        A-share: 两融余额变化 + 北向资金 + 成交量（融资买入额代理）
        HK: 南向资金流向 + 恒指表现
        US: VIX 情绪指标 + ETF 资金流代理

        Phase 2: 可用数据源实现；不可用的 API 跳过，不阻断整体计算。
        """
        try:
            if market == "A":
                return self._compute_liquidity_a()
            elif market == "HK":
                return self._compute_liquidity_hk()
            elif market == "US":
                return self._compute_liquidity_us()
            return LiquidityNowcastResult(
                score=50.0,
                explanation=f"market='{market}' 暂无流动性计算实现",
            )
        except Exception as e:
            return LiquidityNowcastResult(
                score=50.0,
                explanation=f"流动性计算异常: {e}",
            )

    def _compute_liquidity_a(self) -> LiquidityNowcastResult:
        """A 股流动性：两融余额 + 北向资金 + 成交量（融资买入额代理）。"""
        import akshare
        import pandas as pd

        metrics: Dict[str, float] = {}
        score_delta = 0.0
        factors: list[str] = []

        # ── 1. 两融余额变化（5 日百分比）────────────────────────────────
        try:
            df_sh = akshare.macro_china_market_margin_sh()
            df_sz = akshare.macro_china_market_margin_sz()

            if not df_sh.empty and not df_sz.empty and "融资融券余额" in df_sh.columns:
                sh_bal = df_sh[["日期", "融资融券余额"]].copy()
                sh_bal.columns = ["date", "margin_sh"]
                sz_bal = df_sz[["日期", "融资融券余额"]].copy()
                sz_bal.columns = ["date", "margin_sz"]

                merged = pd.merge(sh_bal, sz_bal, on="date", how="outer")
                merged["total_margin"] = (
                    merged["margin_sh"].fillna(0) + merged["margin_sz"].fillna(0)
                )
                merged = merged.sort_values("date").reset_index(drop=True)

                if len(merged) >= 6:
                    latest = float(merged["total_margin"].iloc[-1])
                    prev = float(merged["total_margin"].iloc[-6])  # 5 个交易日前
                    margin_pct = (latest - prev) / max(prev, 1) * 100
                    metrics["margin_balance_5d_pct"] = round(margin_pct, 2)

                    if margin_pct > 2.0:
                        score_delta += 15
                        factors.append(
                            f"两融余额{margin_pct:+.2f}%(增量>2%→+15)"
                        )
                    elif margin_pct < -2.0:
                        score_delta -= 10
                        factors.append(
                            f"两融余额{margin_pct:+.2f}%(缩减>2%→-10)"
                        )
                    else:
                        factors.append(f"两融余额{margin_pct:+.2f}%(中性)")
            else:
                factors.append("两融余额数据不足")
        except Exception as e:
            factors.append(f"两融余额异常: {e}")

        # ── 2. 北向资金流向（当日净买卖额方向）───────────────────────────
        try:
            df_nb = akshare.stock_hsgt_fund_flow_summary_em()
            if df_nb is not None and not df_nb.empty:
                nb_rows = df_nb[df_nb["资金方向"] == "北向"]
                if not nb_rows.empty:
                    net_buy = float(nb_rows["成交净买额"].sum())
                    metrics["northbound_net_buy"] = round(net_buy, 2)
                    if net_buy > 0 and net_buy < 1e7:  # 正常正值
                        score_delta += 10
                        factors.append(f"北向净买入{net_buy:+.2f}亿(+10)")
                    elif net_buy < -1e5:  # 确实为负
                        factors.append(f"北向净卖出{net_buy:+.2f}亿(中性)")
                    else:
                        # 今日数据尚未更新（盘中），尝试历史累计趋势
                        try:
                            df_hist = akshare.stock_hsgt_hist_em(
                                symbol="北向资金"
                            )
                            if (
                                df_hist is not None
                                and not df_hist.empty
                                and "历史累计净买额" in df_hist.columns
                            ):
                                hist_vals = df_hist["历史累计净买额"].dropna().tail(5)
                                if len(hist_vals) >= 2:
                                    hist_trend = (
                                        float(hist_vals.iloc[-1])
                                        - float(hist_vals.iloc[0])
                                    )
                                    if hist_trend > 50:
                                        score_delta += 5
                                        factors.append(
                                            f"北向累计趋势向上(+5)"
                                        )
                                    elif hist_trend > 0:
                                        factors.append(f"北向累计趋势偏强(中性)")
                                    else:
                                        factors.append(f"北向累计趋势偏弱(中性)")
                                else:
                                    factors.append("北向数据暂缺")
                            else:
                                factors.append("北向数据暂缺")
                        except Exception:
                            factors.append("北向数据暂缺")
                else:
                    factors.append("无北向数据")
            else:
                factors.append("北向数据接口异常")
        except Exception as e:
            factors.append(f"北向资金异常: {e}")

        # ── 3. 成交量变化（融资买入额 5 日百分比作为代理）──────────────────
        try:
            df_sh = akshare.macro_china_market_margin_sh()
            df_sz = akshare.macro_china_market_margin_sz()

            if not df_sh.empty and not df_sz.empty and "融资买入额" in df_sh.columns:
                buy_sh = df_sh[["日期", "融资买入额"]].copy()
                buy_sh.columns = ["date", "buy_sh"]
                buy_sz = df_sz[["日期", "融资买入额"]].copy()
                buy_sz.columns = ["date", "buy_sz"]

                merged_buy = pd.merge(buy_sh, buy_sz, on="date", how="outer")
                merged_buy["total_buy"] = (
                    merged_buy["buy_sh"].fillna(0) + merged_buy["buy_sz"].fillna(0)
                )
                merged_buy = merged_buy.sort_values("date").reset_index(drop=True)

                if len(merged_buy) >= 6:
                    latest_buy = float(merged_buy["total_buy"].iloc[-1])
                    prev_buy = float(merged_buy["total_buy"].iloc[-6])
                    volume_pct = (latest_buy - prev_buy) / max(prev_buy, 1) * 100
                    metrics["volume_5d_pct"] = round(volume_pct, 2)

                    if volume_pct > 10.0:
                        score_delta += 10
                        factors.append(f"融资买入额{volume_pct:+.2f}%(增量>10%→+10)")
                    elif volume_pct < -10.0:
                        score_delta -= 10
                        factors.append(f"融资买入额{volume_pct:+.2f}%(缩减>10%→-10)")
                    else:
                        factors.append(f"融资买入额{volume_pct:+.2f}%(中性)")
            else:
                factors.append("成交量数据不足")
        except Exception as e:
            factors.append(f"成交量异常: {e}")

        # ── 4. 行业资金持续流入（API 暂不可用，跳过）──────────────────
        # stock_sector_fund_flow_rank / stock_sector_fund_flow_hist 暂不可用

        # ── 聚合评分 ──────────────────────────────────────────────
        score = max(0.0, min(100.0, 50.0 + score_delta))
        if score >= 55:
            direction = "inflow"
        elif score <= 45:
            direction = "outflow"
        else:
            direction = "neutral"

        explanation = "A股流动性: " + "; ".join(factors)
        return LiquidityNowcastResult(
            score=score,
            fund_flow_direction=direction,
            metrics=metrics,
            explanation=explanation,
            data_sources=["AKShare"],
        )

    def _compute_liquidity_hk(self) -> LiquidityNowcastResult:
        """港股流动性：南向资金流向 + 恒指表现。"""
        import akshare
        import yfinance as yf
        from yf_ratelimit import yf_sleep

        metrics: Dict[str, float] = {}
        score_delta = 0.0
        factors: list[str] = []
        data_sources: list[str] = []

        # ── 1. 南向资金 5 日净买卖方向 ──────────────────────────────
        try:
            df = akshare.stock_hsgt_hist_em(symbol="南向资金")
            if df is not None and not df.empty and "当日成交净买额" in df.columns:
                valid = df.dropna(subset=["当日成交净买额"]).tail(5)
                if len(valid) >= 2:
                    south_5d_sum = float(valid["当日成交净买额"].astype(float).sum())
                    metrics["southbound_5d_sum"] = round(south_5d_sum, 2)
                    data_sources.append("AKShare")
                    if south_5d_sum > 20:
                        score_delta += 10
                        factors.append(
                            f"南向5日净买入{south_5d_sum:+.2f}亿(净流入+10)"
                        )
                    elif south_5d_sum < -20:
                        score_delta -= 5
                        factors.append(
                            f"南向5日净卖出{south_5d_sum:+.2f}亿(净流出-5)"
                        )
                    else:
                        factors.append(
                            f"南向5日资金{south_5d_sum:+.2f}亿(中性)"
                        )
                else:
                    factors.append("南向数据不足")
            else:
                factors.append("无南向数据")
        except Exception as e:
            factors.append(f"南向资金异常: {e}")

        # ── 2. 恒指 5 日表现 ──────────────────────────────────────
        try:
            yf_sleep()
            hsi = yf.Ticker("^HSI")
            hist = hsi.history(period="1mo")
            if hist is not None and not hist.empty and len(hist) >= 5:
                recent = hist["Close"].tail(5)
                hsi_5d_pct = (
                    (float(recent.iloc[-1]) - float(recent.iloc[0]))
                    / float(recent.iloc[0])
                    * 100
                )
                metrics["hsi_5d_pct"] = round(hsi_5d_pct, 2)
                data_sources.append("YFinance")
                if hsi_5d_pct > 3.0:
                    score_delta += 10
                    factors.append(f"恒指5日{hsi_5d_pct:+.2f}%(上涨+10)")
                elif hsi_5d_pct < -3.0:
                    score_delta -= 10
                    factors.append(f"恒指5日{hsi_5d_pct:+.2f}%(下跌-10)")
                else:
                    factors.append(f"恒指5日{hsi_5d_pct:+.2f}%(中性)")
            else:
                factors.append("恒指数据不足")
        except Exception as e:
            factors.append(f"恒指异常: {e}")

        score = max(0.0, min(100.0, 50.0 + score_delta))
        if score >= 55:
            direction = "inflow"
        elif score <= 45:
            direction = "outflow"
        else:
            direction = "neutral"

        explanation = "港股流动性: " + "; ".join(factors)
        data_sources = list(dict.fromkeys(data_sources)) if data_sources else ["YFinance"]
        return LiquidityNowcastResult(
            score=score,
            fund_flow_direction=direction,
            metrics=metrics,
            explanation=explanation,
            data_sources=data_sources if data_sources else ["YFinance"],
        )

    def _compute_liquidity_us(self) -> LiquidityNowcastResult:
        """美股流动性：VIX 情绪指标 + ETF 资金流代理。"""
        import yfinance as yf
        from yf_ratelimit import yf_sleep

        metrics: Dict[str, float] = {}
        score_delta = 0.0
        factors: list[str] = []
        data_sources: list[str] = []

        # ── 1. VIX 情绪指标 ────────────────────────────────────────
        try:
            yf_sleep()
            vix = yf.Ticker("^VIX")
            vix_hist = vix.history(period="1mo")
            if vix_hist is not None and not vix_hist.empty:
                vix_last = float(vix_hist["Close"].iloc[-1])
                metrics["vix"] = round(vix_last, 2)
                data_sources.append("YFinance")

                # VIX 绝对值
                if vix_last < 18:
                    score_delta += 10
                    factors.append(f"VIX={vix_last:.1f}(低波动+10)")
                elif vix_last > 25:
                    score_delta -= 10
                    factors.append(f"VIX={vix_last:.1f}(恐慌-10)")
                else:
                    factors.append(f"VIX={vix_last:.1f}(中性)")

                # VIX 变化方向
                if len(vix_hist) >= 5:
                    vix_prev = float(vix_hist["Close"].iloc[-5])
                    vix_chg = (vix_last - vix_prev) / max(vix_prev, 1) * 100
                    metrics["vix_5d_chg_pct"] = round(vix_chg, 2)
                    if vix_chg > 15:
                        score_delta -= 5
                        factors.append(f"VIX近日飙升{vix_chg:+.0f}%(-5)")
            else:
                factors.append("VIX数据不足")
        except Exception as e:
            factors.append(f"VIX异常: {e}")

        # ── 2. SPY 5 日表现（ETF 资金流代理）────────────────────────
        try:
            yf_sleep()
            spy = yf.Ticker("SPY")
            spy_hist = spy.history(period="1mo")
            if spy_hist is not None and not spy_hist.empty and len(spy_hist) >= 5:
                recent = spy_hist["Close"].tail(5)
                spy_5d_pct = (
                    (float(recent.iloc[-1]) - float(recent.iloc[0]))
                    / float(recent.iloc[0])
                    * 100
                )
                metrics["spy_5d_pct"] = round(spy_5d_pct, 2)
                data_sources.append("YFinance")
                if spy_5d_pct > 2.0:
                    score_delta += 10
                    factors.append(f"SPY 5日{spy_5d_pct:+.2f}%(上涨+10)")
                elif spy_5d_pct < -2.0:
                    score_delta -= 5
                    factors.append(f"SPY 5日{spy_5d_pct:+.2f}%(下跌-5)")
                else:
                    factors.append(f"SPY 5日{spy_5d_pct:+.2f}%(中性)")
            else:
                factors.append("SPY数据不足")
        except Exception as e:
            factors.append(f"SPY异常: {e}")

        score = max(0.0, min(100.0, 50.0 + score_delta))
        if score >= 55:
            direction = "inflow"
        elif score <= 45:
            direction = "outflow"
        else:
            direction = "neutral"

        explanation = "美股流动性: " + "; ".join(factors)
        # 去重数据来源
        data_sources = list(dict.fromkeys(data_sources)) if data_sources else ["YFinance"]
        return LiquidityNowcastResult(
            score=score,
            fund_flow_direction=direction,
            metrics=metrics,
            explanation=explanation,
            data_sources=data_sources,
        )

    def _compute_policy_event(self, market: str) -> Optional[PolicyEventResult]:
        """政策事件风险（Phase 3 实现，Phase 1 返回桩）。"""
        return PolicyEventResult(score=50.0, explanation="政策事件暂未评估（Phase 3 实现）")

    def _compute_commodity_shock(self, market: str) -> Optional[CommodityShockResult]:
        """用 YFinance 期货数据计算大宗商品冲击。

        5 个商品期货的 YFinance 代码：
        - CL=F: WTI 原油
        - HG=F: 铜
        - GC=F: 黄金
        - NG=F: 天然气
        - SI=F: 白银

        冲击判定：abs(5d_return) > 5% 记为冲击。
        严重冲击（magnitude > 10%）每条 -10 分。
        多个冲击同时出现加额外不确定性扣分。
        无冲击 → 50 分（中性）；无显著波动（所有 abs(5d) < 2%）→ +5 分。

        商品价格是全球性的，market 参数仅为接口一致性保留。
        """
        try:
            import yfinance as yf
            from yf_ratelimit import yf_sleep

            futures = {
                "原油": "CL=F",    # WTI crude
                "铜": "HG=F",     # Copper
                "黄金": "GC=F",   # Gold
                "天然气": "NG=F", # Natural gas
                "白银": "SI=F",   # Silver
            }

            impact_map = {
                "原油": {"up": "航空/化工下游承压", "down": "降低通胀预期"},
                "铜": {"up": "工业金属/新能源利好", "down": "工业需求走弱信号"},
                "黄金": {"up": "避险情绪上升", "down": "风险偏好改善"},
                "天然气": {"up": "能源成本上升", "down": "能源成本缓解"},
                "白银": {"up": "贵金属/光伏利好", "down": "工业需求减弱"},
            }

            shocks: Dict[str, Dict[str, Any]] = {}
            severe_shock_count = 0  # magnitude > 10%
            any_shock = False
            max_abs_5d = 0.0

            for name, symbol in futures.items():
                yf_sleep()
                ticker = yf.Ticker(symbol)
                hist = ticker.history(period="1mo")

                if hist is None or hist.empty or len(hist) < 5:
                    continue

                closes = hist["Close"]

                # 5-day return
                d5_ret = (float(closes.iloc[-1]) - float(closes.iloc[-5])) / float(closes.iloc[-5])

                # 20-day return
                if len(closes) >= 20:
                    d20_ret = (float(closes.iloc[-1]) - float(closes.iloc[-20])) / float(closes.iloc[-20])
                else:
                    d20_ret = 0.0

                abs_5d = abs(d5_ret)
                max_abs_5d = max(max_abs_5d, abs_5d)

                if abs_5d > 0.05:
                    any_shock = True
                    direction = "up" if d5_ret > 0 else "down"
                    impact = impact_map[name][direction]

                    shocks[name] = {
                        "direction": direction,
                        "magnitude": round(abs_5d, 4),
                        "trend_20d": round(d20_ret, 4),
                        "impact": impact,
                    }

                    if abs_5d > 0.10:
                        severe_shock_count += 1

            # ── 评分 ─────────────────────────────────────────────
            score = 50.0
            if not any_shock:
                if max_abs_5d < 0.02:
                    score += 5  # 无显著波动
            else:
                # 严重冲击：每条 -10
                score -= severe_shock_count * 10

                # 多个冲击同时出现：附加不确定性扣分
                if len(shocks) >= 2:
                    score -= 5
                if len(shocks) >= 4:
                    score -= 5

            score = max(0.0, min(100.0, score))

            # ── 文本解释 ─────────────────────────────────────────
            if not shocks:
                explanation = "大宗商品近5日无明显波动，市场影响中性"
            else:
                shock_names = "、".join(shocks.keys())
                impacts = [s["impact"] for s in shocks.values()]
                unique_impacts = list(dict.fromkeys(impacts))  # 去重
                explanation = f"{shock_names}近5日波动较大，{'；'.join(unique_impacts)}"

            return CommodityShockResult(
                score=score,
                shocks=shocks,
                explanation=explanation,
            )

        except Exception as e:
            return CommodityShockResult(
                score=50.0,
                explanation=f"大宗商品冲击计算异常: {e}",
            )

    # ── 摘要生成 ───────────────────────────────────────────────

    def _build_summaries(self, temp: MarketTemperature) -> None:
        """根据 5 个维度结果生成面向报告的摘要文本。"""
        breadth = temp.market_breadth
        credit = temp.credit_risk
        liquidity = temp.liquidity

        # 市场环境
        if breadth is not None and breadth.score >= 60:
            temp.temperature_summary = "偏强"
        elif breadth is not None and breadth.score >= 40:
            temp.temperature_summary = "中性"
        else:
            temp.temperature_summary = "偏弱"

        # 赚钱效应
        if breadth is not None and breadth.above_ma50_pct > 0.55:
            temp.money_making_summary = "好"
        elif breadth is not None and breadth.above_ma50_pct > 0.35:
            temp.money_making_summary = "一般"
        else:
            temp.money_making_summary = "差"

        # 资金环境
        if liquidity is not None:
            if liquidity.fund_flow_direction == "inflow":
                temp.capital_env_summary = "流入"
            elif liquidity.fund_flow_direction == "outflow":
                temp.capital_env_summary = "流出"
            else:
                temp.capital_env_summary = "中性"
        else:
            temp.capital_env_summary = "中性"

        # 风险偏好
        if credit is not None:
            if credit.level == "low":
                temp.risk_appetite_summary = "高"
            elif credit.level == "elevated":
                temp.risk_appetite_summary = "中"
            else:
                temp.risk_appetite_summary = "低"
        else:
            temp.risk_appetite_summary = "中"

        # 热点清晰度（Phase 3 接入 HotSectorClassifier 后更新）
        temp.hot_clarity_summary = "一般"
