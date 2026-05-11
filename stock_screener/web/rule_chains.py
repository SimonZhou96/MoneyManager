from __future__ import annotations

from typing import List, Optional

from db import MarketDatabase
from rule_engine import RuleRepository

from .business import BusinessError


def resolve_rule_chain(db: MarketDatabase, markets: List[str], chain_key: Optional[str] = None) -> dict:
    """Resolve the chain used by a Web-created job.

    Explicit chain_key may point to a disabled trial chain; active-chain loading
    remains the default when no key is provided.
    """
    if not markets:
        raise BusinessError("INVALID_RULE_CHAIN", "至少选择一个市场后才能选择规则链")
    repository = RuleRepository(db)
    requested = str(chain_key or "").strip()
    try:
        if requested:
            chains = [repository.load_chain(market, requested) for market in markets]
        else:
            active = repository.load_active_chain(markets[0])
            chains = [active]
            for market in markets[1:]:
                chains.append(repository.load_chain(market, active.chain_key))
    except Exception as exc:
        label = requested or "默认生效链"
        raise BusinessError(
            "RULE_CHAIN_NOT_FOUND",
            f"规则链 {label} 不适用于所选市场，请重新选择规则链",
        ) from exc
    first = chains[0]
    return {
        "chain_key": first.chain_key,
        "chain_name": first.chain_name,
        "enabled": first.enabled,
        "description": first.description,
    }
