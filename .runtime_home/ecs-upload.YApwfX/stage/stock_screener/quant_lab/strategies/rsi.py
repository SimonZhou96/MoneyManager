from __future__ import annotations

import uuid

import pandas as pd
import numpy as np

from ..models import Signal
from .base import BaseStrategy
from .base import _register


@_register
class RSIStrategy(BaseStrategy):
    """RSI 策略 — RSI 低于超卖线买入，高于超买线卖出"""

    @staticmethod
    def strategy_type() -> str:
        return "rsi"

    @staticmethod
    def name() -> str:
        return "RSI"

    @staticmethod
    def param_definitions() -> dict:
        return {
            "period": {"type": "int", "default": 14, "min": 2, "max": 60, "label": "RSI 周期"},
            "oversold": {"type": "int", "default": 30, "min": 10, "max": 50, "label": "超卖阈值"},
            "overbought": {"type": "int", "default": 70, "min": 50, "max": 90, "label": "超买阈值"},
        }

    @staticmethod
    def _calc_rsi(close: pd.Series, period: int = 14) -> pd.Series:
        delta = close.diff()
        gain = delta.clip(lower=0)
        loss = (-delta).clip(lower=0)
        avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
        rs = avg_gain / avg_loss.replace(0, np.nan)
        rsi = 100 - (100 / (1 + rs))
        return rsi

    def generate_signals(self, df: pd.DataFrame) -> list[Signal]:
        period = int(self.config.params.get("period", 14))
        oversold = int(self.config.params.get("oversold", 30))
        overbought = int(self.config.params.get("overbought", 70))
        entry_side = self.config.entry_side

        df = df.sort_values("date").reset_index(drop=True)
        close = df["close"].astype(float)
        rsi_series = self._calc_rsi(close, period)

        prev_rsi = rsi_series.shift(1)

        enter_long = (prev_rsi < oversold) & (rsi_series >= oversold)
        exit_long = (prev_rsi > overbought) & (rsi_series <= overbought)

        signals: list[Signal] = []
        for i in range(1, len(df)):
            row = df.iloc[i]
            ts = row["date"]
            if hasattr(ts, "date"):
                ts = ts.date()

            if not pd.isna(rsi_series.iloc[i]):
                if enter_long.iloc[i] and entry_side in ("long", "both"):
                    signals.append(Signal(
                        signal_id=str(uuid.uuid4()),
                        ts=ts,
                        market="",
                        symbol="",
                        direction="buy",
                        reason=f"RSI超卖回升 RSI={rsi_series.iloc[i]:.1f}",
                        strength=1.0,
                    ))
                elif exit_long.iloc[i] and entry_side in ("long", "both"):
                    signals.append(Signal(
                        signal_id=str(uuid.uuid4()),
                        ts=ts,
                        market="",
                        symbol="",
                        direction="sell",
                        reason=f"RSI超买回落 RSI={rsi_series.iloc[i]:.1f}",
                        strength=1.0,
                    ))

        return signals
