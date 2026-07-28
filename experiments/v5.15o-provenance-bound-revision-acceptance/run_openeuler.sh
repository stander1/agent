#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
BASE_APP_DIR="$REPO_ROOT/experiments/v5.15f-integrated-autogen-semantic-preflight"
cd "$REPO_ROOT"

EXP_ID="${AGENTLITE_V515O_EXP_ID:-$(date +%Y%m%d-%H%M%S)}"
RUN_ROOT="${AGENTLITE_V515O_RUN_ROOT:-runs/v5.15o-provenance-bound-revision-acceptance/$EXP_ID}"
TRACE_ROOT="${AGENTLITE_V515O_TRACE_ROOT:-.agentlite-exp/v5.15o-provenance-bound-revision-acceptance/$EXP_ID}"
EXPORT_BASE="${AGENTLITE_V515O_EXPORT_BASE:-exports/v5.15o-provenance-bound-revision-acceptance-$EXP_ID}"
SCENARIO_FILE="${AGENTLITE_V515O_SCENARIO_FILE:-}"
TEAM_FILE="${AGENTLITE_V515O_TEAM_FILE:-}"
MEMORY_SCOPE="v515o-$EXP_ID"

if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "Error: tracked source changes are present; acceptance is refused." >&2
  exit 2
fi
if [[ -z "$SCENARIO_FILE" || ! -f "$SCENARIO_FILE" ]]; then
  echo "Error: AGENTLITE_V515O_SCENARIO_FILE must name an external JSON file." >&2
  exit 2
fi
if [[ -z "$TEAM_FILE" || ! -f "$TEAM_FILE" ]]; then
  echo "Error: AGENTLITE_V515O_TEAM_FILE must name an external JSON file." >&2
  exit 2
fi
if [[ -z "${OPENAI_API_KEY:-}" ]]; then
  echo "Error: OPENAI_API_KEY must be provided through the environment." >&2
  exit 2
fi
if [[ "${OPENAI_MAX_RETRIES:-0}" != "0" ]]; then
  echo "Error: accepted holdout requires OPENAI_MAX_RETRIES=0." >&2
  exit 2
fi
for path in \
  "$RUN_ROOT" \
  "$TRACE_ROOT" \
  "$EXPORT_BASE.tar.gz" \
  "$EXPORT_BASE.tar.gz.sha256"; do
  if [[ -e "$path" ]]; then
    echo "Error: evidence path already exists: $path" >&2
    exit 2
  fi
done

mkdir -p "$RUN_ROOT/system/frozen-inputs" "$TRACE_ROOT" exports
cp "$SCENARIO_FILE" "$RUN_ROOT/system/frozen-inputs/scenario.json"
cp "$TEAM_FILE" "$RUN_ROOT/system/frozen-inputs/team.json"
(
  cd "$RUN_ROOT/system/frozen-inputs"
  sha256sum scenario.json team.json
) > "$RUN_ROOT/system/frozen-inputs.sha256"
git rev-parse HEAD > "$RUN_ROOT/system/git-commit.txt"
git status --short > "$RUN_ROOT/system/git-status.txt"
python --version > "$RUN_ROOT/system/python-version.txt" 2>&1
python -m pip freeze > "$RUN_ROOT/system/pip-freeze.txt"
agentlite version > "$RUN_ROOT/system/agentlite-version.txt"
date --iso-8601=seconds > "$RUN_ROOT/system/system-time.txt"
timedatectl status > "$RUN_ROOT/system/timedatectl.txt" 2>&1 || true
cat /etc/openEuler-release > "$RUN_ROOT/system/openeuler-release.txt"
uname -a > "$RUN_ROOT/system/uname.txt"

echo "[1/6] Run revision admission regressions"
python -m unittest \
  tests.test_controlled_semantic_disambiguation \
  tests.test_state_memory_bridge \
  tests.test_fact_level_memory \
  tests.test_generic_conflict_resolver \
  tests.test_v515f_integrated_autogen_semantic_preflight \
  tests.test_v515o_provenance_bound_revision_acceptance \
  > "$RUN_ROOT/unittest.txt" 2>&1

