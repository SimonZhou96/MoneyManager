# 交互式筛选 CLI 交互优化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 `interactive_screening.py` 的线性提问重构为数据驱动的步骤引擎，支持返回上一步、执行前确认页与编号编辑、优雅退出、规则链按市场过滤与编号选择校验，并记忆上次配置。

**Architecture:** 新增三个聚焦模块——`screening_config_store.py`（上次配置读写）、`screening_defaults.py`（env→默认值与"上次>env>内置"优先级）、`screening_prompt_flow.py`（`Step` + `PromptFlow` 驱动器 + 导航异常）。`ScreeningInteractiveApp` 的 `_prompt_*` 助手改用可抛导航异常的 `_nav_input`，`prompt_options` 改为构建步骤列表交由 `PromptFlow` 驱动。下游 `run_*`、`run_screening.sh` 非交互路径与 `InteractiveScreeningOptions` 字段保持不变。

**Tech Stack:** Python 3，标准库（`dataclasses`/`json`/`os`），测试用 `unittest`（仓库无 pytest），注入 `input_func`/`print_func` 做纯单测。

**测试运行约定:** 所有命令在 `MoneyManager/stock_screener/` 目录执行：`python3 -m unittest tests.<module> -v`。

**Spec:** `docs/superpowers/specs/2026-06-01-interactive-screening-cli-ux-design.md`

---

## File Structure

- Create: `stock_screener/screening_config_store.py` — 上次配置 JSON 的加载/保存，含 version 校验与异常降级。
- Create: `stock_screener/screening_defaults.py` — 内置默认值、env 覆盖、`resolve_defaults` 优先级合并。
- Create: `stock_screener/screening_prompt_flow.py` — `Step` 数据类、导航异常 `PromptBack/PromptQuit/FlowCancelled`、`PromptFlow` 驱动器（前进/返回/退出/确认页/编号编辑）。
- Modify: `stock_screener/interactive_screening.py` — 新增 `_nav_input`，改造 `_prompt_*` 走 `_nav_input`，重写 `_prompt_chain_for_markets`、`prompt_options`、`run`、`main`；market/pool 编号多选；custom 代码必填；help 仅首呈现一次。
- Create: `stock_screener/tests/test_screening_config_store.py`
- Create: `stock_screener/tests/test_screening_defaults.py`
- Create: `stock_screener/tests/test_screening_prompt_flow.py`
- Create: `stock_screener/tests/test_screening_chain_select.py`
- Modify: `stock_screener/tests/test_interactive_screening.py` — 修正脱节用例并补集成用例。

---

## Task 1: 上次配置读写 `screening_config_store.py`

**Files:**
- Create: `stock_screener/screening_config_store.py`
- Test: `stock_screener/tests/test_screening_config_store.py`

- [ ] **Step 1: Write the failing test**

```python
# stock_screener/tests/test_screening_config_store.py
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from screening_config_store import (
    CONFIG_VERSION,
    load_last_config,
    save_last_config,
)


class ConfigStoreTests(unittest.TestCase):
    def _tmp(self):
        d = tempfile.mkdtemp()
        return os.path.join(d, "last.json")

    def test_round_trip(self):
        path = self._tmp()
        answers = {"mode": "full", "markets": ["HK", "US"], "timeframe": "1d"}
        self.assertTrue(save_last_config(answers, path=path))
        loaded = load_last_config(path=path)
        self.assertEqual(loaded, answers)

    def test_missing_file_returns_none(self):
        self.assertIsNone(load_last_config(path=self._tmp()))

    def test_incompatible_version_ignored(self):
        path = self._tmp()
        with open(path, "w", encoding="utf-8") as fh:
            fh.write('{"version": 999, "answers": {"mode": "full"}}')
        self.assertIsNone(load_last_config(path=path))

    def test_corrupt_file_ignored(self):
        path = self._tmp()
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("not json {{{")
        self.assertIsNone(load_last_config(path=path))

    def test_save_to_unwritable_path_returns_false(self):
        self.assertFalse(save_last_config({"a": 1}, path="/proc/should_not_write/last.json"))

    def test_version_constant(self):
        self.assertEqual(CONFIG_VERSION, 1)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_screening_config_store -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'screening_config_store'`

- [ ] **Step 3: Write minimal implementation**

