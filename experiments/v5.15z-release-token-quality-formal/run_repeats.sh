#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"

BATCH_ID="${AGENTLITE_V515Z_BATCH_ID:-$(date +%Y%m%d-%H%M%S)}"
REPEAT_COUNT="${AGENTLITE_V515Z_REPEAT_COUNT:-3}"
BATCH_ROOT="runs/v5.15z-release-token-quality-formal/${BATCH_ID}-batch"

if [[ "$REPEAT_COUNT" != "3" ]]; then
  echo "Error: the preregistered repeat count is exactly 3." >&2
  exit 2
fi
if [[ -e "$BATCH_ROOT" ]]; then
  echo "Error: batch evidence path already exists: $BATCH_ROOT" >&2
  exit 2
fi

mkdir -p "$BATCH_ROOT" exports
cp "$SCRIPT_DIR/preregistration.json" "$BATCH_ROOT/preregistration.json"
git rev-parse HEAD > "$BATCH_ROOT/harness-git-commit.txt"
printf '%s\n' "$BATCH_ID" > "$BATCH_ROOT/batch-id.txt"
printf '%s\n' "$REPEAT_COUNT" > "$BATCH_ROOT/repeat-count.txt"

statuses=()
for number in $(seq 1 "$REPEAT_COUNT"); do
  repeat_id="${BATCH_ID}-r${number}"
  echo "[repeat ${number}/${REPEAT_COUNT}] starting ${repeat_id}"
  export AGENTLITE_V515Z_EXP_ID="$repeat_id"
  unset AGENTLITE_V515Z_RUN_ROOT
  unset AGENTLITE_V515Z_TRACE_ROOT
  unset AGENTLITE_V515Z_EXPORT_BASE
  set +e
  bash "$SCRIPT_DIR/run_openeuler.sh"
  status=$?
  set -e
  statuses+=("$status")
  printf '%s=%s\n' "$repeat_id" "$status" \
    >> "$BATCH_ROOT/repeat-exit-status.txt"
  repeat_archive="exports/v5.15z-release-token-quality-formal-${repeat_id}.tar.gz"
  if [[ ! -f "$repeat_archive" ]]; then
    echo "Infrastructure failure: repeat archive is missing: $repeat_archive" \
      | tee -a "$BATCH_ROOT/infrastructure-failure.txt" >&2
    break
  fi
done

set +e
python "$SCRIPT_DIR/aggregate_repeats.py" \
  --batch-id "$BATCH_ID" \
  --repeat-count "$REPEAT_COUNT" \
  --output-json "$BATCH_ROOT/repeat_aggregate_report.json" \
  --output-markdown "$BATCH_ROOT/repeat_aggregate_report.md"
aggregate_status=$?
set -e

tar -czf \
  "exports/v5.15z-release-token-quality-formal-${BATCH_ID}-batch.tar.gz" \
  "$BATCH_ROOT"
sha256sum \
  "exports/v5.15z-release-token-quality-formal-${BATCH_ID}-batch.tar.gz" \
  > "exports/v5.15z-release-token-quality-formal-${BATCH_ID}-batch.tar.gz.sha256"
sha256sum -c \
  "exports/v5.15z-release-token-quality-formal-${BATCH_ID}-batch.tar.gz.sha256"

echo "Repeated benchmark complete"
echo "Batch ID: $BATCH_ID"
echo "Aggregate report: $BATCH_ROOT/repeat_aggregate_report.md"
echo "Aggregate exit status: $aggregate_status"
printf 'Repeat exit statuses:'
printf ' %s' "${statuses[@]}"
printf '\n'

exit "$aggregate_status"
