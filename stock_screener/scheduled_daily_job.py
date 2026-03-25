#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
选股定时任务 — 薄入口，转发至 jobs/scheduled_daily_job.py。

主流程实现位于 jobs/ 目录，与 FastAPI/前端 并列，便于区分「Web」与「批处理」。
"""
import os
import runpy
import sys

_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

runpy.run_path(os.path.join(_ROOT, "jobs", "scheduled_daily_job.py"), run_name="__main__")
