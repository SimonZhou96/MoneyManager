#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
K线图表数据 API - 返回 OHLCV + EMA10 + EMA150 + RSI
"""

from typing import Optional

from fastapi import APIRouter, HTTPException

from kline_fetcher import KlineFetcherFactory
from market import normalize_market
from strategy import calculate_ema, calculate_rsi
from timeframe import parse_timeframe

router = APIRouter()


@router.get("/chart")
async def get_chart_data(
    code: str,
    market: str,
    timeframe: str = "1d",
    name: Optional[str] = None,
):
    """
    获取股票 K 线图表数据（含 EMA10、EMA150、RSI）

    Args:
        code: 股票代码（如 00700、000001、AAPL）
        market: 市场 HK / A / US
        timeframe: 时间周期 1d、1wk、1m 等

    Returns:
        {
            "code": "00700",
            "name": "腾讯控股",
            "market": "HK",
            "timeframe": "1d",
            "data": [
                {"time": "2025-01-01", "open": 100, "high": 102, "low": 99, "close": 101, "volume": 1000000, "ema10": 100.5, "ema150": 98.2, "rsi": 55.3},
                ...
            ],
            "latest_rsi": 55.3
        }
    """
    if not code or not market:
        raise HTTPException(status_code=400, detail="缺少 code 或 market 参数")

    try:
        market = normalize_market(market)
        timeframe = parse_timeframe(timeframe)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # 尝试连接 Futu OpenD（用于获取 K 线数据）
    quote_ctx = None
    try:
        from futu import OpenQuoteContext
        quote_ctx = OpenQuoteContext(host='127.0.0.1', port=11111)
    except Exception:
        # Futu 不可用时静默失败，使用其他数据源
        pass

    try:
        # 获取 K 线数据（传入 quote_ctx 以支持 Futu 获取器）
        fetchers = KlineFetcherFactory.create_fetcher_chain(quote_ctx=quote_ctx)
        df = None
        for fetcher in fetchers:
            try:
                df = fetcher.fetch(code, market=market, timeframe=timeframe)
                if df is not None and not df.empty:
                    break
            except Exception:
                continue

        if df is None or df.empty:
            raise HTTPException(status_code=404, detail=f"无法获取 {code} 的 K 线数据")
    finally:
        # 关闭 Futu 连接
        if quote_ctx is not None:
            try:
                quote_ctx.close()
            except Exception:
                pass

    # 标准化日期格式（日线 YYYY-MM-DD，分钟线 YYYY-MM-DD HH:mm）
    df = df.copy()
    def _fmt_time(x):
        if hasattr(x, "strftime"):
            if hasattr(x, "hour") and (getattr(x, "hour", 0) != 0 or getattr(x, "minute", 0) != 0):
                return x.strftime("%Y-%m-%dT%H:%M:%S")
            return x.strftime("%Y-%m-%d")
        return str(x)[:10]

    df["date"] = df["date"].apply(_fmt_time)

    # 计算 EMA10、EMA150、RSI
    close = df["close"].astype(float)
    df["ema10"] = calculate_ema(close, 10)
    df["ema150"] = calculate_ema(close, 150)
    df["rsi"] = calculate_rsi(close, period=14)

    # 构建返回数据
    data = []
    for _, row in df.iterrows():
        item = {
            "time": row["date"],
            "open": round(float(row["open"]), 4),
            "high": round(float(row["high"]), 4),
            "low": round(float(row["low"]), 4),
            "close": round(float(row["close"]), 4),
            "volume": int(row.get("volume", 0) or 0),
            "ema10": round(float(row["ema10"]), 4) if row["ema10"] == row["ema10"] else None,
            "ema150": round(float(row["ema150"]), 4) if row["ema150"] == row["ema150"] else None,
            "rsi": round(float(row["rsi"]), 2) if row["rsi"] == row["rsi"] else None,
        }
        data.append(item)

    # 最新 RSI
    latest_rsi = None
    if "rsi" in df.columns:
        last_rsi = df["rsi"].iloc[-1]
        if last_rsi == last_rsi:  # not NaN
            latest_rsi = round(float(last_rsi), 2)

    # 股票名称：前端可传入，否则使用默认
    display_name = (name or "").strip() or code
    if not name and display_name == code:
        if market == "HK":
            display_name = f"{code} (港股)"
        elif market == "A":
            display_name = f"{code} (A股)"
        elif market == "US":
            display_name = f"{code} (美股)"

    return {
        "code": code,
        "name": display_name,
        "market": market,
        "timeframe": timeframe,
        "data": data,
        "latest_rsi": latest_rsi,
    }
