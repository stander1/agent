#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
BASE_DIR="$REPO_ROOT/experiments/v5.13s-dynamic-capability-acceptance"
FACT_DIR="$REPO_ROOT/experiments/v5.13x-fact-level-memory"
cd "$REPO_ROOT"

APP="$BASE_DIR/code_app.py"
TASKS="$BASE_DIR/task_sequence.json"
AGENTS="$BASE_DIR/agent_config.json"
COMPARE="experiments/ordinary-developer-autogen/compare_stateful_runs.py"
PRIMARY_JUDGE="experiments/ordinary-developer-autogen/judge_stateful_blind_batch.py"
TECHNICAL_JUDGE="experiments/ordinary-developer-autogen/judge_stateful_technical_blind_batch.py"
SUMMARIZE="experiments/ordinary-developer-autogen/summarize_stateful_blind_scores.py"
VERIFY="$BASE_DIR/verify_acceptance.py"
FACT_GUARD="$FACT_DIR/verify_fact_memory.py"
FACT_AUDIT="$SCRIPT_DIR/build_fact_audit.py"

: "${OPENAI_API_KEY:=${MIMO_API_KEY:-}}"
: "${OPENAI_BASE_URL:=https://token-plan-cn.xiaomimimo.com/v1}"
: "${OPENAI_MODEL:=mimo-v2.5}"
: "${OPENAI_TIMEOUT_SECONDS:=300}"
: "${OPENAI_MAX_RETRIES:=6}"
: "${OPENAI_RETRY_BACKOFF_SECONDS:=3}"
V513Y_TEMPERATURE="${AGENTLITE_V513Y_TEMPERATURE:-0}"
V513Y_MAX_TURNS="${AGENTLITE_V513Y_MAX_TURNS:-8}"

if [[ -z "$OPENAI_API_KEY" ]]; then
  echo "ERROR: set OPENAI_API_KEY or MIMO_API_KEY before running." >&2
  exit 2
fi

export OPENAI_API_KEY OPENAI_BASE_URL OPENAI_MODEL
export OPENAI_TIMEOUT_SECONDS OPENAI_MAX_RETRIES OPENAI_RETRY_BACKOFF_SECONDS
export MIMO_API_KEY="${MIMO_API_KEY:-$OPENAI_API_KEY}"

command -v python >/dev/null
command -v agentlite >/dev/null

EXP_ID="${AGENTLITE_V513Y_EXP_ID:-$(date +%Y%m%d-%H%M%S)}"
RUN_ROOT="${AGENTLITE_V513Y_RUN_ROOT:-runs/v5.13y-real-autogen-fact-memory/$EXP_ID}"
TRACE_ROOT="${AGENTLITE_V513Y_TRACE_ROOT:-.agentlite-exp/v5.13y-real-autogen-fact-memory/$EXP_ID}"
EXPORT_BASE="exports/v5.13y-real-autogen-fact-memory-$EXP_ID"

for path in "$RUN_ROOT" "$TRACE_ROOT" "$EXPORT_BASE.tar.gz"; do
  if [[ -e "$path" ]]; then
    echo "ERROR: experiment path already exists: $path" >&2
    echo "Set a new AGENTLITE_V513Y_EXP_ID and retry." >&2
    exit 2
  fi
done
mkdir -p "$RUN_ROOT/system" "$TRACE_ROOT" exports

git rev-parse HEAD > "$RUN_ROOT/system/git-commit.txt"
python --version > "$RUN_ROOT/system/python-version.txt" 2>&1
python -m pip freeze > "$RUN_ROOT/system/pip-freeze.txt"
agentlite version > "$RUN_ROOT/system/agentlite-version.txt" 2>&1
agentlite doctor --framework autogen --json \
  > "$RUN_ROOT/system/agentlite-doctor.json"
if [[ -f /etc/openEuler-release ]]; then
  cat /etc/openEuler-release > "$RUN_ROOT/system/openeuler-release.txt"
fi