```python
# stock_screener/screening_config_store.py
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

CONFIG_VERSION = 1
_FILENAME = "interactive_screening_last.json"


def config_path() -> str:
    base = os.environ.get("RUNTIME_HOME") or os.path.expanduser("~")
    return os.path.join(base, _FILENAME)


def load_last_config(path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    target = path or config_path()
    try:
        with open(target, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (FileNotFoundError, ValueError, OSError):
        return None
    if not isinstance(data, dict) or data.get("version") != CONFIG_VERSION:
        return None
    answers = data.get("answers")
    if not isinstance(answers, dict):
        return None
    return answers


def save_last_config(answers: Dict[str, Any], path: Optional[str] = None) -> bool:
    target = path or config_path()
    payload = {
        "version": CONFIG_VERSION,
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "answers": answers,
    }
    try:
        parent = os.path.dirname(target)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(target, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        return True
    except OSError:
        return False
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_screening_config_store -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add stock_screener/screening_config_store.py stock_screener/tests/test_screening_config_store.py
git commit -m "feat(screening): add interactive config store with version guard"
```

---

## Task 2: 默认值与优先级 `screening_defaults.py`

实现内置默认值、env 覆盖、`resolve_defaults(last_config)` 合并，优先级 `上次 > env > 内置`。

**Files:**
- Create: `stock_screener/screening_defaults.py`
- Test: `stock_screener/tests/test_screening_defaults.py`

- [ ] **Step 1: Write the failing test**

```python
# stock_screener/tests/test_screening_defaults.py
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from screening_defaults import BUILTIN_DEFAULTS, env_defaults, resolve_defaults


class DefaultsTests(unittest.TestCase):
    def test_builtin_keys_present(self):
        for key in ("mode", "timeframe", "markets", "pools", "fetch_pools",
                    "market_workers", "futu_host", "futu_port", "csv_path",
                    "send_feishu", "enable_ai_analysis",
                    "enable_main_force_external_data", "require_fresh_pools",
                    "market", "codes", "chain_by_market"):
            self.assertIn(key, BUILTIN_DEFAULTS)

    def test_env_overrides_parsed(self):
        with patch.dict(os.environ, {
            "TIMEFRAME": "5m",
            "MARKETS": "HK，US",
            "MARKET_WORKERS": "2",
            "NO_FETCH": "1",
            "REQUIRE_FRESH_POOLS": "1",
        }, clear=True):
            env = env_defaults()
        self.assertEqual(env["timeframe"], "5m")
        self.assertEqual(env["markets"], ["HK", "US"])
        self.assertEqual(env["market_workers"], 2)
        self.assertFalse(env["fetch_pools"])
        self.assertTrue(env["require_fresh_pools"])

    def test_resolution_precedence_last_over_env_over_builtin(self):
        with patch.dict(os.environ, {"TIMEFRAME": "5m"}, clear=True):
            merged = resolve_defaults({"timeframe": "1wk", "markets": ["A"]})
        # last config wins over env
        self.assertEqual(merged["timeframe"], "1wk")
        # last config provided markets
        self.assertEqual(merged["markets"], ["A"])
        # builtin still present for untouched key
        self.assertEqual(merged["futu_port"], BUILTIN_DEFAULTS["futu_port"])

    def test_env_over_builtin_when_no_last(self):
        with patch.dict(os.environ, {"TIMEFRAME": "5m"}, clear=True):
            merged = resolve_defaults(None)
        self.assertEqual(merged["timeframe"], "5m")

    def test_last_config_ignores_unknown_keys(self):
        with patch.dict(os.environ, {}, clear=True):
            merged = resolve_defaults({"bogus": 123})
        self.assertNotIn("bogus", merged)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_screening_defaults -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'screening_defaults'`

- [ ] **Step 3: Write minimal implementation**

