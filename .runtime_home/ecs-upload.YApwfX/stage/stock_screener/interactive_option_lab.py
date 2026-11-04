#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, List, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from option_lab.market_data import build_default_option_market_data_provider
from option_lab.models import RiskProfile
from option_lab.service import InMemoryOptionLabRepository, OptionLabService
from web.single_stock import normalize_stock_code


RISK_CHOICES = {"1": "保守", "2": "均衡", "3": "进取", "保守": "保守", "均衡": "均衡", "进取": "进取"}
MARKET_CHOICES = {"1": "US", "2": "HK", "3": "A", "US": "US", "HK": "HK", "A": "A"}
SHELL_COMMANDS = {
    "1": "eval",
    "eval": "eval",
    "single": "eval",
    "单标的": "eval",
    "2": "batch",
    "batch": "batch",
    "批量": "batch",
    "3": "plans",
    "plans": "plans",
    "订单": "plans",
    "4": "fill",
    "fill": "fill",
    "成交": "fill",
    "5": "positions",
    "positions": "positions",
    "pos": "positions",
    "持仓": "positions",
    "6": "refresh",
    "refresh": "refresh",
    "监控": "refresh",
    "help": "help",
    "?": "help",
    "0": "exit",
    "q": "exit",
    "quit": "exit",
    "exit": "exit",
    "退出": "exit",
}


@dataclass
class InteractiveOptionLabOptions:
    mode: str
    market: str
    codes: List[str]
    risk_profile: str
    capital: Optional[float]
    existing_holding: str
    max_loss: Optional[float]
    planned_holding_days: Optional[int]
    enable_macro_analysis: bool
    force_macro_refresh: bool
    macro_cache_ttl_minutes: int
    save_report: bool


