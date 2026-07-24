#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"

EXP_ID="${AGENTLITE_V513X_EXP_ID:-$(date +%Y%m%d-%H%M%S)}"
RUN_ROOT="${AGENTLITE_V513X_RUN_ROOT:-runs/v5.13x-fact-level-memory/$EXP_ID}"
EXPORT_BASE="exports/v5.13x-fact-level-memory-$EXP_ID"

for path in "$RUN_ROOT" "$EXPORT_BASE.tar.gz"; do
  if [[ -e "$path" ]]; then
    echo "ERROR: acceptance path already exists: $path" >&2
    echo "Set a new AGENTLITE_V513X_EXP_ID and retry." >&2
    exit 2
  fi
done

mkdir -p "$RUN_ROOT/system" exports
git rev-parse HEAD > "$RUN_ROOT/system/git-commit.txt"
python --version > "$RUN_ROOT/system/python-version.txt" 2>&1
agentlite version > "$RUN_ROOT/system/agentlite-version.txt" 2>&1
python -m pip freeze > "$RUN_ROOT/system/pip-freeze.txt"
if [[ -f /etc/openEuler-release ]]; then
  cat /etc/openEuler-release > "$RUN_ROOT/system/openeuler-release.txt"
fi

echo "[1/4] Fact-level TLC-Memory deterministic acceptance"
python "$SCRIPT_DIR/verify_fact_memory.py" \
  --output-dir "$RUN_ROOT/fact-memory"

echo "[2/4] Unit tests"
python -m unittest discover -s tests -p "test_*.py" \
  > "$RUN_ROOT/unittest.txt" 2>&1

echo "[3/4] Compile check"
python -m compileall -q agent_runtime experiments/v5.13x-fact-level-memory

echo "[4/4] Package immutable evidence"
tar -czf "$EXPORT_BASE.tar.gz" "$RUN_ROOT"
sha256sum "$EXPORT_BASE.tar.gz" > "$EXPORT_BASE.tar.gz.sha256"
sha256sum -c "$EXPORT_BASE.tar.gz.sha256"

echo
echo "Acceptance complete"
echo "Experiment ID: $EXP_ID"
echo "Report: $RUN_ROOT/fact-memory/fact_memory_report.md"
echo "Archive: $EXPORT_BASE.tar.gz"
echo "Checksum: $EXPORT_BASE.tar.gz.sha256"
