from __future__ import annotations

import uuid
import os
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from .macro_analysis import (
    OptionMacroAnalysis,
    OptionMacroAnalysisProvider,
    apply_macro_analysis_to_candidates,
    build_default_option_macro_analysis_provider,
    cache_key_for_macro_analysis,
    cache_row_is_valid,
)
from .market_data import FakeOptionMarketDataProvider, OptionMarketDataProvider
from .models import ContractDetail, OptionEvaluationResult, OrderPlan, RiskProfile
from .monitor import event_to_display, generate_monitor_events
from .risk import apply_risk_profile
from .strategies import generate_strategy_candidates


class InMemoryOptionLabRepository:
    def __init__(self):
        self.runs: Dict[str, dict] = {}
        self.candidates: Dict[str, list] = {}
        self.candidates_by_id: Dict[str, object] = {}
        self.order_plans: Dict[str, OrderPlan] = {}
        self.positions: Dict[str, dict] = {}
        self.events: Dict[str, list] = {}
        self.macro_cache: Dict[str, dict] = {}

    def save_run(self, run_id: str, item: dict) -> None:
        self.runs[run_id] = item

    def save_candidates(self, run_id: str, candidates: Iterable) -> None:
        rows = list(candidates)
        self.candidates[run_id] = rows
        for candidate in rows:
            self.candidates_by_id[candidate.candidate_id] = candidate

    def get_candidate(self, candidate_id: str):
        return self.candidates_by_id[candidate_id]

    def save_order_plan(self, plan: OrderPlan) -> None:
        self.order_plans[plan.plan_id] = plan

    def get_order_plan(self, plan_id: str) -> OrderPlan:
        return self.order_plans[plan_id]

    def save_position(self, position: dict) -> None:
        self.positions[position["position_id"]] = position

    def get_position(self, position_id: str) -> dict:
        return self.positions[position_id]

    def save_events(self, position_id: str, events: list) -> None:
        self.events.setdefault(position_id, []).extend(events)

    def get_option_macro_analysis_cache(self, cache_key: str) -> Optional[dict]:
        return self.macro_cache.get(cache_key)

    def save_option_macro_analysis_cache(self, cache_key: str, row: dict) -> None:
        self.macro_cache[cache_key] = row

    def upsert_option_macro_analysis_cache(self, row: dict) -> None:
        self.save_option_macro_analysis_cache(row["cache_key"], row)


