MARKET_CONFIG = {
    "HK": {"label": "港股"},
    "US": {"label": "美股"},
    "A": {"label": "A股"},
}


def normalize_market(value: str) -> str:
    market = str(value).upper()
    return market if market in MARKET_CONFIG else "HK"


def market_label(market: str) -> str:
    return MARKET_CONFIG.get(normalize_market(market), {}).get("label", "港股")


def parse_markets(value: str):
    parts = [item.strip().upper() for item in str(value).split(",") if item.strip()]
    if not parts:
        return ["HK"]
    return [normalize_market(item) for item in parts]
