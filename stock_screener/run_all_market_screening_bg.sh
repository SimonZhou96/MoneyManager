#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

mkdir -p logs

LOG_FILE="${LOG_FILE:-logs/screening_bg_$(date +%Y%m%d_%H%M%S).log}"
PID_FILE="${PID_FILE:-logs/screening_bg.pid}"
KEEP_AWAKE="${KEEP_AWAKE:-1}"
STARTUP_CHECK_DELAY="${STARTUP_CHECK_DELAY:-2}"

if [[ -f "$PID_FILE" ]]; then
  old_pid="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [[ -n "$old_pid" ]] && kill -0 "$old_pid" 2>/dev/null; then
    echo "Screening is already running. pid=$old_pid"
    echo "Log: $LOG_FILE"
    exit 1
  fi
fi

cmd=("./run_all_market_screening.sh" "$@")

if [[ "$KEEP_AWAKE" == "1" ]] && command -v caffeinate >/dev/null 2>&1; then
  cmd=("caffeinate" "-dims" "${cmd[@]}")
fi

nohup "${cmd[@]}" >"$LOG_FILE" 2>&1 &
pid="$!"
echo "$pid" >"$PID_FILE"

sleep "$STARTUP_CHECK_DELAY"
if ! kill -0 "$pid" 2>/dev/null; then
  echo "Background process exited during startup."
  echo "log: $SCRIPT_DIR/$LOG_FILE"
  if [[ -s "$LOG_FILE" ]]; then
    echo
    sed -n '1,80p' "$LOG_FILE"
  fi
  exit 1
fi

echo "Started screening in background."
echo "pid: $pid"
echo "log: $SCRIPT_DIR/$LOG_FILE"
echo "pid file: $SCRIPT_DIR/$PID_FILE"
echo
echo "Watch log:"
echo "  tail -f \"$SCRIPT_DIR/$LOG_FILE\""
echo
echo "Stop:"
echo "  kill $pid"