```python
# stock_screener/screening_defaults.py
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from stock_pool import parse_pool_types
from signal_analysis.service import env_flag, env_int

BUILTIN_DEFAULTS: Dict[str, Any] = {
    "mode": "full",
    "timeframe": "1d",
    "enable_ai_analysis": True,
    "enable_main_force_external_data": True,
    "send_feishu": False,
    "csv_path": "logs/screening_result.csv",
    "markets": ["HK", "US", "A"],
    "pools": parse_pool_types("all"),
    "fetch_pools": True,
    "require_fresh_pools": False,
    "market_workers": 3,
    "futu_host": "127.0.0.1",
    "futu_port": 11111,
    "market": "HK",
    "codes": [],
    "chain_by_market": {},
}


def _split_csv(raw: str) -> List[str]:
    return [item.strip() for item in str(raw or "").replace("，", ",").split(",") if item.strip()]


def env_defaults() -> Dict[str, Any]:
    """仅返回设置了对应 env 的键。"""
    out: Dict[str, Any] = {}
    if os.getenv("TIMEFRAME"):
        out["timeframe"] = os.getenv("TIMEFRAME")
    if os.getenv("MARKETS"):
        out["markets"] = _split_csv(os.getenv("MARKETS"))
    if os.getenv("POOLS"):
        out["pools"] = parse_pool_types(os.getenv("POOLS"))
    if os.getenv("CSV_PATH"):
        out["csv_path"] = os.getenv("CSV_PATH")
    if os.getenv("MARKET_WORKERS"):
        out["market_workers"] = env_int("MARKET_WORKERS", BUILTIN_DEFAULTS["market_workers"])
    if os.getenv("FUTU_HOST"):
        out["futu_host"] = os.getenv("FUTU_HOST")
    if os.getenv("FUTU_PORT"):
        out["futu_port"] = env_int("FUTU_PORT", BUILTIN_DEFAULTS["futu_port"])
    if os.getenv("NO_FETCH"):
        out["fetch_pools"] = os.getenv("NO_FETCH").strip() != "1"
    if os.getenv("NO_FEISHU"):
        out["send_feishu"] = os.getenv("NO_FEISHU").strip() != "1"
    if os.getenv("REQUIRE_FRESH_POOLS"):
        out["require_fresh_pools"] = os.getenv("REQUIRE_FRESH_POOLS").strip() == "1"
    if os.getenv("ENABLE_LLM_ANALYSIS"):
        out["enable_ai_analysis"] = env_flag("ENABLE_LLM_ANALYSIS", True)
    if os.getenv("MAIN_FORCE_ENABLE_EXTERNAL_DATA"):
        out["enable_main_force_external_data"] = env_flag("MAIN_FORCE_ENABLE_EXTERNAL_DATA", True)
    return out


def resolve_defaults(last_config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """优先级: 上次配置 > 环境变量 > 内置默认。"""
    merged: Dict[str, Any] = dict(BUILTIN_DEFAULTS)
    merged.update(env_defaults())
    if last_config:
        merged.update({k: v for k, v in last_config.items() if k in BUILTIN_DEFAULTS})
    return merged
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_screening_defaults -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add stock_screener/screening_defaults.py stock_screener/tests/test_screening_defaults.py
git commit -m "feat(screening): centralize interactive defaults with env precedence"
```

---

## Task 3: 步骤引擎 `screening_prompt_flow.py`

`Step` 数据类、导航异常、`PromptFlow` 驱动器（前进/`b` 返回/`q` 退出/确认页/编号编辑）。引擎不依赖 DB/网络，注入 `input_func`/`print_func`。

**Files:**
- Create: `stock_screener/screening_prompt_flow.py`
- Test: `stock_screener/tests/test_screening_prompt_flow.py`

- [ ] **Step 1: Write the failing test**

```python
# stock_screener/tests/test_screening_prompt_flow.py
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from screening_prompt_flow import (
    FlowCancelled,
    PromptBack,
    PromptFlow,
    PromptQuit,
    Step,
)


def _make_flow(steps, answers_inputs, defaults=None):
    inputs = iter(answers_inputs)
    out = []
    flow = PromptFlow(
        steps=steps,
        defaults=defaults or {},
        input_func=lambda prompt="": next(inputs),
        print_func=lambda *a, **k: out.append(" ".join(str(x) for x in a)),
    )
    return flow, out


class PromptFlowTests(unittest.TestCase):
    def _steps(self):
        # ask reads one raw token from input via flow.input; raises nav per token
        def make_ask(key):
            def ask(answers, default):
                raw = ask.flow.input(f"{key}: ").strip().lower()
                if raw in {"b"}:
                    raise PromptBack()
                if raw in {"q"}:
                    raise PromptQuit()
                return raw or default
            return ask

        a = make_ask("a")
        b = make_ask("b")
        return [
            Step(key="a", title="A", ask=a),
            Step(key="b", title="B", ask=b),
        ], a, b

    def _bind(self, steps, *asks, flow):
        for fn in asks:
            fn.flow = flow

    def test_forward_then_confirm(self):
        steps, a, b = self._steps()
        flow, out = _make_flow(steps, ["x", "y", ""])  # a=x, b=y, confirm enter
        self._bind(steps, a, b, flow=flow)
        answers = flow.run()
        self.assertEqual(answers["a"], "x")
        self.assertEqual(answers["b"], "y")
        self.assertIn("配置确认", "\n".join(out))

    def test_back_navigation(self):
        steps, a, b = self._steps()
        # a=x, b=back -> re-ask a, a=z, b=y, confirm
        flow, out = _make_flow(steps, ["x", "b", "z", "y", ""])
        self._bind(steps, a, b, flow=flow)
        answers = flow.run()
        self.assertEqual(answers["a"], "z")
        self.assertEqual(answers["b"], "y")

    def test_quit_cancels(self):
        steps, a, b = self._steps()
        flow, out = _make_flow(steps, ["x", "q"])
        self._bind(steps, a, b, flow=flow)
        with self.assertRaises(FlowCancelled):
            flow.run()

    def test_confirm_edit_by_number(self):
        steps, a, b = self._steps()
        # a=x, b=y, confirm "1" edit a -> "z", confirm enter
        flow, out = _make_flow(steps, ["x", "y", "1", "z", ""])
        self._bind(steps, a, b, flow=flow)
        answers = flow.run()
        self.assertEqual(answers["a"], "z")
        self.assertEqual(answers["b"], "y")

    def test_invisible_step_skipped(self):
        steps, a, b = self._steps()
        steps[1].visible = lambda ans: False  # B invisible
        flow, out = _make_flow(steps, ["x", ""])  # only a asked, confirm enter
        self._bind(steps, a, b, flow=flow)
        answers = flow.run()
        self.assertEqual(answers["a"], "x")
        self.assertNotIn("b", answers)

    def test_confirm_quit(self):
        steps, a, b = self._steps()
        flow, out = _make_flow(steps, ["x", "y", "q"])
        self._bind(steps, a, b, flow=flow)
        with self.assertRaises(FlowCancelled):
            flow.run()


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_screening_prompt_flow -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'screening_prompt_flow'`

