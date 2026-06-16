from __future__ import annotations

import uuid

import pandas as pd

from ..models import Signal
from .base import BaseStrategy
from .base import _register


@_register
class MACDStrategy(BaseStrategy):
    """MACD 策略 — DIF 上穿 DEA 买入，下穿卖出"""

    @staticmethod
    def strategy_type() -> str:
        return "macd"

    @staticmethod
    def name() -> str:
        return "MACD"

    @staticmethod
    def param_definitions() -> dict:
        return {
            "fast": {"type": "int", "default": 12, "min": 2, "max": 60, "label": "快线"},
            "slow": {"type": "int", "default": 26, "min": 5, "max": 120, "label": "慢线"},
            "signal": {"type": "int", "default": 9, "min": 2, "max": 30, "label": "信号线"},
        }

    def generate_signals(self, df: pd.DataFrame) -> list[Signal]:
        fast = int(self.config.params.get("fast", 12))
        slow = int(self.config.params.get("slow", 26))
        sig_period = int(self.config.params.get("signal", 9))
        entry_side = self.config.entry_side

        df = df.sort_values("date").reset_index(drop=True)
        close = df["close"].astype(float)

        ema_fast = close.ewm(span=fast, adjust=False).mean()
        ema_slow = close.ewm(span=slow, adjust=False).mean()
        dif = ema_fast - ema_slow
        dea = dif.ewm(span=sig_period, adjust=False).mean()

        prev_dif = dif.shift(1)
        prev_dea = dea.shift(1)

        golden_cross = (prev_dif <= prev_dea) & (dif > dea)
        dead_cross = (prev_dif >= prev_dea) & (dif < dea)

        signals: list[Signal] = []
        for i in range(1, len(df)):
            row = df.iloc[i]
            ts = row["date"]
            if hasattr(ts, "date"):
                ts = ts.date()

            if golden_cross.iloc[i] and entry_side in ("long", "both"):
                signals.append(Signal(
                    signal_id=str(uuid.uuid4()),
                    ts=ts,
                    market="",
                    symbol="",
                    direction="buy",
                    reason=f"MACD金叉 DIF↑DEA",
                    strength=1.0,
                ))
            elif dead_cross.iloc[i] and entry_side in ("long", "both"):
                signals.append(Signal(
                    signal_id=str(uuid.uuid4()),
                    ts=ts,
                    market="",
                    symbol="",
                    direction="sell",
                    reason=f"MACD死叉 DIF↓DEA",
                    strength=1.0,
                ))

        return signals
