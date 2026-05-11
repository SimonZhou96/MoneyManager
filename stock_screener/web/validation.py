from __future__ import annotations

from typing import Iterable, List

from .business import BusinessError


ALLOWED_MARKETS = {"HK", "US", "A"}
SUPPORTED_TIMEFRAMES = {"1d", "1wk", "1mo", "3mo", "1m", "3m", "5m", "15m", "30m", "60m"}
MAX_AGENT_BULK_ROWS = 2000


def validate_markets(markets: Iterable[str]) -> List[str]:
    """Return normalized markets or raise ValueError for unsafe write input."""
    normalized: List[str] = []
    for value in markets or []:
        market = str(value or "").strip().upper()
        if not market:
            continue
        if market not in ALLOWED_MARKETS:
            raise ValueError(f"不支持的市场: {value}")
        if market not in normalized:
            normalized.append(market)
    if not normalized:
        raise ValueError("至少选择一个市场")
    return normalized


def validate_timeframe(timeframe: str) -> str:
    value = str(timeframe or "").strip()
    if value not in SUPPORTED_TIMEFRAMES:
        raise ValueError(f"不支持的周期: {timeframe}")
    return value


def validate_agent_bulk_size(rows: list[dict], max_rows: int = MAX_AGENT_BULK_ROWS) -> None:
    if len(rows) > max_rows:
        raise BusinessError(
            "AGENT_BULK_TOO_LARGE",
            f"单次推送数据过大，请拆分为 {max_rows} 行以内后重试",
        )
