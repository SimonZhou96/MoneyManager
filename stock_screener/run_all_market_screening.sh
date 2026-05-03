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

PYTHON_BIN="${PYTHON_BIN:-python3}"
MARKETS="${MARKETS:-HK,US,A}"
TIMEFRAME="${TIMEFRAME:-1d}"
CSV_PATH="${CSV_PATH:-logs/screening_result.csv}"
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
