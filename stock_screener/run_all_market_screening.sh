#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [[ -f ".env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source ".env"
  set +a
fi

RUNTIME_HOME="${RUNTIME_HOME:-$SCRIPT_DIR/.runtime_home}"
mkdir -p "$RUNTIME_HOME"
export HOME="$RUNTIME_HOME"
export PYTHONUNBUFFERED="${PYTHONUNBUFFERED:-1}"

if [[ -z "${PYTHON_BIN:-}" ]]; then
  if [[ -x ".venv/bin/python" ]]; then
    PYTHON_BIN=".venv/bin/python"
  else
    PYTHON_BIN="python3"
  fi
fi
MARKETS="${MARKETS:-HK,US,A}"
TIMEFRAME="${TIMEFRAME:-1d}"
CSV_PATH="${CSV_PATH:-logs/screening_result.csv}"
MARKET_WORKERS="${MARKET_WORKERS:-3}"
FUTU_HOST="${FUTU_HOST:-127.0.0.1}"
FUTU_PORT="${FUTU_PORT:-11111}"
NO_FETCH="${NO_FETCH:-0}"
NO_FEISHU="${NO_FEISHU:-0}"
REQUIRE_FRESH_POOLS="${REQUIRE_FRESH_POOLS:-0}"

args=(
  "scheduled_daily_job.py"
  "--markets" "$MARKETS"
  "--timeframe" "$TIMEFRAME"
  "--csv" "$CSV_PATH"
  "--market-workers" "$MARKET_WORKERS"
  "--futu-host" "$FUTU_HOST"
  "--futu-port" "$FUTU_PORT"
)

if [[ "$NO_FETCH" == "1" ]]; then
  args+=("--no-fetch")
fi

if [[ "$NO_FEISHU" == "1" ]]; then
  args+=("--no-feishu")
fi

if [[ "$REQUIRE_FRESH_POOLS" == "1" ]]; then
  args+=("--require-fresh-pools")
fi

exec "$PYTHON_BIN" "${args[@]}" "$@"
