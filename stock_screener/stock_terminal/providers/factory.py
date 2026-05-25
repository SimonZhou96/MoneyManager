from __future__ import annotations

from .eastmoney import EastmoneyStockTerminalProvider


def build_stock_terminal_providers(db=None):
    return [EastmoneyStockTerminalProvider(db=db)]
