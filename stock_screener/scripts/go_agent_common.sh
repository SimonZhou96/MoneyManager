#!/usr/bin/env bash

go_agent_is_interactive() {
  [[ -t 0 && -t 1 && "${GO_AGENT_NON_INTERACTIVE:-0}" != "1" ]]
}

go_agent_prompt_default() {
  local prompt="$1"
  local default_value="$2"
  local reply=""
  read -r -p "${prompt} [${default_value}]: " reply
  printf '%s\n' "${reply:-${default_value}}"
}

go_agent_confirm() {
  local prompt="$1"
  local default_value="${2:-y}"
  local suffix="[Y/n]"
  local reply=""
  if [[ "${default_value}" == "n" ]]; then
    suffix="[y/N]"
  fi
  read -r -p "${prompt} ${suffix}: " reply
  reply="${reply:-${default_value}}"
  case "${reply}" in
    y|Y|yes|YES|Yes) return 0 ;;
    *) return 1 ;;
  esac
}

go_agent_print_command() {
  printf 'Command:'
  printf ' %q' "$@"
  printf '\n'
}

go_agent_split_csv() {
  local raw="$1"
  local item=""
  local parts=()
  IFS=',' read -r -a parts <<< "${raw}"
  for item in "${parts[@]}"; do
    item="${item#"${item%%[![:space:]]*}"}"
    item="${item%"${item##*[![:space:]]}"}"
    if [[ -n "${item}" ]]; then
      printf '%s\n' "${item}"
    fi
  done
}
