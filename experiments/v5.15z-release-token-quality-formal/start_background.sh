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
OPENAI_API_KEY="${OPENAI_API_KEY%$'\r'}"
if [[ -z "${OPENAI_API_KEY:-}" ]]; then
  echo "Error: MiMo API Key is empty; benchmark was not started." >&2
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

BATCH_ID="${AGENTLITE_V515Z_BATCH_ID:-$(date +%Y%m%d-%H%M%S)}"
export AGENTLITE_V515Z_BATCH_ID="$BATCH_ID"
export AGENTLITE_V515Z_REPEAT_COUNT=3
LOG_PATH="logs/v515z-$BATCH_ID.log"

nohup bash "$SCRIPT_DIR/run_repeats.sh" \
  > "$LOG_PATH" 2>&1 < /dev/null &
PID=$!

echo "BATCH_ID=$BATCH_ID"
echo "REPEAT_COUNT=3"
echo "PLANNED_TASK_EXECUTIONS=180"
echo "PID=$PID"
echo "LOG=$LOG_PATH"
