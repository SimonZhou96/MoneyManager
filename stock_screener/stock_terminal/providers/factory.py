from __future__ import annotations

from .eastmoney import EastmoneyStockTerminalProvider
from .futu import FutuStockTerminalProvider
from .yfinance_provider import YFinanceStockTerminalProvider


def build_stock_terminal_providers(db=None):
    return [
        FutuStockTerminalProvider(),
        YFinanceStockTerminalProvider(),
        EastmoneyStockTerminalProvider(db=db),
    ]
