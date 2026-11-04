from __future__ import annotations

import uuid

import numpy as np
import pandas as pd

from ..models import Signal
from .base import BaseStrategy, _register


@_register
class EnergyPhaseStrategy(BaseStrategy):
    """六态能量相位策略 — 进入 RELEASE/TRENDING 买入，离开则卖出。

    基于物理势能/动能隐喻，将股票价格运动分为六种状态：
      COMPRESS (势能积蓄) → RELEASE (势能释放) → TRENDING (动能主导)
      → EXHAUSTION (动能衰竭) → PEAK (到顶) → CRASH (空方动能)
    """

    @staticmethod
    def strategy_type() -> str:
        return "energy_phase"

    @staticmethod
    def name() -> str:
        return "能量相位"

    @staticmethod
    def param_definitions() -> dict:
        return {
            "ma_period": {"type": "int", "default": 20, "min": 5, "max": 60, "label": "均线周期"},
            "pe_threshold": {"type": "float", "default": 100.0, "min": 20.0, "max": 500.0, "label": "势能阈值"},
            "ke_threshold": {"type": "float", "default": 4.0, "min": 1.0, "max": 30.0, "label": "动能沉寂阈值"},
            "epr_release_threshold": {"type": "float", "default": 0.1, "min": 0.01, "max": 2.0, "step": 0.01, "label": "动能占比阈值"},
            "ke_decay_exhaustion": {"type": "float", "default": 0.5, "min": 0.1, "max": 0.9, "step": 0.05, "label": "衰竭衰减阈值"},
            "ke_decay_peak": {"type": "float", "default": 0.8, "min": 0.3, "max": 0.95, "step": 0.05, "label": "到顶衰减阈值"},
            "min_rows": {"type": "int", "default": 30, "min": 10, "max": 120, "label": "最少K线数"},
        }

    # ── 固定窗口参数（不暴露给用户调参，保持与策略设计一致） ──
    CONSISTENCY_WINDOW = 10
    DELTA_WINDOW = 5
    LOOKBACK_PE_DAYS = 3

    def _classify_bar(self, cur: dict) -> str | None:
        """对单根 bar 的指标值应用六态判定逻辑，返回状态名。

        优先级：CRASH > PEAK > EXHAUSTION > RELEASE > TRENDING > COMPRESS
        """
        pe_threshold = float(self.config.params.get("pe_threshold", 100.0))
        ke_threshold = float(self.config.params.get("ke_threshold", 4.0))
        epr_release = float(self.config.params.get("epr_release_threshold", 0.1))
        decay_exhaustion = float(self.config.params.get("ke_decay_exhaustion", 0.5))
        decay_peak = float(self.config.params.get("ke_decay_peak", 0.8))

        # CRASH
        if (
            cur["ke_negative_streak"] >= 5
            and cur["pe_norm"] > pe_threshold
            and cur["ke_path"] < -20.0
        ):
            return "CRASH"

        # PEAK
        if (
            cur["ke_decay"] > decay_peak
            and cur["pe_norm"] > 80.0
            and abs(cur["ke_signed"]) < 1.0
            and cur["delta_e"] < -10.0
        ):
            return "PEAK"

        # EXHAUSTION
        if (
            cur["ke_decay"] > decay_exhaustion
            and cur["pe_rising"]
            and abs(cur["ke_signed"]) > 0.0
        ):
            return "EXHAUSTION"

        # RELEASE (bullish entry)
        if (
            cur["ke_signed"] > 0.0
            and cur["delta_e"] > 10.0
            and cur["epr"] > epr_release
        ):
            return "RELEASE"

        # TRENDING (bullish hold)
        if (
            cur["ke_consistency"] > 0.7
            and cur["ke_signed"] > 0.0
            and cur["pe_norm"] < pe_threshold
            and cur["ke_path"] > 0.0
        ):
            return "TRENDING"

        # COMPRESS
        if (
            cur["pe_norm"] > pe_threshold
            and abs(cur["ke_signed"]) < ke_threshold
            and cur["ke_consistency"] < 0.3
            and cur["ke_path"] < 0.0
        ):
            return "COMPRESS"

        return None

    def generate_signals(self, df: pd.DataFrame) -> list[Signal]:
        ma_period = int(self.config.params.get("ma_period", 20))
        min_rows = int(self.config.params.get("min_rows", 30))
        entry_side = self.config.entry_side

        df = df.sort_values("date").reset_index(drop=True)
        close = df["close"].astype(float)

        n = len(df)
        if n < min_rows:
            return []

        # ── 1. 向量化计算所有能量指标 ──
        ret_pct = close.pct_change() * 100.0
        ke_signed = np.sign(ret_pct) * (ret_pct ** 2)

        # SMA + PE
        ma = close.rolling(window=ma_period, min_periods=ma_period).mean()
        pe_raw = (close - ma) / ma * 100.0
        pe_norm = pe_raw ** 2

        # KE_peak (2×ma_period rolling max)
        ke_peak_window = ma_period * 2
        ke_peak = ke_signed.rolling(window=ke_peak_window, min_periods=ma_period).max()

        # KE_decay
        ke_decay = 1.0 - ke_signed / ke_peak.replace(0, np.nan)
        ke_decay = ke_decay.fillna(0).replace([np.inf, -np.inf], 0)
        ke_decay = ke_decay.clip(0, 1)

        # KE_consistency
        ke_positive = (ke_signed > 0).astype(float)
        ke_consistency = ke_positive.rolling(
            window=self.CONSISTENCY_WINDOW, min_periods=self.CONSISTENCY_WINDOW
        ).mean()
        ke_consistency = ke_consistency.fillna(0)

        # DeltaE_5
        ke_diff = ke_signed.diff()
        delta_e = ke_diff.rolling(window=self.DELTA_WINDOW, min_periods=self.DELTA_WINDOW).sum()
        delta_e = delta_e.fillna(0)

        # KE_path
        ke_path = ke_signed.rolling(
            window=self.CONSISTENCY_WINDOW, min_periods=self.CONSISTENCY_WINDOW
        ).sum()
        ke_path = ke_path.fillna(0)

        # EPR
        epr = ke_signed.abs() / (pe_norm + 1e-10)
        epr = epr.fillna(0).replace([np.inf, -np.inf], 0)

        # ke_negative_streak
        ke_neg = (ke_signed < 0).astype(int)
        ke_neg_streak = ke_neg.groupby((ke_signed >= 0).astype(int).cumsum()).cumsum()

        # PE rising/falling
        pe_rising = pe_norm > pe_norm.shift(self.LOOKBACK_PE_DAYS)
        pe_rising = pe_rising.fillna(False)

        # ── 2. 逐 bar 判定状态并生成信号 ──
        prev_state: str | None = None
        signals: list[Signal] = []

        for i in range(min_rows, n):
            cur = {
                "ke_signed": float(ke_signed.iloc[i]) if not pd.isna(ke_signed.iloc[i]) else 0.0,
                "pe_norm": float(pe_norm.iloc[i]) if not pd.isna(pe_norm.iloc[i]) else 0.0,
                "ke_decay": float(ke_decay.iloc[i]),
                "ke_consistency": float(ke_consistency.iloc[i]),
                "delta_e": float(delta_e.iloc[i]),
                "ke_path": float(ke_path.iloc[i]),
                "epr": float(epr.iloc[i]),
                "ke_negative_streak": int(ke_neg_streak.iloc[i]) if not pd.isna(ke_neg_streak.iloc[i]) else 0,
                "pe_rising": bool(pe_rising.iloc[i]),
            }

            state = self._classify_bar(cur)
            if state is None:
                state = "UNKNOWN"

            bullish = state in ("RELEASE", "TRENDING")
            was_bullish = prev_state in ("RELEASE", "TRENDING")

            row = df.iloc[i]
            ts = row["date"]
            if hasattr(ts, "date"):
                ts = ts.date()

            if bullish and not was_bullish and entry_side in ("long", "both"):
                signals.append(Signal(
                    signal_id=str(uuid.uuid4()),
                    ts=ts,
                    market="",
                    symbol="",
                    direction="buy",
                    reason=f"能量相位入场 {state} KE={cur['ke_signed']:.1f} EPR={cur['epr']:.2f}",
                    strength=1.0,
                ))
            elif not bullish and was_bullish and entry_side in ("long", "both"):
                signals.append(Signal(
                    signal_id=str(uuid.uuid4()),
                    ts=ts,
                    market="",
                    symbol="",
                    direction="sell",
                    reason=f"能量相位离场 → {state} KE_decay={cur['ke_decay']:.2f}",
                    strength=1.0,
                ))

            prev_state = state

        return signals
