from __future__ import annotations

from typing import Dict, List, Tuple

import pandas as pd
import streamlit as st

from stock_screener.data_sources import (
    DEFAULT_TICKERS,
    StockProfile,
    download_bulk_history,
    fetch_profile,
    normalize_hk_ticker,
    parse_tickers,
)
from stock_screener.indicators import ema, hma
from stock_screener.plotting import build_candlestick_chart
from stock_screener.screener import build_result, evaluate_cross


st.set_page_config(page_title="HK EMA Crossover Screener", layout="wide")


@st.cache_data(ttl=6 * 60 * 60)
def cached_bulk_history(tickers: Tuple[str, ...], period: str) -> Dict[str, pd.DataFrame]:
    return download_bulk_history(list(tickers), period=period)


@st.cache_data(ttl=24 * 60 * 60)
def cached_profile(ticker: str) -> StockProfile:
    return fetch_profile(ticker)


def results_to_frame(results: List) -> pd.DataFrame:
    data = [
        {
            "Symbol": item.symbol,
            "Name": item.name,
            "Sector": item.sector,
            "Last Close": round(item.last_close, 3),
            "EMA10": round(item.ema10, 3),
            "EMA150": round(item.ema150, 3),
            "Cross Date": item.cross_date.isoformat(),
        }
        for item in results
    ]
    return pd.DataFrame(data)


def build_overlays(hist: pd.DataFrame) -> Dict[str, pd.Series]:
    close = hist["Close"]
    return {
        "EMA10": ema(close, 10),
        "EMA150": ema(close, 150),
        "HMA40": hma(close, 40),
        "HMA200": hma(close, 200),
        "HMA600": hma(close, 600),
    }


st.title("HK EMA10/EMA150 Crossover Screener")
st.write("Condition: EMA10 crosses above EMA150 on the most recent trading day.")

with st.sidebar:
    st.header("Universe")
    source = st.radio("Ticker source", ["Sample HK list", "Custom input"], index=0)
    if source == "Sample HK list":
        tickers = DEFAULT_TICKERS
        st.caption(f"Using {len(tickers)} sample HK tickers.")
    else:
        default_text = "\n".join(DEFAULT_TICKERS[:10])
        text = st.text_area("Enter HK tickers (one per line)", value=default_text, height=200)
        tickers = parse_tickers(text)

    period = st.selectbox("History period", ["3y", "5y", "10y"], index=1)
    run = st.button("Run screen", type="primary")

if run:
    if not tickers:
        st.warning("No tickers provided.")
        st.stop()

    normalized = [normalize_hk_ticker(ticker) for ticker in tickers]
    normalized = [ticker for ticker in normalized if ticker.endswith(".HK")]
    if not normalized:
        st.warning("No HK tickers detected (must end with .HK).")
        st.stop()

    with st.spinner("Downloading history and screening..."):
        histories = cached_bulk_history(tuple(normalized), period)
        results = []
        profile_cache: Dict[str, StockProfile] = {}
        for ticker in normalized:
            hist = histories.get(ticker)
            if hist is None or hist.empty:
                continue
            profile = cached_profile(ticker)
            profile_cache[ticker] = profile
            crossed, ema10, ema150 = evaluate_cross(hist)
            if crossed:
                results.append(build_result(profile, hist, ema10, ema150))

    st.session_state["screen_results"] = results
    st.session_state["histories"] = histories
    st.session_state["profiles"] = profile_cache

results = st.session_state.get("screen_results", [])
histories = st.session_state.get("histories", {})
profiles = st.session_state.get("profiles", {})

if results:
    frame = results_to_frame(results)
    st.subheader(f"Matches: {len(frame)}")
    for sector, group in frame.sort_values("Symbol").groupby("Sector"):
        st.markdown(f"#### {sector} ({len(group)})")
        st.dataframe(group.reset_index(drop=True), use_container_width=True)

    options = {f"{row.symbol} - {row.name}": row.symbol for row in results}
    selected_label = st.selectbox("Select a stock to chart", list(options.keys()))
    selected_symbol = options[selected_label]

    hist = histories.get(selected_symbol)
    if hist is not None and not hist.empty:
        overlay_series = build_overlays(hist)
        name = profiles.get(selected_symbol).name if selected_symbol in profiles else selected_symbol
        fig = build_candlestick_chart(hist, overlay_series, f"{selected_symbol} - {name}")
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.warning("No price data available for the selected stock.")
else:
    st.info("Run the screener to see matches.")
