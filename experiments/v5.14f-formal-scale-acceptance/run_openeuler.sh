#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
BASE_RUNNER="$REPO_ROOT/experiments/v5.14b-fair-cost-quality-preflight/run_openeuler.sh"

EXP_ID="${AGENTLITE_V514F_EXP_ID:-$(date +%Y%m%d-%H%M%S)}"
RUN_ROOT="${AGENTLITE_V514F_RUN_ROOT:-runs/v5.14f-formal-scale-acceptance/$EXP_ID}"
TRACE_ROOT="${AGENTLITE_V514F_TRACE_ROOT:-.agentlite-exp/v5.14f-formal-scale-acceptance/$EXP_ID}"
EXPORT_BASE="${AGENTLITE_V514F_EXPORT_BASE:-exports/v5.14f-formal-scale-acceptance-$EXP_ID}"

export AGENTLITE_V514B_EXP_ID="$EXP_ID"
export AGENTLITE_V514B_RUN_ROOT="$RUN_ROOT"
export AGENTLITE_V514B_TRACE_ROOT="$TRACE_ROOT"
export AGENTLITE_V514B_EXPORT_BASE="$EXPORT_BASE"
export AGENTLITE_V514B_EXPERIMENT_LABEL="v5.14f"
export AGENTLITE_V514B_PHASE_LABEL="正式规模验收"
export AGENTLITE_V514B_MEMORY_SCOPE_PREFIX="v514f"
export AGENTLITE_V514B_PREREGISTRATION="$SCRIPT_DIR/preregistration.json"
export AGENTLITE_V514B_A_TASKS="$SCRIPT_DIR/question_A_formal.json"
export AGENTLITE_V514B_B_TASKS="$SCRIPT_DIR/question_B_formal.json"
export AGENTLITE_V514B_A_AGENTS="experiments/ordinary-developer-autogen/agent_config.json"
export AGENTLITE_V514B_B_AGENTS="experiments/v5.14b-fair-cost-quality-preflight/agent_config_B.json"
export AGENTLITE_V514B_A_TASKS_COPY_NAME="question_A_formal.json"
export AGENTLITE_V514B_B_TASKS_COPY_NAME="question_B_formal.json"
export AGENTLITE_V514B_POST_VERIFY="$SCRIPT_DIR/verify_acceptance.py"
export AGENTLITE_V514B_POST_VERIFY_JSON="$RUN_ROOT/formal_acceptance_report.json"
export AGENTLITE_V514B_POST_VERIFY_MARKDOWN="$RUN_ROOT/formal_acceptance_report.md"

exec bash "$BASE_RUNNER"
