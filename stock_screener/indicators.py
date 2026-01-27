import math

import numpy as np
import pandas as pd


def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def wma(series: pd.Series, window: int) -> pd.Series:
    if window <= 1:
        return series.copy()

    weights = np.arange(1, window + 1, dtype=float)
    weight_sum = weights.sum()
    return series.rolling(window).apply(
        lambda values: float(np.dot(values, weights) / weight_sum),
        raw=True,
    )


def hma(series: pd.Series, window: int) -> pd.Series:
    if window <= 1:
        return series.copy()

    half_window = max(int(window / 2), 1)
    sqrt_window = max(int(math.sqrt(window)), 1)
    wma_full = wma(series, window)
    wma_half = wma(series, half_window)
    diff = 2 * wma_half - wma_full
    return wma(diff, sqrt_window)