class OptionLabService:
    def __init__(
        self,
        repository: InMemoryOptionLabRepository,
        market_data_provider: Optional[OptionMarketDataProvider] = None,
        macro_analysis_provider: Optional[OptionMacroAnalysisProvider] = None,
    ):
        self.repository = repository
        self.market_data_provider = market_data_provider or FakeOptionMarketDataProvider()
        self.macro_analysis_provider = macro_analysis_provider

    def evaluate_single(
        self,
        market: str,
        code: str,
        risk_profile: RiskProfile,
        user_id: Optional[int] = None,
        max_loss: Optional[float] = None,
        capital: Optional[float] = None,
        planned_holding_days: Optional[int] = None,
        strategy_scope: Optional[List[str]] = None,
        enable_macro_analysis: bool = False,
        force_macro_refresh: bool = False,
        macro_cache_ttl_minutes: int = 60,
    ) -> OptionEvaluationResult:
        run_id = str(uuid.uuid4())
        run_payload = {
            "run_id": run_id,
            "mode": "single",
            "user_id": user_id,
            "market": market,
            "code": code,
            "risk_profile": risk_profile.label,
            "request": {
                "max_loss": max_loss,
                "capital": capital,
                "planned_holding_days": planned_holding_days,
                "strategy_scope": strategy_scope or [],
                "enable_macro_analysis": bool(enable_macro_analysis),
                "force_macro_refresh": bool(force_macro_refresh),
                "macro_cache_ttl_minutes": int(macro_cache_ttl_minutes or 60),
            },
            "status": "running",
        }
        self.repository.save_run(
            run_id,
            run_payload,
        )
        snapshot = self.market_data_provider.fetch_snapshot(market, code)
        if hasattr(self.repository, "create_option_market_snapshot"):
            self.repository.create_option_market_snapshot(snapshot.to_dict())
        candidates = apply_risk_profile(
            generate_strategy_candidates(
                run_id,
                snapshot,
                capital=capital,
                max_loss_limit=max_loss,
                planned_holding_days=planned_holding_days,
                strategy_scope=strategy_scope,
            ),
            risk_profile=risk_profile,
            max_loss=max_loss,
        )
        macro_analysis = None
        warnings = list(snapshot.data_quality.warnings)
        if enable_macro_analysis:
            macro_analysis = self._analyze_macro_context(
                market=market,
                code=code,
                snapshot=snapshot,
                force_refresh=force_macro_refresh,
                ttl_minutes=max(1, int(macro_cache_ttl_minutes or 60)),
            )
            candidates = apply_macro_analysis_to_candidates(candidates, macro_analysis)
            warnings.extend(macro_analysis.warnings)
        self.repository.save_candidates(run_id, candidates)
        result = OptionEvaluationResult(
            run_id=run_id,
            market=market,
            code=code,
            status="completed",
            risk_profile=risk_profile,
            candidates=candidates,
            warnings=warnings,
            data_quality=snapshot.data_quality,
            macro_analysis=macro_analysis,
        )
        if hasattr(self.repository, "finish_option_evaluation_run"):
            self.repository.finish_option_evaluation_run(
                run_id,
                "completed",
                warnings,
                snapshot.data_quality.to_dict(),
            )
        else:
            self.repository.save_run(run_id, {**run_payload, "status": "completed"})
        return result

    def _analyze_macro_context(
        self,
        market: str,
        code: str,
        snapshot,
        force_refresh: bool,
        ttl_minutes: int,
    ) -> OptionMacroAnalysis:
        cache_key = cache_key_for_macro_analysis(market, code)
        cached_row = self._get_macro_cache(cache_key)
        if not force_refresh and cache_row_is_valid(cached_row):
            return OptionMacroAnalysis.from_cache_row(cached_row).with_cached(True)

        provider = self.macro_analysis_provider or build_default_option_macro_analysis_provider()
        try:
            macro = provider.analyze(market=market, code=code, snapshot=snapshot, ttl_minutes=ttl_minutes)
        except Exception as exc:
            if cache_row_is_valid(cached_row):
                cached = OptionMacroAnalysis.from_cache_row(cached_row)
                return cached.with_cached(True, [*cached.warnings, f"刷新失败，使用缓存: {type(exc).__name__}: {exc}"])
            now = datetime.now(timezone.utc).replace(microsecond=0)
            return OptionMacroAnalysis(
                macro_score=None,
                macro_direction="信息不足",
                news_impact="信息不足",
                hot_sector_mark="未知",
                main_force_risk_level="数据不足",
                summary="宏观分析失败",
                positive_factors=[],
                risk_factors=[],
                macro_factors=[],
                source_urls=[],
                warnings=[f"宏观分析失败: {type(exc).__name__}: {exc}"],
                cached=False,
                provider=getattr(provider, "name", provider.__class__.__name__),
                analyzed_at=now.isoformat(),
                expires_at=now.isoformat(),
            )
        if macro.macro_score is not None:
            self._save_macro_cache(cache_key, macro.to_cache_row(market, code))
        return macro

    def _get_macro_cache(self, cache_key: str) -> Optional[dict]:
        getter = getattr(self.repository, "get_option_macro_analysis_cache", None)
        if callable(getter):
            return getter(cache_key)
        return None

    def _save_macro_cache(self, cache_key: str, row: dict) -> None:
        upsert = getattr(self.repository, "upsert_option_macro_analysis_cache", None)
        if callable(upsert):
            upsert(row)
            return
        save = getattr(self.repository, "save_option_macro_analysis_cache", None)
        if callable(save):
            save(cache_key, row)

    def save_order_plan(self, candidate_id: str, user_id: Optional[int] = None) -> OrderPlan | dict:
        candidate = self.repository.get_candidate(candidate_id)
        if not candidate:
            raise ValueError(f"期权策略候选不存在: {candidate_id}")
        if isinstance(candidate, dict):
            plan_payload = {
                "plan_id": str(uuid.uuid4()),
                "candidate_id": candidate_id,
                "user_id": user_id,
                "status": "planned",
                "contract_details": candidate.get("contract_details") or candidate.get("合约明细") or [],
                "order_suggestion": {
                    **(candidate.get("order_suggestion") or {}),
                    "user_id": user_id,
                },
                "risk_metrics": candidate.get("risk_metrics") or {},
                "warnings": candidate.get("warnings") or [],
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            saved = self.repository.save_order_plan(plan_payload)
            return saved or plan_payload
        plan = OrderPlan(
            plan_id=str(uuid.uuid4()),
            candidate_id=candidate_id,
            status="planned",
            contract_details=candidate.contract_details,
            order_suggestion={**candidate.order_suggestion, "user_id": user_id},
            created_at=datetime.now(timezone.utc),
        )
        self.repository.save_order_plan(plan)
        return plan

    def record_fill(
        self,
        plan_id: str,
        filled_price: float,
        quantity: int,
        filled_at: str,
        fee: Optional[float] = None,
    ) -> dict:
        plan = self.repository.get_order_plan(plan_id)
        if not plan:
            raise ValueError(f"订单建议不存在: {plan_id}")
        candidate_id = _field(plan, "candidate_id")
        candidate = self.repository.get_candidate(candidate_id)
        if not candidate:
            raise ValueError(f"期权策略候选不存在: {candidate_id}")
        contract_details = _field(plan, "contract_details") or []
        order_suggestion = _field(plan, "order_suggestion") or {}
        position = {
            "position_id": str(uuid.uuid4()),
            "plan_id": plan_id,
            "user_id": _field(plan, "user_id"),
            "market": _field(candidate, "market"),
            "code": _field(candidate, "code"),
            "strategy_name": _field(candidate, "strategy_name", "策略名称"),
            "策略名称": _field(candidate, "strategy_name", "策略名称"),
            "contract_details": _raw_contracts(contract_details),
            "合约明细": _display_contracts(contract_details),
            "order_suggestion": order_suggestion,
            "filled_price": filled_price,
            "成交价": filled_price,
            "quantity": quantity,
            "数量": quantity,
            "filled_at": filled_at,
            "成交时间": filled_at,
            "fee": fee,
            "费用": fee,
            "止损价": order_suggestion.get("止损价"),
            "止盈价": order_suggestion.get("止盈价"),
            "status": "active",
        }
        self.repository.save_position(position)
        return position

    def refresh_position(self, position_id: str, send_feishu: bool = False) -> List[dict]:
        position = self.repository.get_position(position_id)
        if not position:
            raise ValueError(f"监控持仓不存在: {position_id}")
        position = self._enrich_position_state(position)
        raw_events = generate_monitor_events(position)
        self.repository.save_events(position_id, raw_events)
        display_events = [event_to_display(event) for event in raw_events]
        if send_feishu:
            self._send_feishu_events(display_events)
        return display_events

    def _enrich_position_state(self, position: dict) -> dict:
        try:
            snapshot = self.market_data_provider.fetch_snapshot(position.get("market"), position.get("code"))
            details = position.get("contract_details") or position.get("合约明细") or []
            first_code = None
            if details:
                first_code = details[0].get("option_code") or details[0].get("合约代码")
            quote = next((item for item in snapshot.option_quotes if item.option_code == first_code), None)
            current_price = quote.mid_price if quote else (snapshot.option_quotes[0].mid_price if snapshot.option_quotes else None)
        except Exception:
            current_price = None
        state = {
            "当前估算价格": current_price,
            "止损价": position.get("止损价") or (position.get("order_suggestion") or {}).get("止损价"),
            "止盈价": position.get("止盈价") or (position.get("order_suggestion") or {}).get("止盈价"),
        }
        action = "继续监控"
        if current_price is not None and state["止损价"] is not None and current_price <= state["止损价"]:
            action = "复核止损"
        elif current_price is not None and state["止盈价"] is not None and current_price >= state["止盈价"]:
            action = "复核止盈"
        enriched = {**position, "current_state": state, "current_action": action}
        if hasattr(self.repository, "update_option_tracked_position_state"):
            self.repository.update_option_tracked_position_state(position["position_id"], state, action)
        elif isinstance(getattr(self.repository, "positions", None), dict):
            self.repository.positions[position["position_id"]] = enriched
        return enriched

    def _send_feishu_events(self, events: List[dict]) -> None:
        important = [item for item in events if item.get("提醒级别") in {"紧急", "重要"}]
        webhook_url = os.getenv("FEISHU_WEBHOOK_URL", "").strip()
        if not important or not webhook_url:
            return
        try:
            from feishu_notifier import send_feishu_text
        except Exception:
            return
        lines = ["【期权实验室监控提醒】"]
        for item in important:
            lines.append(f"{item.get('提醒级别')} | {item.get('position_id')} | {item.get('提醒内容')}")
        send_feishu_text(webhook_url, "\n".join(lines))


def _field(item: Any, *names: str) -> Any:
    if item is None:
        return None
    for name in names:
        if isinstance(item, dict) and name in item:
            return item.get(name)
        if hasattr(item, name):
            return getattr(item, name)
    return None


def _display_contracts(contract_details: Iterable[Any]) -> List[dict]:
    rows = []
    for item in contract_details or []:
        if isinstance(item, ContractDetail):
            rows.append(item.to_display_row())
        elif isinstance(item, dict):
            if "合约代码" in item:
                rows.append(dict(item))
            else:
                rows.append({
                    "买卖方向": item.get("side_label") or item.get("side"),
                    "期权类型": item.get("contract_type_label") or item.get("contract_type"),
                    "合约代码": item.get("option_code"),
                    "到期日": item.get("expiration_date"),
                    "行权价": item.get("strike"),
                    "建议价格": item.get("suggested_price"),
                    "数量": item.get("quantity"),
                    "币种": item.get("currency"),
                })
    return rows


def _raw_contracts(contract_details: Iterable[Any]) -> List[dict]:
    rows = []
    for item in contract_details or []:
        if isinstance(item, ContractDetail):
            rows.append(item.to_dict())
        elif isinstance(item, dict):
            rows.append(dict(item))
    return rows
