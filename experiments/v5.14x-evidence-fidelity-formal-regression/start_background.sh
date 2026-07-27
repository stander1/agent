#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"

if [[ -t 0 ]]; then
  read -rsp "MiMo API Key: " OPENAI_API_KEY
  echo
else
  IFS= read -r OPENAI_API_KEY
fi
if [[ -z "${OPENAI_API_KEY:-}" ]]; then
  echo "错误：MiMo API Key 为空，未启动正式实验。" >&2
  exit 2
fi

export OPENAI_API_KEY
export MIMO_API_KEY="$OPENAI_API_KEY"
export OPENAI_BASE_URL="${OPENAI_BASE_URL:-https://token-plan-cn.xiaomimimo.com/v1}"
export OPENAI_MODEL="${OPENAI_MODEL:-mimo-v2.5}"
export OPENAI_TIMEOUT_SECONDS="${OPENAI_TIMEOUT_SECONDS:-300}"
export OPENAI_MAX_RETRIES="${OPENAI_MAX_RETRIES:-6}"
export OPENAI_RETRY_BACKOFF_SECONDS="${OPENAI_RETRY_BACKOFF_SECONDS:-3}"

source .venv-agentlite/bin/activate
mkdir -p logs
umask 077

EXP_ID="${AGENTLITE_V514X_EXP_ID:-$(date +%Y%m%d-%H%M%S)}"
export AGENTLITE_V514X_EXP_ID="$EXP_ID"
LOG_PATH="logs/v514x-$EXP_ID.log"

nohup bash "$SCRIPT_DIR/run_openeuler.sh" \
  > "$LOG_PATH" 2>&1 < /dev/null &
PID=$!

echo "EXPERIMENT_ID=$EXP_ID"
echo "PID=$PID"
echo "LOG=$LOG_PATH"
