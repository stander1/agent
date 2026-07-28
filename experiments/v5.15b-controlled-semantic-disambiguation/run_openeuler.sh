#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"

EXP_ID="${AGENTLITE_V515B_EXP_ID:-$(date +%Y%m%d-%H%M%S)}"
RUN_ROOT="${AGENTLITE_V515B_RUN_ROOT:-runs/v5.15b-controlled-semantic-disambiguation/$EXP_ID}"
EXPORT_BASE="${AGENTLITE_V515B_EXPORT_BASE:-exports/v5.15b-controlled-semantic-disambiguation-$EXP_ID}"
HOLDOUT_FILE="${AGENTLITE_V515B_HOLDOUT_FILE:-}"

if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "Error: tracked source changes are present; acceptance is refused." >&2
  exit 2
fi
if [[ -z "$HOLDOUT_FILE" || ! -f "$HOLDOUT_FILE" ]]; then
  echo "Error: AGENTLITE_V515B_HOLDOUT_FILE must name an external JSON file." >&2
  exit 2
fi
for path in "$RUN_ROOT" "$EXPORT_BASE.tar.gz" "$EXPORT_BASE.tar.gz.sha256"; do
  if [[ -e "$path" ]]; then
    echo "Error: evidence path already exists: $path" >&2
    exit 2
  fi
done

mkdir -p "$RUN_ROOT/system" exports
cp "$HOLDOUT_FILE" "$RUN_ROOT/holdout_input.json"
sha256sum "$RUN_ROOT/holdout_input.json" \
  > "$RUN_ROOT/holdout_input.json.sha256"

git rev-parse HEAD > "$RUN_ROOT/system/git-commit.txt"
git status --short > "$RUN_ROOT/system/git-status.txt"
python --version > "$RUN_ROOT/system/python-version.txt" 2>&1
python -m pip freeze > "$RUN_ROOT/system/pip-freeze.txt"
date --iso-8601=seconds > "$RUN_ROOT/system/system-time.txt"
if command -v timedatectl >/dev/null 2>&1; then
  timedatectl status > "$RUN_ROOT/system/timedatectl.txt" 2>&1 || true
fi
if command -v agentlite >/dev/null 2>&1; then
  agentlite version > "$RUN_ROOT/system/agentlite-version.txt" 2>&1
  agentlite doctor --framework autogen --json \
    > "$RUN_ROOT/system/agentlite-doctor.json"
fi
if [[ -f /etc/openEuler-release ]]; then
  cat /etc/openEuler-release > "$RUN_ROOT/system/openeuler-release.txt"
fi
uname -a > "$RUN_ROOT/system/uname.txt"

echo "[1/5] Run focused controlled-disambiguation regressions"
python -m unittest \
  tests.test_controlled_semantic_disambiguation \
  tests.test_state_memory_bridge \
  tests.test_agent_metrics \
  tests.test_collaboration_kernel \
  tests.test_autogen_semantic_disambiguation \
  tests.test_autogen_session_report \
  tests.test_v515b_controlled_semantic_disambiguation_acceptance \
  tests.test_web_monitor \
  > "$RUN_ROOT/unittest.txt" 2>&1

echo "[2/5] Generate the v5.15b mechanism acceptance report"
set +e
python "$SCRIPT_DIR/verify_acceptance.py" \
  --repo-root "$REPO_ROOT" \
  --unittest-output "$RUN_ROOT/unittest.txt" \
  --holdout-file "$RUN_ROOT/holdout_input.json" \
  --output-json "$RUN_ROOT/acceptance_report.json" \
  --output-markdown "$RUN_ROOT/acceptance_report.md"
ACCEPTANCE_STATUS=$?
set -e

echo "[3/5] Run compilation and patch hygiene checks"
python -m compileall -q \
  agent_runtime \
  web_monitor \
  tests \
  experiments/v5.15b-controlled-semantic-disambiguation
git diff --check

echo "[4/5] Save release provenance"
git show --stat --oneline HEAD > "$RUN_ROOT/system/head-stat.txt"
git diff HEAD^ HEAD -- \
  agent_runtime \
  web_monitor \
  tests \
  experiments/v5.15b-controlled-semantic-disambiguation \
  docs > "$RUN_ROOT/system/release-change.patch"

echo "[5/5] Package immutable evidence"
tar -czf "$EXPORT_BASE.tar.gz" "$RUN_ROOT"
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
