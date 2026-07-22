#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
BASE_DIR="$REPO_ROOT/experiments/v5.13s-dynamic-capability-acceptance"
cd "$REPO_ROOT"

APP="$BASE_DIR/code_app.py"
TASKS="$BASE_DIR/task_sequence.json"
AGENTS="$BASE_DIR/agent_config.json"
COMPARE="experiments/ordinary-developer-autogen/compare_stateful_runs.py"
PRIMARY_JUDGE="experiments/ordinary-developer-autogen/judge_stateful_blind_batch.py"
TECHNICAL_JUDGE="experiments/ordinary-developer-autogen/judge_stateful_technical_blind_batch.py"
SUMMARIZE="experiments/ordinary-developer-autogen/summarize_stateful_blind_scores.py"
VERIFY="$BASE_DIR/verify_acceptance.py"

: "${OPENAI_API_KEY:=${MIMO_API_KEY:-}}"
: "${OPENAI_BASE_URL:=https://token-plan-cn.xiaomimimo.com/v1}"
: "${OPENAI_MODEL:=mimo-v2.5}"
: "${OPENAI_TIMEOUT_SECONDS:=300}"
: "${OPENAI_MAX_RETRIES:=6}"
: "${OPENAI_RETRY_BACKOFF_SECONDS:=3}"
V513V_TEMPERATURE="${AGENTLITE_V513V_TEMPERATURE:-0}"
V513V_MAX_TURNS="${AGENTLITE_V513V_MAX_TURNS:-8}"

if [[ -z "$OPENAI_API_KEY" ]]; then
  echo "ERROR: set OPENAI_API_KEY or MIMO_API_KEY before running." >&2
  exit 2
fi

export OPENAI_API_KEY OPENAI_BASE_URL OPENAI_MODEL
export OPENAI_TIMEOUT_SECONDS OPENAI_MAX_RETRIES OPENAI_RETRY_BACKOFF_SECONDS
export MIMO_API_KEY="${MIMO_API_KEY:-$OPENAI_API_KEY}"

command -v python >/dev/null
command -v agentlite >/dev/null

EXP_ID="${AGENTLITE_V513V_EXP_ID:-$(date +%Y%m%d-%H%M%S)}"
RUN_ROOT="${AGENTLITE_V513V_RUN_ROOT:-runs/v5.13v-conservative-evidence-quality/$EXP_ID}"
TRACE_ROOT="${AGENTLITE_V513V_TRACE_ROOT:-.agentlite-exp/v5.13v-conservative-evidence-quality/$EXP_ID}"
EXPORT_BASE="exports/v5.13v-conservative-evidence-quality-$EXP_ID"

for path in "$RUN_ROOT" "$TRACE_ROOT" "$EXPORT_BASE.tar.gz"; do
  if [[ -e "$path" ]]; then
    echo "ERROR: experiment path already exists: $path" >&2
    echo "Set a new AGENTLITE_V513V_EXP_ID and retry." >&2
    exit 2
  fi
done
mkdir -p "$RUN_ROOT/system" "$TRACE_ROOT" exports

git rev-parse HEAD > "$RUN_ROOT/system/git-commit.txt"
python --version > "$RUN_ROOT/system/python-version.txt" 2>&1
agentlite version > "$RUN_ROOT/system/agentlite-version.txt" 2>&1
if [[ -f /etc/openEuler-release ]]; then
  cat /etc/openEuler-release > "$RUN_ROOT/system/openeuler-release.txt"
fi

echo "[1/8] Native AutoGen"
python "$APP" \
  --task-sequence "$TASKS" \
  --agent-config "$AGENTS" \
  --experiment-mode native \
  --temperature "$V513V_TEMPERATURE" \
  --max-turns "$V513V_MAX_TURNS" \
  --output-dir "$RUN_ROOT/native"

echo "[2/8] AgentLite observed mode"
agentlite autogen \
  --data-dir "$TRACE_ROOT/observed" \
  --experiment-dir "$RUN_ROOT/observed" \
  --rewrite off \
  --broadcast-mode shadow-only \
  -- python "$APP" \
    --task-sequence "$TASKS" \
    --agent-config "$AGENTS" \
    --experiment-mode observed \
    --temperature "$V513V_TEMPERATURE" \
    --max-turns "$V513V_MAX_TURNS" \
    --output-dir "$RUN_ROOT/observed"

