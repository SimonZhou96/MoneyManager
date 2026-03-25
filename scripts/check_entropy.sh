#!/usr/bin/env bash
# 熵管理：对 stock_screener 做「严重级」静态检查（未定义名、语法类），不强制全量 F401（仓库内尚有历史债）。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
exec python3 -m ruff check stock_screener --select F821,F822,F823,E9,F63
