#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Main-force outflow risk analysis for screened stocks."""

from __future__ import annotations

import math
import os
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Protocol

import pandas as pd

from kline_fetcher import KlineFetcherBase
from market import normalize_market


DATA_KEYS = ("fund_flow", "order_book", "lhb", "chip")
MINUTE_TIMEFRAMES = {"1m", "2m", "5m", "15m", "30m", "60m", "90m"}

DATA_LABELS = {
    "fund_flow": "资金流向",
    "order_book": "盘口",
    "lhb": "龙虎榜",
    "chip": "筹码分布",
}

STATUS_LABELS = {
    "available": "可用",
    "insufficient": "数据不足",
    "not_applicable": "不适用",
    "unsupported": "不支持",
    "permission_denied": "无权限",
    "provider_error": "接口异常",
    "proxy_only": "近似",
}

PROVIDER_LABELS = {
    "none": "无",
    "disabled": "未启用",
    "kline": "K线",
    "futu": "富途OpenD",
    "akshare": "AkShare",
    "yfinance": "雅虎财经",
    "volume_profile": "K线成交量分布",
}

RISK_LEVEL_LABELS = {
    "high": "高",
    "medium": "中",
    "low": "低",
    "unknown": "数据不足",
}

SEVERITY_RANK = {
    "high": 3,
    "medium": 2,
    "low": 1,
}


def env_enabled(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _to_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def _safe_pct(current: float, previous: float) -> Optional[float]:
    if previous == 0:
        return None
    return (current / previous - 1.0) * 100.0


def _uses_realtime_order_book(timeframe: str) -> bool:
    return str(timeframe or "").strip().lower() in MINUTE_TIMEFRAMES


def _status_text(status: "DataStatus") -> str:
    if status.status == "proxy_only" and status.provider == "volume_profile":
        status_label = "可用"
    else:
        status_label = STATUS_LABELS.get(status.status, status.status)
    provider_label = PROVIDER_LABELS.get(status.provider, status.provider)
    if status.provider and status.provider != "none":
        return f"{status_label}:{provider_label}"
    return status_label


def _format_compact_number(value: Any, unit: str = "") -> str:
    number = _to_float(value)
    if number is None:
        return ""
    abs_number = abs(number)
    if abs_number >= 100000000:
        text = f"{abs_number / 100000000:.2f}亿"
    elif abs_number >= 10000:
        text = f"{abs_number / 10000:.2f}万"
    else:
        text = f"{abs_number:.0f}"
    return f"{text}{unit}"


def _net_amount_phrase(label: str, value: Any) -> str:
    number = _to_float(value)
    if number is None:
        return ""
    amount = _format_compact_number(number)
    if number > 0:
        return f"{label}净流入{amount}"
    if number < 0:
        return f"{label}净流出{amount}"
    return f"{label}基本持平"


def _fund_flow_observation(metrics: Dict[str, Any]) -> str:
    if not metrics:
        return "资金流向: 暂无明细"
    parts = [
        _net_amount_phrase("整体资金", metrics.get("in_flow") or metrics.get("net_inflow")),
        _net_amount_phrase("主力资金", metrics.get("main_net_inflow") or metrics.get("main_in_flow")),
        _net_amount_phrase("超大单", metrics.get("super_in_flow")),
        _net_amount_phrase("大单", metrics.get("big_in_flow")),
        _net_amount_phrase("中单", metrics.get("mid_in_flow")),
        _net_amount_phrase("小单", metrics.get("sml_in_flow")),
    ]
    values = [part for part in parts if part]
    if not values:
        return "资金流向: 暂无明细"
    time_text = str(metrics.get("capital_flow_item_time") or metrics.get("last_valid_time") or "").strip()
    suffix = f"（{time_text}）" if time_text else ""
    return f"资金流向: {'，'.join(values)}{suffix}"


def _order_book_observation(metrics: Dict[str, Any]) -> str:
    if not metrics:
        return "盘口: 暂无明细"
    buy1 = _to_float(metrics.get("buy1_volume"))
    sell1 = _to_float(metrics.get("sell1_volume"))
    ratio = _to_float(metrics.get("sell1_buy1_ratio"))
    values = []
    if sell1 is not None:
        values.append(f"卖一量{_format_compact_number(sell1, '股')}")
    if buy1 is not None:
        values.append(f"买一量{_format_compact_number(buy1, '股')}")
    if ratio is not None:
        values.append(f"卖一约为买一{ratio:.1f}倍")
    return f"盘口: {'，'.join(values)}" if values else "盘口: 暂无明细"


def _broker_queue_observation(metrics: Dict[str, Any]) -> str:
    if not metrics:
        return ""
    bid_count = _to_float(metrics.get("bid_broker_count"))
    ask_count = _to_float(metrics.get("ask_broker_count"))
    if bid_count is None and ask_count is None:
        return ""
    values = []
    if ask_count is not None:
        values.append(f"卖盘经纪{ask_count:.0f}家")
    if bid_count is not None:
        values.append(f"买盘经纪{bid_count:.0f}家")
    return f"经纪队列: {'，'.join(values)}"


def _volume_profile_observation(
    data_status: Dict[str, "DataStatus"],
    metrics: Dict[str, Any],
    market: str,
) -> str:
    kline_metrics = metrics.get("kline") if isinstance(metrics.get("kline"), dict) else {}
    volume_profile = kline_metrics.get("volume_profile_proxy") if isinstance(kline_metrics, dict) else None
    if isinstance(volume_profile, dict):
        latest_close = _to_float(volume_profile.get("latest_close"))
        volume_weighted_price = _to_float(volume_profile.get("volume_weighted_price"))
        vs_pct = _to_float(volume_profile.get("price_vs_volume_weighted_pct"))
        top_low = _to_float(volume_profile.get("top_volume_price_low"))
        top_high = _to_float(volume_profile.get("top_volume_price_high"))
        top_share = _to_float(volume_profile.get("top_volume_share_pct"))
        sample_days = _to_float(volume_profile.get("sample_days"))
        total_volume = _to_float(volume_profile.get("total_volume"))
        if latest_close is not None and volume_weighted_price is not None:
            relation = "低于" if latest_close < volume_weighted_price else "高于"
            parts = [
                f"现价{latest_close:.2f}",
                f"近120日成交量加权价{volume_weighted_price:.2f}",
            ]
            if vs_pct is not None:
                parts.append(f"{relation}{abs(vs_pct):.1f}%")
            if top_low is not None and top_high is not None:
                bucket = f"最大成交量区间{top_low:.2f}~{top_high:.2f}"
                if top_share is not None:
                    bucket = f"{bucket}，占比{top_share:.1f}%"
                parts.append(bucket)
            if sample_days is not None:
                sample_text = f"样本{sample_days:.0f}日"
                if total_volume is not None:
                    sample_text = f"{sample_text}，总成交量{_format_compact_number(total_volume, '股')}"
                parts.append(sample_text)
            return f"成交量分布: {'；'.join(parts)}"
    status = data_status.get("chip")
    if status and status.status == "proxy_only":
        return "成交量分布: 暂无可展示明细"
    if normalize_market(market) in {"HK", "US"}:
        return "成交量分布: 暂无可展示明细"
    return ""


def _market_data_observation_text(
    data_status: Dict[str, "DataStatus"],
    metrics: Dict[str, Any],
    market: str,
) -> str:
    fund_flow = metrics.get("fund_flow") if isinstance(metrics.get("fund_flow"), dict) else {}
    order_book = metrics.get("order_book") if isinstance(metrics.get("order_book"), dict) else {}
    broker_queue = metrics.get("broker_queue") if isinstance(metrics.get("broker_queue"), dict) else {}
    parts = [_fund_flow_observation(fund_flow)]
    if order_book:
        parts.append(_order_book_observation(order_book))
    if broker_queue:
        parts.append(_broker_queue_observation(broker_queue))
    parts.append(_volume_profile_observation(data_status, metrics, market))
    values = [part for part in parts if part]
    return "；".join(values) if values else "暂无资金与盘面明细"


@dataclass(frozen=True)
class DataStatus:
    """Availability status for one evidence category."""

    key: str
    status: str
    provider: str = "none"
    message: str = ""

    def to_dict(self) -> Dict[str, str]:
        return {
            "key": self.key,
            "label": DATA_LABELS.get(self.key, self.key),
            "status": self.status,
            "status_label": "可用"
            if self.status == "proxy_only" and self.provider == "volume_profile"
            else STATUS_LABELS.get(self.status, self.status),
            "provider": self.provider,
            "provider_label": PROVIDER_LABELS.get(self.provider, self.provider),
            "message": self.message,
        }

    def display(self) -> str:
        return _status_text(self)


@dataclass(frozen=True)
class RiskSignal:
    """One human-facing risk signal."""

    key: str
    label: str
    severity: str
    score: float
    source: str
    evidence: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "severity": self.severity,
            "severity_label": RISK_LEVEL_LABELS.get(self.severity, self.severity),
            "score": self.score,
            "source": self.source,
            "evidence": self.evidence,
        }


