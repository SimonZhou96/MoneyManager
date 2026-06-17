from __future__ import annotations

from dataclasses import asdict
from datetime import date, datetime, timezone
import os
import uuid
from typing import Any

import pandas as pd

from .backtest import BacktestRequest, BacktestRunner
from .data_provider import CachedKlineDataProvider
from .models import bar_to_dict, signal_to_dict, trade_to_dict
from .rule_chain_adapter import RuleChainStrategyAdapter

from rule_engine import RuleEngine, RuleRepository
from strategy import calculate_rsi

# ── 统一默认值（之前在两个代码路径中不一致，现已统一） ──
_DEFAULT_COMMISSION_RATE = 0.001
_DEFAULT_SLIPPAGE_RATE = 0.001

try:
    from futu import OpenQuoteContext
except Exception:  # pragma: no cover - optional local dependency
    OpenQuoteContext = None


class QuantLabService:
    def __init__(self, repository, data_provider=None, strategy=None):
        self.repository = repository
        self.data_provider = data_provider
        self.strategy = strategy

    def submit_backtest(self, payload: dict, user_id: int | None = None) -> dict[str, Any]:
        run_id = str(uuid.uuid4())
        self.repository.create_quant_backtest_run(
            {
                "run_id": run_id,
                "user_id": user_id,
                "status": "queued",
                "progress_pct": 5,
                "current_stage": "queued",
                "request": payload,
                "warnings": [],
                "progress_logs": [_progress_log_entry("回测任务已创建，等待执行")],
            }
        )
        return {"run_id": run_id, "status": "queued"}

    def get_backtest(self, run_id: str, user_id: int | None = None) -> dict[str, Any] | None:
        row = self.repository.get_quant_backtest_run(run_id)
        if not row:
            return None
        owner_id = row.get("user_id")
        if user_id is not None and owner_id is not None and int(owner_id) != int(user_id):
            return None
        return {
            "run_id": row.get("run_id"),
            "status": row.get("status") or "queued",
            "progress_pct": int(row.get("progress_pct") or 0),
            "current_stage": row.get("current_stage") or "",
            "metrics": row.get("metrics") or {},
            "chart": row.get("chart") or {},
            "warnings": row.get("warnings") or [],
            "progress_logs": row.get("progress_logs") or [],
            "error_message": row.get("error_message"),
            "created_at": row.get("created_at"),
            "finished_at": row.get("finished_at"),
        }

    def run_backtest(self, run_id: str) -> None:
        row = self.repository.get_quant_backtest_run(run_id)
        if not row:
            return
        try:
            self.repository.update_quant_backtest_progress(
                run_id,
                status="running",
                progress_pct=15,
                current_stage="准备回测",
                log_message="开始解析回测请求",
            )
            payload = row.get("request") or {}
            created_data_provider = self.data_provider is None
            data_provider = self.data_provider or _build_default_data_provider(self.repository, payload)
            strategy = self.strategy or _build_rule_chain_strategy(self.repository, payload)
            request = BacktestRequest(
                market=str(payload["market"]),
                symbols=list(payload["symbols"]),
                start=_parse_date(payload["start"]),
                end=_parse_date(payload["end"]),
                initial_cash=float(payload["initial_cash"]),
                quantity=int(payload.get("quantity") or 1),
                commission_rate=float(payload.get("commission_rate", _DEFAULT_COMMISSION_RATE)),
                slippage_rate=float(payload.get("slippage_rate", _DEFAULT_SLIPPAGE_RATE)),
                max_position_weight=float(payload.get("max_position_weight") or 1.0),
            )
            self.repository.update_quant_backtest_progress(
                run_id,
                progress_pct=40,
                current_stage="生成信号",
                log_message=f"开始处理 {len(request.symbols)} 个标的",
            )
            try:
                result = BacktestRunner(data_provider=data_provider, strategy=strategy).run(request)
            finally:
                if created_data_provider and hasattr(data_provider, "close"):
                    data_provider.close()
            self.repository.update_quant_backtest_progress(
                run_id,
                progress_pct=85,
                current_stage="计算指标",
                log_message=f"生成 {len(result.signals)} 个信号，成交 {len(result.trades)} 笔",
            )
            self.repository.finish_quant_backtest_run(run_id, asdict(result.metrics), warnings=result.warnings, chart=_chart_payload(result))
        except Exception as exc:
            self.repository.fail_quant_backtest_run(run_id, str(exc))

    # ── 策略回测 ──

    def submit_strategy_backtest(self, payload: dict, user_id: int | None = None) -> dict[str, Any]:
        run_id = str(uuid.uuid4())
        self.repository.create_quant_backtest_run({
            "run_id": run_id, "user_id": user_id, "status": "queued",
            "progress_pct": 5, "current_stage": "queued", "request": payload,
            "warnings": [], "progress_logs": [_progress_log_entry("策略回测任务已创建，等待执行")],
        })
        return {"run_id": run_id, "status": "queued"}

    def run_strategy_backtest(self, run_id: str) -> None:
        row = self.repository.get_quant_backtest_run(run_id)
        if not row:
            return
        try:
            self.repository.update_quant_backtest_progress(
                run_id, status="running", progress_pct=10,
                current_stage="准备数据", log_message="开始加载K线数据",
            )
            payload = row.get("request") or {}
            created_data_provider = self.data_provider is None
            data_provider = self.data_provider or _build_default_data_provider(self.repository, payload)

            from .strategies import create_strategy
            from .models import RiskConfig

            strategy_cfg = payload.get("strategy") or {}
            strategy = create_strategy(
                strategy_cfg["type"],
                strategy_cfg.get("params") or {},
                entry_side=strategy_cfg.get("entry_side", "long"),
            )
            risk_raw = payload.get("risk")
            risk = RiskConfig(
                stop_loss_pct=risk_raw.get("stop_loss_pct") if risk_raw else None,
                take_profit_pct=risk_raw.get("take_profit_pct") if risk_raw else None,
                trailing_stop_pct=risk_raw.get("trailing_stop_pct") if risk_raw else None,
            ) if risk_raw else None

            request = BacktestRequest(
                market=str(payload["market"]),
                symbols=list(payload["symbols"]),
                start=_parse_date(payload["start"]),
                end=_parse_date(payload["end"]),
                initial_cash=float(payload["initial_cash"]),
                quantity=int(payload.get("quantity") or 10),
                commission_rate=float(payload.get("commission_rate", _DEFAULT_COMMISSION_RATE)),
                slippage_rate=float(payload.get("slippage_rate", _DEFAULT_SLIPPAGE_RATE)),
                max_position_weight=float(payload.get("max_position_weight") or 1.0),
                risk_config=risk,
            )
            self.repository.update_quant_backtest_progress(
                run_id, progress_pct=40, current_stage="生成信号",
                log_message=f"策略类型: {strategy_cfg['type']}, 标的: {request.symbols}",
            )
            try:
                runner = BacktestRunner(data_provider=data_provider, strategy=strategy)
                result = runner.run(request)
            finally:
                if created_data_provider and hasattr(data_provider, "close"):
                    data_provider.close()
            self.repository.update_quant_backtest_progress(
                run_id, progress_pct=85, current_stage="计算指标",
                log_message=f"生成 {len(result.signals)} 个信号，成交 {len(result.trades)} 笔",
            )
            self.repository.finish_quant_backtest_run(
                run_id, asdict(result.metrics), warnings=result.warnings,
                chart=_chart_payload_for_strategy(result),
            )
        except Exception as exc:
            self.repository.fail_quant_backtest_run(run_id, str(exc))

    # ── 参数优化 ──

    def submit_optimization(self, payload: dict, user_id: int | None = None) -> dict[str, Any]:
        run_id = str(uuid.uuid4())
        self.repository.create_quant_backtest_run({
            "run_id": run_id, "user_id": user_id, "status": "queued",
            "progress_pct": 5, "current_stage": "queued", "request": payload,
            "warnings": [], "progress_logs": [_progress_log_entry("参数优化任务已创建，等待执行")],
        })
        return {"run_id": run_id, "status": "queued"}

    def run_optimization(self, run_id: str) -> None:
        row = self.repository.get_quant_backtest_run(run_id)
        if not row:
            return
        try:
            self.repository.update_quant_backtest_progress(
                run_id, status="running", progress_pct=10,
                current_stage="准备数据", log_message="开始加载K线数据",
            )
            payload = row.get("request") or {}
            created_data_provider = self.data_provider is None
            data_provider = self.data_provider or _build_default_data_provider(self.repository, payload)

            from .models import ParamGrid
            from .optimizer import GridSearchOptimizer

            param_space = payload.get("param_space") or {}
            objective = str(payload.get("objective") or "sharpe")
            optimizer = GridSearchOptimizer(data_provider, objective=objective)
            param_grid = ParamGrid(
                strategy_type=str(payload["strategy_type"]),
                param_space={str(k): list(v) for k, v in param_space.items()},
                objective=objective,
            )
            self.repository.update_quant_backtest_progress(
                run_id, progress_pct=20, current_stage="网格搜索",
                log_message=f"策略: {param_grid.strategy_type}, 参数空间: {param_space}",
            )
            try:
                results = optimizer.optimize(
                    market=str(payload["market"]),
                    symbol=str(payload["symbol"]),
                    start=_parse_date(payload["start"]),
                    end=_parse_date(payload["end"]),
                    param_grid=param_grid,
                    initial_cash=float(payload.get("initial_cash") or 100000),
                )
            finally:
                if created_data_provider and hasattr(data_provider, "close"):
                    data_provider.close()
            self.repository.update_quant_backtest_progress(
                run_id, progress_pct=85, current_stage="汇总结果",
                log_message=f"测试 {len(results)} 组参数，最优: {results[0].params if results else 'N/A'}",
            )
            best_metrics = asdict(results[0].metrics) if results else {}
            best_metrics["optimization_total"] = len(results)
            best_metrics["optimization_top_params"] = results[0].params if results else {}
            sym = str(payload.get("symbol") or payload.get("symbols", [""])[0])
            self.repository.finish_quant_backtest_run(
                run_id, best_metrics, warnings=[],
                chart=_optimization_chart_payload(results, sym),
            )
        except Exception as exc:
            self.repository.fail_quant_backtest_run(run_id, str(exc))


