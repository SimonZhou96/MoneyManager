import time
from typing import Iterable, List, Optional, Tuple

from market import normalize_market


def _to_yf_symbol(code: str, market: str) -> Optional[str]:
    """
    将内部股票代码转为 yfinance 所需格式。
    - 港股: HK.00700 / 00700 -> 0700.HK
    - 美股: US.AAPL / AAPL -> AAPL
    """
    market = market.upper()
    code = str(code).strip()
    if not code:
        return None
    if market == "HK":
        if code.startswith("HK."):
            code = code[3:]
        if code.isdigit():
            return f"{code.zfill(5)}.HK"
        return None
    if market == "US":
        if code.upper().startswith("US."):
            code = code[3:]
        if "." in code:
            suffix = code.split(".")[-1]
            if suffix.isalpha():
                code = suffix
        return code.upper() if code else None
    return None


def _find_column(columns: Iterable[str], keywords: Iterable[str]) -> Optional[str]:
    for col in columns:
        col_lower = col.lower()
        for keyword in keywords:
            if keyword in col_lower or keyword in col:
                return col
    return None


def _normalize_hk_code(raw_code: str) -> Optional[str]:
    code = str(raw_code).strip()
    if not code:
        return None
    if code.startswith("HK."):
        code = code[3:]
    if code.isdigit():
        return f"HK.{code.zfill(5)}"
    return None


def _normalize_us_code(raw_code: str) -> Optional[str]:
    code = str(raw_code).strip()
    if not code:
        return None
    if code.upper().startswith("US."):
        code = code[3:]
    if "." in code:
        suffix = code.split(".")[-1]
        if suffix.isalpha():
            code = suffix
    return code


def _normalize_a_code(raw_code: str) -> Optional[str]:
    """
    标准化 A 股代码为 6 位 + 交易所后缀
    上交所 6xxxxx -> XXXXXX.SS，深交所 0xxxxx/3xxxxx -> XXXXXX.SZ
    """
    code = str(raw_code).strip()
    if not code:
        return None
    # 去除已有后缀
    if "." in code:
        code = code.split(".")[0]
    if not code.isdigit() or len(code) != 6:
        return None
    # 上交所: 6 开头
    if code.startswith("6"):
        return f"{code}.SS"
    # 深交所: 0 或 3 开头
    if code.startswith("0") or code.startswith("3"):
        return f"{code}.SZ"
    return None


def fetch_stock_list_akshare(market: str) -> List[dict]:
    market = normalize_market(market)
    try:
        import akshare as ak
    except Exception:
        return []

    data = None
    if market == "US":
        if hasattr(ak, "stock_us_spot"):
            try:
                data = ak.stock_us_spot()
            except Exception:
                data = None
        if (data is None or len(data) == 0) and hasattr(ak, "stock_us_spot_em"):
            try:
                data = ak.stock_us_spot_em()
            except Exception:
                data = None
    elif market == "A":
        if hasattr(ak, "stock_zh_a_spot_em"):
            try:
                data = ak.stock_zh_a_spot_em()
            except Exception:
                data = None
    else:
        if hasattr(ak, "stock_hk_spot"):
            try:
                data = ak.stock_hk_spot()
            except Exception:
                data = None
        if (data is None or len(data) == 0) and hasattr(ak, "stock_hk_spot_em"):
            try:
                data = ak.stock_hk_spot_em()
            except Exception:
                data = None

    if data is None or len(data) == 0:
        return []

    code_col = _find_column(data.columns, ["代码", "code", "symbol", "ticker"])
    name_col = _find_column(data.columns, ["名称", "name", "中文名称", "英文名称"])
    market_cap_col = _find_column(data.columns, ["总市值", "流通市值", "market_cap", "市值"])
    pe_col = _find_column(data.columns, ["市盈率", "市盈率-动态", "pe", "pe_ratio"])
    if code_col is None:
        return []

    def _safe_float(val):
        if val is None or (isinstance(val, float) and (val != val or val == float("inf"))):
            return None
        try:
            s = str(val).strip().replace(",", "").replace("--", "")
            if not s:
                return None
            return float(s)
        except (ValueError, TypeError):
            return None

    stocks = []
    for _, row in data.iterrows():
        raw_code = row[code_col]
        if market == "US":
            code = _normalize_us_code(raw_code)
        elif market == "A":
            code = _normalize_a_code(raw_code)
        else:
            code = _normalize_hk_code(raw_code)
        if not code:
            continue
        name = str(row[name_col]).strip() if name_col else code
        item = {"code": code, "name": name}
        if market_cap_col and market_cap_col in row:
            item["market_cap"] = _safe_float(row[market_cap_col])
        if pe_col and pe_col in row:
            item["pe_ratio"] = _safe_float(row[pe_col])
        stocks.append(item)
    return stocks


