from __future__ import annotations

import json
from typing import Dict, List, Optional, Set

from db import MarketDatabase
from rule_engine import RuleRepository

from .business import BusinessError


def resolve_rule_chain(
    db: MarketDatabase,
    markets: List[str],
    timeframe: str = "1d",
    chain_key: Optional[str] = None,
) -> dict:
    """Resolve the chain used by a Web-created job.

    Explicit chain_key may point to a disabled trial chain; active-chain loading
    remains the default when no key is provided.

    When no chain_key is given and markets have different default chains,
    each market uses its own active chain. The returned dict represents the
    primary (first) market's chain; callers iterating per-market should
    rely on each market's own active chain via load_active_chain().
    """
    if not markets:
        raise BusinessError("INVALID_RULE_CHAIN", "至少选择一个市场后才能选择规则链")
    repository = RuleRepository(db)
    requested = str(chain_key or "").strip()
    timeframe = str(timeframe or "1d")
    try:
        if requested:
            chains = [repository.load_chain(market, requested, timeframe) for market in markets]
        else:
            # 每个市场独立加载其活跃链（支持不同市场不同默认链）
            chains = [repository.load_active_chain(market, timeframe) for market in markets]
    except Exception as exc:
        label = requested or "默认生效链"
        raise BusinessError(
            "RULE_CHAIN_NOT_FOUND",
            f"规则链 {label} 不适用于所选市场，请重新选择规则链",
        ) from exc
    first = chains[0]
    result = {
        "chain_key": first.chain_key,
        "chain_timeframe": first.timeframe,
        "chain_name": first.chain_name,
        "enabled": first.enabled,
        "description": first.description,
    }
    # 当不同市场使用不同链时，附加每个市场的链信息
    if not requested and len(markets) > 1:
        per_market = {}
        for m, c in zip(markets, chains):
            if c.chain_key != first.chain_key:
                per_market[m] = {
                    "chain_key": c.chain_key,
                    "chain_name": c.chain_name,
                    "timeframe": c.timeframe,
                }
        if per_market:
            result["per_market_chains"] = per_market
    return result


def parse_rule_expression(raw_expression) -> dict:
    if isinstance(raw_expression, dict):
        expression = raw_expression
    elif isinstance(raw_expression, str):
        try:
            expression = json.loads(raw_expression)
        except json.JSONDecodeError as exc:
            raise BusinessError("INVALID_RULE_CHAIN_EXPRESSION", f"规则链表达式 JSON 解析失败: {exc}") from exc
    else:
        raise BusinessError("INVALID_RULE_CHAIN_EXPRESSION", "规则链表达式必须是 JSON 对象")
    if not isinstance(expression, dict) or not expression:
        raise BusinessError("INVALID_RULE_CHAIN_EXPRESSION", "规则链表达式必须是非空 JSON 对象")
    _validate_expression_shape(expression)
    return expression


def validate_rule_expression_against_market(db: MarketDatabase, market: str, expression: dict) -> dict:
    repository = RuleRepository(db)
    metadata = {item.rule_key for item in repository.load_metadata(market)}
    missing = sorted(_collect_rule_keys(expression) - metadata)
    if missing:
        raise BusinessError(
            "INVALID_RULE_CHAIN_EXPRESSION",
            f"规则链引用了不存在的原子规则: {', '.join(missing)}",
        )
    return expression


def _validate_expression_shape(expression: dict) -> None:
    supported = {"ref", "and", "any", "all_enabled", "any_enabled"}
    keys = set(expression.keys())
    if not keys or not keys.issubset(supported) or len(keys) != 1:
        raise BusinessError("INVALID_RULE_CHAIN_EXPRESSION", "规则链表达式仅支持 ref/and/any/all_enabled/any_enabled")
    if "ref" in expression:
        if not str(expression["ref"] or "").strip():
            raise BusinessError("INVALID_RULE_CHAIN_EXPRESSION", "ref 节点不能为空")
        return
    field = next(iter(keys))
    value = expression.get(field)
    if not isinstance(value, list):
        raise BusinessError("INVALID_RULE_CHAIN_EXPRESSION", f"{field} 节点必须是数组")
    if field in {"and", "any"}:
        for item in value:
            if not isinstance(item, dict):
                raise BusinessError("INVALID_RULE_CHAIN_EXPRESSION", f"{field} 子节点必须是对象")
            _validate_expression_shape(item)
        return
    for item in value:
        if not str(item or "").strip():
            raise BusinessError("INVALID_RULE_CHAIN_EXPRESSION", f"{field} 中的规则 Key 不能为空")


def _collect_rule_keys(expression: dict) -> Set[str]:
    if "ref" in expression:
        return {str(expression["ref"])}
    keys: Set[str] = set()
    if "and" in expression or "any" in expression:
        for item in expression.get("and") or expression.get("any") or []:
            if isinstance(item, dict):
                keys.update(_collect_rule_keys(item))
        return keys
    for item in expression.get("all_enabled") or expression.get("any_enabled") or []:
        keys.add(str(item))
    return keys
