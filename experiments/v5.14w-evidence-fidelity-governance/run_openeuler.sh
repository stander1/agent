#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"

EXP_ID="${AGENTLITE_V514W_EXP_ID:-$(date +%Y%m%d-%H%M%S)}"
RUN_ROOT="${AGENTLITE_V514W_RUN_ROOT:-runs/v5.14w-evidence-fidelity-governance/$EXP_ID}"
EXPORT_BASE="${AGENTLITE_V514W_EXPORT_BASE:-exports/v5.14w-evidence-fidelity-governance-$EXP_ID}"

if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "错误：存在已跟踪但未提交的修改，拒绝开始 v5.14w 验收。" >&2
  echo "请先提交、还原或另存这些修改，再重新运行。" >&2
  exit 2
fi

for path in "$RUN_ROOT" "$EXPORT_BASE.tar.gz" "$EXPORT_BASE.tar.gz.sha256"; do
  if [[ -e "$path" ]]; then
    echo "错误：验收路径已经存在，拒绝覆盖证据：$path" >&2
    echo "请设置新的 AGENTLITE_V514W_EXP_ID 后重试。" >&2
    exit 2
  fi
done

mkdir -p "$RUN_ROOT/system" exports

git rev-parse HEAD > "$RUN_ROOT/system/git-commit.txt"
git status --short > "$RUN_ROOT/system/git-status.txt"
python --version > "$RUN_ROOT/system/python-version.txt" 2>&1
python -m pip freeze > "$RUN_ROOT/system/pip-freeze.txt"
if command -v agentlite >/dev/null 2>&1; then
  agentlite version > "$RUN_ROOT/system/agentlite-version.txt" 2>&1
  agentlite doctor --framework autogen --json \
    > "$RUN_ROOT/system/agentlite-doctor.json"
fi
if [[ -f /etc/openEuler-release ]]; then
  cat /etc/openEuler-release > "$RUN_ROOT/system/openeuler-release.txt"
fi

echo "[1/5] 运行 v5.14w 定向回归"
python -m unittest \
  tests.test_claim_extractor \
  tests.test_fact_level_memory \
  tests.test_memory_adoption_guard \
  tests.test_autogen_shared_memory \
  tests.test_autogen_session_report \
  tests.test_v514w_evidence_fidelity \
  > "$RUN_ROOT/unittest.txt" 2>&1

echo "[2/5] 生成 v5.14w 机制验收报告"
set +e
python "$SCRIPT_DIR/verify_acceptance.py" \
  --repo-root "$REPO_ROOT" \
  --unittest-output "$RUN_ROOT/unittest.txt" \
  --output-json "$RUN_ROOT/acceptance_report.json" \
  --output-markdown "$RUN_ROOT/acceptance_report.md"
ACCEPTANCE_STATUS=$?
set -e

echo "[3/5] 编译和补丁卫生检查"
python -m compileall -q \
  agent_runtime \
  web_monitor \
  tests \
  experiments/v5.14w-evidence-fidelity-governance
git diff --check

echo "[4/5] 保存源码差异与环境指纹"
git show --stat --oneline HEAD > "$RUN_ROOT/system/head-stat.txt"
git diff HEAD^ HEAD -- \
  agent_runtime \
  web_monitor \
  tests \
  experiments/v5.14w-evidence-fidelity-governance \
  docs > "$RUN_ROOT/system/release-change.patch"

echo "[5/5] 打包不可变证据"
tar -czf "$EXPORT_BASE.tar.gz" "$RUN_ROOT"
sha256sum "$EXPORT_BASE.tar.gz" > "$EXPORT_BASE.tar.gz.sha256"
sha256sum -c "$EXPORT_BASE.tar.gz.sha256"

echo
echo "Experiment complete"
echo "Experiment ID: $EXP_ID"
echo "Acceptance report: $RUN_ROOT/acceptance_report.md"
echo "Archive: $EXPORT_BASE.tar.gz"
echo "Checksum: $EXPORT_BASE.tar.gz.sha256"

if [[ "$ACCEPTANCE_STATUS" -ne 0 ]]; then
  echo "Acceptance has failures; complete evidence was still packaged." >&2
fi
exit "$ACCEPTANCE_STATUS"
