from typing import Iterable, List, Optional

from market import normalize_market


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
