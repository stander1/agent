#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"

EXP_ID="${AGENTLITE_V515Y_EXP_ID:-$(date +%Y%m%d-%H%M%S)}"
RUN_ROOT="${AGENTLITE_V515Y_RUN_ROOT:-runs/v5.15y-installed-sdist-delivery-readiness/$EXP_ID}"
EXPORT_BASE="${AGENTLITE_V515Y_EXPORT_BASE:-exports/v5.15y-installed-sdist-delivery-readiness-$EXP_ID}"

if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "Error: tracked source changes are present; acceptance is refused." >&2
  exit 2
fi
for path in \
  "$RUN_ROOT" \
  "$EXPORT_BASE.tar.gz" \
  "$EXPORT_BASE.tar.gz.sha256"; do
  if [[ -e "$path" ]]; then
    echo "Error: evidence path already exists: $path" >&2
    exit 2
  fi
done

mkdir -p "$RUN_ROOT/system" exports
git rev-parse HEAD > "$RUN_ROOT/system/git-commit.txt"
git status --short > "$RUN_ROOT/system/git-status.txt"
python --version > "$RUN_ROOT/system/python-version.txt" 2>&1
python -m pip freeze > "$RUN_ROOT/system/pip-freeze.txt"
agentlite version > "$RUN_ROOT/system/agentlite-version.txt"
date --iso-8601=seconds > "$RUN_ROOT/system/system-time.txt"
timedatectl status > "$RUN_ROOT/system/timedatectl.txt" 2>&1 || true
cat /etc/openEuler-release > "$RUN_ROOT/system/openeuler-release.txt"
uname -a > "$RUN_ROOT/system/uname.txt"

echo "[1/6] Run release and installed-sdist contract regressions"
python -m unittest \
  tests.test_release_gate_evidence \
  tests.test_v515w_package_release_hardening_acceptance \
  tests.test_v515x_release_candidate_freeze_acceptance \
  tests.test_v515y_installed_sdist_delivery_readiness \
  > "$RUN_ROOT/focused-unittest.txt" 2>&1

echo "[2/6] Build and verify the independently installed wheel"
set +e
python examples/run_package_release_gate.py \
  --output-dir "$RUN_ROOT/package-gate"
PACKAGE_STATUS=$?
set -e

echo "[3/6] Run the complete source release gate"
set +e
python examples/run_release_gate.py \
  --output-dir "$RUN_ROOT/release-gate"
RELEASE_STATUS=$?
set -e

echo "[4/6] Build, inspect, and install release candidate artifacts"
set +e
python examples/build_release_artifacts.py \
  --dist-dir "$RUN_ROOT/dist" \
  --report-dir "$RUN_ROOT/artifacts"
ARTIFACT_STATUS=$?
set -e

echo "[5/6] Verify RC identity, installed sdist, delivery surface, and hashes"
set +e
python experiments/v5.15x-release-candidate-freeze/verify_acceptance.py \
  --repo-root "$REPO_ROOT" \
  --package-report "$RUN_ROOT/package-gate/package_release_gate_report.json" \
  --release-report "$RUN_ROOT/release-gate/release_gate_report.json" \
  --artifact-report "$RUN_ROOT/artifacts/release_artifacts_report.json" \
  --output-json "$RUN_ROOT/base_acceptance_report.json" \
  --output-markdown "$RUN_ROOT/base_acceptance_report.md"
BASE_ACCEPTANCE_STATUS=$?

python "$SCRIPT_DIR/verify_acceptance.py" \
  --base-report "$RUN_ROOT/base_acceptance_report.json" \
  --artifact-report "$RUN_ROOT/artifacts/release_artifacts_report.json" \
  --pyproject "$REPO_ROOT/pyproject.toml" \
  --delivery-guide "$REPO_ROOT/docs/competition/v0.5.15rc2-delivery-guide.md" \
  --output-json "$RUN_ROOT/acceptance_report.json" \
  --output-markdown "$RUN_ROOT/acceptance_report.md"
ACCEPTANCE_STATUS=$?
set -e

python -m compileall -q \
  agent_runtime \
  examples \
  tests \
  experiments/v5.15y-installed-sdist-delivery-readiness
git diff --check
git show --stat --oneline HEAD > "$RUN_ROOT/system/head-stat.txt"
git diff HEAD^ HEAD -- \
  LICENSE \
  agent_runtime \
  examples \
  tests \
  experiments/v5.15y-installed-sdist-delivery-readiness \
  docs \
  MANIFEST.in \
  pyproject.toml > "$RUN_ROOT/system/release-change.patch"

echo "[6/6] Package immutable release candidate evidence"
tar -czf "$EXPORT_BASE.tar.gz" "$RUN_ROOT"
sha256sum "$EXPORT_BASE.tar.gz" > "$EXPORT_BASE.tar.gz.sha256"
sha256sum -c "$EXPORT_BASE.tar.gz.sha256"

echo
echo "Experiment complete"
echo "Experiment ID: $EXP_ID"
echo "Package gate status: $PACKAGE_STATUS"
echo "Release gate status: $RELEASE_STATUS"
echo "Artifact gate status: $ARTIFACT_STATUS"
echo "Base acceptance status: $BASE_ACCEPTANCE_STATUS"
echo "Acceptance report: $RUN_ROOT/acceptance_report.md"
echo "Archive: $EXPORT_BASE.tar.gz"
echo "Checksum: $EXPORT_BASE.tar.gz.sha256"

if [[ "$ACCEPTANCE_STATUS" -ne 0 ]]; then
  echo "Acceptance has failures; complete evidence was still packaged." >&2
fi
exit "$ACCEPTANCE_STATUS"
