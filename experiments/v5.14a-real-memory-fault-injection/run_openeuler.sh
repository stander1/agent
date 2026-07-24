#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"

APP="$SCRIPT_DIR/fault_injection_app.py"
MATRIX="$SCRIPT_DIR/fault_matrix.json"
SEED="$SCRIPT_DIR/seed_fault_memory.py"
VERIFY="$SCRIPT_DIR/verify_fault_injection.py"
V513Z_GATE="$REPO_ROOT/experiments/v5.13z-memory-adoption-repair/verify_repair_guard.py"

: "${OPENAI_API_KEY:=${MIMO_API_KEY:-}}"
: "${OPENAI_BASE_URL:=https://token-plan-cn.xiaomimimo.com/v1}"
: "${OPENAI_MODEL:=mimo-v2.5}"
: "${OPENAI_TIMEOUT_SECONDS:=300}"
: "${OPENAI_MAX_RETRIES:=6}"
: "${OPENAI_RETRY_BACKOFF_SECONDS:=3}"

if [[ -z "$OPENAI_API_KEY" ]]; then
  echo "ERROR: set OPENAI_API_KEY or MIMO_API_KEY before running." >&2
  exit 2
fi

export OPENAI_API_KEY OPENAI_BASE_URL OPENAI_MODEL
export OPENAI_TIMEOUT_SECONDS OPENAI_MAX_RETRIES OPENAI_RETRY_BACKOFF_SECONDS
export MIMO_API_KEY="${MIMO_API_KEY:-$OPENAI_API_KEY}"

command -v python >/dev/null
command -v agentlite >/dev/null

EXP_ID="${AGENTLITE_V514A_EXP_ID:-$(date +%Y%m%d-%H%M%S)}"
RUN_ROOT="${AGENTLITE_V514A_RUN_ROOT:-runs/v5.14a-real-memory-fault-injection/$EXP_ID}"
TRACE_ROOT="${AGENTLITE_V514A_TRACE_ROOT:-.agentlite-exp/v5.14a-real-memory-fault-injection/$EXP_ID}"
EXPORT_BASE="exports/v5.14a-real-memory-fault-injection-$EXP_ID"
REPETITIONS="${AGENTLITE_V514A_REPETITIONS:-2}"
TEMPERATURE="${AGENTLITE_V514A_TEMPERATURE:-0}"
MEMORY_SCOPE="v514a-fault-$EXP_ID"

for path in "$RUN_ROOT" "$TRACE_ROOT" "$EXPORT_BASE.tar.gz"; do
  if [[ -e "$path" ]]; then
    echo "ERROR: experiment path already exists: $path" >&2
    echo "Set a new AGENTLITE_V514A_EXP_ID and retry." >&2
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

echo "[1/7] Deterministic v5.13z repair guard"
python "$V513Z_GATE" --output-dir "$RUN_ROOT/deterministic-guard"

echo "[2/7] Native AutoGen real-provider control"
unset AGENTLITE_MEMORY_SCOPE AGENTLITE_AUTOGEN_SHARED_MEMORY
python "$APP" \
  --matrix "$MATRIX" \
  --experiment-mode native \
  --temperature "$TEMPERATURE" \
  --repetitions "$REPETITIONS" \
  --output-dir "$RUN_ROOT/native"

echo "[3/7] Seed isolated active and historical memory"
python "$SEED" \
  --data-dir "$TRACE_ROOT/managed" \
  --memory-scope "$MEMORY_SCOPE" \
  --matrix "$MATRIX" \
  --output "$RUN_ROOT/managed-memory-seed.json"

echo "[4/7] AgentLite-managed AutoGen real-provider probes"
export AGENTLITE_MEMORY_SCOPE="$MEMORY_SCOPE"
export AGENTLITE_AUTOGEN_SHARED_MEMORY="1"
agentlite autogen \
  --data-dir "$TRACE_ROOT/managed" \
  --rewrite all \
  -- python "$APP" \
    --matrix "$MATRIX" \
    --experiment-mode managed \
    --temperature "$TEMPERATURE" \
    --repetitions "$REPETITIONS" \
    --output-dir "$RUN_ROOT/managed"

echo "[5/7] Verify provider, propagation, repair, block, and false positives"
set +e
python "$VERIFY" \
  --matrix "$MATRIX" \
  --native-dir "$RUN_ROOT/native" \
  --managed-dir "$RUN_ROOT/managed" \
  --managed-data-dir "$TRACE_ROOT/managed" \
  --seed-manifest "$RUN_ROOT/managed-memory-seed.json" \
  --output-json "$RUN_ROOT/acceptance_report.json" \
  --output-markdown "$RUN_ROOT/acceptance_report.md" \
  --output-csv "$RUN_ROOT/fault_audit.csv"
ACCEPTANCE_STATUS=$?
set -e

echo "[6/7] Unit and compile checks"
python -m unittest discover -s tests -p "test_*.py" \
  > "$RUN_ROOT/unittest.txt" 2>&1
python -m compileall -q \
  agent_runtime \
  experiments/v5.14a-real-memory-fault-injection

echo "[7/7] Package immutable evidence"
tar -czf "$EXPORT_BASE.tar.gz" "$RUN_ROOT" "$TRACE_ROOT"
sha256sum "$EXPORT_BASE.tar.gz" > "$EXPORT_BASE.tar.gz.sha256"
sha256sum -c "$EXPORT_BASE.tar.gz.sha256"

echo
echo "Experiment complete"
echo "Experiment ID: $EXP_ID"
echo "Acceptance report: $RUN_ROOT/acceptance_report.md"
echo "Row audit: $RUN_ROOT/fault_audit.csv"
echo "Archive: $EXPORT_BASE.tar.gz"
echo "Checksum: $EXPORT_BASE.tar.gz.sha256"

if [[ "$ACCEPTANCE_STATUS" -ne 0 ]]; then
  echo "Acceptance has failures; complete evidence was still packaged." >&2
fi
exit "$ACCEPTANCE_STATUS"
