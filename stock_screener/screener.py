from dataclasses import dataclass
from datetime import date
from typing import Iterable, List, Optional, Tuple

import pandas as pd

from .data_sources import StockProfile
from .indicators import ema


@dataclass(frozen=True)
class ScreenerResult:
    symbol: str
    name: str
    sector: str
    last_close: float
    ema10: float
    ema150: float
    cross_date: date


def evaluate_cross(hist: pd.DataFrame) -> Tuple[bool, pd.Series, pd.Series]:
    close = hist["Close"]
    ema10 = ema(close, 10)
    ema150 = ema(close, 150)
    if len(close) < 151:
        return False, ema10, ema150
    crossed = ema10.iloc[-2] <= ema150.iloc[-2] and ema10.iloc[-1] > ema150.iloc[-1]
    return crossed, ema10, ema150


def build_result(
    profile: StockProfile,
    hist: pd.DataFrame,
    ema10: pd.Series,
    ema150: pd.Series,
) -> ScreenerResult:
    last_row = hist.iloc[-1]
    last_date = hist.index[-1].date()
    return ScreenerResult(
        symbol=profile.symbol,
        name=profile.name,
        sector=profile.sector,
        last_close=float(last_row["Close"]),
        ema10=float(ema10.iloc[-1]),
        ema150=float(ema150.iloc[-1]),
        cross_date=last_date,
    )


def screen_crosses(
    histories: Iterable[Tuple[StockProfile, pd.DataFrame]],
) -> List[ScreenerResult]:
    results: List[ScreenerResult] = []
    for profile, hist in histories:
        if hist.empty or "Close" not in hist.columns:
            continue
        crossed, ema10, ema150 = evaluate_cross(hist)
        if crossed:
            results.append(build_result(profile, hist, ema10, ema150))
    return results
