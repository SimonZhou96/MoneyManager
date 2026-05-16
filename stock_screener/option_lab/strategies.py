from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional

from .models import (
    ContractDetail,
    MarketSnapshot,
    OptionContractType,
    OptionQuote,
    OptionSide,
    StrategyCandidate,
)


STRATEGY_LABELS: Dict[str, str] = {
    "long_call": "买入看涨期权",
    "long_put": "买入看跌期权",
    "bull_call_spread": "牛市看涨价差",
    "bear_put_spread": "熊市看跌价差",
    "bull_put_spread": "牛市看跌价差",
    "bear_call_spread": "熊市看涨价差",
    "covered_call": "备兑看涨",
    "cash_secured_put": "现金担保卖出看跌",
    "long_straddle": "买入跨式",
    "long_strangle": "买入宽跨式",
    "calendar_spread": "日历价差",
    "iron_condor": "铁鹰式",
    "iron_butterfly": "铁蝶式",
    "short_straddle": "卖出跨式",
    "short_strangle": "卖出宽跨式",
    "butterfly": "蝶式价差",
    "ratio_spread": "比率价差",
    "backspread": "反向比率价差",
}

UNLIMITED_LOSS_STRATEGIES = {"short_straddle", "short_strangle", "ratio_spread"}


def strategy_label(strategy_key: str) -> str:
    return STRATEGY_LABELS.get(strategy_key, strategy_key)


