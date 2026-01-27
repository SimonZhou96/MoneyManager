import re
from dataclasses import dataclass
from typing import Dict, Iterable, List, Tuple

import pandas as pd
from futu import AuType, KLType, Market, OpenQuoteContext, PlateClass, RET_OK

FUTU_HOST = "127.0.0.1"
FUTU_PORT = 11111

DEFAULT_TICKERS = [
    "HK.00700",
    "HK.00939",
    "HK.00941",
    "HK.00992",
    "HK.01024",
    "HK.01038",
    "HK.01093",
    "HK.01113",
    "HK.01177",
    "HK.01211",
    "HK.01299",
    "HK.01810",
    "HK.01818",
    "HK.01918",
    "HK.01928",
    "HK.02007",
    "HK.02015",
    "HK.02269",
    "HK.02318",
    "HK.02319",
    "HK.02331",
    "HK.02382",
    "HK.02388",
    "HK.02628",
    "HK.03690",
    "HK.03968",
    "HK.03988",
    "HK.06098",
    "HK.06690",
    "HK.09618",
    "HK.09888",
    "HK.09898",
    "HK.09999",
]


@dataclass(frozen=True)
class StockProfile:
    symbol: str
    name: str
    sector: str


def normalize_hk_ticker(raw: str) -> str:
    raw = raw.strip().upper()
    if not raw:
        return ""
    if raw.startswith("HK."):
        code = raw[3:]
        if code.isdigit():
            return f"HK.{code.zfill(5)}"
        return raw
    if raw.endswith(".HK"):
        raw = raw[:-3]
    elif raw.endswith("HK") and raw[:-2].isdigit():
        raw = raw[:-2]
    elif raw.endswith(".HKG"):
        raw = raw[:-4]
    if raw.isdigit():
        return f"HK.{raw.zfill(5)}"
    return raw


def to_display_symbol(code: str) -> str:
    if code.startswith("HK.") and code[3:].isdigit():
        return f"{code[3:]}.HK"
    return code


def parse_tickers(text: str) -> List[str]:
    parts = re.split(r"[,\s]+", text.strip())
    tickers = []
    seen = set()
    for part in parts:
        if not part:
            continue
        normalized = normalize_hk_ticker(part)
        if not normalized:
            continue
        if normalized not in seen:
            tickers.append(normalized)
            seen.add(normalized)
    return tickers


def open_quote_context() -> OpenQuoteContext:
    return OpenQuoteContext(host=FUTU_HOST, port=FUTU_PORT)


def period_to_dates(period: str) -> Tuple[str, str]:
    end = pd.Timestamp.today().normalize()
    years = 5
    if period.endswith("y") and period[:-1].isdigit():
        years = int(period[:-1])
    start = end - pd.DateOffset(years=years)
    return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")


def _format_kline_frame(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame
    frame = frame.drop_duplicates(subset=["time_key"]).copy()
    frame["time_key"] = pd.to_datetime(frame["time_key"])
    frame = frame.sort_values("time_key")
    frame = frame.rename(
        columns={
            "open": "Open",
            "high": "High",
            "low": "Low",
            "close": "Close",
            "volume": "Volume",
        }
    )
    columns = [col for col in ["Open", "High", "Low", "Close", "Volume"] if col in frame.columns]
    frame = frame.set_index("time_key")[columns]
    frame = frame.dropna(how="any")
    return frame


def _fetch_history_with_ctx(
    quote_ctx: OpenQuoteContext, ticker: str, start: str, end: str
) -> pd.DataFrame:
    frames: List[pd.DataFrame] = []
    page_req_key = None
    while True:
        try:
            ret, data, page_req_key = quote_ctx.request_history_kline(
                ticker,
                start=start,
                end=end,
                ktype=KLType.K_DAY,
                autype=AuType.NONE,
                page_req_key=page_req_key,
            )
        except Exception:
            break
        if ret != RET_OK:
            break
        if data is not None and not data.empty:
            frames.append(data)
        if page_req_key is None:
            break
    if not frames:
        return pd.DataFrame()
    combined = pd.concat(frames, ignore_index=True)
    return _format_kline_frame(combined)


def download_price_history(ticker: str, period: str = "5y") -> pd.DataFrame:
    start, end = period_to_dates(period)
    try:
        quote_ctx = open_quote_context()
    except Exception:
        return pd.DataFrame()
    try:
        return _fetch_history_with_ctx(quote_ctx, ticker, start, end)
    finally:
        quote_ctx.close()


def download_bulk_history(tickers: Iterable[str], period: str = "5y") -> Dict[str, pd.DataFrame]:
    tickers_list = list(tickers)
    results: Dict[str, pd.DataFrame] = {}
    try:
        quote_ctx = open_quote_context()
    except Exception:
        return results
    start, end = period_to_dates(period)
    try:
        for ticker in tickers_list:
            hist = _fetch_history_with_ctx(quote_ctx, ticker, start, end)
            if not hist.empty:
                results[ticker] = hist
    finally:
        quote_ctx.close()
    return results


def _fetch_plate_map(
    quote_ctx: OpenQuoteContext, tickers: Iterable[str]
) -> Dict[str, str]:
    try:
        ret, plate_data = quote_ctx.get_plate_list(Market.HK, PlateClass.INDUSTRY)
    except Exception:
        return {}
    if ret != RET_OK or plate_data is None or plate_data.empty:
        return {}

    remaining = set(tickers)
    mapping: Dict[str, str] = {}
    for _, row in plate_data.iterrows():
        if not remaining:
            break
        plate_code = row.get("plate_code")
        plate_name = row.get("plate_name")
        if not plate_code or not plate_name:
            continue
        try:
            ret, members = quote_ctx.get_plate_stock(plate_code)
        except Exception:
            continue
        if ret != RET_OK or members is None or members.empty:
            continue
        hits = members[members["code"].isin(remaining)]
        for code in hits["code"].tolist():
            mapping[code] = plate_name
            remaining.discard(code)
    return mapping


def fetch_profiles(tickers: Iterable[str]) -> Dict[str, StockProfile]:
    tickers_list = list(tickers)
    if not tickers_list:
        return {}
    try:
        quote_ctx = open_quote_context()
    except Exception:
        return {}
    try:
        name_map: Dict[str, str] = {}
        try:
            ret, name_data = quote_ctx.get_stock_name(tickers_list)
        except Exception:
            ret, name_data = None, None
        if ret == RET_OK and name_data is not None and not name_data.empty:
            name_map = dict(zip(name_data["code"], name_data["name"]))
        plate_map = _fetch_plate_map(quote_ctx, tickers_list)
    finally:
        quote_ctx.close()

    profiles: Dict[str, StockProfile] = {}
    for ticker in tickers_list:
        profiles[ticker] = StockProfile(
            symbol=ticker,
            name=name_map.get(ticker, ticker),
            sector=plate_map.get(ticker, "未知"),
        )
    return profiles
