#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"

APP="experiments/ordinary-developer-autogen/code_app.py"
COMPARE="experiments/ordinary-developer-autogen/compare_stateful_runs.py"
PRIMARY_JUDGE="experiments/ordinary-developer-autogen/judge_stateful_blind_batch.py"
TECHNICAL_JUDGE="experiments/ordinary-developer-autogen/judge_stateful_technical_blind_batch.py"
SUMMARIZE="experiments/ordinary-developer-autogen/summarize_stateful_blind_scores.py"
VERIFY="$SCRIPT_DIR/verify_preflight.py"
PREREG="${AGENTLITE_V514B_PREREGISTRATION:-$SCRIPT_DIR/preregistration.json}"
A_TASKS="${AGENTLITE_V514B_A_TASKS:-$SCRIPT_DIR/question_A_preflight.json}"
B_TASKS="${AGENTLITE_V514B_B_TASKS:-$SCRIPT_DIR/question_B_preflight.json}"
A_AGENTS="${AGENTLITE_V514B_A_AGENTS:-experiments/ordinary-developer-autogen/agent_config.json}"
B_AGENTS="${AGENTLITE_V514B_B_AGENTS:-$SCRIPT_DIR/agent_config_B.json}"
A_TASKS_COPY_NAME="${AGENTLITE_V514B_A_TASKS_COPY_NAME:-question_A_preflight.json}"
B_TASKS_COPY_NAME="${AGENTLITE_V514B_B_TASKS_COPY_NAME:-question_B_preflight.json}"
PHASE_LABEL="${AGENTLITE_V514B_PHASE_LABEL:-预检}"

: "${OPENAI_API_KEY:=${MIMO_API_KEY:-}}"
: "${OPENAI_BASE_URL:=https://token-plan-cn.xiaomimimo.com/v1}"
: "${OPENAI_MODEL:=mimo-v2.5}"
: "${OPENAI_TIMEOUT_SECONDS:=300}"
: "${OPENAI_MAX_RETRIES:=6}"
: "${OPENAI_RETRY_BACKOFF_SECONDS:=3}"

if [[ -z "$OPENAI_API_KEY" ]]; then
  echo "错误：请先设置 OPENAI_API_KEY 或 MIMO_API_KEY。" >&2
  exit 2
fi
if [[ "$OPENAI_BASE_URL" != "https://token-plan-cn.xiaomimimo.com/v1" ]]; then
  echo "错误：v5.14b 预注册要求 OPENAI_BASE_URL=https://token-plan-cn.xiaomimimo.com/v1" >&2
  exit 2
fi
if [[ "$OPENAI_MODEL" != "mimo-v2.5" ]]; then
  echo "错误：v5.14b 预注册要求 OPENAI_MODEL=mimo-v2.5" >&2
  exit 2
fi

export OPENAI_API_KEY OPENAI_BASE_URL OPENAI_MODEL
export OPENAI_TIMEOUT_SECONDS OPENAI_MAX_RETRIES OPENAI_RETRY_BACKOFF_SECONDS
export MIMO_API_KEY="$OPENAI_API_KEY"

command -v python >/dev/null
command -v agentlite >/dev/null
command -v tar >/dev/null
command -v sha256sum >/dev/null

for input_path in "$PREREG" "$A_TASKS" "$B_TASKS" "$A_AGENTS" "$B_AGENTS"; do
  if [[ ! -f "$input_path" ]]; then
    echo "错误：冻结输入不存在：$input_path" >&2
    exit 2
  fi
done

if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "错误：存在已跟踪但未提交的修改，拒绝开始$PHASE_LABEL。" >&2
  echo "请先提交、还原或另存这些修改，再重新运行。" >&2
  exit 2
fi

EXP_ID="${AGENTLITE_V514B_EXP_ID:-$(date +%Y%m%d-%H%M%S)}"
RUN_ROOT="${AGENTLITE_V514B_RUN_ROOT:-runs/v5.14b-fair-cost-quality-preflight/$EXP_ID}"
TRACE_ROOT="${AGENTLITE_V514B_TRACE_ROOT:-.agentlite-exp/v5.14b-fair-cost-quality-preflight/$EXP_ID}"
EXPORT_BASE="${AGENTLITE_V514B_EXPORT_BASE:-exports/v5.14b-fair-cost-quality-preflight-$EXP_ID}"
EXPERIMENT_LABEL="${AGENTLITE_V514B_EXPERIMENT_LABEL:-v5.14b}"
MEMORY_SCOPE_PREFIX="${AGENTLITE_V514B_MEMORY_SCOPE_PREFIX:-v514b}"
POST_VERIFY="${AGENTLITE_V514B_POST_VERIFY:-}"
POST_VERIFY_JSON="${AGENTLITE_V514B_POST_VERIFY_JSON:-$RUN_ROOT/post_verify_report.json}"
POST_VERIFY_MARKDOWN="${AGENTLITE_V514B_POST_VERIFY_MARKDOWN:-$RUN_ROOT/post_verify_report.md}"
TEMPERATURE=0
MAX_TURNS=9