- [ ] **Step 3: Write minimal implementation**

```python
# stock_screener/screening_prompt_flow.py
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional


class PromptBack(Exception):
    """用户在某步骤输入返回 (b)。"""


class PromptQuit(Exception):
    """用户在某步骤输入退出 (q)。"""


class FlowCancelled(Exception):
    """流程被用户取消。"""


@dataclass
class Step:
    key: str
    title: str
    ask: Callable[[Dict[str, Any], Any], Any]
    render_label: Callable[[Any], str] = field(default=lambda v: "" if v is None else str(v))
    visible: Callable[[Dict[str, Any]], bool] = field(default=lambda answers: True)
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
        return self._confirm(answers)

    def _confirm_steps(self, answers: Dict[str, Any]) -> List[Step]:
        return [s for s in self.steps if s.visible(answers) or s.advanced]

    def _confirm(self, answers: Dict[str, Any]) -> Dict[str, Any]:
        while True:
            visible = self._confirm_steps(answers)
            self.print("")
            self.print("━━━ 配置确认 ━━━")
            for idx, step in enumerate(visible, 1):
                label = step.render_label(answers.get(step.key, self.defaults.get(step.key)))
                tag = "[高级] " if step.advanced else ""
                self.print(f"  {idx}. {tag}{step.title}  {label}")
            self.print("回车=开始执行 | 输入编号=修改该项 | q=退出")
            raw = self.input("请确认: ").strip().lower()
            if raw in {"q", "quit", "退出"}:
                raise FlowCancelled()
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_screening_prompt_flow -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add stock_screener/screening_prompt_flow.py stock_screener/tests/test_screening_prompt_flow.py
git commit -m "feat(screening): add step-driven prompt flow engine"
```

---

## Task 4: 导航输入 `_nav_input` 与 `_prompt_*` 适配

让现有提问助手在用户输入 `b`/`q` 时抛 `PromptBack`/`PromptQuit`，供 `PromptFlow` 捕获。仅对"选择/数值/布尔"等受控提示启用；free-text（代码、CSV 路径）保持原样以免误伤。

**Files:**
- Modify: `stock_screener/interactive_screening.py` — 新增 `_nav_input`；将 `_prompt_choice`/`_prompt_timeframe`/`_prompt_bool`/`_prompt_int`/`_prompt_market`/`_prompt_markets`/`_prompt_pools` 内部的 `self.input(...)` 换为 `self._nav_input(...)`。`_prompt_text`/`_prompt_optional_text`/`_prompt_codes` 维持 `self.input`（free-text 不拦截）。
- Test: `stock_screener/tests/test_interactive_screening.py`（新增用例）

- [ ] **Step 1: Write the failing test**（追加到现有测试文件末尾的新类）