@dataclass
class ExternalRiskSnapshot:
    """Provider-normalized optional market microstructure evidence."""

    statuses: Dict[str, DataStatus] = field(default_factory=dict)
    metrics: Dict[str, Any] = field(default_factory=dict)
    signals: List[RiskSignal] = field(default_factory=list)
    missing_data: List[str] = field(default_factory=list)

    def set_status(self, status: DataStatus) -> None:
        self.statuses[status.key] = status

    @classmethod
    def disabled(cls, market: str) -> "ExternalRiskSnapshot":
        snapshot = cls()
        normalized = normalize_market(market)
        snapshot.set_status(DataStatus("fund_flow", "unsupported", "disabled", "外部资金流向数据未启用"))
        snapshot.set_status(DataStatus("order_book", "unsupported", "disabled", "外部盘口数据未启用"))
        if normalized == "A":
            snapshot.set_status(DataStatus("lhb", "unsupported", "disabled", "外部龙虎榜数据未启用"))
            snapshot.set_status(DataStatus("chip", "unsupported", "disabled", "外部筹码分布数据未启用"))
        else:
            snapshot.set_status(DataStatus("lhb", "not_applicable", "none", "港美股没有A股龙虎榜同口径数据"))
            snapshot.set_status(DataStatus("chip", "proxy_only", "volume_profile", "使用K线成交量分布观察"))
        return snapshot