for path in "$RUN_ROOT" "$TRACE_ROOT" "$EXPORT_BASE.tar.gz"; do
  if [[ -e "$path" ]]; then
    echo "错误：实验路径已经存在，拒绝覆盖证据：$path" >&2
    echo "请设置新的 AGENTLITE_V514B_EXP_ID 后重试。" >&2
    exit 2
  fi
done

mkdir -p "$RUN_ROOT/system/frozen-inputs" "$TRACE_ROOT" exports
cp "$PREREG" "$RUN_ROOT/system/preregistration.json"
cp "$PREREG" "$RUN_ROOT/system/frozen-inputs/preregistration.json"
cp "$A_TASKS" "$RUN_ROOT/system/frozen-inputs/$A_TASKS_COPY_NAME"
cp "$B_TASKS" "$RUN_ROOT/system/frozen-inputs/$B_TASKS_COPY_NAME"
cp "$A_AGENTS" "$RUN_ROOT/system/frozen-inputs/agent_config_A.json"
cp "$B_AGENTS" "$RUN_ROOT/system/frozen-inputs/agent_config_B.json"
sha256sum \
  "$PREREG" "$A_TASKS" "$B_TASKS" "$A_AGENTS" "$B_AGENTS" \
  > "$RUN_ROOT/system/frozen-inputs.sha256"
(
  cd "$RUN_ROOT/system/frozen-inputs"
  sha256sum \
    preregistration.json \
    "$A_TASKS_COPY_NAME" \
    "$B_TASKS_COPY_NAME" \
    agent_config_A.json \
    agent_config_B.json
) > "$RUN_ROOT/system/frozen-input-copies.sha256"
git rev-parse HEAD > "$RUN_ROOT/system/git-commit.txt"
git status --short --untracked-files=no > "$RUN_ROOT/system/git-status.txt"
python --version > "$RUN_ROOT/system/python-version.txt" 2>&1
python -m pip freeze > "$RUN_ROOT/system/pip-freeze.txt"
agentlite version > "$RUN_ROOT/system/agentlite-version.txt"
if [[ -f /etc/openEuler-release ]]; then
  cat /etc/openEuler-release > "$RUN_ROOT/system/openeuler-release.txt"
fi
uname -a > "$RUN_ROOT/system/uname.txt"

