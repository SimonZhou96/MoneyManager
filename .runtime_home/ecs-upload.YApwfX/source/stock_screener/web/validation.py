from __future__ import annotations

import json
import re
from typing import Any, Iterable, List

from .business import BusinessError


ALLOWED_MARKETS = {"HK", "US", "A"}
SUPPORTED_TIMEFRAMES = {"1d", "1wk", "1mo", "3mo", "1m", "3m", "5m", "15m", "30m", "60m"}
MAX_AGENT_BULK_ROWS = 2000
MAX_AGENT_JSON_BYTES = 2 * 1024 * 1024
MAX_AGENT_ARTIFACT_BYTES = 20 * 1024 * 1024
CHAIN_KEY_PATTERN = re.compile(r"^[a-z0-9_]+$")


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


def validate_rule_chain_timeframe(timeframe: str) -> str:
    value = str(timeframe or "*").strip() or "*"
    if value == "*":
        return value
    return validate_timeframe(value)


def validate_rule_chain_key(chain_key: str) -> str:
    value = str(chain_key or "").strip()
    if not value:
        raise ValueError("规则链 Key 不能为空")
    if not CHAIN_KEY_PATTERN.fullmatch(value):
        raise ValueError("规则链 Key 仅支持小写字母、数字和下划线")
    return value


def validate_agent_bulk_size(rows: list[dict], max_rows: int = MAX_AGENT_BULK_ROWS) -> None:
    if len(rows) > max_rows:
        raise BusinessError(
            "AGENT_BULK_TOO_LARGE",
            f"单次推送数据过大，请拆分为 {max_rows} 行以内后重试",
        )


def validate_agent_json_payload_size(payload: Any, max_bytes: int = MAX_AGENT_JSON_BYTES) -> None:
    size = len(json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8"))
    if size > max_bytes:
        mib = max_bytes / 1024 / 1024
        raise BusinessError(
            "AGENT_PAYLOAD_TOO_LARGE",
            f"单次推送内容过大，请拆分为 {mib:.0f} MiB 以内后重试",
        )


def validate_agent_artifact_size(size: int, max_bytes: int = MAX_AGENT_ARTIFACT_BYTES) -> None:
    if size > max_bytes:
        mib = max_bytes / 1024 / 1024
        raise BusinessError(
            "AGENT_ARTIFACT_TOO_LARGE",
            f"单个导出文件过大，请控制在 {mib:.0f} MiB 以内后重试",
        )