def _parse_date(value: Any) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _progress_log_entry(message: str) -> dict[str, str]:
    return {"time": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds"), "message": message}


def _build_default_data_provider(repository, payload: dict) -> CachedKlineDataProvider:
    return CachedKlineDataProvider(
        repository=repository,
        timeframe=str(payload.get("timeframe") or "1d"),
        quote_ctx=_open_futu_quote_context(),
        close_quote_ctx=True,
    )


def _open_futu_quote_context():
    if not _env_enabled("QUANT_ENABLE_FUTU_OPEND", default=_env_enabled("KLINE_USE_FUTU_OPEND", default=False)):
        return None
    context_cls = OpenQuoteContext
    if context_cls is None:
        return None
    try:
        host = os.getenv("FUTU_HOST", "127.0.0.1")
        port = int(os.getenv("FUTU_PORT", "11111"))
        return context_cls(host=host, port=port)
    except Exception:
        return None


def _env_enabled(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _chart_payload(result) -> dict[str, Any]:
    symbols = []
    for symbol, bars in result.bars_by_symbol.items():
        close_by_date = {bar.ts: float(bar.close) for bar in bars}
        signals = [signal_to_dict(signal, close_by_date.get(signal.ts)) for signal in result.signals if signal.symbol == symbol]
        trades = [trade_to_dict(trade) for trade in result.trades if trade.symbol == symbol]
        symbols.append(
            {
                "symbol": symbol,
                "bars": [bar_to_dict(bar) for bar in bars],
                "signals": signals,
                "trades": trades,
                "overlays": _strategy_overlays(symbol, bars, result.signals),
            }
        )
    return {"symbols": symbols}


def _strategy_overlays(symbol: str, bars, signals) -> list[dict[str, Any]]:
    symbol_signals = [signal for signal in signals if signal.symbol == symbol and signal.direction == "buy"]
    rule_outputs = []
    for signal in symbol_signals:
        for output in (signal.rule_chain_snapshot or {}).get("rule_outputs") or []:
            item = dict(output)
            item.setdefault("date", signal.ts.isoformat())
            rule_outputs.append(item)

    overlays = []
    zuoyi = _zuoyi_overlay(rule_outputs)
    if zuoyi:
        overlays.append(zuoyi)
    ema = _ema_overlay(rule_outputs, bars)
    if ema:
        overlays.append(ema)
    rsi = _rsi_overlay(rule_outputs, bars)
    if rsi:
        overlays.append(rsi)
    volume = _volume_overlay(rule_outputs, bars)
    if volume:
        overlays.append(volume)
    pct_change = _pct_change_overlay(rule_outputs)
    if pct_change:
        overlays.append(pct_change)
    return overlays


def _zuoyi_overlay(rule_outputs: list[dict[str, Any]]) -> dict[str, Any] | None:
    items = []
    seen = set()
    for output in rule_outputs:
        if output.get("rule_key") != "zuoyi_signal":
            continue
        for item in ((output.get("details") or {}).get("signals") or []):
            key = (
                item.get("direction"),
                item.get("left_one_date"),
                item.get("median_date"),
                item.get("breakout_date"),
                item.get("left_one_high"),
                item.get("left_one_low"),
            )
            if key in seen:
                continue
            seen.add(key)
            items.append(dict(item))
    if not items:
        return None
    return {"type": "zuoyi", "rule_key": "zuoyi_signal", "rule_name": "左一战法", "items": items}


def _ema_overlay(rule_outputs: list[dict[str, Any]], bars) -> dict[str, Any] | None:
    outputs = [item for item in rule_outputs if item.get("rule_key") == "ema_breakout"]
    if not outputs:
        return None
    periods = _ema_periods(outputs)
    frame = _bars_frame(bars)
    lines = []
    for period in periods:
        values = frame["close"].ewm(span=period, adjust=False).mean()
        lines.append(
            {
                "name": f"EMA{period}",
                "points": [
                    {"date": row_date.isoformat(), "value": float(value)}
                    for row_date, value in zip(frame["date"], values)
                    if pd.notna(value)
                ],
            }
        )
    signal_items = []
    for output in outputs:
        if output.get("result") != "pass":
            continue
        details = output.get("details") or {}
        breakout_date = details.get("breakout_date")
        if breakout_date:
            signal_items.append({"date": str(breakout_date), "label": "EMA突破"})
    return {"type": "ema", "rule_key": "ema_breakout", "rule_name": "EMA 突破", "lines": lines, "signals": signal_items}


def _rsi_overlay(rule_outputs: list[dict[str, Any]], bars) -> dict[str, Any] | None:
    outputs = [item for item in rule_outputs if item.get("rule_key") in {"rsi_oversold", "rsi_overbought"}]
    if not outputs:
        return None
    period = int(((outputs[0].get("details") or {}).get("period")) or 14)
    frame = _bars_frame(bars)
    values = calculate_rsi(frame["close"], period=period)
    signals = []
    thresholds = {30, 70}
    for output in outputs:
        details = output.get("details") or {}
        if details.get("threshold") is not None:
            thresholds.add(float(details["threshold"]))
        if output.get("result") == "pass":
            signals.append(
                {
                    "date": _signal_date_from_output(output),
                    "rule_key": output.get("rule_key"),
                    "rule_name": output.get("rule_name"),
                    "value": details.get("rsi"),
                }
            )
    return {
        "type": "rsi",
        "rule_key": "rsi",
        "rule_name": f"RSI({period})",
        "thresholds": sorted(thresholds),
        "lines": [
            {
                "name": f"RSI{period}",
                "points": [
                    {"date": row_date.isoformat(), "value": float(value)}
                    for row_date, value in zip(frame["date"], values)
                    if pd.notna(value)
                ],
            }
        ],
        "signals": [item for item in signals if item.get("date")],
    }


def _volume_overlay(rule_outputs: list[dict[str, Any]], bars) -> dict[str, Any] | None:
    outputs = [item for item in rule_outputs if item.get("rule_key") == "volume_spike_prior3"]
    if not outputs:
        return None
    passed = [item for item in outputs if item.get("result") == "pass"]
    return {
        "type": "volume",
        "rule_key": "volume_spike_prior3",
        "rule_name": "放量超前三日",
        "bars": [{"date": bar.ts.isoformat(), "value": float(bar.volume)} for bar in bars],
        "signals": [
            {
                "date": _signal_date_from_output(item),
                "today_volume": (item.get("details") or {}).get("today_volume"),
                "max_volume_prior3": (item.get("details") or {}).get("max_volume_prior3"),
            }
            for item in passed
        ],
    }


def _pct_change_overlay(rule_outputs: list[dict[str, Any]]) -> dict[str, Any] | None:
    outputs = [item for item in rule_outputs if item.get("rule_key") in {"daily_drop_6_65", "daily_rise_4_45"}]
    if not outputs:
        return None
    items = []
    for output in outputs:
        if output.get("result") != "pass":
            continue
        details = output.get("details") or {}
        items.append(
            {
                "date": _signal_date_from_output(output),
                "rule_key": output.get("rule_key"),
                "rule_name": output.get("rule_name"),
                "pct_change": details.get("pct_change"),
                "band_min": details.get("band_min"),
                "band_max": details.get("band_max"),
            }
        )
    return {"type": "pct_change", "rule_key": "daily_pct_change", "rule_name": "单日涨跌幅", "signals": items}


def _ema_periods(outputs: list[dict[str, Any]]) -> list[int]:
    periods = set()
    for output in outputs:
        for key in (output.get("details") or {}).keys():
            if isinstance(key, str) and key.startswith("ema") and key[3:].isdigit():
                periods.add(int(key[3:]))
    return sorted(periods or {10, 150})


def _signal_date_from_output(output: dict[str, Any]) -> str:
    if output.get("date"):
        return str(output["date"])
    details = output.get("details") or {}
    for key in ("date", "breakout_date", "check_date"):
        if details.get(key):
            return str(details[key])
    return ""


def _bars_frame(bars) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": [bar.ts for bar in bars],
            "close": [float(bar.close) for bar in bars],
        }
    )


