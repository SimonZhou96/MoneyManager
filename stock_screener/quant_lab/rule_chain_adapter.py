from __future__ import annotations

import uuid
from datetime import date
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
        for item in dates:
            context = FilterContext(check_date=item, market=stock.market)
            result = self.rule_engine.evaluate_stock(stock, context)
            if not getattr(result, "passed", False):
                continue
            signals.append(
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
                    },
                    passed_rules=self._passed_rules(result),
                    failed_rules=[],
                )
            )
        return signals

    def _passed_rules(self, result) -> list[str]:
        outputs = getattr(result, "filter_outputs", []) or []
        values = []
        for output in outputs:
            name = getattr(output, "filter_name", "") or getattr(output, "name", "")
            if name:
                values.append(str(name))
        return values
