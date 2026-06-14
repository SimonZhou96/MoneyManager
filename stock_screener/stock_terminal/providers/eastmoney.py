from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, List

from ..models import FundFlowPoint, KlinePoint, MinutePoint, QuoteSnapshot


def _parse_dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif value in (None, ""):
        parsed = datetime.now(timezone.utc)
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


class EastmoneyStockTerminalProvider:
    name = "eastmoney"

    def __init__(self, db=None, session=None, timeout_sec: float = 5.0):
        if session is None:
            import requests
            session = requests.Session()
        self.db = db
        self.session = session
        self.timeout_sec = timeout_sec
        self.last_source = self.name

    def fetch_quote(self, market: str, code: str) -> QuoteSnapshot:
        response = self.session.get(
            "https://push2.eastmoney.com/api/qt/stock/get",
            params={
                "secid": self._secid(market, code),
                "fields": "f43,f44,f45,f46,f47,f48,f57,f58,f60,f169,f170",
            },
            timeout=self.timeout_sec,
        )
        response.raise_for_status()
        return parse_eastmoney_quote(market, code, response.json())

    def fetch_klines(self, market: str, code: str, timeframe: str, limit: int) -> List[KlinePoint]:
        import sys
        try:
            from ...kline_fetcher import KlineFetcherFactory
        except ImportError:
            from kline_fetcher import KlineFetcherFactory

        # 跳过 DB 缓存——MySqlStockTerminalRepository.get_klines() 已经查过同一张表了
        fetchers = KlineFetcherFactory.create_fetcher_chain(db=self.db, skip_db_cache=True)
        failures: list[str] = []
        for fetcher in fetchers:
            source = fetcher.get_name()
            try:
                frame = fetcher.fetch(code, market=market, timeframe=timeframe, max_count=limit)
            except Exception as exc:
                msg = f"{source}: {exc}"
                print(f"[eastmoney] {msg}", file=sys.stderr)
                failures.append(msg)
                continue
            rows = self._rows_from_frame(frame, limit)
            if rows:
                self.last_source = source
                print(f"[eastmoney] OK {len(rows)} bars via {source} code={code} timeframe={timeframe}", file=sys.stderr)
                return rows
            else:
                msg = f"{source}: returned empty"
                print(f"[eastmoney] {msg} code={code} timeframe={timeframe}", file=sys.stderr)
                failures.append(msg)

        detail = "; ".join(failures) if failures else "no fetchers available"
        raise RuntimeError(f"K-line fetch failed: {detail}")

    def fetch_minute(self, market: str, code: str) -> List[MinutePoint]:
        response = self.session.get(
            "https://push2.eastmoney.com/api/qt/stock/trends2/get",
            params={
                "secid": self._secid(market, code),
                "fields1": "f1,f2,f3",
                "fields2": "f51,f53,f54,f55,f56",
            },
            timeout=self.timeout_sec,
        )
        response.raise_for_status()
        return parse_eastmoney_minute_rows(response.json())

    def fetch_fund_flow(self, market: str, code: str) -> List[FundFlowPoint]:
        response = self.session.get(
            "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get",
            params={
                "secid": self._secid(market, code),
                "lmt": "120",
                "fields1": "f1,f2,f3",
                "fields2": "f51,f52,f53,f54,f55,f56",
            },
            timeout=self.timeout_sec,
        )
        response.raise_for_status()
        return parse_eastmoney_fund_flow_rows(response.json())

    def _secid(self, market: str, code: str) -> str:
        normalized_market = str(market).upper()
        raw = _strip_market_prefix(code).upper()
        if normalized_market == "A":
            prefix = "1" if str(code).upper().startswith("SH.") or raw.startswith(("6", "9")) else "0"
            return f"{prefix}.{raw}"
        if normalized_market == "HK":
            return f"116.{raw.zfill(5)}"
        return f"105.{raw}"

    def _rows_from_frame(self, frame: Any, limit: int) -> List[KlinePoint]:
        if frame is None or getattr(frame, "empty", False):
            return []
        limited = frame.tail(max(1, int(limit))) if hasattr(frame, "tail") else frame
        rows: List[KlinePoint] = []
        for _, row in limited.iterrows():
            rows.append(
                KlinePoint(
                    at=_parse_dt(row.get("date") or row.get("bar_time")),
                    open=row.get("open"),
                    high=row.get("high"),
                    low=row.get("low"),
                    close=row.get("close"),
                    volume=row.get("volume"),
                    turnover=row.get("turnover"),
                )
            )
        return rows


def _strip_market_prefix(code: str) -> str:
    raw = str(code or "").strip()
    for prefix in ("SH.", "SZ.", "BJ.", "HK.", "US."):
        if raw.upper().startswith(prefix):
            return raw[len(prefix):]
    if raw.upper().endswith(".US"):
        return raw[:-3]
    if raw.upper().endswith(".HK"):
        return raw[:-3]
    return raw


def _eastmoney_price(value: Any) -> float | None:
    if value in (None, "-", ""):
        return None
    return float(value) / 100.0


def _eastmoney_percent(value: Any) -> float | None:
    if value in (None, "-", ""):
        return None
    return float(value) / 100.0


def _eastmoney_number(value: Any, default: float | None = None) -> float | None:
    if value in (None, "-", ""):
        return default
    return float(value)


def parse_eastmoney_quote(market: str, code: str, payload: dict) -> QuoteSnapshot:
    data = payload.get("data") or {}
    fetched_at = datetime.now(timezone.utc)
    return QuoteSnapshot(
        market=market,
        code=code,
        name=str(data.get("f58") or code),
        price=_eastmoney_price(data.get("f43")),
        change=_eastmoney_price(data.get("f169")),
        change_percent=_eastmoney_percent(data.get("f170")),
        open_price=_eastmoney_price(data.get("f46")),
        high=_eastmoney_price(data.get("f44")),
        low=_eastmoney_price(data.get("f45")),
        previous_close=_eastmoney_price(data.get("f60")),
        volume=_eastmoney_number(data.get("f47"), 0.0),
        turnover=_eastmoney_number(data.get("f48"), 0.0),
        fetched_at=fetched_at,
        source="eastmoney",
    )


def parse_eastmoney_minute_rows(payload: dict) -> List[MinutePoint]:
    rows: List[MinutePoint] = []
    for text in ((payload.get("data") or {}).get("trends") or []):
        parts = str(text).split(",")
        if len(parts) < 5:
            continue
        rows.append(
            MinutePoint(
                at=_parse_dt(parts[0]),
                price=float(parts[1]),
                average_price=float(parts[2]),
                volume=float(parts[3]),
                turnover=float(parts[4]),
            )
        )
    return rows


def parse_eastmoney_fund_flow_rows(payload: dict) -> List[FundFlowPoint]:
    rows: List[FundFlowPoint] = []
    for text in ((payload.get("data") or {}).get("klines") or []):
        parts = str(text).split(",")
        if len(parts) < 6:
            continue
        rows.append(
            FundFlowPoint(
                at=_parse_dt(parts[0]),
                inflow=float(parts[1]),
                outflow=float(parts[2]),
                net_inflow=float(parts[3]),
                main_net_inflow=float(parts[4]),
                retail_net_inflow=float(parts[5]),
            )
        )
    return rows