echo "[2/6] Run the post-commit AutoGen holdout through AgentLite"
export AGENTLITE_MEMORY_SCOPE="$MEMORY_SCOPE"
export AGENTLITE_AUTOGEN_SEMANTIC_DISAMBIGUATION=1
export AGENTLITE_SEMANTIC_DISAMBIGUATION_API_KEY_ENV=OPENAI_API_KEY
export AGENTLITE_SEMANTIC_DISAMBIGUATION_MAX_CALLS_PER_TASK=2
export AGENTLITE_SEMANTIC_DISAMBIGUATION_MAX_CONTROL_TOKENS_PER_TASK=4096
agentlite autogen \
  --data-dir "$TRACE_ROOT" \
  --rewrite all \
  -- python "$BASE_APP_DIR/run_autogen_preflight_app.py" \
    --repo-root "$REPO_ROOT" \
    --scenario-file "$RUN_ROOT/system/frozen-inputs/scenario.json" \
    --team-config "$RUN_ROOT/system/frozen-inputs/team.json" \
    --output-dir "$RUN_ROOT/app"
unset AGENTLITE_MEMORY_SCOPE

echo "[3/6] Export the bound AgentLite session report"
agentlite report autogen-session \
  --data-dir "$TRACE_ROOT" \
  --session-id latest \
  --provider-usage "$RUN_ROOT/app/llm_usage_summary.json" \
  --format json \
  --output "$RUN_ROOT/agentlite_session_report.json"

MEMORY_SNAPSHOT="$TRACE_ROOT/shared_memory/autogen/$MEMORY_SCOPE/memory_store_snapshot.json"
if [[ ! -f "$MEMORY_SNAPSHOT" ]]; then
  echo "Error: memory snapshot is missing: $MEMORY_SNAPSHOT" >&2
  exit 2
fi

echo "[4/6] Verify output, typed revision, history, and field-fetch cost"
set +e
python "$SCRIPT_DIR/verify_acceptance.py" \
  --repo-root "$REPO_ROOT" \
  --scenario "$RUN_ROOT/system/frozen-inputs/scenario.json" \
  --team-config "$RUN_ROOT/system/frozen-inputs/team.json" \
  --workflow-result "$RUN_ROOT/app/workflow_result.json" \
  --session-report "$RUN_ROOT/agentlite_session_report.json" \
  --memory-snapshot "$MEMORY_SNAPSHOT" \
  --output-json "$RUN_ROOT/acceptance_report.json" \
  --output-markdown "$RUN_ROOT/acceptance_report.md"
ACCEPTANCE_STATUS=$?
set -e

echo "[5/6] Run compilation and patch hygiene checks"
python -m compileall -q \
  agent_runtime \
  tests \
  experiments/v5.15o-provenance-bound-revision-acceptance
git diff --check
git show --stat --oneline HEAD > "$RUN_ROOT/system/head-stat.txt"
git diff HEAD^ HEAD -- \
  agent_runtime \
  tests \
  experiments/v5.15o-provenance-bound-revision-acceptance \
  docs > "$RUN_ROOT/system/release-change.patch"

echo "[6/6] Package immutable evidence"
tar -czf "$EXPORT_BASE.tar.gz" "$RUN_ROOT" "$TRACE_ROOT"
sha256sum "$EXPORT_BASE.tar.gz" > "$EXPORT_BASE.tar.gz.sha256"
sha256sum -c "$EXPORT_BASE.tar.gz.sha256"

echo
echo "Experiment complete"
echo "Experiment ID: $EXP_ID"
echo "Acceptance report: $RUN_ROOT/acceptance_report.md"
echo "Workflow outputs: $RUN_ROOT/app/workflow_result.json"
echo "Archive: $EXPORT_BASE.tar.gz"
echo "Checksum: $EXPORT_BASE.tar.gz.sha256"

if [[ "$ACCEPTANCE_STATUS" -ne 0 ]]; then
  echo "Acceptance has failures; complete evidence was still packaged." >&2
fi
exit "$ACCEPTANCE_STATUS"