def generate_strategy_candidates(
    run_id: str,
    snapshot: MarketSnapshot,
    capital: Optional[float] = None,
    max_loss_limit: Optional[float] = None,
    planned_holding_days: Optional[int] = None,
    strategy_scope: Optional[Iterable[str]] = None,
) -> List[StrategyCandidate]:
    allowed = {item for item in (strategy_scope or []) if item}
    calls = _quotes(snapshot, OptionContractType.CALL)
    puts = _quotes(snapshot, OptionContractType.PUT)
    call_atm = _nearest_quote(calls, snapshot.underlying.price)
    put_atm = _nearest_quote(puts, snapshot.underlying.price)
    lower_call, higher_call = _spread_pair(calls, snapshot.underlying.price, prefer="call")
    lower_put, higher_put = _spread_pair(puts, snapshot.underlying.price, prefer="put")
    far_call = _same_strike_later_quote(calls, call_atm)
    now = datetime.now(timezone.utc)
    candidates: List[StrategyCandidate] = []

    def add(strategy_key: str, details: List[ContractDetail], base_score: float, fit_reason: str) -> None:
        if allowed and strategy_key not in allowed:
            return
        if not details:
            return
        debit = _premium_total(details, OptionSide.BUY)
        credit = _premium_total(details, OptionSide.SELL)
        net_price = round(debit - credit, 4)
        max_loss = _estimate_max_loss(strategy_key, details, net_price)
        combo_quantity = _suggest_quantity(capital, max_loss, max_loss_limit)
        target_profit = round(max(abs(net_price) * 100 * 0.45, 80.0) * combo_quantity, 2)
        warnings = []
        if strategy_key in UNLIMITED_LOSS_STRATEGIES:
            warnings.append("无限亏损")
        if not _has_acceptable_liquidity(details):
            warnings.append("流动性不足")
        holding_text = f"{planned_holding_days}天" if planned_holding_days else "30-45天"
        candidates.append(
            StrategyCandidate(
                candidate_id=str(uuid.uuid4()),
                run_id=run_id,
                market=snapshot.underlying.market,
                code=snapshot.underlying.code,
                strategy_key=strategy_key,
                strategy_name=strategy_label(strategy_key),
                score=round(base_score + _quality_bonus(details), 2),
                recommendation_status="recommended" if base_score >= 70 else "observe",
                fit_reason=fit_reason,
                contract_details=details,
                risk_metrics={
                    "最大亏损": round(max_loss * combo_quantity, 2),
                    "单组最大亏损": max_loss,
                    "资金占用": round(max_loss * combo_quantity, 2),
                    "目标收益": target_profit,
                    "盈亏平衡点": _breakeven(details, net_price),
                    "是否有限亏损": strategy_key not in UNLIMITED_LOSS_STRATEGIES,
                },
                order_suggestion={
                    "建议限价": net_price,
                    "允许滑点": 0.05,
                    "建议数量": combo_quantity,
                    "止损价": round(net_price * 0.55, 4) if net_price > 0 else None,
                    "止盈价": round(net_price * 1.45, 4) if net_price > 0 else None,
                    "计划持有期": holding_text,
                    "退出条件": "达到止盈止损、到期前两周或正股信号转弱时退出",
                },
                warnings=warnings,
                data_quality=snapshot.data_quality,
                created_at=now,
            )
        )

    if call_atm:
        add("long_call", [_detail(OptionSide.BUY, call_atm)], 82, "正股趋势偏多，适合买入看涨期权参与上涨")
        add("covered_call", [_detail(OptionSide.SELL, call_atm)], 66, "持有正股时可用备兑看涨增强收益")
    if put_atm:
        add("long_put", [_detail(OptionSide.BUY, put_atm)], 72, "正股转弱或需要保护时可用买入看跌期权")
        add("cash_secured_put", [_detail(OptionSide.SELL, put_atm)], 64, "愿意接货时可用现金担保卖出看跌")
    if lower_call and higher_call:
        add("bull_call_spread", [_detail(OptionSide.BUY, lower_call), _detail(OptionSide.SELL, higher_call)], 74, "偏多但控制成本，适合牛市看涨价差")
        add("bear_call_spread", [_detail(OptionSide.SELL, lower_call), _detail(OptionSide.BUY, higher_call)], 58, "偏空或区间上沿压力明显，适合熊市看涨价差")
        add("butterfly", [_detail(OptionSide.BUY, lower_call), _detail(OptionSide.SELL, call_atm or lower_call, 2), _detail(OptionSide.BUY, higher_call)], 57, "预期到期靠近中间行权价，可用蝶式控制成本")
    if lower_put and higher_put:
        add("bear_put_spread", [_detail(OptionSide.BUY, higher_put), _detail(OptionSide.SELL, lower_put)], 70, "偏空但控制成本，适合熊市看跌价差")
        add("bull_put_spread", [_detail(OptionSide.SELL, higher_put), _detail(OptionSide.BUY, lower_put)], 62, "偏多且愿意用有限风险收取权利金")
    if call_atm and put_atm:
        add("long_straddle", [_detail(OptionSide.BUY, call_atm), _detail(OptionSide.BUY, put_atm)], 68, "预期波动放大但方向不确定")
        add("long_strangle", [_detail(OptionSide.BUY, higher_call or call_atm), _detail(OptionSide.BUY, lower_put or put_atm)], 62, "预期大波动且希望降低权利金")
        add("short_straddle", [_detail(OptionSide.SELL, call_atm), _detail(OptionSide.SELL, put_atm)], 45, "高风险卖波动策略，仅适合严格监控")
        add("short_strangle", [_detail(OptionSide.SELL, higher_call or call_atm), _detail(OptionSide.SELL, lower_put or put_atm)], 43, "高风险宽跨卖方策略，仅适合进取模式观察")
        add("ratio_spread", [_detail(OptionSide.BUY, call_atm), _detail(OptionSide.SELL, higher_call or call_atm, 2)], 52, "比率价差包含尾部风险，需要明确止损")
        add("backspread", [_detail(OptionSide.SELL, put_atm), _detail(OptionSide.BUY, higher_call or call_atm, 2)], 55, "反向比率价差用于捕捉极端波动")
        if lower_put and higher_call:
            add("iron_condor", [_detail(OptionSide.BUY, lower_put), _detail(OptionSide.SELL, put_atm), _detail(OptionSide.SELL, call_atm), _detail(OptionSide.BUY, higher_call)], 60, "预期区间震荡，使用铁鹰式收取权利金并限制尾部风险")
            add("iron_butterfly", [_detail(OptionSide.BUY, lower_put), _detail(OptionSide.SELL, put_atm), _detail(OptionSide.SELL, call_atm), _detail(OptionSide.BUY, higher_call)], 59, "预期围绕当前价窄幅震荡，可用铁蝶式提高权利金效率")
    if call_atm and far_call:
        add("calendar_spread", [_detail(OptionSide.SELL, call_atm), _detail(OptionSide.BUY, far_call)], 61, "预期短期震荡、远期波动仍有价值，适合日历价差")

    return sorted(candidates, key=lambda item: item.score, reverse=True)


