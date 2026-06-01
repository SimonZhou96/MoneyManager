#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from screening_config_store import load_last_config, save_last_config
from screening_prompt_flow import FlowCancelled, PromptBack, PromptFlow, PromptQuit, Step
from custom_list import CustomListCodeParser, CustomListScreeningRunner
from db import MarketDatabase, MySqlConfig
from fetch_stock_pools import get_db_config
from feishu_notifier import send_screening_result
from market import market_label, normalize_market
from scheduled_daily_job import (
    MarketScreeningResult,
    _load_dotenv,
    fetch_all_markets_pools,
    get_default_screening_params,
    run_market_screening_worker,
    sync_sector_memberships_for_markets,
)
from signal_analysis.service import env_flag
from stock_pool import DEFAULT_POOL_TYPES_TEXT, POOL_LABELS, parse_pool_types
from timeframe import parse_timeframe


TIMEFRAME_GROUPS = [
    ("分钟级", ["1m", "2m", "5m", "15m", "30m", "60m", "90m"]),
    ("小时级", ["1h"]),
    ("日线及以上", ["1d", "5d", "1wk", "1mo", "3mo"]),
]

MARKET_HELP = {
    "HK": "港股",
    "US": "美股",
    "A": "A 股",
}

POOL_HELP = POOL_LABELS

CODE_EXAMPLES = {
    "HK": "00700,09988",
    "US": "AAPL,MSFT",
    "A": "600519,000001",
}


# ═══════════════════════════════════════════════════════════════════
# 规则链表达式渲染（将 JSON DSL 展开为可读的规则路径）
# ═══════════════════════════════════════════════════════════════════

_DSL_LABELS: Dict[str, str] = {
    "and":            "全部满足 (AND)",
    "any":            "任一满足 (OR)",
    "all_enabled":    "全部启用 (AND)",
    "any_enabled":    "任一启用 (OR)",
    "ref":            "必须命中",
}

_RULE_NAME_MAP: Dict[str, str] = {
    "market_cap_range": "市值范围",
    "avg_daily_volume_range": "日均交易量",
    "price_range": "价格范围",
    "pe_range": "PE范围",
    "profitability": "公司盈利",
    "zuoyi_signal": "左一战法",
    "ema_breakout": "EMA突破",
    "rsi_oversold": "RSI超卖",
    "rsi_overbought": "RSI超买",
    "volume_spike_prior3": "放量超前三日",
    "daily_drop_6_65": "当日跌6%~6.5%",
    "daily_rise_4_45": "当日涨4%~4.5%",
    "company_event_hot_sector_link": "公司时事×热点板块",
    "company_event_hot_news_link": "公司时事×热点新闻",
    "market_intel_macro_score_link": "市场情报宏观评分",
    "macro_factor_analysis": "宏观因子采集",
    "enterprise_potential_analysis": "企业潜力分析(五模块)",
}


def _render_expression(expr, metadata_by_key: dict, indent: int = 0) -> List[str]:
    """将 JSON DSL 表达式渲染为缩进文本行"""
    prefix = "  " * indent
    lines = []

    if isinstance(expr, str):
        name = _RULE_NAME_MAP.get(expr, expr)
        lines.append(f"{prefix}├─ {name}")
        return lines

    if not isinstance(expr, dict) or not expr:
        return lines

    if "ref" in expr:
        key = str(expr["ref"])
        name = _RULE_NAME_MAP.get(key, key)
        lines.append(f"{prefix}├─ 必须命中: {name}")
        return lines

    for op in ("and", "any", "all_enabled", "any_enabled"):
        if op in expr:
            label = _DSL_LABELS.get(op, op)
            lines.append(f"{prefix}├─ {label}:")
            items = expr[op] if isinstance(expr[op], list) else []
            for i, item in enumerate(items):
                is_last = (i == len(items) - 1)
                sub_lines = _render_expression(item, metadata_by_key, indent + 1)
                for j, sl in enumerate(sub_lines):
                    marker = "  " if j > 0 else ("└─ " if is_last else "├─ ")
                    lines.append(f"{prefix}│ {marker}{sl[(indent+1)*2+2:]}")
            return lines

    return lines


