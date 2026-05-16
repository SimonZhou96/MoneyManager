from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import Dict, List

from .models import MonitorEvent, MonitorEventSeverity


def generate_monitor_events(position: Dict) -> List[MonitorEvent]:
    state = position.get("current_state") or {}
    action = position.get("current_action") or ""
    current_price = _float_or_none(state.get("当前估算价格"))
    stop_loss = _float_or_none(position.get("止损价") or state.get("止损价"))
    take_profit = _float_or_none(position.get("止盈价") or state.get("止盈价"))
    position_id = position["position_id"]
    events: List[MonitorEvent] = []
    if current_price is not None and stop_loss is not None and current_price <= stop_loss:
        events.append(_event(position_id, MonitorEventSeverity.URGENT, "stop_loss", f"当前估算价格 {current_price} 已触发止损价 {stop_loss}，建议立即复核并准备退出"))
    if current_price is not None and take_profit is not None and current_price >= take_profit:
        events.append(_event(position_id, MonitorEventSeverity.IMPORTANT, "take_profit", f"当前估算价格 {current_price} 已达到止盈价 {take_profit}，建议分批止盈或收紧止损"))
    days_to_expiry = _days_to_first_expiry(position.get("contract_details") or position.get("合约明细") or [])
    if days_to_expiry is not None and days_to_expiry <= 14:
        events.append(_event(position_id, MonitorEventSeverity.IMPORTANT, "expiry_near", f"距离到期仅 {days_to_expiry} 天，建议降低隔夜风险"))
    if not events:
        message = action or "持仓已刷新，当前未触发止盈止损"
        events.append(_event(position_id, MonitorEventSeverity.INFO, "refresh", message))
    return events


def event_to_display(event: MonitorEvent) -> Dict:
    return {
        "event_id": event.event_id,
        "position_id": event.position_id,
        "提醒级别": event.severity.label,
        "事件类型": event.event_type,
        "提醒内容": event.message,
        "created_at": event.created_at.isoformat(),
    }


def _event(position_id: str, severity: MonitorEventSeverity, event_type: str, message: str) -> MonitorEvent:
    return MonitorEvent(
        event_id=str(uuid.uuid4()),
        position_id=position_id,
        severity=severity,
        event_type=event_type,
        message=message,
        created_at=datetime.now(timezone.utc),
    )


def _days_to_first_expiry(details: List[Dict]) -> int | None:
    expirations = []
    for item in details:
        raw = item.get("expiration_date") or item.get("到期日")
        if not raw:
            continue
        try:
            expirations.append(date.fromisoformat(str(raw)[:10]))
        except ValueError:
            continue
    if not expirations:
        return None
    return (min(expirations) - date.today()).days


def _float_or_none(value):
    try:
        if value is None or value == "":
            return None
        return float(value)
    except Exception:
        return None