@dataclass(frozen=True)
class MainForceRiskResult:
    """Final risk result for one screened stock."""

    code: str
    name: str
    market: str
    risk_level: str
    risk_score: Optional[float]
    risk_summary: str
    triggered_signals: List[RiskSignal]
    data_status: Dict[str, DataStatus]
    missing_data: List[str]
    metrics: Dict[str, Any]
    analysis_status: str = "success"
    error_message: str = ""

    @property
    def risk_level_text(self) -> str:
        return RISK_LEVEL_LABELS.get(self.risk_level, self.risk_level)

    def data_status_text(self, key: str) -> str:
        status = self.data_status.get(key)
        if status is None:
            return STATUS_LABELS["insufficient"]
        return status.display()

    def signal_labels_text(self, limit: int = 3) -> str:
        ordered = sorted(
            self.triggered_signals,
            key=lambda item: (SEVERITY_RANK.get(item.severity, 0), item.score),
            reverse=True,
        )
        return "；".join(item.label for item in ordered[:limit])

    def missing_data_text(self) -> str:
        return "；".join(self.missing_data)

    def market_data_observation_text(self) -> str:
        return _market_data_observation_text(self.data_status, self.metrics, self.market)

    def to_record_fields(self) -> Dict[str, Any]:
        score_text = "" if self.risk_score is None else f"{self.risk_score:.2f}"
        return {
            "main_force_risk_level": self.risk_level,
            "main_force_risk_level_text": self.risk_level_text,
            "main_force_risk_score": self.risk_score,
            "main_force_risk_score_text": score_text,
            "main_force_risk_summary": self.risk_summary,
            "main_force_risk_signals_text": self.signal_labels_text(),
            "main_force_fund_flow_data_text": self.data_status_text("fund_flow"),
            "main_force_order_book_data_text": self.data_status_text("order_book"),
            "main_force_lhb_data_text": self.data_status_text("lhb"),
            "main_force_chip_data_text": self.data_status_text("chip"),
            "main_force_missing_data_text": self.missing_data_text(),
            "main_force_market_data_observation_text": self.market_data_observation_text(),
            "main_force_risk_signals": [signal.to_dict() for signal in self.triggered_signals],
            "main_force_data_status": {
                key: status.to_dict() for key, status in self.data_status.items()
            },
            "main_force_missing_data": list(self.missing_data),
            "main_force_metrics": dict(self.metrics),
        }

    def to_db_row(
        self,
        *,
        task_id: str,
        check_date,
        csv_path: str,
    ) -> Dict[str, Any]:
        return {
            "task_id": task_id,
            "market": self.market,
            "code": self.code,
            "name": self.name,
            "check_date": check_date,
            "csv_path": csv_path,
            "analysis_status": self.analysis_status,
            "risk_level": self.risk_level,
            "risk_score": self.risk_score,
            "risk_summary": self.risk_summary,
            "triggered_signals": [signal.to_dict() for signal in self.triggered_signals],
            "missing_data": list(self.missing_data),
            "provider_status": {
                key: status.to_dict() for key, status in self.data_status.items()
            },
            "metrics_json": dict(self.metrics),
            "error_message": self.error_message,
        }


class MainForceDataProvider(Protocol):
    """Optional external-data boundary for main-force risk analysis."""

    def fetch(self, market: str, code: str, timeframe: str = "1d") -> ExternalRiskSnapshot:
        ...

    def close(self) -> None:
        ...


class NullMainForceDataProvider:
    """Provider used when optional external evidence is disabled."""

    def fetch(self, market: str, code: str, timeframe: str = "1d") -> ExternalRiskSnapshot:
        return ExternalRiskSnapshot.disabled(market)

    def close(self) -> None:
        return None