```python
# 追加到 stock_screener/tests/test_interactive_screening.py
from screening_prompt_flow import PromptBack, PromptQuit


class NavInputTests(unittest.TestCase):
    def _app(self, value):
        return ScreeningInteractiveApp(
            input_func=lambda _prompt="": value,
            print_func=lambda *a, **k: None,
        )

    def test_nav_input_back(self):
        app = self._app("b")
        with self.assertRaises(PromptBack):
            app._nav_input("x: ")

    def test_nav_input_quit(self):
        app = self._app("q")
        with self.assertRaises(PromptQuit):
            app._nav_input("x: ")

    def test_nav_input_passthrough(self):
        app = self._app("HK")
        self.assertEqual(app._nav_input("x: "), "HK")

    def test_prompt_bool_back_propagates(self):
        app = self._app("b")
        with self.assertRaises(PromptBack):
            app._prompt_bool("启用?", True)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_interactive_screening.NavInputTests -v`
Expected: FAIL with `AttributeError: 'ScreeningInteractiveApp' object has no attribute '_nav_input'`

- [ ] **Step 3: Write minimal implementation**

在 `interactive_screening.py` 顶部 import 区添加：

```python
from screening_prompt_flow import PromptBack, PromptQuit
```

在 `ScreeningInteractiveApp` 的 Prompt helpers 区（`_prompt_choice` 之前）新增：

```python
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
```

然后将下列助手内部对 `self.input(` 的调用替换为 `self._nav_input(`：
`_prompt_choice`（`value = self._nav_input(f"请输入选项 [{default}]: ")`）、
`_prompt_bool`（`value = self._nav_input(...).strip().lower()`）、
`_prompt_int`（`value = self._nav_input(...).strip()`）。
`_prompt_timeframe`/`_prompt_market`/`_prompt_markets`/`_prompt_pools` 均通过 `_prompt_text` 取值，改为这些方法直接调用 `_nav_input` 的封装见 Task 6；本任务仅需保证 `_prompt_bool`/`_prompt_int`/`_prompt_choice` 已走 `_nav_input`。

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_interactive_screening.NavInputTests -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add stock_screener/interactive_screening.py stock_screener/tests/test_interactive_screening.py
git commit -m "feat(screening): add b/q navigation input for prompt helpers"
```

---

## Task 5: 规则链选择按市场过滤 + 编号选择 + 校验

新增纯函数 `resolve_chain_choice(raw, chains, default=None)`（可单测），并重写 `_prompt_chain_for_markets` 仅展示选中市场的链、支持编号、校验。

**Files:**
- Modify: `stock_screener/interactive_screening.py` — 新增 `resolve_chain_choice` 模块级函数；重写 `_prompt_chain_for_markets`。
- Test: `stock_screener/tests/test_screening_chain_select.py`

- [ ] **Step 1: Write the failing test**

```python
# stock_screener/tests/test_screening_chain_select.py
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from interactive_screening import resolve_chain_choice


CHAINS = [
    {"chain_key": "HK_default_zuoyi_and_other", "chain_name": "左一+其他", "enabled": True},
    {"chain_key": "HK_trial_macro", "chain_name": "宏观试跑", "enabled": False},
]


