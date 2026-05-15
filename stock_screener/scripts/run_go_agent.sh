#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STOCK_SCREENER_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
MONEYMANAGER_DIR="$(cd "${STOCK_SCREENER_DIR}/.." && pwd)"
ORCHESTRATOR_DIR="${MONEYMANAGER_DIR}/agent_orchestrator"
source "${SCRIPT_DIR}/go_agent_common.sh"

ENV_FILES=()
for candidate in "${STOCK_SCREENER_DIR}/.env" "${STOCK_SCREENER_DIR}/.agent.env"; do
  if [[ -f "${candidate}" ]]; then
    ENV_FILES+=("${candidate}")
  fi
done
if [[ -n "${ENV_FILE:-}" ]]; then
  ENV_FILES=()
  while IFS= read -r env_file; do
    ENV_FILES+=("${env_file}")
  done < <(go_agent_split_csv "${ENV_FILE}")
fi

INTERACTIVE_RUN=0
if go_agent_is_interactive && { [[ $# -eq 0 ]] || [[ "${GO_AGENT_FORCE_INTERACTIVE:-0}" == "1" ]]; }; then
  INTERACTIVE_RUN=1
  echo "MoneyManager Go Agent"
  echo

  MODE="${GO_AGENT_PRESET_MODE:-}"
  if [[ -z "${MODE}" ]]; then
    echo "Select run mode:"
    echo "  1) worker-once"
    echo "  2) worker"
    echo "  3) help"
    MODE_CHOICE="$(go_agent_prompt_default "Choice" "1")"
    case "${MODE_CHOICE}" in
      2) MODE="worker" ;;
      3) MODE="help" ;;
      *) MODE="worker-once" ;;
    esac
  else
    echo "Run mode: ${MODE}"
  fi

  DEFAULT_ENV_TEXT=""
  if [[ ${#ENV_FILES[@]} -gt 0 ]]; then
    IFS=,
    DEFAULT_ENV_TEXT="${ENV_FILES[*]}"
    unset IFS
  fi
  ENV_TEXT="$(go_agent_prompt_default "Env files, comma separated; use none to skip" "${DEFAULT_ENV_TEXT:-none}")"
  if [[ "${ENV_TEXT}" == "none" ]]; then
    ENV_FILES=()
  else
    ENV_FILES=()
    while IFS= read -r env_file; do
      ENV_FILES+=("${env_file}")
    done < <(go_agent_split_csv "${ENV_TEXT}")
  fi

  AGENT_ARGS=()
  if [[ "${MODE}" == "help" ]]; then
    AGENT_ARGS=(-h)
  else
    AGENT_ARGS=(--mode "${MODE}")
    AGENT_ID_VALUE="$(go_agent_prompt_default "Agent ID" "${AGENT_ID:-go-agent}")"
    AGENT_ARGS+=(--agent-id "${AGENT_ID_VALUE}")
    if [[ "${MODE}" == "worker" ]]; then
      POLL_INTERVAL_VALUE="$(go_agent_prompt_default "Poll interval seconds" "${AGENT_POLL_INTERVAL_SEC:-30}")"
      AGENT_ARGS+=(--poll-interval-sec "${POLL_INTERVAL_VALUE}")
    fi
    if [[ -z "${TRADINGVIEW_MCP_CMD:-}" ]]; then
      MCP_CMD_VALUE="$(go_agent_prompt_default "TRADINGVIEW_MCP_CMD; use none to skip MCP evidence" "none")"
      if [[ "${MCP_CMD_VALUE}" != "none" ]]; then
        export TRADINGVIEW_MCP_CMD="${MCP_CMD_VALUE}"
      fi
    fi
  fi

  set -- "${AGENT_ARGS[@]}"
fi

ENV_ARGS=()
if [[ ${#ENV_FILES[@]} -gt 0 ]]; then
  IFS=,
  ENV_ARGS=(--env-file "${ENV_FILES[*]}")
  unset IFS
fi

cd "${ORCHESTRATOR_DIR}"
CMD=(go run ./cmd/worker)
if [[ ${#ENV_ARGS[@]} -gt 0 ]]; then
  CMD+=("${ENV_ARGS[@]}")
fi
CMD+=("$@")
if [[ "${INTERACTIVE_RUN}" == "1" && "${GO_AGENT_SKIP_CONFIRM:-0}" != "1" ]]; then
  go_agent_print_command "${CMD[@]}"
  go_agent_confirm "Run now" "y" || exit 0
fi
exec "${CMD[@]}"