class AkShareMainForceDataProvider:
    """Best-effort A-share main-force evidence from AkShare."""

    name = "akshare"

    def __init__(self, ak_module=None):
        if ak_module is not None:
            self.ak = ak_module
        else:
            import akshare as ak
            self.ak = ak

    def close(self) -> None:
        return None

    def fetch(self, market: str, code: str, timeframe: str = "1d") -> ExternalRiskSnapshot:
        if normalize_market(market) != "A":
            return ExternalRiskSnapshot.disabled(market)
        a_code = self._a_code(code)
        snapshot = ExternalRiskSnapshot()
        self._fetch_fund_flow(a_code, snapshot)
        if _uses_realtime_order_book(timeframe):
            self._fetch_order_book(a_code, snapshot)
        else:
            snapshot.set_status(DataStatus("order_book", "not_applicable", "none", "日线级别不使用盘口快照"))
        self._fetch_lhb(a_code, snapshot)
        self._fetch_chip(a_code, snapshot)
        return snapshot

    @staticmethod
    def _a_code(code: str) -> str:
        value = str(code or "").strip()
        if value.upper().startswith(("SH.", "SZ.")):
            value = value[3:]
        if "." in value:
            value = value.split(".", 1)[0]
        return value.zfill(6) if value.isdigit() else value

    def _fetch_fund_flow(self, code: str, snapshot: ExternalRiskSnapshot) -> None:
        fn = getattr(self.ak, "stock_individual_fund_flow", None)
        if fn is None:
            snapshot.set_status(DataStatus("fund_flow", "unsupported", self.name, "AkShare缺少个股资金流向接口"))
            return
        try:
            df = fn(stock=code)
            if df is None or getattr(df, "empty", True):
                snapshot.set_status(DataStatus("fund_flow", "insufficient", self.name, "资金流向为空"))
                return
            row = df.iloc[-1].to_dict()
            main_flow = _first_numeric_by_keywords(row, ("主力", "净流入"), exclude=("占比",))
            snapshot.metrics["fund_flow"] = {"main_net_inflow": main_flow}
            snapshot.set_status(DataStatus("fund_flow", "available", self.name))
            if main_flow is not None and main_flow < 0:
                snapshot.signals.append(RiskSignal(
                    key="fund_flow_negative",
                    label="主力资金净流出",
                    severity="medium",
                    score=12,
                    source="资金流向",
                    evidence=f"主力资金净流入为{main_flow:.0f}",
                ))
        except Exception as exc:
            snapshot.set_status(DataStatus("fund_flow", "provider_error", self.name, str(exc)[:120]))

    def _fetch_order_book(self, code: str, snapshot: ExternalRiskSnapshot) -> None:
        fn = getattr(self.ak, "stock_bid_ask_em", None)
        if fn is None:
            snapshot.set_status(DataStatus("order_book", "unsupported", self.name, "AkShare缺少盘口接口"))
            return
        try:
            df = fn(symbol=code)
            if df is None or getattr(df, "empty", True):
                snapshot.set_status(DataStatus("order_book", "insufficient", self.name, "盘口为空"))
                return
            values = df.to_dict("records")
            metrics = _extract_bid_ask_metrics(values)
            snapshot.metrics["order_book"] = metrics
            snapshot.set_status(DataStatus("order_book", "available", self.name))
            ratio = _to_float(metrics.get("sell1_buy1_ratio"))
            if ratio is not None and ratio >= 2.5:
                snapshot.signals.append(RiskSignal(
                    key="sell_order_pressure",
                    label="盘口卖盘压制",
                    severity="medium",
                    score=10,
                    source="盘口",
                    evidence=f"卖一量约为买一量{ratio:.1f}倍",
                ))
        except Exception as exc:
            snapshot.set_status(DataStatus("order_book", "provider_error", self.name, str(exc)[:120]))

    def _fetch_lhb(self, code: str, snapshot: ExternalRiskSnapshot) -> None:
        fn = getattr(self.ak, "stock_lhb_detail_em", None)
        if fn is None:
            snapshot.set_status(DataStatus("lhb", "unsupported", self.name, "AkShare缺少龙虎榜明细接口"))
            return
        snapshot.set_status(DataStatus("lhb", "insufficient", self.name, "未按交易日拉取到个股龙虎榜明细"))

    def _fetch_chip(self, code: str, snapshot: ExternalRiskSnapshot) -> None:
        fn = getattr(self.ak, "stock_cyq_em", None)
        if fn is None:
            snapshot.set_status(DataStatus("chip", "unsupported", self.name, "AkShare缺少筹码分布接口"))
            return
        try:
            df = fn(symbol=code)
            if df is None or getattr(df, "empty", True):
                snapshot.set_status(DataStatus("chip", "insufficient", self.name, "筹码分布为空"))
                return
            row = df.iloc[-1].to_dict()
            snapshot.metrics["chip"] = {str(k): _to_float(v) for k, v in row.items() if _to_float(v) is not None}
            snapshot.set_status(DataStatus("chip", "available", self.name))
        except Exception as exc:
            snapshot.set_status(DataStatus("chip", "provider_error", self.name, str(exc)[:120]))