echo "[1/10] Deterministic fact-level memory gate"
python "$FACT_GUARD" --output-dir "$RUN_ROOT/fact-memory-gate"

echo "[2/10] Native AutoGen"
unset AGENTLITE_MEMORY_SCOPE
python "$APP" \
  --task-sequence "$TASKS" \
  --agent-config "$AGENTS" \
  --experiment-mode native \
  --temperature "$V513Y_TEMPERATURE" \
  --max-turns "$V513Y_MAX_TURNS" \
  --output-dir "$RUN_ROOT/native"

echo "[3/10] AgentLite observed mode"
unset AGENTLITE_MEMORY_SCOPE
agentlite autogen \
  --data-dir "$TRACE_ROOT/observed" \
  --experiment-dir "$RUN_ROOT/observed" \
  --rewrite off \
  --broadcast-mode shadow-only \
  -- python "$APP" \
    --task-sequence "$TASKS" \
    --agent-config "$AGENTS" \
    --experiment-mode observed \
    --temperature "$V513Y_TEMPERATURE" \
    --max-turns "$V513Y_MAX_TURNS" \
    --output-dir "$RUN_ROOT/observed"

echo "[4/10] AgentLite managed mode"
export AGENTLITE_MEMORY_SCOPE="v513y-fact-memory-$EXP_ID"
export AGENTLITE_SHARED_MEMORY="1"
agentlite autogen \
  --data-dir "$TRACE_ROOT/managed" \
  --experiment-dir "$RUN_ROOT/managed" \
  --rewrite all \
  -- python "$APP" \
    --task-sequence "$TASKS" \
    --agent-config "$AGENTS" \
    --experiment-mode managed \
    --temperature "$V513Y_TEMPERATURE" \
    --max-turns "$V513Y_MAX_TURNS" \
    --output-dir "$RUN_ROOT/managed"

echo "[5/10] Bound comparison and normalized common calls"
python "$COMPARE" \
  --native-dir "$RUN_ROOT/native" \
  --observed-dir "$RUN_ROOT/observed" \
  --managed-dir "$RUN_ROOT/managed" \
  --output-dir "$RUN_ROOT/comparison"

echo "[6/10] Primary anonymous quality judge"
python "$PRIMARY_JUDGE" \
  --batch "$RUN_ROOT/comparison/quality_blind_batch.json" \
  --output "$RUN_ROOT/comparison/quality_blind_primary_scores.json" \
  --config configs/llm.mimo.example.json \
  --temperature 0 \
  --timeout-seconds "$OPENAI_TIMEOUT_SECONDS" \
  --max-retries "$OPENAI_MAX_RETRIES" \
  --format-retries 2

echo "[7/10] Independent anonymous technical judge"
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

echo "[8/10] Export fact-level adoption sample"
python "$FACT_AUDIT" \
  --data-dir "$TRACE_ROOT/managed" \
  --session-id latest \
  --output-dir "$RUN_ROOT/fact-attribution-audit"

echo "[9/10] Verify end-to-end evidence"
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
  --report-version v5.13y
ACCEPTANCE_STATUS=$?
set -e

echo "[10/10] Package immutable evidence"
tar -czf "$EXPORT_BASE.tar.gz" "$RUN_ROOT" "$TRACE_ROOT"
sha256sum "$EXPORT_BASE.tar.gz" > "$EXPORT_BASE.tar.gz.sha256"
sha256sum -c "$EXPORT_BASE.tar.gz.sha256"

echo
echo "Experiment complete"
echo "Experiment ID: $EXP_ID"
echo "Acceptance report: $RUN_ROOT/acceptance_report.md"
echo "Fact audit: $RUN_ROOT/fact-attribution-audit/fact_attribution_audit.md"
echo "Archive: $EXPORT_BASE.tar.gz"
echo "Checksum: $EXPORT_BASE.tar.gz.sha256"

if [[ "$ACCEPTANCE_STATUS" -ne 0 ]]; then
  echo "Acceptance has failures; complete evidence was still packaged." >&2
fi
exit "$ACCEPTANCE_STATUS"
