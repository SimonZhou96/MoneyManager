import re
from dataclasses import dataclass
from typing import Dict, Iterable, List

import pandas as pd
import yfinance as yf

DEFAULT_TICKERS = [
    "0700.HK",
    "0939.HK",
    "0941.HK",
    "0992.HK",
    "1024.HK",
    "1038.HK",
    "1093.HK",
    "1113.HK",
    "1177.HK",
    "1211.HK",
    "1299.HK",
    "1810.HK",
    "1818.HK",
    "1918.HK",
    "1928.HK",
    "2007.HK",
    "2015.HK",
    "2269.HK",
    "2318.HK",
    "2319.HK",
    "2331.HK",
    "2382.HK",
    "2388.HK",
    "2628.HK",
    "3690.HK",
    "3968.HK",
    "3988.HK",
    "6098.HK",
    "6690.HK",
    "9618.HK",
    "9888.HK",
    "9898.HK",
    "9999.HK",
]


@dataclass(frozen=True)
class StockProfile:
    symbol: str
    name: str
    sector: str


def normalize_hk_ticker(raw: str) -> str:
    raw = raw.strip().upper()
    if not raw:
        return ""
    if raw.endswith(".HK"):
        return raw
    if raw.endswith("HK") and raw[:-2].isdigit():
        return raw[:-2].zfill(4) + ".HK"
    if raw.isdigit():
        return raw.zfill(4) + ".HK"
    if raw.endswith(".HKG"):
        return raw.replace(".HKG", ".HK")
    return raw


def parse_tickers(text: str) -> List[str]:
    parts = re.split(r"[,\s]+", text.strip())
    tickers = []
    seen = set()
    for part in parts:
        if not part:
            continue
        normalized = normalize_hk_ticker(part)
        if not normalized:
            continue
        if normalized not in seen:
            tickers.append(normalized)
            seen.add(normalized)
    return tickers


def download_price_history(ticker: str, period: str = "5y") -> pd.DataFrame:
    try:
        data = yf.download(
            ticker,
            period=period,
            interval="1d",
            auto_adjust=False,
            progress=False,
        )
    except Exception:
        return pd.DataFrame()
    if data.empty:
        return data
    data = data.dropna(how="any")
    data.index = pd.to_datetime(data.index)
    return data


def download_bulk_history(tickers: Iterable[str], period: str = "5y") -> Dict[str, pd.DataFrame]:
    tickers_list = list(tickers)
    try:
        data = yf.download(
            tickers=tickers_list,
            period=period,
            interval="1d",
            auto_adjust=False,
            group_by="ticker",
            progress=False,
        )
    except Exception:
        return {}
    results: Dict[str, pd.DataFrame] = {}
    if data.empty:
        return results

    if isinstance(data.columns, pd.MultiIndex):
        for ticker in tickers_list:
            if ticker not in data.columns.levels[0]:
                continue
            frame = data[ticker].dropna(how="any")
            if not frame.empty:
                frame.index = pd.to_datetime(frame.index)
                results[ticker] = frame
        return results

    data = data.dropna(how="any")
    data.index = pd.to_datetime(data.index)
    if tickers_list:
        results[tickers_list[0]] = data
    return results


def fetch_profile(ticker: str) -> StockProfile:
    try:
        info = yf.Ticker(ticker).info or {}
    except Exception:
        info = {}
    name = info.get("shortName") or info.get("longName") or ticker
    sector = info.get("sector") or info.get("industry") or "未知"
    return StockProfile(symbol=ticker, name=name, sector=sector)