def fetch_stock_list_futu(quote_ctx, market: str) -> List[dict]:
    market = normalize_market(market)
    try:
        import futu as ft
    except Exception:
        return []

    market_map = {"US": ft.Market.US, "HK": ft.Market.HK, "A": ft.Market.CN}
    market_enum = market_map.get(market, ft.Market.HK)
    ret, data = quote_ctx.get_stock_basicinfo(market=market_enum, stock_type=ft.SecurityType.STOCK)
    if ret != ft.RET_OK:
        return []
    records = data[["code", "name"]].to_dict("records")
    # Futu A 股返回 SH.600000 / SZ.000001，统一为 600000.SS / 000001.SZ
    if market == "A":
        for r in records:
            raw = str(r.get("code", ""))
            if raw.startswith("SH."):
                r["code"] = raw[3:] + ".SS"
            elif raw.startswith("SZ."):
                r["code"] = raw[3:] + ".SZ"
    return records


def _enrich_with_yfinance(
    stocks: List[dict],
    market: str,
    sleep_seconds: float = 0.2,
    enrich_max_count: Optional[int] = None,
) -> None:
    """
    用 yfinance 补全港股/美股市值、PE。
    仅对 market_cap 或 pe_ratio 为 None 的股票补全，已有值不覆盖。
    """
    market = normalize_market(market)
    if market not in ("HK", "US"):
        return
    try:
        import yfinance as yf
    except ImportError:
        return

    need_enrich = [
        i
        for i, item in enumerate(stocks)
        if item.get("market_cap") is None or item.get("pe_ratio") is None
    ]
    if enrich_max_count is not None and len(need_enrich) > enrich_max_count:
        need_enrich = need_enrich[:enrich_max_count]

    for i in need_enrich:
        item = stocks[i]
        if item.get("market_cap") is not None and item.get("pe_ratio") is not None:
            continue
        yf_symbol = _to_yf_symbol(item.get("code", ""), market)
        if not yf_symbol:
            continue
        try:
            info = yf.Ticker(yf_symbol).info
            if info and isinstance(info, dict):
                if item.get("market_cap") is None:
                    mc = info.get("marketCap")
                    if mc is not None and isinstance(mc, (int, float)):
                        val = float(mc)
                        if val == val and abs(val) != float("inf"):
                            item["market_cap"] = val
                if item.get("pe_ratio") is None:
                    pe = info.get("trailingPE") or info.get("forwardPE")
                    if pe is not None and isinstance(pe, (int, float)):
                        val = float(pe)
                        if val == val and abs(val) != float("inf"):
                            item["pe_ratio"] = val
        except Exception:
            pass
        time.sleep(sleep_seconds)


def fetch_stock_list(
    market: str,
    quote_ctx=None,
    enrich_fundamentals: bool = True,
    enrich_sleep_seconds: float = 0.2,
    enrich_max_count: Optional[int] = None,
) -> Tuple[List[dict], str]:
    """
    获取股票列表（统一入口）。
    - 先 AKShare，失败则 Futu 兜底（需 quote_ctx）
    - 对 HK/US 且 enrich_fundamentals=True 时，用 yfinance 补全市值、PE

    Returns:
        (stocks, source): source 为 "AKShare" 或 "Futu"，表示列表来源
    """
    market = normalize_market(market)
    stocks = fetch_stock_list_akshare(market)
    source = "AKShare"
    if not stocks and quote_ctx:
        stocks = fetch_stock_list_futu(quote_ctx, market)
        source = "Futu"
    if not stocks:
        return [], ""
    if enrich_fundamentals and market in ("HK", "US"):
        _enrich_with_yfinance(
            stocks,
            market,
            sleep_seconds=enrich_sleep_seconds,
            enrich_max_count=enrich_max_count,
        )
    return stocks, source