class InteractiveOptionLabApp:
    def __init__(
        self,
        input_func: Callable[[str], str] = input,
        print_func: Callable[..., None] = print,
    ):
        self.input = input_func
        self.print = print_func
        self._pending_input: Optional[str] = None
        self.repository = InMemoryOptionLabRepository()
        self.service = OptionLabService(
            repository=self.repository,
            market_data_provider=build_default_option_market_data_provider(),
        )

    def run(self) -> int:
        self.run_evaluation()
        return 0

    def run_shell(self) -> int:
        self.print("")
        self.print("MoneyManager 期权实验室")
        self.print("=" * 40)
        self.print_help()
        while True:
            action = self._prompt_shell_command()
            if action == "exit":
                self.print("已退出期权实验室")
                return 0
            try:
                if action == "help":
                    self.print_help()
                elif action == "eval":
                    self.run_evaluation("single")
                elif action == "batch":
                    self.run_evaluation("batch")
                elif action == "plans":
                    self.print_order_plans()
                elif action == "fill":
                    self.prompt_and_record_fill()
                elif action == "positions":
                    self.print_positions()
                elif action == "refresh":
                    position_id = self._prompt_text("持仓编号", self._default_position_id())
                    events = self.service.refresh_position(position_id)
                    self.print_events(events)
            except Exception as exc:
                self.print(f"操作失败: {type(exc).__name__}: {exc}")
        return 0

    def run_evaluation(self, mode: Optional[str] = None) -> None:
        options = self.prompt_evaluation_options(default_mode=mode)
        self.print("")
        self.print("开始期权评估")
        self.print("=" * 40)
        for code in options.codes:
            result = self.service.evaluate_single(
                market=options.market,
                code=code,
                risk_profile=RiskProfile.from_input(options.risk_profile),
                max_loss=options.max_loss,
                capital=options.capital,
                planned_holding_days=options.planned_holding_days,
                enable_macro_analysis=options.enable_macro_analysis,
                force_macro_refresh=options.force_macro_refresh,
                macro_cache_ttl_minutes=options.macro_cache_ttl_minutes,
            )
            self._print_result(code, result)

    def prompt_evaluation_options(self, default_mode: Optional[str] = None) -> InteractiveOptionLabOptions:
        self.print("")
        self.print("MoneyManager 期权实验室")
        self.print("=" * 40)
        mode = default_mode or self._prompt_choice(
            "请选择运行模式",
            {"1": "single", "2": "batch"},
            {"1": "单标的评估", "2": "批量评估"},
            "1",
        )
        market = self._prompt_choice(
            "请选择市场",
            MARKET_CHOICES,
            {"1": "美股", "2": "港股", "3": "A股ETF/指数", "US": "美股", "HK": "港股", "A": "A股ETF/指数"},
            "US",
        )
        if mode == "single":
            raw_codes = [self._prompt_text("标的代码", "AAPL")]
        else:
            raw_codes = self._split_csv_values(self._prompt_text("标的代码列表，逗号分隔", "AAPL,MSFT"))
        codes = [normalize_stock_code(market, code) for code in raw_codes]
        risk_profile = self._prompt_choice(
            "风险偏好",
            RISK_CHOICES,
            {"1": "保守", "2": "均衡", "3": "进取", "保守": "保守", "均衡": "均衡", "进取": "进取"},
            "2",
        )
        capital = self._prompt_optional_float("资金规模")
        existing_holding = self._prompt_text("已有持仓（没有可留空）", "")
        max_loss = self._prompt_optional_float("最大可接受亏损")
        planned_days = self._prompt_optional_int("计划持有期（天）")
        enable_macro_analysis = self._prompt_bool("是否开启宏观层面分析", False)
        force_macro_refresh = False
        if enable_macro_analysis:
            force_macro_refresh = self._prompt_bool("是否强制重新分析宏观层面", False)
        save_report = self._prompt_bool("是否保存报告", False)
        return InteractiveOptionLabOptions(
            mode=mode,
            market=market,
            codes=codes,
            risk_profile=risk_profile,
            capital=capital,
            existing_holding=existing_holding,
            max_loss=max_loss,
            planned_holding_days=planned_days,
            enable_macro_analysis=enable_macro_analysis,
            force_macro_refresh=force_macro_refresh,
            macro_cache_ttl_minutes=60,
            save_report=save_report,
        )

    def _print_result(self, code: str, result) -> None:
        self.print("")
        self.print(f"{code} 评估完成 | 风险偏好: {result.risk_profile.label} | 候选策略: {len(result.candidates)}")
        if result.warnings:
            self.print("风险提示: " + "；".join(result.warnings))
        if getattr(result, "macro_analysis", None) is not None:
            macro = result.macro_analysis.to_dict()
            self.print(
                "宏观分析: "
                f"评分 {macro.get('宏观分析评分', '-')} | "
                f"方向 {macro.get('宏观方向', '-')} | "
                f"新闻影响 {macro.get('新闻影响', '-')}"
            )
            summary = macro.get("宏观摘要")
            if summary:
                self.print(f"宏观摘要: {summary}")
        visible = result.candidates[:8]
        for index, candidate in enumerate(visible, start=1):
            score_text = f"期权评分 {candidate.option_score if candidate.option_score is not None else candidate.score}"
            if candidate.macro_score is not None:
                score_text += f" | 宏观分析评分 {candidate.macro_score} | 综合评分 {candidate.composite_score}"
            self.print(f"{index}. {candidate.strategy_name} | {score_text} | {candidate.fit_reason}")
            self.print(f"   订单建议: {candidate.order_suggestion}")
            self.print(f"   风险指标: {candidate.risk_metrics}")
            if candidate.macro_score is not None:
                self.print(
                    "   宏观层面: "
                    f"{candidate.macro_direction}，新闻影响 {candidate.news_impact}，"
                    f"热点匹配 {candidate.hot_sector_mark}，主力资金风险 {candidate.main_force_risk_level}"
                )
            for detail in candidate.contract_details:
                self.print(f"   合约明细: {detail.to_display_row()}")
            if candidate.warnings:
                self.print(f"   风险提示: {'；'.join(candidate.warnings)}")
        if visible:
            selected_index = self._prompt_int("选择要保存订单建议的候选序号（0 表示不保存）", 1, minimum=0, maximum=len(visible))
        else:
            selected_index = 0
        if selected_index > 0:
            plan = self.service.save_order_plan(visible[selected_index - 1].candidate_id)
            self.print("")
            self.print(f"已生成首选策略的订单建议: {plan.plan_id}")
            self.print("系统不会自动下单。请在富途手动下单后，再通过前端或 API 回填成交信息进入监控。")

    def print_order_plans(self) -> None:
        rows = list(self.repository.order_plans.values())
        if not rows:
            self.print("暂无订单建议")
            return
        self.print("")
        self.print("订单建议列表")
        self.print("=" * 40)
        for plan in rows:
            candidate = self.repository.candidates_by_id.get(plan.candidate_id)
            strategy_name = getattr(candidate, "strategy_name", "未知策略")
            self.print(f"{plan.plan_id} | {strategy_name} | {plan.status} | {plan.order_suggestion}")

    def prompt_and_record_fill(self) -> int:
        plan_id = self._prompt_text("订单建议编号", "")
        filled_price = self._prompt_optional_float("成交价格") or 0.0
        quantity = self._prompt_int("成交数量", 1, minimum=1, maximum=100)
        filled_at = self._prompt_text("成交时间", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        fee = self._prompt_optional_float("手续费")
        position = self.service.record_fill(plan_id, filled_price, quantity, filled_at, fee)
        self.print(f"已加入监控: {position['position_id']}")
        return 0

    def print_positions(self) -> None:
        rows = list(self.repository.positions.values())
        if not rows:
            self.print("暂无监控持仓")
            return
        for row in rows:
            self.print(f"{row.get('position_id')} | {row.get('market')} {row.get('code')} | {row.get('策略名称')} | {row.get('status')}")

    def print_events(self, events: List[dict]) -> None:
        if not events:
            self.print("暂无监控提醒")
            return
        for event in events:
            self.print(f"{event.get('提醒级别')} | {event.get('事件类型')} | {event.get('提醒内容')}")

    def print_help(self) -> None:
        self.print("")
        self.print("option-lab> 可用命令")
        self.print("  eval / 1       单标的评估")
        self.print("  batch / 2      批量评估")
        self.print("  plans / 3      查看订单建议")
        self.print("  fill / 4       回填成交并加入监控")
        self.print("  positions / 5  查看持仓监控")
        self.print("  refresh / 6    手动刷新监控")
        self.print("  help           查看帮助")
        self.print("  exit / 0       退出")

    def _prompt_shell_command(self) -> str:
        while True:
            value = self._read("option-lab> ").strip().lower()
            if not value:
                return "eval"
            command = SHELL_COMMANDS.get(value)
            if command:
                return command
            self.print("未知命令，输入 help 查看可用命令。")

    def _default_position_id(self) -> str:
        rows = list(self.repository.positions.values())
        return rows[0]["position_id"] if rows else ""

    def _prompt_choice(self, title: str, choices: dict[str, str], labels: dict[str, str], default: str) -> str:
        self.print("")
        self.print(title)
        printed = set()
        for key, label in labels.items():
            if label in printed and key not in choices:
                continue
            suffix = " 默认" if key == default else ""
            self.print(f"  {key}. {label}{suffix}")
            printed.add(label)
        while True:
            value = self._read(f"请输入选项 [{default}]: ").strip() or default
            value = value.upper() if value.upper() in choices else value
            if value in choices:
                return choices[value]
            self.print("输入无效，请重新选择。")

    def _prompt_text(self, title: str, default: str) -> str:
        value = self._read(f"{title} [{default}]: ").strip()
        return value if value else default

    def _prompt_optional_float(self, title: str) -> Optional[float]:
        value = self._read(f"{title}（可留空）: ").strip()
        return float(value) if value else None

    def _prompt_optional_int(self, title: str) -> Optional[int]:
        value = self._read(f"{title}（可留空）: ").strip()
        if not value:
            return None
        if value.lower() in {"y", "yes", "n", "no", "是", "否"}:
            self._pending_input = value
            return None
        return int(value)

    def _prompt_int(self, title: str, default: int, minimum: int, maximum: int) -> int:
        while True:
            value = self._read(f"{title} [{default}]（请输入 {minimum}-{maximum} 的整数）: ").strip()
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

    def _prompt_bool(self, title: str, default: bool) -> bool:
        suffix = "Y/n" if default else "y/N"
        value = self._read(f"{title} [{suffix}]: ").strip().lower()
        if not value:
            return default
        return value in {"y", "yes", "1", "true", "是"}

    def _read(self, prompt: str) -> str:
        if self._pending_input is not None:
            value = self._pending_input
            self._pending_input = None
            return value
        return self.input(prompt)

    @staticmethod
    def _split_csv_values(value: str) -> List[str]:
        return [item.strip() for item in str(value or "").replace("，", ",").split(",") if item.strip()]


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="MoneyManager option lab interactive CLI")
    parser.add_argument("--shell", action="store_true", help="start persistent option-lab command shell")
    args = parser.parse_args(argv)
    app = InteractiveOptionLabApp()
    if args.shell:
        return app.run_shell()
    return app.run()


if __name__ == "__main__":
    raise SystemExit(main())
