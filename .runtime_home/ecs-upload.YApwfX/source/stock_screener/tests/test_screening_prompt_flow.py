#!/usr/bin/env python3
# -*- coding: utf-8 -*-

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

    def _bind(self, *asks, flow):
        for fn in asks:
            fn.flow = flow

    def test_forward_then_confirm(self):
        steps, a, b = self._steps()
        flow, out = _make_flow(steps, ["x", "y", ""])
        self._bind(a, b, flow=flow)
        answers = flow.run()
        self.assertEqual(answers["a"], "x")
        self.assertEqual(answers["b"], "y")
        self.assertIn("配置确认", "\n".join(out))

    def test_back_navigation(self):
        steps, a, b = self._steps()
        flow, _ = _make_flow(steps, ["x", "b", "z", "y", ""])
        self._bind(a, b, flow=flow)
        answers = flow.run()
        self.assertEqual(answers["a"], "z")
        self.assertEqual(answers["b"], "y")

    def test_quit_cancels(self):
        steps, a, b = self._steps()
        flow, _ = _make_flow(steps, ["x", "q"])
        self._bind(a, b, flow=flow)
        with self.assertRaises(FlowCancelled):
            flow.run()

    def test_confirm_edit_by_number(self):
        steps, a, b = self._steps()
        flow, _ = _make_flow(steps, ["x", "y", "1", "z", ""])
        self._bind(a, b, flow=flow)
        answers = flow.run()
        self.assertEqual(answers["a"], "z")
        self.assertEqual(answers["b"], "y")

    def test_invisible_step_skipped(self):
        steps, a, b = self._steps()
        steps[1].visible = lambda ans: False
        flow, _ = _make_flow(steps, ["x", ""])
        self._bind(a, b, flow=flow)
        answers = flow.run()
        self.assertEqual(answers["a"], "x")
        self.assertNotIn("b", answers)

    def test_confirm_quit(self):
        steps, a, b = self._steps()
        flow, _ = _make_flow(steps, ["x", "y", "q"])
        self._bind(a, b, flow=flow)
        with self.assertRaises(FlowCancelled):
            flow.run()


if __name__ == "__main__":
    unittest.main()
