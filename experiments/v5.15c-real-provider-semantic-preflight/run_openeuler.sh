#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"

EXP_ID="${AGENTLITE_V515C_EXP_ID:-$(date +%Y%m%d-%H%M%S)}"
RUN_ROOT="${AGENTLITE_V515C_RUN_ROOT:-runs/v5.15c-real-provider-semantic-preflight/$EXP_ID}"
EXPORT_BASE="${AGENTLITE_V515C_EXPORT_BASE:-exports/v5.15c-real-provider-semantic-preflight-$EXP_ID}"
SCENARIO_FILE="${AGENTLITE_V515C_SCENARIO_FILE:-}"

if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "Error: tracked source changes are present; preflight is refused." >&2
  exit 2
fi
if [[ -z "$SCENARIO_FILE" || ! -f "$SCENARIO_FILE" ]]; then
  echo "Error: AGENTLITE_V515C_SCENARIO_FILE must name an external JSON file." >&2
  exit 2
fi
if [[ -z "${OPENAI_API_KEY:-}" ]]; then
  echo "Error: OPENAI_API_KEY must be provided through the environment." >&2
  exit 2
fi
for path in "$RUN_ROOT" "$EXPORT_BASE.tar.gz" "$EXPORT_BASE.tar.gz.sha256"; do
  if [[ -e "$path" ]]; then
    echo "Error: evidence path already exists: $path" >&2
    exit 2
  fi
done

mkdir -p "$RUN_ROOT/system" exports
cp "$SCENARIO_FILE" "$RUN_ROOT/scenario_input.json"
sha256sum "$RUN_ROOT/scenario_input.json" \
  > "$RUN_ROOT/scenario_input.json.sha256"
git rev-parse HEAD > "$RUN_ROOT/system/git-commit.txt"
git status --short > "$RUN_ROOT/system/git-status.txt"
python --version > "$RUN_ROOT/system/python-version.txt" 2>&1
python -m pip freeze > "$RUN_ROOT/system/pip-freeze.txt"
date --iso-8601=seconds > "$RUN_ROOT/system/system-time.txt"
timedatectl status > "$RUN_ROOT/system/timedatectl.txt" 2>&1 || true
cat /etc/openEuler-release > "$RUN_ROOT/system/openeuler-release.txt"
uname -a > "$RUN_ROOT/system/uname.txt"

echo "[1/4] Run focused regression tests"
python -m unittest \
  tests.test_controlled_semantic_disambiguation \
  tests.test_state_memory_bridge \
  tests.test_v515c_real_provider_semantic_preflight \
  > "$RUN_ROOT/unittest.txt" 2>&1

echo "[2/4] Run the real Provider semantic preflight"
set +e
python "$SCRIPT_DIR/run_provider_preflight.py" \
  --repo-root "$REPO_ROOT" \
  --scenario-file "$RUN_ROOT/scenario_input.json" \
  --output-dir "$RUN_ROOT/provider"
PREFLIGHT_STATUS=$?
set -e

echo "[3/4] Run compilation and patch hygiene checks"
python -m compileall -q \
  agent_runtime \
  tests \
  experiments/v5.15c-real-provider-semantic-preflight
git diff --check
git show --stat --oneline HEAD > "$RUN_ROOT/system/head-stat.txt"
git diff HEAD^ HEAD -- \
  agent_runtime \
  tests \
  experiments/v5.15c-real-provider-semantic-preflight \
  docs > "$RUN_ROOT/system/release-change.patch"

echo "[4/4] Package immutable evidence"
tar -czf "$EXPORT_BASE.tar.gz" "$RUN_ROOT"
sha256sum "$EXPORT_BASE.tar.gz" > "$EXPORT_BASE.tar.gz.sha256"
sha256sum -c "$EXPORT_BASE.tar.gz.sha256"

echo
echo "Experiment complete"
echo "Experiment ID: $EXP_ID"
echo "Preflight report: $RUN_ROOT/provider/preflight_report.md"
echo "Provider outputs: $RUN_ROOT/provider/provider_outputs.jsonl"
echo "Archive: $EXPORT_BASE.tar.gz"
echo "Checksum: $EXPORT_BASE.tar.gz.sha256"

if [[ "$PREFLIGHT_STATUS" -ne 0 ]]; then
  echo "Preflight has failures; complete evidence was still packaged." >&2
fi
exit "$PREFLIGHT_STATUS"