class ChainChoiceTests(unittest.TestCase):
    def test_blank_returns_default_none(self):
        self.assertIsNone(resolve_chain_choice("", CHAINS))

    def test_zero_returns_default_none(self):
        self.assertIsNone(resolve_chain_choice("0", CHAINS))

    def test_number_selects_chain_key(self):
        self.assertEqual(resolve_chain_choice("1", CHAINS), "HK_default_zuoyi_and_other")
        self.assertEqual(resolve_chain_choice("2", CHAINS), "HK_trial_macro")

    def test_direct_key_accepted(self):
        self.assertEqual(resolve_chain_choice("HK_trial_macro", CHAINS), "HK_trial_macro")

    def test_invalid_number_raises(self):
        with self.assertRaises(ValueError):
            resolve_chain_choice("9", CHAINS)

    def test_unknown_key_raises(self):
        with self.assertRaises(ValueError):
            resolve_chain_choice("NOPE", CHAINS)

    def test_no_chains_blank_ok_key_raises(self):
        self.assertIsNone(resolve_chain_choice("", []))
        with self.assertRaises(ValueError):
            resolve_chain_choice("anything", [])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_screening_chain_select -v`
Expected: FAIL with `ImportError: cannot import name 'resolve_chain_choice'`

- [ ] **Step 3: Write minimal implementation**

在 `interactive_screening.py` 模块级（`_query_chains_with_expressions` 之后）新增：

```python
def resolve_chain_choice(raw, chains, default=None):
    """将用户输入解析为 chain_key 或 None(默认链)。

    - 空 / "0" -> default(None)
    - 数字 N -> chains[N-1].chain_key（越界抛 ValueError）
    - 文本 -> 必须等于某个 chain_key（否则抛 ValueError）
    """
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
```

重写 `_prompt_chain_for_markets`（替换整个方法）：

```python
    def _prompt_chain_for_markets(self, markets):
        try:
            db = MarketDatabase(get_db_config())
            all_chains = _query_chains_with_expressions(db)
            db.close()
        except Exception as e:
            self.print(f"⚠ 无法连接数据库读取规则链列表: {e}")
            all_chains = []

        result = {}
        for market in markets:
            label = MARKET_HELP.get(market, market)
            chains = [c for c in all_chains
                      if (c.get("market") or "") in (market, "", "*", "通用")]
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_screening_chain_select -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add stock_screener/interactive_screening.py stock_screener/tests/test_screening_chain_select.py
git commit -m "feat(screening): filter+validate rule-chain selection per market"
```

---

## Task 6: 用步骤引擎重写 `prompt_options` + 启动复用 + 输入便捷性

将 `prompt_options` 改为构建 `Step` 列表交给 `PromptFlow`；启动时若有上次配置给"复用/作默认/重置"选择；market/pool 编号多选；custom 代码必填；help 仅首呈现一次。

**Files:**
- Modify: `stock_screener/interactive_screening.py` — import 新模块；重写 `prompt_options`；新增 `_build_steps`、`_startup_reuse_choice`、`_options_from_answers`；增强 `_prompt_markets`/`_prompt_pools` 编号多选；`_prompt_codes` 去掉示例默认值；`_prompt_text` 的 help 仅首呈现。
- Test: `stock_screener/tests/test_interactive_screening.py`

- [ ] **Step 1: Write the failing test**（新增集成用例类，追加到测试文件）

```python
# 追加到 stock_screener/tests/test_interactive_screening.py
from unittest.mock import patch as _patch


class PromptOptionsFlowTests(unittest.TestCase):
    def _app(self, answers):
        it = iter(answers)
        return ScreeningInteractiveApp(
            input_func=lambda _prompt="": next(it),
            print_func=lambda *a, **k: None,
        )

    def test_custom_flow_minimal(self):
        # 顺序: mode=2, timeframe="", ai=n, mainforce=y, feishu=n, csv="",
        #       market=US, codes=AAPL,MSFT, chain="", confirm=enter
        app = self._app(["2", "", "n", "y", "n", "", "US", "AAPL,MSFT", "", ""])
        with _patch.object(ScreeningInteractiveApp, "_prompt_chain_for_markets",
                           return_value={"US": None}):
            options = app.prompt_options()
        self.assertEqual(options.mode, "custom")
        self.assertEqual(options.market, "US")
        self.assertEqual(options.codes, ["AAPL", "MSFT"])

    def test_full_flow_no_fetch(self):
        # mode=1, timeframe=1d, ai=y, mainforce=n, feishu=n, csv=logs/x.csv,
        # markets=HK,A, pools=best, fetch=n, workers=2,
        # advanced? n, confirm=enter ; chain mocked
        app = self._app(["1", "1d", "y", "n", "n", "logs/x.csv",
                         "HK,A", "best", "n", "2", "n", ""])
        with _patch.object(ScreeningInteractiveApp, "_prompt_chain_for_markets",
                           return_value={"HK": None, "A": "a_trial"}):
            options = app.prompt_options()
        self.assertEqual(options.mode, "full")
        self.assertEqual(options.markets, ["HK", "A"])
        self.assertEqual(options.pools, ["best"])
        self.assertFalse(options.fetch_pools)
        self.assertEqual(options.market_workers, 2)
        self.assertEqual(options.chain_by_market, {"HK": None, "A": "a_trial"})

    def test_codes_required_no_example_default(self):
        app = self._app(["", "AAPL"])  # 第一次空 -> 重试; 第二次有效
        codes = app._prompt_codes("US")
        self.assertEqual(codes, ["AAPL"])


if __name__ == "__main__":
    unittest.main()
```

注意：`prompt_options` 现有"先问全局项再按模式分支"的顺序在新引擎下通过 `Step.visible` 表达；上面的输入顺序对应 `_build_steps` 中定义的步骤顺序。实现 `_build_steps` 时务必让顺序与此一致（mode → timeframe → ai → mainforce → feishu → csv → [full: markets → pools → fetch → (require_fresh) → workers → advanced_gate → (futu_host/port)] / [custom: market → codes] → chain）。

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_interactive_screening.PromptOptionsFlowTests -v`
Expected: FAIL（`prompt_options` 旧实现顺序/字段不匹配，断言失败或 StopIteration）

