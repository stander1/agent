#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"

APP="$SCRIPT_DIR/code_app.py"
TASKS="$SCRIPT_DIR/task_sequence.json"
AGENTS="$SCRIPT_DIR/agent_config.json"
COMPARE="experiments/ordinary-developer-autogen/compare_stateful_runs.py"
JUDGE="experiments/ordinary-developer-autogen/judge_stateful_blind_batch.py"
SUMMARIZE="experiments/ordinary-developer-autogen/summarize_stateful_blind_scores.py"
VERIFY="$SCRIPT_DIR/verify_acceptance.py"

: "${OPENAI_API_KEY:=${MIMO_API_KEY:-}}"
: "${OPENAI_BASE_URL:=https://token-plan-cn.xiaomimimo.com/v1}"
: "${OPENAI_MODEL:=mimo-v2.5}"
: "${OPENAI_TIMEOUT_SECONDS:=300}"
: "${OPENAI_MAX_RETRIES:=6}"
: "${OPENAI_RETRY_BACKOFF_SECONDS:=3}"
V513S_TEMPERATURE="${AGENTLITE_V513S_TEMPERATURE:-0}"
V513S_MAX_TURNS="${AGENTLITE_V513S_MAX_TURNS:-8}"

if [[ -z "$OPENAI_API_KEY" ]]; then
  echo "错误：请先设置 OPENAI_API_KEY 或 MIMO_API_KEY。" >&2
  exit 2
fi

export OPENAI_API_KEY OPENAI_BASE_URL OPENAI_MODEL
export OPENAI_TIMEOUT_SECONDS OPENAI_MAX_RETRIES OPENAI_RETRY_BACKOFF_SECONDS
export MIMO_API_KEY="${MIMO_API_KEY:-$OPENAI_API_KEY}"

command -v python >/dev/null
command -v agentlite >/dev/null

EXP_ID="${AGENTLITE_V513S_EXP_ID:-$(date +%Y%m%d-%H%M%S)}"
RUN_ROOT="${AGENTLITE_V513S_RUN_ROOT:-runs/v5.13s-dynamic-capability/$EXP_ID}"
TRACE_ROOT="${AGENTLITE_V513S_TRACE_ROOT:-.agentlite-exp/v5.13s-dynamic-capability/$EXP_ID}"
EXPORT_BASE="exports/v5.13s-dynamic-capability-$EXP_ID"

for path in "$RUN_ROOT" "$TRACE_ROOT" "$EXPORT_BASE.tar.gz"; do
  if [[ -e "$path" ]]; then
    echo "错误：实验路径已经存在，拒绝覆盖证据：$path" >&2
    echo "请设置新的 EXP_ID 后重试。" >&2
    exit 2
  fi
done
mkdir -p "$RUN_ROOT" "$TRACE_ROOT" exports

echo "[1/7] 运行原生 AutoGen 组"
python "$APP" \
  --task-sequence "$TASKS" \
  --agent-config "$AGENTS" \
  --experiment-mode native \
  --temperature "$V513S_TEMPERATURE" \
  --max-turns "$V513S_MAX_TURNS" \
  --output-dir "$RUN_ROOT/native"

echo "[2/7] 运行 AgentLite 仅观测组"
agentlite autogen \
  --data-dir "$TRACE_ROOT/observed" \
  --experiment-dir "$RUN_ROOT/observed" \
  --rewrite off \
  --broadcast-mode shadow-only \
  -- python "$APP" \
    --task-sequence "$TASKS" \
    --agent-config "$AGENTS" \
    --experiment-mode observed \
    --temperature "$V513S_TEMPERATURE" \
    --max-turns "$V513S_MAX_TURNS" \
    --output-dir "$RUN_ROOT/observed"

echo "[3/7] 运行 AgentLite 正式接管组"
export AGENTLITE_MEMORY_SCOPE="v513s-dynamic-$EXP_ID"
agentlite autogen \
  --data-dir "$TRACE_ROOT/managed" \
  --experiment-dir "$RUN_ROOT/managed" \
  --rewrite all \
  -- python "$APP" \
    --task-sequence "$TASKS" \
    --agent-config "$AGENTS" \
    --experiment-mode managed \
    --temperature "$V513S_TEMPERATURE" \
    --max-turns "$V513S_MAX_TURNS" \
    --output-dir "$RUN_ROOT/managed"

echo "[4/7] 校验归档绑定并生成三组对比"
python "$COMPARE" \
  --native-dir "$RUN_ROOT/native" \
  --observed-dir "$RUN_ROOT/observed" \
  --managed-dir "$RUN_ROOT/managed" \
  --output-dir "$RUN_ROOT/comparison"

echo "[5/7] 执行匿名质量评分"
python "$JUDGE" \
  --batch "$RUN_ROOT/comparison/quality_blind_batch.json" \
  --output "$RUN_ROOT/comparison/quality_blind_scores.json" \
  --config configs/llm.mimo.example.json \
  --temperature 0 \
  --timeout-seconds "$OPENAI_TIMEOUT_SECONDS" \
  --max-retries "$OPENAI_MAX_RETRIES" \
  --format-retries 2

python "$SUMMARIZE" \
  --scores "$RUN_ROOT/comparison/quality_blind_scores.json" \
  --mapping "$RUN_ROOT/comparison/quality_blind_mapping.json" \
  --output "$RUN_ROOT/comparison/quality_blind_summary.json"

echo "[6/7] 验证动态能力画像、真实工具、连续记忆与交付质量"
set +e
python "$VERIFY" \
  --native-dir "$RUN_ROOT/native" \
  --observed-dir "$RUN_ROOT/observed" \
  --managed-dir "$RUN_ROOT/managed" \
  --managed-data-dir "$TRACE_ROOT/managed" \
  --quality-summary "$RUN_ROOT/comparison/quality_blind_summary.json" \
  --output-json "$RUN_ROOT/acceptance_report.json" \
  --output-markdown "$RUN_ROOT/acceptance_report.md"
ACCEPTANCE_STATUS=$?
set -e

echo "[7/7] 打包不可变实验数据"
tar -czf "$EXPORT_BASE.tar.gz" "$RUN_ROOT" "$TRACE_ROOT"
sha256sum "$EXPORT_BASE.tar.gz" > "$EXPORT_BASE.tar.gz.sha256"
sha256sum -c "$EXPORT_BASE.tar.gz.sha256"

echo
echo "实验完成"
echo "实验编号：$EXP_ID"
echo "验收报告：$RUN_ROOT/acceptance_report.md"
echo "压缩包：$EXPORT_BASE.tar.gz"
echo "校验文件：$EXPORT_BASE.tar.gz.sha256"

if [[ "$ACCEPTANCE_STATUS" -ne 0 ]]; then
  echo "验收未全部通过，但完整证据已经打包，请取回后分析 acceptance_report.md。" >&2
fi
exit "$ACCEPTANCE_STATUS"