echo "[3/8] AgentLite managed mode"
export AGENTLITE_MEMORY_SCOPE="v513v-conservative-evidence-$EXP_ID"
agentlite autogen \
  --data-dir "$TRACE_ROOT/managed" \
  --experiment-dir "$RUN_ROOT/managed" \
  --rewrite all \
  -- python "$APP" \
    --task-sequence "$TASKS" \
    --agent-config "$AGENTS" \
    --experiment-mode managed \
    --temperature "$V513V_TEMPERATURE" \
    --max-turns "$V513V_MAX_TURNS" \
    --output-dir "$RUN_ROOT/managed"

echo "[4/8] Build bound comparison and blind batch"
python "$COMPARE" \
  --native-dir "$RUN_ROOT/native" \
  --observed-dir "$RUN_ROOT/observed" \
  --managed-dir "$RUN_ROOT/managed" \
  --output-dir "$RUN_ROOT/comparison"

echo "[5/8] Primary anonymous quality judge"
python "$PRIMARY_JUDGE" \
  --batch "$RUN_ROOT/comparison/quality_blind_batch.json" \
  --output "$RUN_ROOT/comparison/quality_blind_primary_scores.json" \
  --config configs/llm.mimo.example.json \
  --temperature 0 \
  --timeout-seconds "$OPENAI_TIMEOUT_SECONDS" \
  --max-retries "$OPENAI_MAX_RETRIES" \
  --format-retries 2

echo "[6/8] Independent anonymous technical judge"
python "$TECHNICAL_JUDGE" \
  --batch "$RUN_ROOT/comparison/quality_blind_batch.json" \
  --output "$RUN_ROOT/comparison/quality_blind_technical_scores.json" \
  --config configs/llm.mimo.example.json \
  --temperature 0 \
  --timeout-seconds "$OPENAI_TIMEOUT_SECONDS" \
  --max-retries "$OPENAI_MAX_RETRIES" \
  --format-retries 2

python "$SUMMARIZE" \
  --scores "$RUN_ROOT/comparison/quality_blind_primary_scores.json" \
  --technical-scores "$RUN_ROOT/comparison/quality_blind_technical_scores.json" \
  --mapping "$RUN_ROOT/comparison/quality_blind_mapping.json" \
  --output "$RUN_ROOT/comparison/quality_blind_summary.json"

echo "[7/8] Verify conservative attribution, normalized cost, and quality"
set +e
python "$VERIFY" \
  --native-dir "$RUN_ROOT/native" \
  --observed-dir "$RUN_ROOT/observed" \
  --managed-dir "$RUN_ROOT/managed" \
  --managed-data-dir "$TRACE_ROOT/managed" \
  --quality-summary "$RUN_ROOT/comparison/quality_blind_summary.json" \
  --comparison-summary "$RUN_ROOT/comparison/stateful_comparison.json" \
  --output-json "$RUN_ROOT/acceptance_report.json" \
  --output-markdown "$RUN_ROOT/acceptance_report.md" \
  --report-version v5.13v
ACCEPTANCE_STATUS=$?
set -e

echo "[8/8] Package immutable evidence"
tar -czf "$EXPORT_BASE.tar.gz" "$RUN_ROOT" "$TRACE_ROOT"
sha256sum "$EXPORT_BASE.tar.gz" > "$EXPORT_BASE.tar.gz.sha256"
sha256sum -c "$EXPORT_BASE.tar.gz.sha256"

echo
echo "Experiment complete"
echo "Experiment ID: $EXP_ID"
echo "Acceptance report: $RUN_ROOT/acceptance_report.md"
echo "Archive: $EXPORT_BASE.tar.gz"
echo "Checksum: $EXPORT_BASE.tar.gz.sha256"

if [[ "$ACCEPTANCE_STATUS" -ne 0 ]]; then
  echo "Acceptance has failures; complete evidence was still packaged." >&2
fi
exit "$ACCEPTANCE_STATUS"
