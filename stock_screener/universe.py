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
    if code_col is None:
        return []

    stocks = []
    for _, row in data.iterrows():
        raw_code = row[code_col]
        if market == "US":
            code = _normalize_us_code(raw_code)
        else:
            code = _normalize_hk_code(raw_code)
        if not code:
            continue
        name = str(row[name_col]).strip() if name_col else code
        stocks.append({"code": code, "name": name})
    return stocks


def fetch_stock_list_futu(quote_ctx, market: str) -> List[dict]:
    market = normalize_market(market)
    try:
        import futu as ft
    except Exception:
        return []

    market_enum = ft.Market.US if market == "US" else ft.Market.HK
    ret, data = quote_ctx.get_stock_basicinfo(market=market_enum, stock_type=ft.SecurityType.STOCK)
    if ret != ft.RET_OK:
        return []
    return data[["code", "name"]].to_dict("records")
