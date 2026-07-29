#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"

ABLATION_ID="${AGENTLITE_V515Z_ABLATION_ID:-$(date +%Y%m%d-%H%M%S)}"
REPEAT_COUNT="${AGENTLITE_V515Z_ABLATION_REPEAT_COUNT:-1}"
OUTPUT_ROOT="runs/v5.15z-release-token-quality-formal/${ABLATION_ID}-memory-ablation"

if [[ ! "$REPEAT_COUNT" =~ ^[1-9][0-9]*$ ]]; then
  echo "Error: ablation repeat count must be a positive integer." >&2
  exit 2
fi
if [[ -e "$OUTPUT_ROOT" ]]; then
  echo "Error: ablation output already exists: $OUTPUT_ROOT" >&2
  exit 2
fi

mkdir -p "$OUTPUT_ROOT"
git rev-parse HEAD > "$OUTPUT_ROOT/harness-git-commit.txt"
printf '%s\n' "$ABLATION_ID" > "$OUTPUT_ROOT/ablation-id.txt"
printf '%s\n' "$REPEAT_COUNT" > "$OUTPUT_ROOT/repeat-count.txt"
printf '%s\n' \
  "Only AGENTLITE_AUTOGEN_SHARED_MEMORY changes between variants." \
  > "$OUTPUT_ROOT/isolation-contract.txt"

for variant in memory-on memory-off; do
  if [[ "$variant" == "memory-on" ]]; then
    shared_memory="1"
  else
    shared_memory="0"
  fi
  for number in $(seq 1 "$REPEAT_COUNT"); do
    run_id="${ABLATION_ID}-${variant}-r${number}"
    echo "[$variant ${number}/${REPEAT_COUNT}] starting $run_id"
    export AGENTLITE_AUTOGEN_SHARED_MEMORY="$shared_memory"
    export AGENTLITE_V515Z_EXP_ID="$run_id"
    unset AGENTLITE_V515Z_RUN_ROOT
    unset AGENTLITE_V515Z_TRACE_ROOT
    unset AGENTLITE_V515Z_EXPORT_BASE
    set +e
    bash "$SCRIPT_DIR/run_openeuler.sh"
    status=$?
    set -e
    printf '%s=%s\n' "$run_id" "$status" \
      >> "$OUTPUT_ROOT/run-exit-status.txt"
    archive="exports/v5.15z-release-token-quality-formal-${run_id}.tar.gz"
    if [[ ! -f "$archive" ]]; then
      echo "Infrastructure failure: missing archive: $archive" >&2
      exit 3
    fi
  done
done
unset AGENTLITE_AUTOGEN_SHARED_MEMORY

python "$SCRIPT_DIR/compare_memory_ablation.py" \
  --ablation-id "$ABLATION_ID" \
  --repeat-count "$REPEAT_COUNT" \
  --output-json "$OUTPUT_ROOT/memory_ablation_report.json" \
  --output-markdown "$OUTPUT_ROOT/memory_ablation_report.md"

tar -czf \
  "exports/v5.15z-release-token-quality-formal-${ABLATION_ID}-memory-ablation.tar.gz" \
  "$OUTPUT_ROOT"
sha256sum \
  "exports/v5.15z-release-token-quality-formal-${ABLATION_ID}-memory-ablation.tar.gz" \
  > "exports/v5.15z-release-token-quality-formal-${ABLATION_ID}-memory-ablation.tar.gz.sha256"
sha256sum -c \
  "exports/v5.15z-release-token-quality-formal-${ABLATION_ID}-memory-ablation.tar.gz.sha256"

echo "Shared-memory ablation complete"
echo "Report: $OUTPUT_ROOT/memory_ablation_report.md"
