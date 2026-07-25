#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
BASE_RUNNER="$REPO_ROOT/experiments/v5.14b-fair-cost-quality-preflight/run_openeuler.sh"

EXP_ID="${AGENTLITE_V514D_EXP_ID:-$(date +%Y%m%d-%H%M%S)}"
RUN_ROOT="${AGENTLITE_V514D_RUN_ROOT:-runs/v5.14d-final-artifact-resolution-acceptance/$EXP_ID}"
TRACE_ROOT="${AGENTLITE_V514D_TRACE_ROOT:-.agentlite-exp/v5.14d-final-artifact-resolution-acceptance/$EXP_ID}"
EXPORT_BASE="${AGENTLITE_V514D_EXPORT_BASE:-exports/v5.14d-final-artifact-resolution-acceptance-$EXP_ID}"

export AGENTLITE_V514B_EXP_ID="$EXP_ID"
export AGENTLITE_V514B_RUN_ROOT="$RUN_ROOT"
export AGENTLITE_V514B_TRACE_ROOT="$TRACE_ROOT"
export AGENTLITE_V514B_EXPORT_BASE="$EXPORT_BASE"
export AGENTLITE_V514B_EXPERIMENT_LABEL="v5.14d"
export AGENTLITE_V514B_MEMORY_SCOPE_PREFIX="v514d"
export AGENTLITE_V514B_POST_VERIFY="$SCRIPT_DIR/verify_acceptance.py"
export AGENTLITE_V514B_POST_VERIFY_JSON="$RUN_ROOT/acceptance_report.json"
export AGENTLITE_V514B_POST_VERIFY_MARKDOWN="$RUN_ROOT/acceptance_report.md"

exec bash "$BASE_RUNNER"