run_scenario() {
  local label="$1"
  local tasks="$2"
  local agents="$3"
  local scenario_root="$RUN_ROOT/$label"
  local trace_root="$TRACE_ROOT/$label"

  mkdir -p "$scenario_root/reports"

  echo "[$label 1/8] Native：原生 AutoGen"
  python "$APP" \
    --question-sequence-file "$tasks" \
    --agent-config "$agents" \
    --temperature "$TEMPERATURE" \
    --max-turns "$MAX_TURNS" \
    --experiment-mode native \
    --output-dir "$scenario_root/native"

  echo "[$label 2/8] Observed：AgentLite 只观察，不改写"
  agentlite autogen \
    --data-dir "$trace_root/observed" \
    --experiment-dir "$scenario_root/observed" \
    --rewrite off \
    --broadcast-mode shadow-only \
    -- python "$APP" \
      --question-sequence-file "$tasks" \
      --agent-config "$agents" \
      --temperature "$TEMPERATURE" \
      --max-turns "$MAX_TURNS" \
      --experiment-mode observed \
      --output-dir "$scenario_root/observed"

  echo "[$label 3/8] Managed：AgentLite 正式接管"
  export AGENTLITE_MEMORY_SCOPE="${MEMORY_SCOPE_PREFIX}-${label}-${EXP_ID}"
  agentlite autogen \
    --data-dir "$trace_root/managed" \
    --experiment-dir "$scenario_root/managed" \
    --rewrite all \
    -- python "$APP" \
      --question-sequence-file "$tasks" \
      --agent-config "$agents" \
      --temperature "$TEMPERATURE" \
      --max-turns "$MAX_TURNS" \
      --experiment-mode managed \
      --output-dir "$scenario_root/managed"
  unset AGENTLITE_MEMORY_SCOPE

  echo "[$label 4/8] 校验三组不可变绑定并生成匿名候选"
  python "$COMPARE" \
    --native-dir "$scenario_root/native" \
    --observed-dir "$scenario_root/observed" \
    --managed-dir "$scenario_root/managed" \
    --output-dir "$scenario_root/comparison"

  echo "[$label 5/8] 导出 AgentLite 观测与接管报告"
  agentlite report autogen-session \
    --experiment-dir "$scenario_root/observed" \
    --format json \
    --output "$scenario_root/reports/observed-agentlite.json"
  agentlite report autogen-session \
    --experiment-dir "$scenario_root/managed" \
    --format json \
    --output "$scenario_root/reports/managed-agentlite.json"

  echo "[$label 6/8] 冻结匿名综合质量评分"
  python "$PRIMARY_JUDGE" \
    --batch "$scenario_root/comparison/quality_blind_batch.json" \
    --output "$scenario_root/comparison/quality_blind_scores.json" \
    --config configs/llm.mimo.example.json \
    --temperature 0 \
    --timeout-seconds "$OPENAI_TIMEOUT_SECONDS" \
    --max-retries "$OPENAI_MAX_RETRIES" \
    --format-retries 2

  echo "[$label 7/8] 冻结匿名技术质量评分"
  python "$TECHNICAL_JUDGE" \
    --batch "$scenario_root/comparison/quality_blind_batch.json" \
    --output "$scenario_root/comparison/quality_blind_technical_scores.json" \
    --config configs/llm.mimo.example.json \
    --temperature 0 \
    --timeout-seconds "$OPENAI_TIMEOUT_SECONDS" \
    --max-retries "$OPENAI_MAX_RETRIES" \
    --format-retries 2

  echo "[$label 8/8] 解盲并汇总双重质量结果"
  python "$SUMMARIZE" \
    --scores "$scenario_root/comparison/quality_blind_scores.json" \
    --technical-scores "$scenario_root/comparison/quality_blind_technical_scores.json" \
    --mapping "$scenario_root/comparison/quality_blind_mapping.json" \
    --output "$scenario_root/comparison/quality_blind_summary.json"
}

echo "开始 $EXPERIMENT_LABEL $PHASE_LABEL，实验编号：$EXP_ID"
run_scenario "A" "$A_TASKS" "$A_AGENTS"
run_scenario "B" "$B_TASKS" "$B_AGENTS"

echo "[final 1/3] 按预注册阈值执行统一$PHASE_LABEL"
set +e
python "$VERIFY" \
  --run-root "$RUN_ROOT" \
  --preregistration "$RUN_ROOT/system/preregistration.json" \
  --output-json "$RUN_ROOT/preflight_report.json" \
  --output-markdown "$RUN_ROOT/preflight_report.md"
ACCEPTANCE_STATUS=$?
set -e

if [[ -n "$POST_VERIFY" ]]; then
  echo "[final 2/3] 执行阶段专用附加验收"
  set +e
  python "$POST_VERIFY" \
    --run-root "$RUN_ROOT" \
    --preflight-report "$RUN_ROOT/preflight_report.json" \
    --output-json "$POST_VERIFY_JSON" \
    --output-markdown "$POST_VERIFY_MARKDOWN"
  POST_VERIFY_STATUS=$?
  set -e
  if [[ "$POST_VERIFY_STATUS" -ne 0 ]]; then
    ACCEPTANCE_STATUS="$POST_VERIFY_STATUS"
  fi
else
  echo "[final 2/3] 未配置阶段专用附加验收，跳过"
fi

echo "[final 3/3] 打包不可变证据"
tar -czf "$EXPORT_BASE.tar.gz" "$RUN_ROOT" "$TRACE_ROOT"
sha256sum "$EXPORT_BASE.tar.gz" > "$EXPORT_BASE.tar.gz.sha256"
sha256sum -c "$EXPORT_BASE.tar.gz.sha256"

echo
echo "Experiment complete"
echo "Experiment ID: $EXP_ID"
echo "Preflight report: $RUN_ROOT/preflight_report.md"
if [[ -n "$POST_VERIFY" ]]; then
  echo "Stage acceptance report: $POST_VERIFY_MARKDOWN"
fi
echo "Archive: $EXPORT_BASE.tar.gz"
echo "Checksum: $EXPORT_BASE.tar.gz.sha256"

if [[ "$ACCEPTANCE_STATUS" -ne 0 ]]; then
  echo "Preflight has failures; complete evidence was still packaged." >&2
fi
exit "$ACCEPTANCE_STATUS"
