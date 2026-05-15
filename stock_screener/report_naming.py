#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Shared artifact naming helpers for screening reports."""

from __future__ import annotations

import re
from pathlib import Path

from market import market_label


_INVALID_FILENAME_CHARS = re.compile(r'[\\/:*?"<>|\s]+')


def normalize_timeframe_for_filename(timeframe: str) -> str:
    """Keep timeframe readable while removing characters unsafe for filenames."""
    text = str(timeframe or "1d").strip() or "1d"
    return _INVALID_FILENAME_CHARS.sub("_", text)


def market_signal_report_stem(market: str, timeframe: str) -> str:
    """Return `{股票类型}市场信号{timeframe}复核报告`."""
    return f"{market_label(market)}市场信号{normalize_timeframe_for_filename(timeframe)}复核报告"


def market_signal_report_path(csv_base: str, market: str, timeframe: str, extension: str) -> str:
    """Build an output path next to the configured CSV base."""
    suffix = extension if extension.startswith(".") else f".{extension}"
    base = Path(csv_base)
    output_dir = base.parent if str(base.parent) else Path(".")
    return str(output_dir / f"{market_signal_report_stem(market, timeframe)}{suffix}")


def analysis_report_path_for_csv(csv_path: str) -> str:
    """The AI markdown report shares the CSV basename and uses `.md`."""
    path = Path(csv_path)
    if path.suffix:
        return str(path.with_suffix(".md"))
    return f"{csv_path}.md"