def _query_chains_with_expressions(db: MarketDatabase) -> List[dict]:
    """查询所有市场的规则链及其展开后的表达式文本"""
    all_rows = []
    seen = set()
    for market in ("HK", "US", "A"):
        rows = db.list_screening_rule_chains(market=market, timeframe=None) or []
        for row in rows:
            key = (row.get("market", ""), row.get("chain_key", ""))
            if key not in seen:
                seen.add(key)
                all_rows.append(row)
    result = []
    for row in all_rows:
        expression = row.get("expression_json") or {}
        if isinstance(expression, str):
            import json
            try: expression = json.loads(expression)
            except Exception: expression = {}
        tree = _render_expression(expression, {})
        result.append({
            "market": row.get("market", ""),
            "chain_key": row.get("chain_key", ""),
            "chain_name": row.get("chain_name", ""),
            "enabled": bool(row.get("enabled")),
            "expression_tree": tree,
            "tree_text": "\n".join(tree),
        })
    result.sort(key=lambda r: (r["market"], r["chain_key"]))
    return result


def resolve_chain_choice(raw, chains, default=None):
    """解析规则链输入：空/0=默认链，编号=列表项，文本=chain_key。"""
    value = str(raw or "").strip()
    if value == "" or value == "0":
        return default
    keys = [str(c.get("chain_key") or "") for c in chains]
    if value.isdigit():
        idx = int(value)
        if 1 <= idx <= len(chains):
            return keys[idx - 1]
        raise ValueError(f"编号超出范围: {value}")
    if value in keys:
        return value
    raise ValueError(f"未知规则链: {value}")


# ═══════════════════════════════════════════════════════════════════
# InteractiveScreeningOptions
# ═══════════════════════════════════════════════════════════════════


@dataclass
class InteractiveScreeningOptions:
    mode: str
    markets: List[str] = field(default_factory=list)
    market: Optional[str] = None
    codes: List[str] = field(default_factory=list)
    timeframe: str = "1d"
    pools: List[str] = field(default_factory=lambda: parse_pool_types("all"))
    fetch_pools: bool = True
    require_fresh_pools: bool = False
    send_feishu: bool = False
    enable_ai_analysis: bool = True
    enable_main_force_external_data: bool = True
    csv_path: str = "logs/screening_result.csv"
    market_workers: int = 3
    chain_by_market: Dict[str, Optional[str]] = field(default_factory=dict)
    futu_host: str = "127.0.0.1"
    futu_port: int = 11111


# ═══════════════════════════════════════════════════════════════════
# ScreeningInteractiveApp
# ═══════════════════════════════════════════════════════════════════


