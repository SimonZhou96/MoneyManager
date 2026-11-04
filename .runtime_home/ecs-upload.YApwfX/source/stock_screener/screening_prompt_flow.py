#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""交互式筛选 — 步骤引擎与确认页"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional


class PromptBack(Exception):
    """用户输入返回 (b/back/返回)。"""


class PromptQuit(Exception):
    """用户输入退出 (q/quit/退出)。"""


class FlowCancelled(Exception):
    """流程被用户取消。"""


@dataclass
class Step:
    key: str
    title: str
    ask: Callable[[Dict[str, Any], Any], Any]
    render_label: Callable[[Any], str] = field(default=lambda v: "" if v is None else str(v))
    visible: Callable[[Dict[str, Any]], bool] = field(default=lambda _answers: True)
    advanced: bool = False


class PromptFlow:
    def __init__(
        self,
        steps: List[Step],
        defaults: Dict[str, Any],
        input_func: Callable[..., str],
        print_func: Callable[..., None],
    ):
        self.steps = list(steps)
        self.defaults = dict(defaults or {})
        self.input = input_func
        self.print = print_func

    def _prev_visible(self, index: int, answers: Dict[str, Any]) -> Optional[int]:
        j = index - 1
        while j >= 0:
            if self.steps[j].visible(answers):
                return j
            j -= 1
        return None

    def _default_for(self, step: Step, answers: Dict[str, Any]) -> Any:
        if step.key in answers:
            return answers[step.key]
        return self.defaults.get(step.key)

    def run(self, answers: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        answers = dict(answers or {})
        i = 0
        while i < len(self.steps):
            step = self.steps[i]
            if not step.visible(answers):
                i += 1
                continue
            try:
                value = step.ask(answers, self._default_for(step, answers))
            except PromptBack:
                prev = self._prev_visible(i, answers)
                if prev is not None:
                    i = prev
                continue
            except PromptQuit:
                raise FlowCancelled()
            answers[step.key] = value
            i += 1
        return self.confirm(answers)

    def confirm_steps(self, answers: Dict[str, Any]) -> List[Step]:
        return [s for s in self.steps if s.visible(answers) or s.advanced]

    def confirm(self, answers: Dict[str, Any]) -> Dict[str, Any]:
        while True:
            visible = self.confirm_steps(answers)
            self.print("")
            self.print("━━━ 配置确认 ━━━")
            for idx, step in enumerate(visible, 1):
                label = step.render_label(answers.get(step.key, self.defaults.get(step.key)))
                tag = "[高级] " if step.advanced else ""
                self.print(f"  {idx}. {tag}{step.title}  {label}")
            self.print("回车=开始执行 | 输入编号=修改该项 | b=返回上一步 | q=退出")
            raw = self.input("请确认: ").strip().lower()
            if raw in {"q", "quit", "退出"}:
                raise FlowCancelled()
            if raw in {"b", "back", "返回"}:
                return self.run(answers)
            if raw in {"", "y", "yes", "是"}:
                return answers
            if raw.isdigit():
                n = int(raw)
                if 1 <= n <= len(visible):
                    step = visible[n - 1]
                    try:
                        value = step.ask(answers, self._default_for(step, answers))
                    except PromptBack:
                        continue
                    except PromptQuit:
                        raise FlowCancelled()
                    answers[step.key] = value
                    continue
            self.print("输入无效，请重新选择")