class FutuOpenDMainForceDataProvider:
    """Best-effort HK/US main-force evidence from Futu OpenD."""

    name = "futu"

    def __init__(self, quote_ctx=None, host: str = "127.0.0.1", port: int = 11111):
        self._owns_context = quote_ctx is None
        if quote_ctx is not None:
            self.quote_ctx = quote_ctx
            self.ft = sys.modules.get("futu")
        else:
            import futu as ft
            self.ft = ft
            self.quote_ctx = ft.OpenQuoteContext(host=host, port=port)

    def close(self) -> None:
        if self._owns_context and self.quote_ctx is not None:
            try:
                self.quote_ctx.close()
            except Exception:
                pass

    def fetch(self, market: str, code: str, timeframe: str = "1d") -> ExternalRiskSnapshot:
        normalized = normalize_market(market)
        if normalized not in {"HK", "US"}:
            return ExternalRiskSnapshot.disabled(market)
        snapshot = ExternalRiskSnapshot()
        self._fetch_fund_flow(code, snapshot)
        if _uses_realtime_order_book(timeframe):
            self._fetch_order_book(code, snapshot)
        else:
            snapshot.set_status(DataStatus("order_book", "not_applicable", "none", "日线级别不使用盘口快照"))
        if normalized == "HK" and _uses_realtime_order_book(timeframe):
            self._fetch_broker_queue(code, snapshot)
        snapshot.set_status(DataStatus("lhb", "not_applicable", "none", "港美股没有A股龙虎榜同口径数据"))
        snapshot.set_status(DataStatus("chip", "proxy_only", "volume_profile", "使用K线成交量分布观察"))
        return snapshot

    def _fetch_fund_flow(self, code: str, snapshot: ExternalRiskSnapshot) -> None:
        if not hasattr(self.quote_ctx, "get_capital_flow"):
            snapshot.set_status(DataStatus("fund_flow", "unsupported", self.name, "富途SDK缺少资金流向接口"))
            return
        try:
            ret, data = self.quote_ctx.get_capital_flow(code)
            if not _is_ret_ok(self.ft, ret):
                snapshot.set_status(_futu_error_status("fund_flow", self.name, data))
                return
            metrics = _frame_or_dict_latest(data)
            snapshot.metrics["fund_flow"] = metrics
            snapshot.set_status(DataStatus("fund_flow", "available", self.name))
            main_flow = _first_numeric_by_keywords(metrics, ("net", "in"), exclude=("ratio", "rate"))
            if main_flow is not None and main_flow < 0:
                snapshot.signals.append(RiskSignal(
                    key="fund_flow_negative",
                    label="主力资金净流出",
                    severity="medium",
                    score=12,
                    source="资金流向",
                    evidence=f"资金净流入为{main_flow:.0f}",
                ))
        except Exception as exc:
            snapshot.set_status(DataStatus("fund_flow", "provider_error", self.name, str(exc)[:120]))

    def _fetch_order_book(self, code: str, snapshot: ExternalRiskSnapshot) -> None:
        subtype = getattr(self.ft, "SubType", None)
        if subtype is None or not hasattr(subtype, "ORDER_BOOK"):
            snapshot.set_status(DataStatus("order_book", "unsupported", self.name, "富途SDK缺少盘口订阅类型"))
            return
        try:
            ret, message = self.quote_ctx.subscribe([code], [subtype.ORDER_BOOK], subscribe_push=False)
            if not _is_ret_ok(self.ft, ret):
                snapshot.set_status(_futu_error_status("order_book", self.name, message))
                return
            ret, data = self.quote_ctx.get_order_book(code, num=10)
            if not _is_ret_ok(self.ft, ret):
                snapshot.set_status(_futu_error_status("order_book", self.name, data))
                return
            metrics = _extract_futu_order_book_metrics(data)
            snapshot.metrics["order_book"] = metrics
            snapshot.set_status(DataStatus("order_book", "available", self.name))
            ratio = _to_float(metrics.get("sell1_buy1_ratio"))
            if ratio is not None and ratio >= 2.5:
                snapshot.signals.append(RiskSignal(
                    key="sell_order_pressure",
                    label="盘口卖盘压制",
                    severity="medium",
                    score=10,
                    source="盘口",
                    evidence=f"卖一量约为买一量{ratio:.1f}倍",
                ))
        except Exception as exc:
            snapshot.set_status(DataStatus("order_book", "provider_error", self.name, str(exc)[:120]))
        finally:
            try:
                self.quote_ctx.unsubscribe([code], [subtype.ORDER_BOOK])
            except Exception:
                pass

    def _fetch_broker_queue(self, code: str, snapshot: ExternalRiskSnapshot) -> None:
        subtype = getattr(self.ft, "SubType", None)
        if subtype is None or not hasattr(subtype, "BROKER"):
            return
        try:
            ret, message = self.quote_ctx.subscribe([code], [subtype.BROKER], subscribe_push=False)
            if not _is_ret_ok(self.ft, ret):
                return
            ret, bid_frame, ask_frame = self.quote_ctx.get_broker_queue(code)
            if not _is_ret_ok(self.ft, ret):
                return
            bid_count = 0 if bid_frame is None else len(bid_frame)
            ask_count = 0 if ask_frame is None else len(ask_frame)
            snapshot.metrics["broker_queue"] = {"bid_broker_count": bid_count, "ask_broker_count": ask_count}
            if ask_count >= max(5, bid_count * 2):
                snapshot.signals.append(RiskSignal(
                    key="broker_queue_sell_pressure",
                    label="经纪队列卖盘集中",
                    severity="low",
                    score=6,
                    source="经纪队列",
                    evidence=f"卖盘经纪席位数量{ask_count}，买盘经纪席位数量{bid_count}",
                ))
        except Exception:
            return
        finally:
            try:
                self.quote_ctx.unsubscribe([code], [subtype.BROKER])
            except Exception:
                pass


