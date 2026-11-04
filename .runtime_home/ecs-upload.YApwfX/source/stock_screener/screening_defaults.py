import os
from typing import Any, Callable, Dict, Mapping

BUILTIN_DEFAULTS: Dict[str, Any] = {
    "timeframe": "1d",
    "markets": "HK,US,A",
    "pools": "best,major_index,industry_top5,recent_ipo_2y,all_etf",
    "csv_path": "logs/screening_result.csv",
    "market_workers": 3,
    "futu_host": "127.0.0.1",
    "futu_port": 11111,
    "no_fetch": False,
    "no_feishu": False,
    "require_fresh_pools": False,
    "enable_llm_analysis": True,
    "main_force_enable_external_data": True,
}

_ENV_NAME_BY_KEY = {
    "timeframe": "TIMEFRAME",
    "markets": "MARKETS",
    "pools": "POOLS",
    "csv_path": "CSV_PATH",
    "market_workers": "MARKET_WORKERS",
    "futu_host": "FUTU_HOST",
    "futu_port": "FUTU_PORT",
    "no_fetch": "NO_FETCH",
    "no_feishu": "NO_FEISHU",
    "require_fresh_pools": "REQUIRE_FRESH_POOLS",
    "enable_llm_analysis": "ENABLE_LLM_ANALYSIS",
    "main_force_enable_external_data": "MAIN_FORCE_ENABLE_EXTERNAL_DATA",
}


def _parse_bool(value: Any) -> bool:
    text = str(value or "").strip().lower()
    return text in {"1", "true", "yes", "y", "on"}


def _parse_int(value: Any) -> int:
    return int(str(value).strip())


_PARSERS: Dict[str, Callable[[Any], Any]] = {
    "timeframe": lambda value: str(value).strip(),
    "markets": lambda value: str(value).strip(),
    "pools": lambda value: str(value).strip(),
    "csv_path": lambda value: str(value).strip(),
    "market_workers": _parse_int,
    "futu_host": lambda value: str(value).strip(),
    "futu_port": _parse_int,
    "no_fetch": _parse_bool,
    "no_feishu": _parse_bool,
    "require_fresh_pools": _parse_bool,
    "enable_llm_analysis": _parse_bool,
    "main_force_enable_external_data": _parse_bool,
}


def _normalize_value(key: str, value: Any) -> Any:
    parser = _PARSERS.get(key)
    if parser is None:
        raise KeyError(key)
    return parser(value)


def env_defaults() -> Dict[str, Any]:
    resolved: Dict[str, Any] = {}
    for key, env_name in _ENV_NAME_BY_KEY.items():
        raw = os.getenv(env_name)
        if raw is None:
            continue
        try:
            resolved[key] = _normalize_value(key, raw)
        except (TypeError, ValueError):
            continue
    return resolved


def resolve_defaults(last_config: Mapping[str, Any] | None) -> Dict[str, Any]:
    env = env_defaults()
    resolved: Dict[str, Any] = {}
    for key, builtin_value in BUILTIN_DEFAULTS.items():
        value = builtin_value
        if key in env:
            value = env[key]
        if isinstance(last_config, Mapping) and key in last_config:
            try:
                value = _normalize_value(key, last_config[key])
            except (TypeError, ValueError):
                pass
        resolved[key] = value
    return resolved
