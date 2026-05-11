#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STOCK_SCREENER_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
ENV_FILE="${ENV_FILE:-${STOCK_SCREENER_DIR}/.env}"

cd "${STOCK_SCREENER_DIR}"
exec env PYTHONUNBUFFERED=1 python3 local_agent.py --env-file "${ENV_FILE}" "$@"
