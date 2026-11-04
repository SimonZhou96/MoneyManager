from __future__ import annotations

from dataclasses import replace
from typing import Iterable, List, Optional

from .models import RiskProfile, StrategyCandidate


def apply_risk_profile(
    candidates: Iterable[StrategyCandidate],
    risk_profile: RiskProfile,
    max_loss: Optional[float] = None,
) -> List[StrategyCandidate]:
    filtered: List[StrategyCandidate] = []
    for candidate in candidates:
        warnings = set(candidate.warnings or [])
        max_loss_value = float((candidate.risk_metrics or {}).get("最大亏损") or 0)
        if max_loss is not None and max_loss_value > float(max_loss):
            continue
        if risk_profile == RiskProfile.CONSERVATIVE and "无限亏损" in warnings:
            continue
        if risk_profile == RiskProfile.BALANCED and "无限亏损" in warnings:
            filtered.append(_adjust_candidate(candidate, max(candidate.score - 25, 0), "高风险策略仅作观察"))
            continue
        if risk_profile == RiskProfile.AGGRESSIVE and "无限亏损" in warnings:
            filtered.append(_adjust_candidate(candidate, candidate.score + 5, "进取模式需严格止损"))
            continue
        filtered.append(candidate)
    return sorted(filtered, key=lambda item: item.score, reverse=True)


def _adjust_candidate(candidate: StrategyCandidate, score: float, warning: str) -> StrategyCandidate:
    recommendation_status = "observe" if score < 70 else candidate.recommendation_status
    return replace(
        candidate,
        score=round(score, 2),
        recommendation_status=recommendation_status,
        warnings=[*candidate.warnings, warning],
    )
