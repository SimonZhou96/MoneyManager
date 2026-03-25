#!/usr/bin/env bash
# 最小 smoke：跑筛选管线集成测试（需 MySQL + 外网）
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export PYTHONPATH="${ROOT}/stock_screener${PYTHONPATH:+:$PYTHONPATH}"
exec python -m pytest tests/test_screen_pipeline_integration.py -v --tb=short -m integration "$@"
