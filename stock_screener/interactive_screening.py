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
from typing import Callable, List, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

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
    chain_key: Optional[str] = None
    futu_host: str = "127.0.0.1"
    futu_port: int = 11111


class ScreeningInteractiveApp:
    """Interactive CLI for full-market and custom-code screening."""

    def __init__(
        self,
        input_func: Callable[[str], str] = input,
        print_func: Callable[..., None] = print,
    ):
        self.input = input_func
        self.print = print_func

    def run(self, default_mode: Optional[str] = None) -> int:
        _load_dotenv()
        self.print("")
        self.print("MoneyManager 股票筛选")
        self.print("=" * 40)
        options = self.prompt_options(default_mode=default_mode)
        if options.mode == "custom":
            return self.run_custom_code_screening(get_db_config(), options)
        return self.run_full_market_screening(get_db_config(), options)

    def prompt_options(self, default_mode: Optional[str] = None) -> InteractiveScreeningOptions:
        if default_mode:
            mode = default_mode
        else:
            mode = self._prompt_choice(
                "请选择运行模式",
                choices={"1": "full", "2": "custom"},
                labels={"1": "全市场筛选", "2": "个股筛选器"},
                default="1",
            )
        timeframe = self._prompt_timeframe("K 线周期", "1d")
        enable_ai_analysis = self._prompt_bool(
            "是否启用搜索+模型辅助分析",
            env_flag("ENABLE_LLM_ANALYSIS", True),
        )
        enable_main_force_external_data = self._prompt_bool(
            "是否启用主力资金外部数据（资金流向/盘口等）",
            env_flag("MAIN_FORCE_ENABLE_EXTERNAL_DATA", True),
        )
        send_feishu = self._prompt_bool("是否发送飞书通知", False)
        csv_path = self._prompt_text(
            "CSV 输出路径",
            "logs/screening_result.csv",
            help_text="输入方式: 输入相对路径或绝对路径；直接回车写入默认 CSV。",
        )
        chain_key = self._prompt_optional_text(
            "规则链 key（留空使用默认链）",
            help_text="输入方式: 输入已配置的规则链 key；不确定就直接回车使用默认链。",
        )

        if mode == "custom":
            market = self._prompt_market("自选代码所属市场", "HK")
            codes = self._prompt_codes(market)
            return InteractiveScreeningOptions(
                mode="custom",
                market=market,
                codes=codes,
                timeframe=timeframe,
                enable_ai_analysis=enable_ai_analysis,
                enable_main_force_external_data=enable_main_force_external_data,
                send_feishu=send_feishu,
                csv_path=csv_path,
                chain_key=chain_key,
            )

        markets = self._prompt_markets("筛选市场，逗号分隔", "HK,US,A")
        pools = self._prompt_pools()
        fetch_pools = self._prompt_bool("是否先刷新股票池", True)
        require_fresh_pools = False
        if fetch_pools:
            require_fresh_pools = self._prompt_bool("股票池刷新失败时是否直接退出", False)
        market_workers = self._prompt_int("市场并发数", 3, minimum=1, maximum=max(1, len(markets)))
        futu_host = self._prompt_text(
            "Futu OpenD host",
            os.getenv("FUTU_HOST", "127.0.0.1"),
            help_text="输入方式: 输入 Futu OpenD 地址；本机运行通常直接回车。",
        )
        futu_port = self._prompt_int("Futu OpenD port", int(os.getenv("FUTU_PORT", "11111")), minimum=1, maximum=65535)
        return InteractiveScreeningOptions(
            mode="full",
            markets=markets,
            timeframe=timeframe,
            pools=pools,
            fetch_pools=fetch_pools,
            require_fresh_pools=require_fresh_pools,
            send_feishu=send_feishu,
            enable_ai_analysis=enable_ai_analysis,
            enable_main_force_external_data=enable_main_force_external_data,
            csv_path=csv_path,
            market_workers=market_workers,
            chain_key=chain_key,
            futu_host=futu_host,
            futu_port=futu_port,
        )

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
        if options.chain_key:
            params["chain_key"] = options.chain_key
        csv_base = self._csv_base(options.csv_path)
        today_str = date.today().strftime("%Y-%m-%d")
        self.print("")
        self.print(f"[{datetime.now()}] 开始全市场筛选 | 市场: {options.markets} | 周期: {options.timeframe}")
        self.print(f"搜索+模型辅助分析: {'已启用' if options.enable_ai_analysis else '已关闭'}")
        self.print(f"主力资金外部数据: {'已启用' if options.enable_main_force_external_data else '已关闭'}")

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
                    options.chain_key,
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

        job_id = f"interactive-{uuid.uuid4()}"
        job = {
            "job_id": job_id,
            "markets": [market],
            "timeframe": options.timeframe,
            "chain_key": options.chain_key,
            "options": {
                "chain_key": options.chain_key,
                "chain_name": options.chain_key,
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

    def _prompt_choice(self, title: str, choices: dict[str, str], labels: dict[str, str], default: str) -> str:
        self.print("")
        self.print(title)
        for key in choices:
            suffix = " 默认" if key == default else ""
            self.print(f"  {key}. {labels[key]}{suffix}")
        while True:
            value = self.input(f"请输入选项 [{default}]: ").strip() or default
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
        if help_text:
            self.print(help_text)
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
            value = self.input(
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
            value = self.input(f"{title} [{default}]（请输入 {minimum}-{maximum} 的整数，回车使用默认值）: ").strip()
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
        self._print_market_help(single=False)
        while True:
            values = self._split_csv_values(self._prompt_text(title, default))
            try:
                markets = []
                for item in values:
                    market = normalize_market(item)
                    if market not in markets:
                        markets.append(market)
                if markets:
                    return markets
            except Exception as exc:
                self.print(f"市场列表无效: {exc}")

    def _prompt_pools(self) -> List[str]:
        self.print("")
        self.print("股票池类型")
        for key, label in POOL_HELP.items():
            self.print(f"  {key}: {label}")
        self.print(
            "输入方式: 多个类型用英文逗号或中文逗号分隔，"
            f"例如 best,major_index,all_etf；回车使用全部（{DEFAULT_POOL_TYPES_TEXT}）。"
        )
        while True:
            raw = self._prompt_text("股票池类型，逗号分隔", DEFAULT_POOL_TYPES_TEXT)
            try:
                values = parse_pool_types(raw)
            except ValueError as exc:
                self.print(str(exc))
                continue
            return values

    def _prompt_codes(self, market: str) -> List[str]:
        example = CODE_EXAMPLES.get(market, "00700")
        self.print("")
        self.print("股票代码")
        self.print(f"  当前市场: {market} - {MARKET_HELP.get(market, market)}")
        self.print(f"  输入示例: {example}")
        self.print("输入方式: 只输入股票代码，多个代码用英文逗号或中文逗号分隔。")
        while True:
            values = self._split_csv_values(self._prompt_text("股票代码，逗号分隔", example.split(",")[0]))
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
    return ScreeningInteractiveApp().run(default_mode=args.mode)


if __name__ == "__main__":
    raise SystemExit(main())