- [ ] **Step 3: Write minimal implementation**

在 import 区新增：

```python
from screening_config_store import load_last_config, save_last_config
from screening_defaults import resolve_defaults
from screening_prompt_flow import FlowCancelled, PromptBack, PromptFlow, PromptQuit, Step
```

增强 `_prompt_codes`（去掉示例默认值，必填）：

```python
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
```

增强 `_prompt_markets` 支持编号多选（保留代码输入）：

```python
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
```

增强 `_prompt_pools` 支持编号多选：

```python
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
```

新增 `_startup_reuse_choice`：

```python
    def _startup_reuse_choice(self, last_config) -> str:
        """返回 'reuse' / 'defaults' / 'reset'。无上次配置返回 'reset'。"""
        if not last_config:
            return "reset"
        summary = " | ".join(filter(None, [
            ",".join(last_config.get("markets") or []) or last_config.get("market", ""),
            str(last_config.get("timeframe", "")),
        ]))
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
```

新增 `_build_steps`（定义步骤顺序与可见性）：

```python
    def _build_steps(self, default_mode):
        def ask_mode(a, d):
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
                 ask=lambda a, d: self._prompt_bool("是否启用主力资金外部数据（资金流向/盘口等）", bool(d)),
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
                 ask=lambda a, d: self._prompt_markets("筛选市场，逗号分隔",
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
                 ask=lambda a, d: self._prompt_int("市场并发数", int(d or 3),
                                                   minimum=1, maximum=max(1, len(a.get("markets") or [1])))),
            Step("_advanced", "配置高级项", visible=is_full,
                 ask=lambda a, d: self._prompt_bool("是否配置高级项（CSV/Futu）", bool(d)),
                 render_label=lambda v: "是" if v else "否"),
            Step("csv_path", "CSV 路径", advanced=True,
                 visible=lambda a: is_full(a) and adv_on(a),
                 ask=lambda a, d: self._prompt_text("CSV 输出路径", d or "logs/screening_result.csv",
                                                    help_text="输入相对/绝对路径；回车写默认 CSV。")),
            Step("futu_host", "Futu host", advanced=True,
                 visible=lambda a: is_full(a) and adv_on(a),
                 ask=lambda a, d: self._prompt_text("Futu OpenD host", d or "127.0.0.1")),
            Step("futu_port", "Futu port", advanced=True,
                 visible=lambda a: is_full(a) and adv_on(a),
                 ask=lambda a, d: self._prompt_int("Futu OpenD port", int(d or 11111),
                                                   minimum=1, maximum=65535)),
            Step("chain_by_market", "各市场规则链",
                 ask=lambda a, d: self._prompt_chain_for_markets(
                     a.get("markets") if is_full(a) else [a.get("market") or "HK"]),
                 render_label=lambda v: " / ".join(
                     f"{k}→{val or '(默认)'}" for k, val in (v or {}).items())),
        ]
```

重写 `prompt_options`：

```python
    def prompt_options(self, default_mode: Optional[str] = None) -> InteractiveScreeningOptions:
        last_config = load_last_config()
        mode_choice = self._startup_reuse_choice(last_config) if not default_mode else "reset"
        base = last_config if mode_choice in {"reuse", "defaults"} else None
        defaults = resolve_defaults(base)

        steps = self._build_steps(default_mode)
        flow = PromptFlow(steps, defaults, self.input, self.print)

        if mode_choice == "reuse" and last_config:
            answers = dict(last_config)
            # 通过 confirm-only 进入确认页
            answers = flow._confirm(answers)
        else:
            answers = flow.run({} if mode_choice == "reset" else {})

        save_last_config(answers)
        return self._options_from_answers(answers, default_mode)
```

新增 `_options_from_answers`：

```python
    def _options_from_answers(self, answers, default_mode) -> InteractiveScreeningOptions:
        mode = default_mode or answers.get("mode", "full")
        common = dict(
            timeframe=answers.get("timeframe", "1d"),
            enable_ai_analysis=bool(answers.get("enable_ai_analysis", True)),
            enable_main_force_external_data=bool(answers.get("enable_main_force_external_data", True)),
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
```

注意：`_prompt_text` 的 `help_text` 仅首呈现一次——在 `_prompt_text` 中改为：

