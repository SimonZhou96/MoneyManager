#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/go_agent_common.sh"

if go_agent_is_interactive && [[ $# -eq 0 ]]; then
  export GO_AGENT_PRESET_MODE=worker-once
  export GO_AGENT_FORCE_INTERACTIVE=1
  exec "${SCRIPT_DIR}/run_go_agent.sh"
fi

exec "${SCRIPT_DIR}/run_go_agent.sh" --mode worker-once "$@"
