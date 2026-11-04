"""Country-level macro time series, independent of stocks and screening jobs."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from functools import lru_cache
import math
import os
import threading
import time

import pandas as pd

SUCCESS_TTL = 6 * 3600
FAILURE_TTL = 60
INFLATION_SERIES = {"cpi": ("CPIAUCNS", "CPIAUCSL"), "ppi": ("PPIFID", "PPIFIS")}
NBS_PPI_PATH = "价格指数 > 工业生产者出厂价格分类指数 > 工业生产者出厂价格指数(上月=100)"


class MacroCache:
    """Per-key single flight; return copies so callers cannot modify cached data."""
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.entries = {}
        self.locks = {}
        self.guard = threading.Lock()

    def get(self, key, loader):
        with self.guard:
            lock = self.locks.setdefault(key, threading.Lock())
        with lock:
            entry = self.entries.get(key)
            if entry and entry[0] > self.clock():
                if entry[2]:
                    raise RuntimeError(entry[2])
                return deepcopy(entry[1])
            try:
                value = loader()
            except Exception as exc:
                self.entries[key] = (self.clock() + FAILURE_TTL, None, str(exc))
                raise
            ttl = FAILURE_TTL if isinstance(value, dict) and value.get("status") in {"partial", "error"} else SUCCESS_TTL
            self.entries[key] = (self.clock() + ttl, deepcopy(value), None)
            return deepcopy(value)


_cache = MacroCache()


@lru_cache(maxsize=4)
def _fred_client(api_key):
    from fredapi import Fred
    return Fred(api_key=api_key, timeout=20)


def fred_series(series_id, api_key=None):
    key = api_key or os.getenv("FRED_API_KEY", "")
    if not key:
        raise RuntimeError("FRED_API_KEY 未设置")
    # A single observation window also allows the existing Provider to reuse CPI/DGS reads.
    start = (pd.Timestamp.now(tz="UTC").tz_localize(None) - pd.DateOffset(months=38)).date().isoformat()
    def load():
        series = _fred_client(key).get_series(series_id, observation_start=start)
        if series is None or series.dropna().empty:
            raise ValueError(f"{series_id}: 无有效数据")
        return series
    return _cache.get(("fred", key, series_id, start), load)


def _number(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (ValueError, TypeError):
        return None


def _monthly(series):
    """Normalize exact source contracts, including NBS YYYY年MM月份 labels."""
    result = {}
    for date, value in series.items():
        date = str(date).replace("月份", "").replace("月", "").replace("年", "-")
        period = pd.Period(date, freq="M")
        # Do not let a future release placeholder determine the latest month.
        if period < pd.Timestamp.now().to_period("M"):
            result[period] = _number(value)
    return result


def _delta(a, b):
    return round(a - b, 4) if a is not None and b is not None else None


def inflation_history(yoy, mom):
    yoy, mom = _monthly(yoy), _monthly(mom)
    periods = sorted(p for p in yoy.keys() | mom.keys() if yoy.get(p) is not None or mom.get(p) is not None)
    if not periods:
        return []
    cutoff = periods[-1] - 23
    return [{"period": str(p), "yoy": yoy.get(p), "mom": mom.get(p),
             "previous_yoy": yoy.get(p - 1), "previous_mom": mom.get(p - 1),
             "yoy_change_pp": _delta(yoy.get(p), yoy.get(p - 1)),
             "mom_change_pp": _delta(mom.get(p), mom.get(p - 1))}
            for p in periods if p >= cutoff]


def index_growth(series, lag):
    values = _monthly(series)
    return pd.Series({str(p): round((v / values[p - lag] - 1) * 100, 4)
                      for p, v in values.items()
                      if v is not None and values.get(p - lag) not in (None, 0)})


def bond_history(series, cutoff):
    values = {}
    for date, value in series.items():
        value = _number(value)
        if value is not None:
            values[pd.Timestamp(date).date().isoformat()] = value
    result = []
    previous_date, previous = None, None
    for date, value in sorted(values.items()):
        if date >= cutoff:
            result.append({"date": date, "value": value, "previous_date": previous_date,
                           "previous_value": previous, "change_bp": None if previous is None else round((value - previous) * 100, 4)})
        previous_date, previous = date, value
    return result


def _daily(series):
    values = {pd.Timestamp(date).date().isoformat(): _number(value) for date, value in series.items()}
    return pd.Series(values, dtype=float).sort_index()


def _payload(country, category):
    return {"country": country, "category": category, "fetched_at": datetime.now(timezone.utc).isoformat(),
            "published_at": None, "status": "ok", "indicators": {}, "data_gaps": []}


def _read(payload, label, loader):
    try:
        return loader()
    except Exception as exc:
        payload["data_gaps"].append(f"{label}: {exc}")
        return pd.Series(dtype=float)


def _finish(payload):
    indicators = list(payload["indicators"].values())
    if all(item["status"] == "ok" for item in indicators) and not payload["data_gaps"]:
        payload["status"] = "ok"
    else:
        payload["status"] = "partial" if any(item["history"] for item in indicators) else "error"
    return payload


def _cn_ppi_mom(ak):
    frame = ak.macro_china_nbs_nation(kind="月度数据", path=NBS_PPI_PATH, period="LAST38")
    # NBS returns indicators as rows, month labels as columns.
    frame.index = frame.index.str.replace(r"\s+", "", regex=True)
    return (pd.to_numeric(frame.loc["工业生产者出厂价格指数(上月=100)"], errors="coerce") - 100).round(4)


def _load_inflation(country, api_key=None):
    payload = _payload(country, "inflation")
    if country == "CN":
        import akshare as ak
        cpi = _read(payload, "中国CPI", ak.macro_china_cpi)
        ppi = _read(payload, "中国PPI同比", ak.macro_china_ppi)
        def column(frame, name):
            return frame.set_index("月份")[name]
        rates = {
            "cpi": (_read(payload, "CPI同比", lambda: column(cpi, "全国-同比增长")),
                    _read(payload, "CPI环比", lambda: column(cpi, "全国-环比增长"))),
            "ppi": (_read(payload, "PPI同比", lambda: column(ppi, "当月同比增长")),
                    _read(payload, "PPI环比", lambda: _cn_ppi_mom(ak))),
        }
    else:
        rates = {}
        for name, (nsa, sa) in INFLATION_SERIES.items():
            rates[name] = (_read(payload, nsa, lambda s=nsa: index_growth(fred_series(s, api_key), 12)),
                           _read(payload, sa, lambda s=sa: index_growth(fred_series(s, api_key), 1)))
    for name, (yoy, mom) in rates.items():
        try:
            history = inflation_history(yoy, mom)
        except (ValueError, TypeError) as exc:
            payload["data_gaps"].append(f"{name}: {exc}")
            history = []
        latest = history[-1] if history else None
        status = "ok" if latest and latest["yoy"] is not None and latest["mom"] is not None else "missing"
        if status != "ok":
            payload["data_gaps"].append(f"{name}: 同比或环比缺失")
        payload["indicators"][name] = {
            "source": "AKShare/Eastmoney" if country == "CN" else "FRED/BLS",
            "mom_source": "AKShare/NBS" if country == "CN" and name == "ppi" else "AKShare/Eastmoney" if country == "CN" else "FRED/BLS",
            "series": list(INFLATION_SERIES[name]) if country == "US" else [],
            "unit": "%", "change_unit": "percentage_point",
            "adjustment": {"yoy": "NSA", "mom": "SA" if country == "US" else "NSA"},
            "status": status, "latest": latest, "history": history,
        }
    return _finish(payload)


def _load_bonds(country, api_key=None):
    payload = _payload(country, "bond_yields")
    cutoff = (pd.Timestamp.now() - pd.DateOffset(months=24)).date().isoformat()
    if country == "CN":
        import akshare as ak
        start = (pd.Timestamp(cutoff) - pd.Timedelta(days=31)).strftime("%Y%m%d")
        frame = _read(payload, "中国国债", lambda: ak.bond_zh_us_rate(start_date=start))
    series = {}
    for tenor, fred_id in (("2y", "DGS2"), ("10y", "DGS10")):
        series[tenor] = _read(payload, tenor, lambda t=tenor, s=fred_id: _daily(
            fred_series(s, api_key) if country == "US" else frame.set_index("日期")[f"中国国债收益率{t[:-1]}年"]))
        history = bond_history(series[tenor], cutoff)
        payload["indicators"][tenor] = {"source": "FRED/H.15" if country == "US" else "AKShare/Eastmoney",
            "series": fred_id if country == "US" else f"中国国债收益率{tenor[:-1]}年",
            "unit": "%", "change_unit": "bp", "status": "ok" if history else "missing",
            "latest": history[-1] if history else None, "history": history}
        if not history:
            payload["data_gaps"].append(f"{tenor}: 收益率缺失")
    # Align by observation date before computing the spread; never carry values forward.
    aligned = pd.concat([series["2y"].rename("2y"), series["10y"].rename("10y")], axis=1)
    spread = pd.to_numeric(aligned["10y"], errors="coerce") - pd.to_numeric(aligned["2y"], errors="coerce")
    history = bond_history(spread, cutoff)
    for row in history:
        for key in ("value", "previous_value"):
            row[key] = None if row[key] is None else round(row[key] * 100, 4)
    payload["indicators"]["10y_2y_spread"] = {"source": "derived:10y-2y", "unit": "bp", "change_unit": "bp",
        "status": "ok" if history else "missing", "latest": history[-1] if history else None, "history": history}
    if not history:
        payload["data_gaps"].append("10y_2y_spread: 无同日有效观测")
    return _finish(payload)


def get_macro_timeseries(category, country, api_key=None):
    if country not in {"CN", "US"} or category not in {"inflation", "bond_yields"}:
        raise ValueError("country 仅支持 CN/US，category 仅支持 inflation/bond_yields")
    key = (api_key or os.getenv("FRED_API_KEY", "")) if country == "US" else ""
    loader = _load_inflation if category == "inflation" else _load_bonds
    return _cache.get((category, country, key), lambda: loader(country, api_key))


def enrich_macro_snapshot(snapshot, country, api_key=None):
    """Only latest values enter the existing evidence model, never full histories."""
    from .models import MacroFactorValue
    for category in ("inflation", "bond_yields"):
        payload = get_macro_timeseries(category, country, api_key)
        snapshot.data_gaps.extend(payload["data_gaps"])
        snapshot.provider_status[f"{country}_{category}"] = payload["status"]
        for name, item in payload["indicators"].items():
            row = item["latest"] or {}
            if category == "inflation":
                fields = {f"{name}_yoy": row.get("yoy"), f"{name}_mom": row.get("mom")}
            else:
                field = {"2y": "two_year_yield", "10y": "ten_year_yield", "10y_2y_spread": "yield_spread"}[name]
                value = row.get("value")
                # Existing snapshot uses percentage points for yield_spread, API uses bp.
                fields = {field: value / 100 if field == "yield_spread" and value is not None else value}
            snapshot.macro_details[name] = {k: v for k, v in item.items() if k != "history"}
            for field, value in fields.items():
                setattr(snapshot, field, value)
                factor_key = "cn_10y_yield" if country == "CN" and field == "ten_year_yield" else field
                snapshot.factors.append(MacroFactorValue(factor_key=factor_key, factor_name=field, value=value,
                    unit="%", source=item["source"], status="ok" if value is not None else "missing"))
            if name == "cpi":
                change = row.get("yoy_change_pp")
                snapshot.cpi_trend = "" if change is None else "rising" if change > 0 else "falling" if change < 0 else "stable"