def _quotes(snapshot: MarketSnapshot, contract_type: OptionContractType) -> List[OptionQuote]:
    return sorted(
        [quote for quote in snapshot.option_quotes if quote.contract_type == contract_type and quote.mid_price is not None],
        key=lambda quote: (quote.expiration_date, quote.strike),
    )


def _nearest_quote(quotes: List[OptionQuote], price: Optional[float]) -> Optional[OptionQuote]:
    if not quotes:
        return None
    if price is None:
        return quotes[len(quotes) // 2]
    return min(quotes, key=lambda quote: abs(quote.strike - float(price)))


def _spread_pair(quotes: List[OptionQuote], price: Optional[float], prefer: str) -> tuple[Optional[OptionQuote], Optional[OptionQuote]]:
    if len(quotes) < 2:
        return None, None
    same_expiry = [quote for quote in quotes if quote.expiration_date == quotes[0].expiration_date]
    if len(same_expiry) < 2:
        same_expiry = quotes
    ordered = sorted(same_expiry, key=lambda quote: quote.strike)
    anchor = _nearest_quote(ordered, price)
    if anchor is None:
        return ordered[0], ordered[-1]
    lower = max((quote for quote in ordered if quote.strike < anchor.strike), default=ordered[0], key=lambda quote: quote.strike)
    higher = min((quote for quote in ordered if quote.strike > anchor.strike), default=ordered[-1], key=lambda quote: quote.strike)
    if lower.option_code == higher.option_code and len(ordered) >= 2:
        lower, higher = ordered[0], ordered[-1]
    return (anchor, higher) if prefer == "call" and anchor.strike < higher.strike else (lower, anchor)


def _same_strike_later_quote(quotes: List[OptionQuote], anchor: Optional[OptionQuote]) -> Optional[OptionQuote]:
    if anchor is None:
        return None
    later = [
        quote for quote in quotes
        if quote.expiration_date > anchor.expiration_date and abs(quote.strike - anchor.strike) < 0.0001
    ]
    return later[0] if later else None


def _detail(side: OptionSide, quote: OptionQuote, quantity: int = 1) -> ContractDetail:
    return ContractDetail(
        side=side,
        contract_type=quote.contract_type,
        option_code=quote.option_code,
        provider_code=quote.provider_code,
        expiration_date=quote.expiration_date,
        strike=quote.strike,
        suggested_price=quote.mid_price,
        quantity=quantity,
        currency=quote.currency,
    )


def _premium_total(details: List[ContractDetail], side: OptionSide) -> float:
    return sum((item.suggested_price or 0.0) * item.quantity for item in details if item.side == side)


def _estimate_max_loss(strategy_key: str, details: List[ContractDetail], net_price: float) -> float:
    if strategy_key in UNLIMITED_LOSS_STRATEGIES:
        return 999999.0
    strikes = [item.strike for item in details]
    width = max(strikes) - min(strikes) if len(strikes) >= 2 else 0.0
    if net_price > 0:
        return round(net_price * 100, 2)
    if width > 0:
        return round(max(width * 100 + net_price * 100, 0), 2)
    return round(max(abs(net_price) * 100, 0), 2)


def _suggest_quantity(capital: Optional[float], max_loss: float, max_loss_limit: Optional[float]) -> int:
    budget_candidates = [value for value in (capital, max_loss_limit) if value is not None and value > 0]
    budget = min(budget_candidates) if budget_candidates else None
    if budget is None or max_loss <= 0 or max_loss >= 999999:
        return 1
    return max(1, min(int(budget // max_loss), 10))


def _quality_bonus(details: List[ContractDetail]) -> float:
    return min(sum(1.0 for item in details if item.suggested_price is not None), 3.0)


def _has_acceptable_liquidity(details: List[ContractDetail]) -> bool:
    return all(item.suggested_price is not None and item.suggested_price > 0 for item in details)


def _breakeven(details: List[ContractDetail], net_price: float):
    if not details:
        return None
    first = details[0]
    if first.contract_type == OptionContractType.CALL:
        return round(first.strike + max(net_price, 0), 4)
    return round(first.strike - max(net_price, 0), 4)
