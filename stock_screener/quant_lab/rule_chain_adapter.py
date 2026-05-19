from __future__ import annotations

import uuid
from datetime import date
from enum import Enum
from typing import Iterable, Optional

from filters import FilterContext, StockInfo
from rule_engine import RuleChainConfig

from .models import Signal


class RuleChainStrategyAdapter:
    def __init__(self, chain: RuleChainConfig, rule_engine, exit_policy: Optional[dict] = None):
        self.chain = chain
        self.rule_engine = rule_engine
        self.exit_policy = exit_policy or {}
        self.mode = "trade_backtest" if self.exit_policy else "signal_analysis"

    def evaluate(self, stock: StockInfo, dates: Iterable[date]) -> list[Signal]:
        signals: list[Signal] = []
        date_list = list(dates)
        next_entry_index = 0
        for index, item in enumerate(date_list):
            if index < next_entry_index:
                continue
            context = FilterContext(check_date=item, market=stock.market)
            result = self.rule_engine.evaluate_stock(stock, context)
            if not getattr(result, "passed", False):
                continue
            buy_signal = (
                Signal(
                    signal_id=str(uuid.uuid4()),
                    ts=item,
                    market=stock.market,
                    symbol=stock.code,
                    direction="buy",
                    reason=f"rule chain passed: {self.chain.chain_name or self.chain.chain_key}",
                    rule_chain_key=self.chain.chain_key,
                    rule_chain_snapshot={
                        "market": self.chain.market,
                        "timeframe": self.chain.timeframe,
                        "chain_key": self.chain.chain_key,
                        "chain_name": self.chain.chain_name,
                        "expression": self.chain.expression,
                        "exit_policy": self.exit_policy,
                        "rule_outputs": self._rule_outputs(result, item),
                    },
                    passed_rules=self._passed_rules(result),
                    failed_rules=[],
                )
            )
            signals.append(buy_signal)
            sell_signal = self._fixed_holding_sell_signal(stock, date_list, index, buy_signal)
            if sell_signal:
                signals.append(sell_signal)
                next_entry_index = date_list.index(sell_signal.ts) + 1
        return signals

    def _fixed_holding_sell_signal(self, stock: StockInfo, dates: list[date], buy_index: int, buy_signal: Signal) -> Optional[Signal]:
        if self.exit_policy.get("type") != "fixed_holding_days":
            return None
        days = int(self.exit_policy.get("days") or 0)
        if days <= 0:
            return None
        sell_index = buy_index + days
        if sell_index >= len(dates):
            return None
        return Signal(
            signal_id=str(uuid.uuid4()),
            ts=dates[sell_index],
            market=stock.market,
            symbol=stock.code,
            direction="sell",
            reason=f"fixed holding exit after {days} bars",
            rule_chain_key=self.chain.chain_key,
            rule_chain_snapshot=buy_signal.rule_chain_snapshot,
            passed_rules=buy_signal.passed_rules,
            failed_rules=[],
        )

    def _passed_rules(self, result) -> list[str]:
        outputs = getattr(result, "filter_outputs", []) or []
        values = []
        for output in outputs:
            name = getattr(output, "filter_name", "") or getattr(output, "name", "")
            if name:
                values.append(str(name))
        return values

    def _rule_outputs(self, result, check_date: date) -> list[dict]:
        metadata_by_impl = {
            getattr(item, "implementation", ""): item
            for item in getattr(self.rule_engine, "metadata", []) or []
        }
        values = []
        for output in getattr(result, "filter_outputs", []) or []:
            implementation = str(getattr(output, "filter_name", "") or getattr(output, "name", ""))
            metadata = metadata_by_impl.get(implementation)
            result_value = getattr(output, "result", "")
            if isinstance(result_value, Enum):
                result_value = result_value.value
            values.append(
                {
                    "rule_key": getattr(metadata, "rule_key", implementation) if metadata else implementation,
                    "rule_name": getattr(metadata, "rule_name", implementation) if metadata else implementation,
                    "rule_type": getattr(metadata, "rule_type", "") if metadata else "",
                    "implementation": implementation,
                    "result": str(result_value),
                    "reason": str(getattr(output, "reason", "") or ""),
                    "date": check_date.isoformat(),
                    "details": dict(getattr(output, "details", {}) or {}),
                }
            )
        return values