class ScreeningInteractiveApp:
    """Interactive CLI for full-market and custom-code screening."""

    def __init__(
        self,
        input_func: Callable[[str], str] = input,
        print_func: Callable[..., None] = print,
    ):
        self.input = input_func
        self.print = print_func
        self._help_shown: set = set()

    def run(self, default_mode: Optional[str] = None) -> int:
        _load_dotenv()
        self.print("")
        self.print("MoneyManager 股票筛选")
        self.print("=" * 40)
        try:
            options = self.prompt_options(default_mode=default_mode)
        except FlowCancelled:
            self.print("")
            self.print("已取消")
            return 130
        except KeyboardInterrupt:
            self.print("")
            self.print("已取消")
            return 130
        if options.mode == "custom":
            return self.run_custom_code_screening(get_db_config(), options)
        return self.run_full_market_screening(get_db_config(), options)

    @staticmethod
    def _default_answers(last_config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        from screening_defaults import env_defaults

        def _markets_list(raw) -> List[str]:
            if isinstance(raw, list):
                return [str(m) for m in raw]
            return [m.strip() for m in str(raw or "").replace("，", ",").split(",") if m.strip()]

        answers: Dict[str, Any] = {
            "mode": "full",
            "timeframe": os.getenv("TIMEFRAME", "1d"),
            "enable_ai_analysis": env_flag("ENABLE_LLM_ANALYSIS", True),
            "enable_main_force_external_data": env_flag("MAIN_FORCE_ENABLE_EXTERNAL_DATA", True),
            "send_feishu": False,
            "csv_path": os.getenv("CSV_PATH", "logs/screening_result.csv"),
            "market": "HK",
            "codes": [],
            "markets": _markets_list(os.getenv("MARKETS", "HK,US,A")),
            "pools": parse_pool_types(os.getenv("POOLS", "all")),
            "fetch_pools": os.getenv("NO_FETCH", "").strip() != "1",
            "require_fresh_pools": os.getenv("REQUIRE_FRESH_POOLS", "").strip() == "1",
            "market_workers": int(os.getenv("MARKET_WORKERS", "3") or 3),
            "futu_host": os.getenv("FUTU_HOST", "127.0.0.1"),
            "futu_port": int(os.getenv("FUTU_PORT", "11111") or 11111),
            "chain_by_market": {},
            "_advanced": False,
        }
        env = env_defaults()
        if "timeframe" in env:
            answers["timeframe"] = env["timeframe"]
        if "markets" in env:
            answers["markets"] = _markets_list(env["markets"])
        if "pools" in env:
            answers["pools"] = parse_pool_types(env["pools"]) if isinstance(env["pools"], str) else env["pools"]
        if "csv_path" in env:
            answers["csv_path"] = env["csv_path"]
        if "market_workers" in env:
            answers["market_workers"] = env["market_workers"]
        if "futu_host" in env:
            answers["futu_host"] = env["futu_host"]
        if "futu_port" in env:
            answers["futu_port"] = env["futu_port"]
        if "no_fetch" in env:
            answers["fetch_pools"] = not env["no_fetch"]
        if "no_feishu" in env:
            answers["send_feishu"] = not env["no_feishu"]
        if "require_fresh_pools" in env:
            answers["require_fresh_pools"] = env["require_fresh_pools"]
        if "enable_llm_analysis" in env:
            answers["enable_ai_analysis"] = env["enable_llm_analysis"]
        if "main_force_enable_external_data" in env:
            answers["enable_main_force_external_data"] = env["main_force_enable_external_data"]
        if last_config:
            for key, value in last_config.items():
                answers[key] = value
        return answers

    def _startup_reuse_choice(self, last_config: Optional[Dict[str, Any]]) -> str:
        if not last_config:
            return "reset"
        markets = last_config.get("markets")
        if isinstance(markets, list):
            market_summary = ",".join(markets)
        else:
            market_summary = str(last_config.get("market") or markets or "")
        summary = " | ".join(filter(None, [market_summary, str(last_config.get("timeframe", ""))]))
        self.print("")
        self.print(f"检测到上次配置（{summary}）")
        self.print("  1. 沿用上次配置直接执行")
        self.print("  2. 以上次配置为默认值，逐步确认/修改  默认")
        self.print("  3. 全部用系统默认重新开始")
        while True:
            raw = self.input("请选择 [2]: ").strip() or "2"
            if raw == "1":
                return "reuse"
            if raw == "2":
                return "defaults"
            if raw == "3":
                return "reset"
            self.print("选项无效，请重新输入")

    def _build_steps(self, default_mode: Optional[str]) -> List[Step]:
        def ask_mode(_answers, default):
            if default_mode:
                return default_mode
            return self._prompt_choice(
                "请选择运行模式",
                choices={"1": "full", "2": "custom"},
                labels={"1": "全市场筛选", "2": "个股筛选器"},
                default="1",
            )

        is_full = lambda a: a.get("mode") == "full"
        is_custom = lambda a: a.get("mode") == "custom"
        adv_on = lambda a: bool(a.get("_advanced"))

        return [
            Step("mode", "运行模式", ask=ask_mode,
                 render_label=lambda v: "全市场筛选" if v == "full" else "个股筛选器"),
            Step("timeframe", "K线周期",
                 ask=lambda a, d: self._prompt_timeframe("K 线周期", d or "1d")),
            Step("enable_ai_analysis", "搜索+模型分析",
                 ask=lambda a, d: self._prompt_bool("是否启用搜索+模型辅助分析", bool(d)),
                 render_label=lambda v: "已启用" if v else "已关闭"),
            Step("enable_main_force_external_data", "主力资金外部数据",
                 ask=lambda a, d: self._prompt_bool(
                     "是否启用主力资金外部数据（资金流向/盘口等）", bool(d)),
                 render_label=lambda v: "已启用" if v else "已关闭"),
            Step("send_feishu", "飞书通知",
                 ask=lambda a, d: self._prompt_bool("是否发送飞书通知", bool(d)),
                 render_label=lambda v: "是" if v else "否"),
            Step("market", "自选代码市场", visible=is_custom,
                 ask=lambda a, d: self._prompt_market("自选代码所属市场", d or "HK")),
            Step("codes", "股票代码", visible=is_custom,
                 ask=lambda a, d: self._prompt_codes(a.get("market") or "HK"),
                 render_label=lambda v: ",".join(v or [])),
            Step("markets", "筛选市场", visible=is_full,
                 ask=lambda a, d: self._prompt_markets(
                     "筛选市场，逗号分隔",
                     ",".join(d) if isinstance(d, list) else (d or "HK,US,A")),
                 render_label=lambda v: ", ".join(v or [])),
            Step("pools", "股票池", visible=is_full,
                 ask=lambda a, d: self._prompt_pools(),
                 render_label=lambda v: ", ".join(v or [])),
            Step("fetch_pools", "刷新股票池", visible=is_full,
                 ask=lambda a, d: self._prompt_bool("是否先刷新股票池", bool(d)),
                 render_label=lambda v: "是" if v else "否"),
            Step("require_fresh_pools", "刷新失败退出",
                 visible=lambda a: is_full(a) and bool(a.get("fetch_pools")),
                 ask=lambda a, d: self._prompt_bool("股票池刷新失败时是否直接退出", bool(d)),
                 render_label=lambda v: "是" if v else "否"),
            Step("market_workers", "市场并发", visible=is_full,
                 ask=lambda a, d: self._prompt_int(
                     "市场并发数", int(d or 3),
                     minimum=1, maximum=max(1, len(a.get("markets") or [1])))),
            Step("_advanced", "配置高级项", visible=is_full,
                 ask=lambda a, d: self._prompt_bool("是否配置高级项（CSV/Futu）", bool(d)),
                 render_label=lambda v: "是" if v else "否"),
            Step("csv_path", "CSV 路径", advanced=True,
                 visible=lambda a: is_full(a) and adv_on(a),
                 ask=lambda a, d: self._prompt_text(
                     "CSV 输出路径", d or "logs/screening_result.csv",
                     help_text="输入相对/绝对路径；回车写默认 CSV。")),
            Step("futu_host", "Futu host", advanced=True,
                 visible=lambda a: is_full(a) and adv_on(a),
                 ask=lambda a, d: self._prompt_text("Futu OpenD host", d or "127.0.0.1")),
            Step("futu_port", "Futu port", advanced=True,
                 visible=lambda a: is_full(a) and adv_on(a),
                 ask=lambda a, d: self._prompt_int(
                     "Futu OpenD port", int(d or 11111), minimum=1, maximum=65535)),
            Step("chain_by_market", "各市场规则链",
                 ask=lambda a, d: self._prompt_chain_for_markets(
                     a.get("markets") if is_full(a) else [a.get("market") or "HK"]),
                 render_label=lambda v: " / ".join(
                     f"{k}→{val or '(默认)'}" for k, val in (v or {}).items())),
        ]

    def _options_from_answers(
        self, answers: Dict[str, Any], default_mode: Optional[str],
    ) -> InteractiveScreeningOptions:
        mode = default_mode or answers.get("mode", "full")
        common = dict(
            timeframe=answers.get("timeframe", "1d"),
            enable_ai_analysis=bool(answers.get("enable_ai_analysis", True)),
            enable_main_force_external_data=bool(
                answers.get("enable_main_force_external_data", True)),
            send_feishu=bool(answers.get("send_feishu", False)),
            csv_path=answers.get("csv_path", "logs/screening_result.csv"),
            chain_by_market=answers.get("chain_by_market", {}) or {},
        )
        if mode == "custom":
            return InteractiveScreeningOptions(
                mode="custom",
                market=answers.get("market", "HK"),
                codes=answers.get("codes", []),
                **common,
            )
        return InteractiveScreeningOptions(
            mode="full",
            markets=answers.get("markets", ["HK", "US", "A"]),
            pools=answers.get("pools", parse_pool_types("all")),
            fetch_pools=bool(answers.get("fetch_pools", True)),
            require_fresh_pools=bool(answers.get("require_fresh_pools", False)),
            market_workers=int(answers.get("market_workers", 3)),
            futu_host=answers.get("futu_host", "127.0.0.1"),
            futu_port=int(answers.get("futu_port", 11111)),
            **common,
        )

    def prompt_options(self, default_mode: Optional[str] = None) -> InteractiveScreeningOptions:
        last_config = load_last_config()
        mode_choice = self._startup_reuse_choice(last_config) if not default_mode else "reset"
        base = last_config if mode_choice in {"reuse", "defaults"} else None
        defaults = self._default_answers(base)
        if default_mode:
            defaults["mode"] = default_mode

        steps = self._build_steps(default_mode)
        flow = PromptFlow(steps, defaults, self.input, self.print)

        if mode_choice == "reuse" and last_config:
            answers = flow.confirm(dict(last_config))
        else:
            answers = flow.run({} if mode_choice == "reset" else {})

        save_last_config(answers)
        return self._options_from_answers(answers, default_mode)

    # ── 规则链选择（多市场，每市场独立输入） ──────────────────────

    def _prompt_chain_for_markets(self, markets: List[str]) -> Dict[str, Optional[str]]:
        try:
            db = MarketDatabase(get_db_config())
            all_chains = _query_chains_with_expressions(db)
            db.close()
        except Exception as e:
            self.print(f"⚠ 无法连接数据库读取规则链列表: {e}")
            all_chains = []

        result: Dict[str, Optional[str]] = {}
        for market in markets:
            label = MARKET_HELP.get(market, market)
            chains = [
                c for c in all_chains
                if (c.get("market") or "") in (market, "", "*", "通用")
            ]
            self.print("")
            self.print(f"━━━ {label} ({market}) 规则链选择 ━━━")
            self.print(f"  0. (默认链 {market}_default_zuoyi_and_other)")
            for i, c in enumerate(chains, 1):
                status = "✅" if c.get("enabled") else "⛔"
                self.print(f"  {i}. {status} {c.get('chain_key')}  {c.get('chain_name') or ''}")
                for line in c.get("expression_tree") or []:
                    self.print(f"       {line}")
            while True:
                raw = self._nav_input(
                    f"  {market} 规则链 [0]（输入编号或 chain_key；回车=默认链）: "
                )
                try:
                    result[market] = resolve_chain_choice(raw, chains, default=None)
                    break
                except ValueError as exc:
                    self.print(f"  {exc}，请重新选择")
        return result

    # ── 全市场筛选 ────────────────────────────────────────────────

    def run_full_market_screening(self, mysql_config: MySqlConfig, options: InteractiveScreeningOptions) -> int:
        self._apply_main_force_external_data_env(options)
        db = MarketDatabase(mysql_config)
        db.init_stock_pool_schema()
        using_stale_pools = not options.fetch_pools
        if options.fetch_pools:
            try:
                import futu as ft
                from stock_pool import StockPoolFetcher

                quote_ctx = ft.OpenQuoteContext(host=options.futu_host, port=options.futu_port)
                try:
                    fetcher = StockPoolFetcher(quote_ctx=quote_ctx, db=db)
                    fetch_all_markets_pools(db, fetcher, options.markets, options.pools)
                    sync_sector_memberships_for_markets(db, fetcher, options.markets)
                finally:
                    quote_ctx.close()
            except Exception as exc:
                self.print(f"捞取股票池失败: {type(exc).__name__}: {exc}")
                if options.require_fresh_pools:
                    db.close()
                    return 1
                self.print("继续使用数据库中已有股票池数据")
                using_stale_pools = True
        else:
            self.print("跳过股票池刷新，使用数据库中已有股票池数据")
        db.close()

        params = get_default_screening_params()
        csv_base = self._csv_base(options.csv_path)
        today_str = date.today().strftime("%Y-%m-%d")
        self.print("")
        self.print(f"[{datetime.now()}] 开始全市场筛选 | 市场: {options.markets} | 周期: {options.timeframe}")
        self.print(f"搜索+模型辅助分析: {'已启用' if options.enable_ai_analysis else '已关闭'}")
        self.print(f"主力资金外部数据: {'已启用' if options.enable_main_force_external_data else '已关闭'}")
        self.print("各市场规则链:")
        for mkt in options.markets:
            ck = options.chain_by_market.get(mkt) or "(默认)"
            self.print(f"  {mkt} → {ck}")

        processed: List[str] = []
        skipped: List[str] = []
        with ThreadPoolExecutor(max_workers=max(1, options.market_workers)) as executor:
            future_to_market = {
                executor.submit(
                    run_market_screening_worker,
                    mysql_config,
                    market,
                    options.timeframe,
                    params,
                    csv_base,
                    today_str,
                    False,
                    options.enable_ai_analysis,
                    options.chain_by_market.get(market),
                    options.pools,
                ): market
                for market in options.markets
            }
            for future in as_completed(future_to_market):
                market = future_to_market[future]
                try:
                    result = future.result()
                except Exception as exc:
                    result = MarketScreeningResult(market=market, skipped=True, error=f"{type(exc).__name__}: {exc}")
                if result.error:
                    skipped.append(result.market)
                    self.print(f"✗ {market_label(result.market)} 筛选失败: {result.error}")
                    continue
                if result.skipped or not result.task_id:
                    skipped.append(result.market)
                    continue
                processed.append(result.market)
                self._send_feishu_if_needed(options, today_str, result)

        self.print("")
        self.print(f"完成市场: {processed or []}")
        if skipped:
            self.print(f"跳过/失败市场: {skipped}")
        if using_stale_pools:
            self.print("提示: 本次使用了数据库已有股票池数据")
        return 0 if processed else 1

    # ── 个股筛选 ──────────────────────────────────────────────────

    def run_custom_code_screening(self, mysql_config: MySqlConfig, options: InteractiveScreeningOptions) -> int:
        self._apply_main_force_external_data_env(options)
        market = normalize_market(options.market or "HK")
        parsed = CustomListCodeParser().parse(market, options.codes)
        self.print("")
        self.print("输入解析结果:")
        for row in parsed.input_status_rows:
            self.print(f"  {row.get('input') or '-'} -> {row.get('code') or '-'} | {row.get('状态')}")
        if not parsed.valid_codes:
            self.print("没有可筛选的有效代码")
            return 1

        chain_key = options.chain_by_market.get(market)
        job_id = f"interactive-{uuid.uuid4()}"
        job = {
            "job_id": job_id,
            "markets": [market],
            "timeframe": options.timeframe,
            "chain_key": chain_key,
            "options": {
                "chain_key": chain_key,
                "chain_name": chain_key,
                "watchlist_by_market": {
                    market: [{"code": code, "name": code} for code in parsed.valid_codes],
                },
            },
        }
        csv_base = self._csv_base(options.csv_path)
        today_str = date.today().strftime("%Y-%m-%d")
        result = CustomListScreeningRunner(mysql_config).run(
            job=job,
            csv_base=csv_base,
            today_str=today_str,
            enable_ai_analysis=options.enable_ai_analysis,
        )
        self._print_custom_results(mysql_config, result.task_id)
        self._send_feishu_if_needed(options, today_str, result)
        return 0

    # ── Helpers ───────────────────────────────────────────────────

    @staticmethod
    def _apply_main_force_external_data_env(options: InteractiveScreeningOptions) -> None:
        os.environ["ENABLE_MAIN_FORCE_RISK_ANALYSIS"] = "1"
        os.environ["MAIN_FORCE_ENABLE_EXTERNAL_DATA"] = "1" if options.enable_main_force_external_data else "0"
        os.environ["MAIN_FORCE_ENABLE_FUTU_OPEND"] = "1" if options.enable_main_force_external_data else "0"
        if options.futu_host:
            os.environ["FUTU_HOST"] = options.futu_host
        if options.futu_port:
            os.environ["FUTU_PORT"] = str(options.futu_port)

    def _print_custom_results(self, mysql_config: MySqlConfig, task_id: Optional[str]) -> None:
        if not task_id:
            self.print("未生成筛选任务")
            return
        db = MarketDatabase(mysql_config)
        try:
            rows = db.get_screening_results_by_task(task_id, limit=1000, offset=0, passed_only=False)
        finally:
            db.close()
        self.print("")
        self.print(f"自选代码筛选结果 task_id={task_id}:")
        for row in rows:
            status = "已通过" if row.get("is_passed") else "未通过"
            self.print(f"  {row.get('code')} {row.get('name') or ''} | {status} | {row.get('filter_summary') or ''}")

    def _send_feishu_if_needed(self, options: InteractiveScreeningOptions, today_str: str, result) -> None:
        if not options.send_feishu or not getattr(result, "csv_paths", None):
            return
        webhook_url = os.getenv("FEISHU_WEBHOOK_URL", "").strip()
        if not webhook_url:
            self.print("未配置 FEISHU_WEBHOOK_URL，跳过飞书通知")
            return
        title = f"【交互式筛选】{today_str} - {market_label(result.market)}\n通过: {len(result.passed)} 只"
        ok = send_screening_result(webhook_url, title, result.csv_paths)
        if not ok:
            self.print(f"{market_label(result.market)} 飞书发送不完整")

    # ── Prompt helpers ────────────────────────────────────────────

    _NAV_BACK = {"b", "back", "返回"}
    _NAV_QUIT = {"q", "quit", "退出"}

    def _nav_input(self, prompt: str) -> str:
        raw = self.input(prompt)
        token = raw.strip().lower()
        if token in self._NAV_BACK:
            raise PromptBack()
        if token in self._NAV_QUIT:
            raise PromptQuit()
        return raw

    def _prompt_choice(self, title: str, choices: dict[str, str], labels: dict[str, str], default: str) -> str:
        self.print("")
        self.print(title)
        for key in choices:
            suffix = " 默认" if key == default else ""
            self.print(f"  {key}. {labels[key]}{suffix}")
        while True:
            value = self._nav_input(f"请输入选项 [{default}]: ").strip() or default
            if value in choices:
                return choices[value]
            self.print("选项无效，请重新输入")

    def _prompt_timeframe(self, title: str, default: str) -> str:
        self.print("")
        self.print(title)
        for group_name, values in TIMEFRAME_GROUPS:
            self.print(f"  {group_name}: {', '.join(values)}")
        self.print("输入方式: 直接输入上面的周期代码，例如 1d；直接回车使用默认值。")
        while True:
            value = self._prompt_text(title, default)
            try:
                return parse_timeframe(value)
            except ValueError:
                self.print(
                    "K 线周期无效，请输入: "
                    + ", ".join([item for _, values in TIMEFRAME_GROUPS for item in values])
                )

    def _prompt_text(self, title: str, default: str, help_text: Optional[str] = None) -> str:
        if help_text and title not in self._help_shown:
            self.print(help_text)
            self._help_shown.add(title)
        value = self.input(f"{title} [{default}]: ").strip()
        return value or default

    def _prompt_optional_text(self, title: str, help_text: Optional[str] = None) -> Optional[str]:
        if help_text:
            self.print(help_text)
        value = self.input(f"{title}: ").strip()
        return value or None

    def _prompt_bool(self, title: str, default: bool) -> bool:
        default_text = "Y/n" if default else "y/N"
        default_label = "是" if default else "否"
        while True:
            value = self._nav_input(
                f"{title} [{default_text}]（输入 y/yes/是 或 n/no/否，回车默认{default_label}）: "
            ).strip().lower()
            if not value:
                return default
            if value in {"y", "yes", "1", "true", "是"}:
                return True
            if value in {"n", "no", "0", "false", "否"}:
                return False
            self.print("请输入 y 或 n")

    def _prompt_int(self, title: str, default: int, minimum: int, maximum: int) -> int:
        while True:
            value = self._nav_input(
                f"{title} [{default}]（请输入 {minimum}-{maximum} 的整数，回车使用默认值）: "
            ).strip()
            if not value:
                return default
            try:
                number = int(value)
            except ValueError:
                self.print("请输入数字")
                continue
            if minimum <= number <= maximum:
                return number
            self.print(f"请输入 {minimum}-{maximum} 之间的数字")

    def _prompt_market(self, title: str, default: str) -> str:
        self.print("")
        self.print(title)
        self._print_market_help(single=True)
        while True:
            value = self._prompt_text(title, default)
            try:
                return normalize_market(value)
            except Exception as exc:
                self.print(f"市场无效: {exc}")

    def _prompt_markets(self, title: str, default: str) -> List[str]:
        self.print("")
        self.print(title)
        keys = list(MARKET_HELP.keys())
        for i, key in enumerate(keys, 1):
            self.print(f"  {i}. {key}: {MARKET_HELP[key]}")
        self.print("输入方式: 市场代码或编号，逗号分隔，例如 HK,US 或 1,2；回车使用默认值。")
        while True:
            raw = self._nav_input(f"{title} [{default}]: ").strip() or default
            tokens = self._split_csv_values(raw)
            try:
                markets = []
                for tok in tokens:
                    if tok.isdigit() and 1 <= int(tok) <= len(keys):
                        market = keys[int(tok) - 1]
                    else:
                        market = normalize_market(tok)
                    if market not in markets:
                        markets.append(market)
                if markets:
                    return markets
            except Exception as exc:
                self.print(f"市场列表无效: {exc}")

    def _prompt_pools(self) -> List[str]:
        self.print("")
        self.print("股票池类型")
        keys = list(POOL_HELP.keys())
        for i, key in enumerate(keys, 1):
            self.print(f"  {i}. {key}: {POOL_HELP[key]}")
        self.print(
            "输入方式: 类型代码或编号，逗号分隔，例如 best,all_etf 或 1,5；"
            f"回车使用全部（{DEFAULT_POOL_TYPES_TEXT}）。"
        )
        while True:
            raw = self._nav_input(f"股票池类型，逗号分隔 [{DEFAULT_POOL_TYPES_TEXT}]: ").strip()
            if not raw:
                return parse_pool_types("all")
            tokens = self._split_csv_values(raw)
            mapped = []
            for tok in tokens:
                if tok.isdigit() and 1 <= int(tok) <= len(keys):
                    mapped.append(keys[int(tok) - 1])
                else:
                    mapped.append(tok)
            try:
                return parse_pool_types(",".join(mapped))
            except ValueError as exc:
                self.print(str(exc))

    def _prompt_codes(self, market: str) -> List[str]:
        example = CODE_EXAMPLES.get(market, "00700")
        self.print("")
        self.print("股票代码")
        self.print(f"  当前市场: {market} - {MARKET_HELP.get(market, market)}")
        self.print(f"  输入示例: {example}")
        self.print("输入方式: 只输入股票代码，多个用英文逗号或中文逗号分隔。")
        while True:
            raw = self.input("股票代码，逗号分隔: ").strip()
            values = self._split_csv_values(raw)
            if values:
                return values
            self.print("至少输入一个股票代码")

    def _print_market_help(self, single: bool) -> None:
        for key, label in MARKET_HELP.items():
            self.print(f"  {key}: {label}")
        if single:
            self.print("输入方式: 输入一个市场代码，例如 HK；直接回车使用默认值。")
        else:
            self.print("输入方式: 多个市场用英文逗号或中文逗号分隔，例如 HK,US,A；直接回车使用默认值。")

    @staticmethod
    def _split_csv_values(value: str) -> List[str]:
        return [item.strip() for item in str(value or "").replace("，", ",").split(",") if item.strip()]

    @staticmethod
    def _csv_base(csv_path: str) -> str:
        return csv_path[:-4] if csv_path.endswith(".csv") else csv_path


def main() -> int:
    parser = argparse.ArgumentParser(description="MoneyManager 股票筛选交互式 shell")
    parser.add_argument("--mode", choices=["full", "custom"], default=None, help="直接进入全市场筛选或个股筛选器")
    args = parser.parse_args()
    try:
        return ScreeningInteractiveApp().run(default_mode=args.mode)
    except KeyboardInterrupt:
        print("\n已取消")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
