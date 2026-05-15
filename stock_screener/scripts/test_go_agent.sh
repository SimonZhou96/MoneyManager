#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STOCK_SCREENER_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
MONEYMANAGER_DIR="$(cd "${STOCK_SCREENER_DIR}/.." && pwd)"
ORCHESTRATOR_DIR="${MONEYMANAGER_DIR}/agent_orchestrator"
source "${SCRIPT_DIR}/go_agent_common.sh"

INTERACTIVE_TEST=0
if go_agent_is_interactive && [[ $# -eq 0 ]]; then
  INTERACTIVE_TEST=1
  echo "MoneyManager Go Agent Tests"
  echo
  echo "Select test mode:"
  echo "  1) fresh full test: go test -count=1 ./..."
  echo "  2) cached full test: go test ./..."
  echo "  3) specific package or -run pattern"
  TEST_CHOICE="$(go_agent_prompt_default "Choice" "1")"
  case "${TEST_CHOICE}" in
    2)
      set -- ./...
      ;;
    3)
      PACKAGE_VALUE="$(go_agent_prompt_default "Package" "./...")"
      RUN_VALUE="$(go_agent_prompt_default "Run pattern; use none for all tests" "none")"
      if [[ "${RUN_VALUE}" == "none" ]]; then
        set -- "${PACKAGE_VALUE}"
      else
        set -- -run "${RUN_VALUE}" "${PACKAGE_VALUE}"
      fi
      ;;
    *)
      set -- -count=1 ./...
      ;;
  esac
  EXTRA_ARGS="$(go_agent_prompt_default "Extra go test args; use none to skip" "none")"
  if [[ "${EXTRA_ARGS}" != "none" ]]; then
    read -r -a EXTRA_ARG_ARRAY <<< "${EXTRA_ARGS}"
    set -- "$@" "${EXTRA_ARG_ARRAY[@]}"
  fi
fi

cd "${ORCHESTRATOR_DIR}"
if [[ $# -eq 0 ]]; then
  set -- ./...
fi
if [[ "${INTERACTIVE_TEST}" == "1" && "${GO_AGENT_SKIP_CONFIRM:-0}" != "1" ]]; then
  go_agent_print_command go test "$@"
  go_agent_confirm "Run tests now" "y" || exit 0
fi
exec go test "$@"