class MainForceRiskAnalyzer:
    """Calculates risk signals from K-line and optional provider evidence."""

    def analyze(
        self,
        *,
        record: Dict[str, Any],
        market: str,
        kline: Optional[pd.DataFrame],
        external: ExternalRiskSnapshot,
    ) -> MainForceRiskResult:
        code = str(record.get("code") or record.get("股票代码") or "").strip()
        name = str(record.get("name") or record.get("名称") or code).strip()
        metrics: Dict[str, Any] = {}
        signals: List[RiskSignal] = []
        statuses: Dict[str, DataStatus] = dict(external.statuses)
        missing_data: List[str] = list(external.missing_data)

        kline_signals, kline_metrics = self._analyze_kline(kline)
        signals.extend(kline_signals)
        metrics.update(external.metrics)
        metrics["kline"] = kline_metrics
        signals.extend(external.signals)

        if normalize_market(market) in {"HK", "US"} and "chip" not in statuses:
            statuses["chip"] = DataStatus("chip", "proxy_only", "volume_profile", "使用K线成交量分布观察")
        if normalize_market(market) in {"HK", "US"} and "lhb" not in statuses:
            statuses["lhb"] = DataStatus("lhb", "not_applicable", "none", "港美股没有A股龙虎榜同口径数据")
        for key in DATA_KEYS:
            statuses.setdefault(key, DataStatus(key, "insufficient", "none", "没有可用数据"))

        missing_data.extend(self._missing_data_from_statuses(statuses))
        risk_score = self._score(signals)
        risk_level = self._level(risk_score, kline_metrics)
        summary = self._summary(risk_level, signals, kline_metrics)

        return MainForceRiskResult(
            code=code,
            name=name,
            market=normalize_market(market),
            risk_level=risk_level,
            risk_score=risk_score,
            risk_summary=summary,
            triggered_signals=signals,
            data_status=statuses,
            missing_data=_dedupe(missing_data),
            metrics=metrics,
        )

    def _analyze_kline(self, df: Optional[pd.DataFrame]) -> tuple[List[RiskSignal], Dict[str, Any]]:
        if df is None or getattr(df, "empty", True):
            return [], {"status": "insufficient", "message": "缺少K线数据"}

        data = df.copy()
        for column in ("open", "high", "low", "close", "volume"):
            if column not in data.columns:
                return [], {"status": "insufficient", "message": f"缺少{column}列"}
            data[column] = pd.to_numeric(data[column], errors="coerce")
        data = data.dropna(subset=["open", "high", "low", "close"])
        if len(data) < 20:
            return [], {"status": "insufficient", "message": "K线数量不足20根"}

        latest = data.iloc[-1]
        prev = data.iloc[-2]
        close = float(latest["close"])
        prev_close = float(prev["close"])
        pct = _safe_pct(close, prev_close)
        volume = _to_float(latest.get("volume"))
        prior_volume = pd.to_numeric(data["volume"].iloc[-21:-1], errors="coerce").dropna()
        avg_volume = float(prior_volume.mean()) if len(prior_volume) else None
        volume_ratio = volume / avg_volume if volume is not None and avg_volume else None
        high_60 = float(data["high"].tail(60).max())
        high_position_pct = close / high_60 * 100.0 if high_60 else None
        support_low = float(data["low"].iloc[-21:-1].min())

        metrics = {
            "status": "available",
            "close_change_pct": pct,
            "volume_ratio_20d": volume_ratio,
            "high_position_pct_60d": high_position_pct,
            "support_low_20d": support_low,
        }
        signals: List[RiskSignal] = []

        if pct is not None and volume_ratio is not None and pct <= -3.0 and volume_ratio >= 1.8:
            signals.append(RiskSignal(
                key="volume_price_down",
                label="放量下跌",
                severity="high",
                score=18,
                source="K线",
                evidence=f"收盘跌幅{pct:.1f}%，成交量为20日均量{volume_ratio:.1f}倍",
            ))

        if support_low and close < support_low * 0.995:
            signals.append(RiskSignal(
                key="support_break",
                label="跌破重要支撑",
                severity="high",
                score=16,
                source="K线",
                evidence=f"收盘价{close:.2f}跌破近20日支撑{support_low:.2f}",
            ))

        if (
            high_position_pct is not None
            and volume_ratio is not None
            and pct is not None
            and high_position_pct >= 90.0
            and volume_ratio >= 1.8
            and pct <= 1.5
        ):
            signals.append(RiskSignal(
                key="high_volume_stagnation",
                label="高位放量滞涨",
                severity="medium",
                score=14,
                source="K线",
                evidence=f"价格位于近60日高位{high_position_pct:.0f}%，成交量为20日均量{volume_ratio:.1f}倍但涨幅有限",
            ))

        macd_signal = self._macd_signal(data)
        if macd_signal is not None:
            signals.append(macd_signal)

        rsi_signal = self._rsi_signal(data)
        if rsi_signal is not None:
            signals.append(rsi_signal)

        volume_profile_signal, volume_profile_metrics = self._volume_profile_proxy(data)
        metrics["volume_profile_proxy"] = volume_profile_metrics
        if volume_profile_signal is not None:
            signals.append(volume_profile_signal)

        return signals, metrics

    def _macd_signal(self, data: pd.DataFrame) -> Optional[RiskSignal]:
        if len(data) < 35:
            return None
        close = data["close"].astype(float)
        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        dif = ema12 - ema26
        dea = dif.ewm(span=9, adjust=False).mean()
        hist = dif - dea
        latest = float(hist.iloc[-1])
        previous = float(hist.iloc[-2])
        if latest < 0 <= previous:
            return RiskSignal(
                key="macd_hist_green",
                label="MACD柱翻绿",
                severity="medium",
                score=12,
                source="技术指标",
                evidence="MACD柱由红转绿，短线动能转弱",
            )
        recent = hist.tail(4)
        if len(recent) == 4 and all(float(recent.iloc[i]) < float(recent.iloc[i - 1]) for i in range(1, 4)):
            return RiskSignal(
                key="macd_hist_shrinking",
                label="MACD柱连续缩短",
                severity="low",
                score=8,
                source="技术指标",
                evidence="MACD柱连续3日缩短，短线动能减弱",
            )
        return None

    def _rsi_signal(self, data: pd.DataFrame) -> Optional[RiskSignal]:
        if len(data) < 20:
            return None
        close = data["close"].astype(float)
        delta = close.diff()
        gain = delta.clip(lower=0).rolling(14).mean()
        loss = (-delta.clip(upper=0)).rolling(14).mean()
        rs = gain / loss.replace(0, pd.NA)
        rsi = (100 - (100 / (1 + rs))).dropna()
        if len(rsi) < 5:
            return None
        latest = float(rsi.iloc[-1])
        previous = float(rsi.iloc[-2])
        recent_max = float(rsi.tail(10).max())
        if recent_max >= 70 and latest < 65 and latest < previous:
            return RiskSignal(
                key="rsi_high_pullback",
                label="RSI高位回落",
                severity="low",
                score=8,
                source="技术指标",
                evidence=f"RSI曾高于70，当前回落至{latest:.1f}",
            )
        return None

    def _volume_profile_proxy(self, data: pd.DataFrame) -> tuple[Optional[RiskSignal], Dict[str, Any]]:
        recent = data.tail(120).copy()
        recent["close"] = pd.to_numeric(recent["close"], errors="coerce")
        recent["volume"] = pd.to_numeric(recent["volume"], errors="coerce")
        recent = recent.dropna(subset=["close", "volume"])
        recent = recent[recent["volume"] > 0]
        total_volume = float(recent["volume"].sum()) if not recent.empty else 0.0
        if total_volume <= 0:
            return None, {"status": "insufficient"}
        close = recent["close"].astype(float)
        volume = recent["volume"].astype(float)
        volume_weighted_price = float((close * volume).sum() / total_volume)
        latest_close = float(close.iloc[-1])
        price_vs_vwap_pct = _safe_pct(latest_close, volume_weighted_price)
        top_low, top_high, top_share = self._top_volume_price_interval(close, volume, total_volume)
        metrics = {
            "status": "proxy_only",
            "method": "K线成交量分布",
            "volume_weighted_price": volume_weighted_price,
            "latest_close": latest_close,
            "price_vs_volume_weighted_pct": price_vs_vwap_pct,
            "top_volume_price_low": top_low,
            "top_volume_price_high": top_high,
            "top_volume_share_pct": top_share,
            "sample_days": len(recent),
            "total_volume": total_volume,
        }
        if latest_close < volume_weighted_price * 0.97:
            bucket_text = ""
            if top_low is not None and top_high is not None:
                bucket_text = f"，最大成交量区间{top_low:.2f}~{top_high:.2f}"
                if top_share is not None:
                    bucket_text = f"{bucket_text}，占比{top_share:.1f}%"
            return RiskSignal(
                key="volume_profile_pressure",
                label="成交量分布显示上方压力",
                severity="low",
                score=6,
                source="成交量分布",
                evidence=(
                    f"收盘价{latest_close:.2f}低于近120日成交量加权价"
                    f"{volume_weighted_price:.2f}{bucket_text}"
                ),
            ), metrics
        return None, metrics

    @staticmethod
    def _top_volume_price_interval(
        close: pd.Series,
        volume: pd.Series,
        total_volume: float,
    ) -> tuple[Optional[float], Optional[float], Optional[float]]:
        if close.empty or total_volume <= 0:
            return None, None, None
        min_close = float(close.min())
        max_close = float(close.max())
        if min_close == max_close:
            return min_close, max_close, 100.0
        bucket_count = min(10, max(2, int(close.nunique())))
        buckets = pd.cut(close, bins=bucket_count, include_lowest=True)
        grouped = volume.groupby(buckets, observed=True).sum()
        if grouped.empty:
            return None, None, None
        top_bucket = grouped.idxmax()
        top_volume = float(grouped.max())
        return float(top_bucket.left), float(top_bucket.right), top_volume / total_volume * 100.0

    @staticmethod
    def _score(signals: Iterable[RiskSignal]) -> Optional[float]:
        return min(100.0, sum(max(0.0, signal.score) for signal in signals))

    @staticmethod
    def _level(score: Optional[float], kline_metrics: Dict[str, Any]) -> str:
        if kline_metrics.get("status") != "available" and (score is None or score <= 0):
            return "unknown"
        if score is None:
            return "unknown"
        if score >= 60:
            return "high"
        if score >= 30:
            return "medium"
        return "low"

    @staticmethod
    def _summary(risk_level: str, signals: List[RiskSignal], kline_metrics: Dict[str, Any]) -> str:
        if risk_level == "unknown":
            return "数据不足，无法完整判断主力流出风险"
        if not signals:
            return "暂未看到明确主力流出迹象"
        labels = "、".join(signal.label for signal in sorted(
            signals,
            key=lambda item: (SEVERITY_RANK.get(item.severity, 0), item.score),
            reverse=True,
        )[:3])
        prefix = {
            "high": "主力流出风险偏高",
            "medium": "主力流出风险中等",
            "low": "主力流出风险较低",
        }.get(risk_level, "主力流出风险待确认")
        return f"{prefix}，主要信号为{labels}"

    @staticmethod
    def _missing_data_from_statuses(statuses: Dict[str, DataStatus]) -> List[str]:
        missing = []
        for key in DATA_KEYS:
            status = statuses.get(key)
            if status is None or status.status == "available":
                continue
            if status.status == "proxy_only":
                continue
            missing.append(f"{DATA_LABELS.get(key, key)}{STATUS_LABELS.get(status.status, status.status)}")
        return missing


