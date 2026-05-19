#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STOCK_SCREENER_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "$STOCK_SCREENER_DIR"

PYTHON_BIN="${PYTHON_BIN:-}"
if [[ -z "$PYTHON_BIN" ]]; then
  if [[ -x "$STOCK_SCREENER_DIR/.venv/bin/python" ]]; then
    PYTHON_BIN="$STOCK_SCREENER_DIR/.venv/bin/python"
  else
    PYTHON_BIN="python3"
  fi
fi

export PYTHONDONTWRITEBYTECODE="${PYTHONDONTWRITEBYTECODE:-1}"
exec "$PYTHON_BIN" "$STOCK_SCREENER_DIR/interactive_option_lab.py" --shell "$@"
