#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
TECHNICAL_JUDGE="$REPO_ROOT/experiments/ordinary-developer-autogen/judge_stateful_technical_blind_batch.py"
SUMMARIZE="$REPO_ROOT/experiments/ordinary-developer-autogen/summarize_stateful_blind_scores.py"

EXP_ID="${AGENTLITE_V514L_EXP_ID:-}"
SCENARIO="${1:-A}"
RUN_ROOT="${AGENTLITE_V514L_RUN_ROOT:-runs/v5.14l-typed-reliability-formal-regression/$EXP_ID}"
SCENARIO_ROOT="$RUN_ROOT/$SCENARIO"
COMPARISON="$SCENARIO_ROOT/comparison"

if [[ -z "$EXP_ID" ]]; then
  echo "错误：请先设置 AGENTLITE_V514L_EXP_ID。" >&2
  exit 2
fi
if [[ "$SCENARIO" != "A" && "$SCENARIO" != "B" ]]; then
  echo "错误：场景只能是 A 或 B，当前为：$SCENARIO" >&2
  exit 2
fi
if [[ -n "${OPENAI_API_KEY:-}" && -z "${MIMO_API_KEY:-}" ]]; then
  export MIMO_API_KEY="$OPENAI_API_KEY"
elif [[ -n "${MIMO_API_KEY:-}" && -z "${OPENAI_API_KEY:-}" ]]; then
  export OPENAI_API_KEY="$MIMO_API_KEY"
elif [[ -n "${MIMO_API_KEY:-}" && -n "${OPENAI_API_KEY:-}" ]] \
  && [[ "$MIMO_API_KEY" != "$OPENAI_API_KEY" ]]; then
  echo "错误：MIMO_API_KEY 与 OPENAI_API_KEY 不一致，拒绝选择其中之一。" >&2
  exit 2
elif [[ -z "${MIMO_API_KEY:-}" && -z "${OPENAI_API_KEY:-}" ]]; then
  echo "错误：缺少 API Key，请设置 MIMO_API_KEY 或 OPENAI_API_KEY。" >&2
  exit 2
fi
for path in \
  "$COMPARISON/quality_blind_batch.json" \
  "$COMPARISON/quality_blind_mapping.json" \
  "$COMPARISON/quality_blind_scores.json"; do
  if [[ ! -f "$path" ]]; then
    echo "错误：缺少已冻结的评分输入，不能定点续跑：$path" >&2
    exit 2
  fi
done

mkdir -p "$RUN_ROOT/system"
{
  printf 'technical_resume_started_at=%s\n' "$(date --iso-8601=seconds)"
  printf 'scenario=%s\n' "$SCENARIO"
  printf 'git_commit=%s\n' "$(git rev-parse HEAD)"
  printf 'checkpoint=%s\n' "$COMPARISON/quality_blind_technical_scores.json"
  printf '%s\n' "---"
} >> "$RUN_ROOT/system/scoring-recovery-history.txt"

python "$TECHNICAL_JUDGE" \
  --batch "$COMPARISON/quality_blind_batch.json" \
  --output "$COMPARISON/quality_blind_technical_scores.json" \
  --config configs/llm.mimo.example.json \
  --temperature 0 \
  --timeout-seconds "${OPENAI_TIMEOUT_SECONDS:-300}" \
  --max-retries "${OPENAI_MAX_RETRIES:-6}" \
  --format-retries 2 \
  --resume

python "$SUMMARIZE" \
  --scores "$COMPARISON/quality_blind_scores.json" \
  --technical-scores "$COMPARISON/quality_blind_technical_scores.json" \
  --mapping "$COMPARISON/quality_blind_mapping.json" \
  --output "$COMPARISON/quality_blind_summary.json"

{
  printf 'technical_resume_completed_at=%s\n' "$(date --iso-8601=seconds)"
  printf 'scenario=%s\n' "$SCENARIO"
  printf '%s\n' "---"
} >> "$RUN_ROOT/system/scoring-recovery-history.txt"

echo "技术评分续跑完成：$SCENARIO"
echo "技术评分：$COMPARISON/quality_blind_technical_scores.json"
echo "质量汇总：$COMPARISON/quality_blind_summary.json"
