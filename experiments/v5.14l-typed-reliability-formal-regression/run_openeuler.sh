#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
BASE_RUNNER="$REPO_ROOT/experiments/v5.14b-fair-cost-quality-preflight/run_openeuler.sh"
FORMAL_INPUTS="$REPO_ROOT/experiments/v5.14f-formal-scale-acceptance"

EXP_ID="${AGENTLITE_V514L_EXP_ID:-$(date +%Y%m%d-%H%M%S)}"
RUN_ROOT="${AGENTLITE_V514L_RUN_ROOT:-runs/v5.14l-typed-reliability-formal-regression/$EXP_ID}"
TRACE_ROOT="${AGENTLITE_V514L_TRACE_ROOT:-.agentlite-exp/v5.14l-typed-reliability-formal-regression/$EXP_ID}"
EXPORT_BASE="${AGENTLITE_V514L_EXPORT_BASE:-exports/v5.14l-typed-reliability-formal-regression-$EXP_ID}"

export AGENTLITE_V514B_EXP_ID="$EXP_ID"
export AGENTLITE_V514B_RUN_ROOT="$RUN_ROOT"
export AGENTLITE_V514B_TRACE_ROOT="$TRACE_ROOT"
export AGENTLITE_V514B_EXPORT_BASE="$EXPORT_BASE"
export AGENTLITE_V514B_EXPERIMENT_LABEL="v5.14l"
export AGENTLITE_V514B_PHASE_LABEL="typed reliability formal regression"
export AGENTLITE_V514B_MEMORY_SCOPE_PREFIX="v514l"
export AGENTLITE_V514B_PREREGISTRATION="$SCRIPT_DIR/preregistration.json"
export AGENTLITE_V514B_A_TASKS="$FORMAL_INPUTS/question_A_formal.json"
export AGENTLITE_V514B_B_TASKS="$FORMAL_INPUTS/question_B_formal.json"
export AGENTLITE_V514B_A_AGENTS="experiments/ordinary-developer-autogen/agent_config.json"
export AGENTLITE_V514B_B_AGENTS="experiments/v5.14b-fair-cost-quality-preflight/agent_config_B.json"
export AGENTLITE_V514B_A_TASKS_COPY_NAME="question_A_formal.json"
export AGENTLITE_V514B_B_TASKS_COPY_NAME="question_B_formal.json"
export AGENTLITE_V514B_POST_VERIFY="$SCRIPT_DIR/verify_acceptance.py"
export AGENTLITE_V514B_POST_VERIFY_JSON="$RUN_ROOT/typed_reliability_formal_report.json"
export AGENTLITE_V514B_POST_VERIFY_MARKDOWN="$RUN_ROOT/typed_reliability_formal_report.md"
export AGENTLITE_V514B_RESUME="${AGENTLITE_V514L_RESUME:-0}"

exec bash "$BASE_RUNNER"
