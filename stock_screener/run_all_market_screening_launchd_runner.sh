#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

mkdir -p logs
LOG_FILE="logs/launchd_screening_$(date +%Y%m%d_%H%M%S).log"

echo "launchd screening started at $(date)" >>"$LOG_FILE"
exec ./run_all_market_screening.sh >>"$LOG_FILE" 2>&1
