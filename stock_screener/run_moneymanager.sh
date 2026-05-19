#!/usr/bin/env bash
set -euo pipefail

STOCK_SCREENER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPTS_DIR="${STOCK_SCREENER_DIR}/scripts"
FRONTEND_DIR="${STOCK_SCREENER_DIR}/web_frontend"

MM_API_HOST="${MM_API_HOST:-127.0.0.1}"
MM_API_PORT="${MM_API_PORT:-8000}"
MM_WEB_HOST="${MM_WEB_HOST:-127.0.0.1}"
MM_WEB_PORT="${MM_WEB_PORT:-5173}"

resolve_python() {
  if [[ -n "${PYTHON_BIN:-}" ]]; then
    printf '%s\n' "${PYTHON_BIN}"
  elif [[ -x "${STOCK_SCREENER_DIR}/.venv/bin/python" ]]; then
    printf '%s\n' "${STOCK_SCREENER_DIR}/.venv/bin/python"
  else
    printf '%s\n' "python3"
  fi
}

load_env() {
  if [[ -f "${STOCK_SCREENER_DIR}/.env" ]]; then
    set -a
    # shellcheck disable=SC1091
    source "${STOCK_SCREENER_DIR}/.env"
    set +a
  fi
}

ensure_frontend_deps() {
  if [[ -d "${FRONTEND_DIR}/node_modules" ]]; then
    return
  fi
  cd "${FRONTEND_DIR}"
  if [[ -f "package-lock.json" ]]; then
    npm ci
  else
    npm install
  fi
}

print_commands() {
  cat <<'EOF'
Available commands:
  screening        Start stock screener
  option           Start Option Lab shell
  local-agent      Start Python local agent
  go-agent         Start Go agent worker
  go-agent-once    Run Go agent once
  go-agent-test    Run Go agent tests
  backend          Start backend API service
  frontend         Start frontend web service
  web              Start backend and frontend together
EOF
}

print_help() {
  cat <<EOF
MoneyManager launcher

Usage:
  ./run_moneymanager.sh
  ./run_moneymanager.sh <command> [args...]

Environment:
  MM_API_HOST=${MM_API_HOST}
  MM_API_PORT=${MM_API_PORT}
  MM_WEB_HOST=${MM_WEB_HOST}
  MM_WEB_PORT=${MM_WEB_PORT}

EOF
  print_commands
}

run_backend() {
  load_env
  cd "${STOCK_SCREENER_DIR}"
  export PYTHONDONTWRITEBYTECODE="${PYTHONDONTWRITEBYTECODE:-1}"
  export QUANT_ENABLE_FUTU_OPEND="${QUANT_ENABLE_FUTU_OPEND:-1}"
  exec "$(resolve_python)" -m uvicorn web.main:app --host "${MM_API_HOST}" --port "${MM_API_PORT}" --reload "$@"
}

run_frontend() {
  ensure_frontend_deps
  cd "${FRONTEND_DIR}"
  export VITE_BACKEND_URL="${VITE_BACKEND_URL:-http://${MM_API_HOST}:${MM_API_PORT}}"
  exec npm run dev -- --host "${MM_WEB_HOST}" --port "${MM_WEB_PORT}" "$@"
}

run_fullstack() {
  load_env
  cd "${STOCK_SCREENER_DIR}"
  export PYTHONDONTWRITEBYTECODE="${PYTHONDONTWRITEBYTECODE:-1}"
  export QUANT_ENABLE_FUTU_OPEND="${QUANT_ENABLE_FUTU_OPEND:-1}"
  "$(resolve_python)" -m uvicorn web.main:app --host "${MM_API_HOST}" --port "${MM_API_PORT}" --reload &
  backend_pid=$!

  cleanup() {
    if kill -0 "${backend_pid}" 2>/dev/null; then
      kill "${backend_pid}" 2>/dev/null || true
      wait "${backend_pid}" 2>/dev/null || true
    fi
  }
  trap cleanup EXIT INT TERM

  echo "Backend API: http://${MM_API_HOST}:${MM_API_PORT}"
  echo "Frontend: http://${MM_WEB_HOST}:${MM_WEB_PORT}"
  echo

  ensure_frontend_deps
  cd "${FRONTEND_DIR}"
  export VITE_BACKEND_URL="${VITE_BACKEND_URL:-http://${MM_API_HOST}:${MM_API_PORT}}"
  npm run dev -- --host "${MM_WEB_HOST}" --port "${MM_WEB_PORT}" "$@"
}

dispatch() {
  local command="${1:-}"
  if [[ $# -gt 0 ]]; then
    shift
  fi

  case "${command}" in
    screening|screen|stock-screener)
      exec "${SCRIPTS_DIR}/run_screening.sh" "$@"
      ;;
    option|option-lab)
      exec "${SCRIPTS_DIR}/run_option_lab_shell.sh" "$@"
      ;;
    local-agent|python-agent)
      exec "${SCRIPTS_DIR}/run_local_agent.sh" "$@"
      ;;
    go-agent|agent)
      exec "${SCRIPTS_DIR}/run_go_agent.sh" "$@"
      ;;
    go-agent-once|agent-once)
      exec "${SCRIPTS_DIR}/run_go_agent_once.sh" "$@"
      ;;
    go-agent-test|test-go-agent)
      exec "${SCRIPTS_DIR}/test_go_agent.sh" "$@"
      ;;
    backend|api)
      run_backend "$@"
      ;;
    frontend|web-frontend)
      run_frontend "$@"
      ;;
    web|fullstack|full-stack)
      run_fullstack "$@"
      ;;
    --list|list)
      print_commands
      ;;
    --help|-h|help)
      print_help
      ;;
    "")
      interactive_menu
      ;;
    *)
      echo "Unknown command: ${command}" >&2
      echo >&2
      print_commands >&2
      exit 2
      ;;
  esac
}

interactive_menu() {
  while true; do
    cat <<'EOF'
MoneyManager 启动菜单

  1) 选股器
  2) 期权实验室
  3) Python 本地 Agent
  4) Go Agent 常驻 worker
  5) Go Agent 单次执行
  6) Go Agent 测试
  7) 后端 API 服务
  8) 前端 Web 服务
  9) 前后端一起启动
  0) 退出

EOF
    read -r -p "请选择场景 [0]: " choice
    case "${choice:-0}" in
      1) dispatch screening ;;
      2) dispatch option ;;
      3) dispatch local-agent ;;
      4) dispatch go-agent ;;
      5) dispatch go-agent-once ;;
      6) dispatch go-agent-test ;;
      7) dispatch backend ;;
      8) dispatch frontend ;;
      9) dispatch web ;;
      0|q|Q|exit) exit 0 ;;
      *) echo "无效选择：${choice}" ;;
    esac
  done
}

dispatch "$@"