def _build_rule_chain_strategy(repository, payload: dict) -> RuleChainStrategyAdapter:
    market = str(payload["market"])
    timeframe = str(payload.get("timeframe") or "*")
    chain_key = str(payload.get("entry_chain_key") or "default")
    rule_repository = RuleRepository(repository)
    metadata = rule_repository.load_metadata(market)
    try:
        chain = rule_repository.load_chain(market, chain_key, timeframe)
    except ValueError:
        if chain_key != "default":
            raise
        chain = rule_repository.load_active_chain(market, timeframe)
    engine = RuleEngine(metadata=metadata, chain_config=chain)
    return RuleChainStrategyAdapter(chain=chain, rule_engine=engine, exit_policy=payload.get("exit_policy") or {})


def _chart_payload_for_strategy(result) -> dict[str, Any]:
    """策略回测的 chart payload（简化版，无规则链叠加层）"""
    symbols = []
    for symbol, bars in result.bars_by_symbol.items():
        symbols.append({
            "symbol": symbol,
            "bars": [bar_to_dict(bar) for bar in bars],
            "signals": [signal_to_dict(s, None) for s in result.signals if s.symbol == symbol],
            "trades": [trade_to_dict(t) for t in result.trades if t.symbol == symbol],
            "overlays": [],
        })
    return {"symbols": symbols}


def _optimization_chart_payload(results: list, symbol: str) -> dict[str, Any]:
    """参数优化结果的 chart payload"""
    if not results:
        return {"symbols": [], "optimization_results": []}
    return {
        "symbols": [{"symbol": symbol, "bars": [], "signals": [], "trades": [], "overlays": []}],
        "optimization_results": [
            {
                "params": r.params, "rank": r.rank,
                "objective_value": r.objective_value,
                "sharpe": r.metrics.sharpe,
                "total_return": r.metrics.total_return,
                "max_drawdown": r.metrics.max_drawdown,
                "win_rate": r.metrics.win_rate,
            }
            for r in results[:50]
        ],
    }