class MainForceRiskService:
    """Coordinates K-line fetchers, external provider, and risk analyzer."""

    def __init__(
        self,
        *,
        kline_fetchers: List[KlineFetcherBase],
        data_provider: MainForceDataProvider,
        analyzer: Optional[MainForceRiskAnalyzer] = None,
        max_kline_count: int = 180,
    ):
        self.kline_fetchers = kline_fetchers
        self.data_provider = data_provider
        self.analyzer = analyzer or MainForceRiskAnalyzer()
        self.max_kline_count = max_kline_count

    def close(self) -> None:
        close = getattr(self.data_provider, "close", None)
        if callable(close):
            close()

    def analyze_records(
        self,
        *,
        market: str,
        timeframe: str,
        records: List[Dict[str, Any]],
    ) -> List[MainForceRiskResult]:
        results = []
        for record in records:
            code = str(record.get("code") or "").strip()
            if not code:
                continue
            kline = self._fetch_kline(code, market, timeframe)
            external = self.data_provider.fetch(market, code, timeframe=timeframe)
            result = self.analyzer.analyze(
                record=record,
                market=market,
                kline=kline,
                external=external,
            )
            record.update(result.to_record_fields())
            results.append(result)
        return results

    def _fetch_kline(self, code: str, market: str, timeframe: str) -> Optional[pd.DataFrame]:
        for fetcher in self.kline_fetchers:
            try:
                df = fetcher.fetch(code, market=market, timeframe=timeframe, max_count=self.max_kline_count)
            except Exception:
                df = None
            if df is not None and not df.empty:
                return df
        return None