```python
    def _prompt_text(self, title: str, default: str, help_text: Optional[str] = None) -> str:
        if help_text and title not in self._help_shown:
            self.print(help_text)
            self._help_shown.add(title)
        value = self.input(f"{title} [{default}]: ").strip()
        return value or default
```

并在 `__init__` 增加 `self._help_shown: set = set()`。

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_interactive_screening.PromptOptionsFlowTests -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add stock_screener/interactive_screening.py stock_screener/tests/test_interactive_screening.py
git commit -m "feat(screening): drive prompt_options via step flow with config reuse"
```

---

## Task 7: 优雅退出 + 修正脱节测试 + 全量回归

`run`/`main` 捕获取消与 `Ctrl+C`；修正旧用例（单 `chain_key`/旧顺序）以匹配新引擎；跑全量交互测试。

**Files:**
- Modify: `stock_screener/interactive_screening.py` — `run` 捕获 `FlowCancelled`/`KeyboardInterrupt`。
- Modify: `stock_screener/tests/test_interactive_screening.py` — 删除/重写脱节用例（`test_prompt_custom_code_options`、`test_prompt_options_can_enter_frontend_aligned_mode_directly`、`test_prompt_full_market_options_without_fetch`），其覆盖已由 `PromptOptionsFlowTests` 接管；保留 `_split_csv_values`、`_prompt_timeframe`、`_prompt_pools`、`_apply_main_force_external_data_env` 用例（必要时按编号多选输出微调断言）。

- [ ] **Step 1: Write the failing test**

```python
# 追加到 stock_screener/tests/test_interactive_screening.py
class RunCancelTests(unittest.TestCase):
    def test_run_returns_130_on_cancel(self):
        from screening_prompt_flow import FlowCancelled

        app = ScreeningInteractiveApp(
            input_func=lambda _prompt="": "q",
            print_func=lambda *a, **k: None,
        )

        def boom(*_a, **_k):
            raise FlowCancelled()

        app.prompt_options = boom  # type: ignore
        self.assertEqual(app.run(), 130)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_interactive_screening.RunCancelTests -v`
Expected: FAIL（`run` 未捕获 `FlowCancelled`，异常抛出）

- [ ] **Step 3: Write minimal implementation**

重写 `run`：

```python
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
```

并在 `main()` 外层兜底 `KeyboardInterrupt`：

```python
def main() -> int:
    parser = argparse.ArgumentParser(description="MoneyManager 股票筛选交互式 shell")
    parser.add_argument("--mode", choices=["full", "custom"], default=None,
                        help="直接进入全市场筛选或个股筛选器")
    args = parser.parse_args()
    try:
        return ScreeningInteractiveApp().run(default_mode=args.mode)
    except KeyboardInterrupt:
        print("\n已取消")
        return 130
```

- [ ] **Step 4: Run test + 修正脱节用例 + 全量回归**

删除/重写脱节用例后，运行交互全量与新模块：

Run:
```
python3 -m unittest tests.test_interactive_screening -v
python3 -m unittest tests.test_screening_config_store tests.test_screening_defaults tests.test_screening_prompt_flow tests.test_screening_chain_select -v
```
Expected: 全部 PASS（脱节用例已移除/重写，无 `chain_key` 单字段断言残留）

- [ ] **Step 5: Commit**

```bash
git add stock_screener/interactive_screening.py stock_screener/tests/test_interactive_screening.py
git commit -m "feat(screening): graceful cancel handling and refresh interactive tests"
```

---

## Self-Review 结论

- **Spec 覆盖**：§4 步骤引擎→Task 3；§5 确认页/纠错/Ctrl+C→Task 3 + Task 7；§6 规则链→Task 5；§7 记忆配置/优先级→Task 1/2 + Task 6；§8 文件结构→Task 1/2/3 + Modify；§9 附带修整（help 一次/编号多选/custom 必填/修测）→Task 6 + Task 7；§10 测试→各 Task 测试用例。无遗漏。
- **类型一致性**：`chain_by_market: Dict[str, Optional[str]]` 全程一致；`resolve_chain_choice`/`resolve_defaults`/`load_last_config`/`save_last_config`/`Step`/`PromptFlow`/`PromptBack`/`PromptQuit`/`FlowCancelled` 命名在定义与引用处一致。
- **占位符**：无 TBD/TODO；每个代码步骤均给出完整代码。
- **已知取舍**：`b`/`q` 为受控提示保留字（free-text 的代码/CSV 不拦截），spec §7.1 与 Task 4 一致；`reuse` 路径调用 `flow._confirm` 复用确认页逻辑（同模块内部方法，可接受）。
