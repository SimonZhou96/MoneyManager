from __future__ import annotations

import uuid

import pandas as pd

from ..models import Signal
from .base import BaseStrategy
from .base import _register


@_register
class MACrossStrategy(BaseStrategy):
    """双均线交叉策略 — 快线上穿慢线买入，下穿卖出"""

    @staticmethod
    def strategy_type() -> str:
        return "ma_cross"

    @staticmethod
    def name() -> str:
        return "双均线交叉"

    @staticmethod
    def param_definitions() -> dict:
        return {
            "fast": {"type": "int", "default": 5, "min": 2, "max": 120, "label": "快线周期"},
            "slow": {"type": "int", "default": 20, "min": 5, "max": 250, "label": "慢线周期"},
        }

    def generate_signals(self, df: pd.DataFrame) -> list[Signal]:
        fast = int(self.config.params.get("fast", 5))
        slow = int(self.config.params.get("slow", 20))
        entry_side = self.config.entry_side

        df = df.sort_values("date").reset_index(drop=True)
        close = df["close"].astype(float)

        ema_fast = close.ewm(span=fast, adjust=False).mean()
        ema_slow = close.ewm(span=slow, adjust=False).mean()

        prev_fast = ema_fast.shift(1)
        prev_slow = ema_slow.shift(1)

        golden_cross = (prev_fast <= prev_slow) & (ema_fast > ema_slow)
        dead_cross = (prev_fast >= prev_slow) & (ema_fast < ema_slow)

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
                    reason=f"MA金叉 EMA{fast}↑EMA{slow}",
                    strength=1.0,
                ))
            elif dead_cross.iloc[i] and entry_side in ("long", "both"):
                signals.append(Signal(
                    signal_id=str(uuid.uuid4()),
                    ts=ts,
                    market="",
                    symbol="",
                    direction="sell",
                    reason=f"MA死叉 EMA{fast}↓EMA{slow}",
                    strength=1.0,
                ))

        return signals