class MainForceRiskServiceFactory:
    """Builds the default service without leaking provider choices to callers."""

    @staticmethod
    def from_env(*, kline_fetchers: List[KlineFetcherBase], market: str) -> MainForceRiskService:
        max_count = _env_int("MAIN_FORCE_KLINE_MAX_COUNT", 180)
        provider = MainForceRiskServiceFactory._provider_from_env(market)
        return MainForceRiskService(
            kline_fetchers=kline_fetchers,
            data_provider=provider,
            max_kline_count=max_count,
        )

    @staticmethod
    def _provider_from_env(market: str) -> MainForceDataProvider:
        if not env_enabled("MAIN_FORCE_ENABLE_EXTERNAL_DATA", True):
            return NullMainForceDataProvider()
        normalized = normalize_market(market)
        if normalized == "A":
            try:
                return AkShareMainForceDataProvider()
            except Exception:
                return NullMainForceDataProvider()
        if normalized in {"HK", "US"}:
            if not env_enabled("MAIN_FORCE_ENABLE_FUTU_OPEND", True):
                return NullMainForceDataProvider()
            try:
                host = os.getenv("FUTU_HOST", "127.0.0.1")
                port = _env_int("FUTU_PORT", 11111)
                return FutuOpenDMainForceDataProvider(host=host, port=port)
            except Exception:
                return NullMainForceDataProvider()
        return NullMainForceDataProvider()


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        return int(raw)
    except ValueError:
        return default


def _dedupe(items: Iterable[str]) -> List[str]:
    result = []
    seen = set()
    for item in items:
        value = str(item or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _first_numeric_by_keywords(
    row: Dict[str, Any],
    keywords: Iterable[str],
    exclude: Iterable[str] = (),
) -> Optional[float]:
    lower_keywords = tuple(str(item).lower() for item in keywords)
    lower_exclude = tuple(str(item).lower() for item in exclude)
    for key, value in row.items():
        key_text = str(key).lower()
        if all(keyword in key_text for keyword in lower_keywords) and not any(ex in key_text for ex in lower_exclude):
            number = _to_float(value)
            if number is not None:
                return number
    return None


def _extract_bid_ask_metrics(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    flat: Dict[str, Any] = {}
    for row in rows:
        for key, value in row.items():
            flat[str(key)] = value
    bid_volume = _first_numeric_by_keywords(flat, ("买一", "量")) or _first_numeric_by_keywords(flat, ("bid", "1"))
    ask_volume = _first_numeric_by_keywords(flat, ("卖一", "量")) or _first_numeric_by_keywords(flat, ("ask", "1"))
    ratio = ask_volume / bid_volume if ask_volume is not None and bid_volume not in (None, 0) else None
    return {
        "buy1_volume": bid_volume,
        "sell1_volume": ask_volume,
        "sell1_buy1_ratio": ratio,
    }


def _extract_futu_order_book_metrics(data: Any) -> Dict[str, Any]:
    if isinstance(data, dict):
        bids = data.get("Bid") or data.get("bid") or []
        asks = data.get("Ask") or data.get("ask") or []
    else:
        bids = []
        asks = []
    bid1 = bids[0] if bids else None
    ask1 = asks[0] if asks else None
    bid_volume = _tuple_volume(bid1)
    ask_volume = _tuple_volume(ask1)
    ratio = ask_volume / bid_volume if ask_volume is not None and bid_volume not in (None, 0) else None
    return {
        "buy1_volume": bid_volume,
        "sell1_volume": ask_volume,
        "sell1_buy1_ratio": ratio,
    }


def _tuple_volume(value: Any) -> Optional[float]:
    if isinstance(value, (tuple, list)) and len(value) >= 2:
        return _to_float(value[1])
    if isinstance(value, dict):
        return _to_float(value.get("volume") or value.get("order_volume"))
    return None


def _frame_or_dict_latest(data: Any) -> Dict[str, Any]:
    if isinstance(data, pd.DataFrame):
        if data.empty:
            return {}
        return {str(k): v for k, v in data.iloc[-1].to_dict().items()}
    if isinstance(data, dict):
        return {str(k): v for k, v in data.items()}
    return {}


def _is_ret_ok(ft_module: Any, ret: Any) -> bool:
    ret_ok = getattr(ft_module, "RET_OK", 0) if ft_module is not None else 0
    return ret == ret_ok


def _futu_error_status(key: str, provider: str, message: Any) -> DataStatus:
    text = str(message or "")
    lowered = text.lower()
    if any(keyword in lowered for keyword in ("permission", "auth", "quota", "subscribe", "权限", "订阅", "额度")):
        return DataStatus(key, "permission_denied", provider, text[:120])
    return DataStatus(key, "provider_error", provider, text[:120])
