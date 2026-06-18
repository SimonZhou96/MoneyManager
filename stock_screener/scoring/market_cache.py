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
        """流动性即时报（Phase 2 实现，Phase 1 返回桩）。"""
        return LiquidityNowcastResult(score=50.0, explanation="流动性暂未计算（Phase 2 实现）")

    def _compute_policy_event(self, market: str) -> Optional[PolicyEventResult]:
        """政策事件风险（Phase 3 实现，Phase 1 返回桩）。"""
        return PolicyEventResult(score=50.0, explanation="政策事件暂未评估（Phase 3 实现）")

    def _compute_commodity_shock(self, market: str) -> Optional[CommodityShockResult]:
        """大宗商品冲击（Phase 2 实现，Phase 1 返回桩）。"""
        return CommodityShockResult(score=50.0, explanation="商品冲击暂未计算（Phase 2 实现）")

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
